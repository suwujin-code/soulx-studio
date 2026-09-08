"""Inference adapters used by the persistent queue."""

from __future__ import annotations

import hashlib
import math
import os
import re
import struct
import tempfile
import wave
from pathlib import Path
from typing import Protocol

from .store import ValidationError


class Synthesizer(Protocol):
    def synthesize(
        self,
        job: dict,
        voices: list[dict],
        output_path: Path,
        progress,
    ) -> dict:
        ...


def _speaker_script(text: str, voice_count: int) -> str:
    text = text.strip()
    if voice_count == 1 and not re.search(r"\[S\d+\]", text):
        return f"[S1]{text}"
    tags = [int(value) for value in re.findall(r"\[S(\d+)\]", text)]
    if not tags:
        raise ValidationError("多音色任务必须使用 [S1]、[S2] 等说话人标记")
    if max(tags) > voice_count:
        raise ValidationError("文本引用了未选择的说话人")
    return text


class DryRunSynthesizer:
    """Deterministic WAV generator for setup validation and automated tests."""

    sample_rate = 24000

    def synthesize(self, job, voices, output_path: Path, progress) -> dict:
        progress(25, "dry-run-reference")
        duration = max(0.5, min(8.0, len(job["text"]) / 18.0))
        frequency = 220 + (int(hashlib.sha256(job["id"].encode()).hexdigest()[:4], 16) % 180)
        frame_count = int(duration * self.sample_rate)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.wav")
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            for index in range(frame_count):
                envelope = min(1.0, index / 240.0, (frame_count - index) / 240.0)
                sample = int(0.18 * envelope * 32767 * math.sin(2 * math.pi * frequency * index / self.sample_rate))
                output.writeframesraw(struct.pack("<h", sample))
        os.replace(temporary, output_path)
        progress(90, "dry-run-finalize")
        return {
            "sample_rate": self.sample_rate,
            "duration_seconds": round(duration, 3),
            "channels": 1,
            "mode": "dry-run",
            "voice_ids": [voice["id"] for voice in voices],
        }


class SoulXSynthesizer:
    """Lazy adapter around the existing SoulX model service."""

    def synthesize(self, job, voices, output_path: Path, progress) -> dict:
        progress(15, "loading-model")
        from api.service import get_service
        import scipy.io.wavfile as wavfile

        settings = job.get("settings") or {}
        service = get_service()
        progress(30, "reference-features")
        script = _speaker_script(job["text"], len(voices))
        prompt_audio_paths = []
        for voice in voices:
            data_root = output_path.parents[1].resolve()
            asset = (data_root / voice["audio_path"]).resolve()
            if not asset.is_relative_to(data_root):
                raise ValidationError("无效的参考音频路径")
            if not asset or not asset.is_file():
                raise ValidationError(f"音色参考文件缺失：{voice['name']}")
            prompt_audio_paths.append(str(asset))
        prompt_texts = [voice["transcript"] for voice in voices]
        dialect_prompts = []
        for voice in voices:
            prompt = (voice.get("dialect_prompt") or "").strip()
            if job.get("dialect") == "yue" and not prompt:
                prompt = f"<|Yue|>{voice['transcript']}"
            dialect_prompts.append(prompt)
        progress(45, "synthesizing")
        sample_rate, audio = service.generate(
            prompt_audio_paths=prompt_audio_paths,
            prompt_texts=prompt_texts,
            dialogue_text=script,
            seed=int(settings.get("seed", 1988)),
            temperature=float(settings.get("temperature", 0.6)),
            top_k=int(settings.get("top_k", 100)),
            top_p=float(settings.get("top_p", 0.9)),
            repetition_penalty=float(settings.get("repetition_penalty", 1.25)),
            dialect_prompt_texts=dialect_prompts,
            max_tokens=int(settings.get("max_tokens", 512)),
        )
        progress(88, "saving")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.wav")
        wavfile.write(str(temporary), sample_rate, audio)
        os.replace(temporary, output_path)
        duration = len(audio) / sample_rate if sample_rate else 0
        return {
            "sample_rate": sample_rate,
            "duration_seconds": round(duration, 3),
            "channels": 1,
            "mode": "soulx",
            "model": "SoulX-Podcast-1.7B-dialect",
            "voice_ids": [voice["id"] for voice in voices],
            "settings": settings,
        }


def build_synthesizer(dry_run: bool) -> Synthesizer:
    return DryRunSynthesizer() if dry_run else SoulXSynthesizer()
