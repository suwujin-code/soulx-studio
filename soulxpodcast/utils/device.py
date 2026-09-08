"""Device and dtype helpers for CUDA, Apple MPS, and CPU execution."""

import os

import torch


def get_device() -> torch.device:
    """Choose the fastest supported local backend unless explicitly overridden."""
    requested = os.getenv("SOULX_DEVICE", "auto").lower()
    if requested not in {"auto", "cuda", "mps", "cpu"}:
        raise ValueError("SOULX_DEVICE must be one of: auto, cuda, mps, cpu")

    if requested == "cuda" or (requested == "auto" and torch.cuda.is_available()):
        if torch.cuda.is_available():
            return torch.device("cuda:0")
        if requested == "cuda":
            raise RuntimeError("SOULX_DEVICE=cuda was requested, but CUDA is unavailable")

    mps_available = (
        hasattr(torch.backends, "mps")
        and torch.backends.mps.is_available()
        and torch.backends.mps.is_built()
    )
    if requested == "mps" or (requested == "auto" and mps_available):
        if mps_available:
            # Some operations in the reference implementation still fall back
            # to CPU on MPS. This makes that behavior explicit and recoverable.
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
            return torch.device("mps")
        if requested == "mps":
            raise RuntimeError("SOULX_DEVICE=mps was requested, but MPS is unavailable")

    return torch.device("cpu")


def llm_dtype(device: torch.device) -> torch.dtype:
    """BF16 is the reference CUDA dtype; float32 is the portable fallback."""
    return torch.bfloat16 if device.type == "cuda" else torch.float32


def autocast_context(device: torch.device, fp16_flow: bool):
    """Return a safe autocast context for the selected backend."""
    from contextlib import nullcontext

    if device.type == "cuda" and fp16_flow:
        return torch.amp.autocast("cuda", dtype=torch.float16)
    return nullcontext()
