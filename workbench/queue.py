"""Persistent single-model queue suitable for Apple Silicon memory limits."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from .config import WorkbenchConfig
from .process_lock import ProcessFileLock
from .store import WorkbenchStore
from .synthesizer import Synthesizer, build_synthesizer


logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(
        self,
        store: WorkbenchStore,
        synthesizer: Synthesizer | None = None,
        config: WorkbenchConfig | None = None,
    ):
        self.store = store
        self.config = config or store.config
        self.synthesizer = synthesizer or build_synthesizer(self.config.dry_run)
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_handle = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        if not self._acquire_process_lock():
            logger.info("Another SoulX worker owns the shared model queue")
            return
        self.store.recover_interrupted_jobs()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="soulx-job-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=max(0.1, timeout))
        if not self.running:
            self._release_process_lock()

    def wake(self) -> None:
        self._wake_event.set()

    def pause(self) -> dict:
        self.store.set_queue_paused(True)
        self.wake()
        return self.store.queue_stats()

    def resume(self) -> dict:
        self.store.set_queue_paused(False)
        self.wake()
        return self.store.queue_stats()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                job = self.store.claim_next_job()
                if not job:
                    self._wake_event.wait(self.config.worker_poll_seconds)
                    self._wake_event.clear()
                    continue
                self._process(job)
        finally:
            self._release_process_lock()

    def _acquire_process_lock(self) -> bool:
        if self._lock_handle is not None:
            return True
        lock = ProcessFileLock(self.store.data_dir / '.worker.lock')
        if not lock.acquire():
            return False
        self._lock_handle = lock
        return True

    def _release_process_lock(self) -> None:
        lock, self._lock_handle = self._lock_handle, None
        if lock is not None:
            lock.release()

    def _process(self, job: dict) -> None:
        job_id = job["id"]
        try:
            voices = [self.store.get_voice(voice_id) for voice_id in job["voice_ids"]]
            current = self.store.get_job(job_id)
            if current["cancel_requested"]:
                self.store.cancel_job(job_id)
                return
            output_path = self.store.data_dir / "results" / f"{job_id}.wav"

            def progress(value: int, stage: str) -> None:
                self.store.update_job_progress(job_id, value, stage)

            metadata = self.synthesizer.synthesize(job, voices, output_path, progress)
            self.store.complete_job(job_id, output_path, metadata)
        except Exception as exc:
            logger.exception("SoulX job failed: %s", job_id)
            self.store.fail_job(job_id, str(exc))
            self.wake()
