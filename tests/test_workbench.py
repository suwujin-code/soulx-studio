from __future__ import annotations

import tempfile
import time
import unittest
import wave
from dataclasses import replace
from pathlib import Path

from workbench.config import WorkbenchConfig
from workbench.queue import JobWorker
from workbench.store import ConflictError, WorkbenchStore
from workbench.synthesizer import DryRunSynthesizer


def make_wav(path: Path, sample: bytes = b"\x00\x00") -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(sample * 24000)


class WorkbenchTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="soulx-test-")
        self.root = Path(self.temporary.name)
        self.config = replace(
            WorkbenchConfig.from_env(),
            data_dir=self.root / "data",
            dry_run=True,
            worker_poll_seconds=0.03,
        )
        self.store = WorkbenchStore(self.config)
        self.reference = self.root / "reference.wav"
        make_wav(self.reference)

    def tearDown(self):
        self.temporary.cleanup()

    def create_voice(self, name="香港本地男声"):
        return self.store.create_voice(
            name=name,
            audio_file=self.reference,
            transcript="香港嘅夜景真係好靓。",
            dialect="yue",
            region="香港粤语",
            gender="男声",
            style="沉稳",
            dialect_prompt="<|Yue|>香港嘅夜景真係好靓。",
            tags=["香港", "播客"],
            source_authorization="测试音频，仅用于自动化验收",
            favorite=True,
        )

    def test_voice_crud_duplicate_and_safe_delete(self):
        voice = self.create_voice()
        self.assertTrue(voice["favorite"])
        changed = self.store.update_voice(
            voice["id"], favorite=False, tags=["本地", "粤语"], name="香港旁白"
        )
        self.assertEqual(changed["name"], "香港旁白")
        self.assertEqual(changed["tags"], ["本地", "粤语"])
        with self.assertRaises(ConflictError):
            self.create_voice("重复音色")
        job = self.store.create_job(text="测试", voice_ids=[voice["id"]])
        with self.assertRaises(ConflictError):
            self.store.delete_voice(voice["id"])
        self.store.cancel_job(job["id"])
        deleted = self.store.delete_voice(voice["id"])
        self.assertTrue(deleted["deleted"])
        restored = self.store.restore_voice(voice["id"])
        self.assertFalse(restored["deleted"])

    def test_priority_pause_retry_and_cancel(self):
        voice = self.create_voice()
        low = self.store.create_job(text="低优先级", voice_ids=[voice["id"]], priority=-5)
        high = self.store.create_job(
            text="高优先级", voice_ids=[voice["id"]], priority=9, max_attempts=1
        )
        self.store.set_queue_paused(True)
        self.assertIsNone(self.store.claim_next_job())
        self.store.set_queue_paused(False)
        claimed = self.store.claim_next_job()
        self.assertEqual(claimed["id"], high["id"])
        failed = self.store.fail_job(high["id"], "expected")
        self.assertEqual(failed["status"], "failed")
        retried = self.store.retry_job(high["id"])
        self.assertEqual(retried["status"], "queued")
        self.store.pause_job(low["id"])
        self.assertEqual(self.store.get_job(low["id"])["status"], "paused")
        self.store.resume_job(low["id"])
        self.assertEqual(self.store.cancel_job(low["id"])["status"], "cancelled")

    def test_worker_and_cross_process_lock(self):
        voice = self.create_voice()
        job = self.store.create_job(text="完整生成测试", voice_ids=[voice["id"]])
        first = JobWorker(self.store, DryRunSynthesizer(), self.config)
        second = JobWorker(self.store, DryRunSynthesizer(), self.config)
        first.start()
        second.start()
        self.assertTrue(first.running)
        self.assertFalse(second.running)
        first.wake()
        deadline = time.time() + 5
        while time.time() < deadline and self.store.get_job(job["id"])["status"] != "completed":
            time.sleep(0.03)
        first.stop()
        result = self.store.get_job(job["id"])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(self.store.resolve_asset(result["result_path"]).is_file())

    def test_project_and_portable_bundle(self):
        voice = self.create_voice()
        project = self.store.create_project("粤语节目", {"voice_id": voice["id"]})
        self.store.update_project(project["id"], archived=True)
        bundle = self.store.export_portable_bundle()
        inspected = self.store.inspect_portable_bundle(bundle)
        self.assertEqual(inspected["schema_version"], 1)
        restored = WorkbenchStore.restore_portable_bundle(bundle, self.root / "other-mac")
        self.assertEqual(restored.get_project(project["id"])["name"], "粤语节目")
        self.assertEqual(restored.get_voice(voice["id"])["name"], "香港本地男声")


if __name__ == "__main__":
    unittest.main()
