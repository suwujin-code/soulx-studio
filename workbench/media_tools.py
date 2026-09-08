"""Resolve system or installed wheel FFmpeg without shell execution."""
import os
import shutil
from pathlib import Path

def find_ffmpeg():
    configured = os.environ.get('SOULX_FFMPEG')
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        return path if Path(path).is_file() else None
    except (ImportError, RuntimeError, OSError):
        return None
