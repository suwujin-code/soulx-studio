#!/usr/bin/env python3
"""Run a no-model end-to-end smoke test of storage, queue and portable export."""

from __future__ import annotations

import tempfile
import time
import wave
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workbench.config import WorkbenchConfig
from workbench.queue import JobWorker
from workbench.store import WorkbenchStore
from workbench.synthesizer import DryRunSynthesizer


def make_reference(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 24000)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="soulx-verify-") as directory:
        root = Path(directory)
        reference = root / "reference.wav"
        make_reference(reference)
        config = replace(
            WorkbenchConfig.from_env(),
            data_dir=root / "data",
            dry_run=True,
            worker_poll_seconds=0.05,
        )
        store = WorkbenchStore(config)
        voice = store.create_voice(
            name="验收粤语音色",
            audio_file=reference,
            transcript="香港嘅夜景真係好靓。",
            dialect="yue",
            region="香港粤语",
            dialect_prompt="<|Yue|>香港嘅夜景真係好靓。",
            tags=["验收", "粤语"],
            source_authorization="自动化测试生成的静音参考文件",
            favorite=True,
        )
        job = store.create_job(
            text="呢一段係工作台完整链路验收。",
            voice_ids=[voice["id"]],
            settings={"seed": 23, "max_tokens": 128},
        )
        worker = JobWorker(store, DryRunSynthesizer(), config)
        worker.start()
        worker.wake()
        deadline = time.time() + 8
        while time.time() < deadline:
            current = store.get_job(job["id"])
            if current["status"] == "completed":
                break
            time.sleep(0.05)
        worker.stop()
        current = store.get_job(job["id"])
        assert current["status"] == "completed", current
        assert store.resolve_asset(current["result_path"]).is_file()
        bundle = store.export_portable_bundle()
        metadata = store.inspect_portable_bundle(bundle)
        assert metadata["format"] == "soulx-workbench-portable"
        restored = WorkbenchStore.restore_portable_bundle(bundle, root / "restored")
        assert restored.get_voice(voice["id"])["favorite"] is True
        assert restored.get_job(job["id"])["status"] == "completed"
        print("SoulX workbench smoke test: PASS")


if __name__ == "__main__":
    main()
