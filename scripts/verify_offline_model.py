#!/usr/bin/env python3
"""Verify the bundled offline SoulX model without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "pretrained_models" / "SoulX-Podcast-1.7B-dialect"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="校验项目内 SoulX 离线模型")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args()
    model_dir = args.model_dir.expanduser().resolve()
    manifest_path = model_dir / "OFFLINE_MODEL_MANIFEST.json"
    if not manifest_path.is_file():
        raise SystemExit(f"缺少模型清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    total = 0
    for item in manifest["files"]:
        path = model_dir / item["path"]
        if not path.is_file() or path.stat().st_size != item["size"]:
            raise SystemExit(f"模型文件缺失或大小错误：{item['path']}")
        if item.get("sha256") and digest(path) != item["sha256"]:
            raise SystemExit(f"模型文件校验失败：{item['path']}")
        total += item["size"]
    print(f"SoulX 离线模型校验通过：{len(manifest['files'])} 个文件，{total} 字节")
    print(f"仓库：{manifest['repo']}")
    print(f"提交：{manifest['revision']}")


if __name__ == "__main__":
    main()
