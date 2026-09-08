#!/usr/bin/env python3
"""Build native online bootstrap installers from the checked-in desktop payload.

Requires Python 3.11+ and Go 1.23+. Uses no third-party Python/Go modules.
Cross compilation verifies buildability only, not target OS installation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '3.0.1-preview.1'
PAYLOAD_PATHS = (
    'workbench', 'api', 'soulxpodcast', 'frontend/dist', 'example',
    'packaging/OFFLINE_MODEL_MANIFEST.json',
    'scripts/prepare_desktop_model.py', 'desktop_server.py', 'run_studio.py',
    'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'requirements.desktop.txt', 'requirements.speech-desktop.txt',
)
TARGETS = {
    'windows-x64': ('windows', 'amd64', 'SoulX-Studio-Setup.exe'),
    'mac-arm64': ('darwin', 'arm64', 'SoulXStudio'),
    'mac-intel': ('darwin', 'amd64', 'SoulXStudio'),
    'linux-qa': ('linux', 'amd64', 'soulx-installer-linux'),
}


def sha(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def add_zip(z: zipfile.ZipFile, path: Path, name: str, executable: bool = False) -> None:
    # Deterministic timestamps and POSIX executable bits for Finder .app extraction.
    info = zipfile.ZipInfo(name, (2026, 9, 9, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, path.read_bytes())


def payload() -> dict:
    selected: dict[str, Path] = {}
    for name in PAYLOAD_PATHS:
        path = ROOT / name
        if not path.exists():
            raise FileNotFoundError(f'Missing required payload component: {path}')
        candidates = path.rglob('*') if path.is_dir() else [path]
        for p in candidates:
            if p.is_symlink():
                raise ValueError(f'Symlink not allowed in payload: {p}')
            if not p.is_file() or '__pycache__' in p.parts or p.suffix in {'.pyc', '.pyo'}:
                continue
            # Old execution outputs must never be shipped as user data.
            relative = p.relative_to(ROOT).as_posix()
            if relative.startswith(('api/temp/', 'api/outputs/')):
                continue
            selected[relative] = p
    dest = ROOT / 'desktop-installer' / 'payload.zip'
    with zipfile.ZipFile(dest, 'w') as z:
        for name, p in sorted(selected.items()):
            add_zip(z, p, name)
    return {'file_count': len(selected), 'sha256': sha(dest), 'bytes': dest.stat().st_size}


def build(target: str, out: Path, go: str) -> dict:
    goos, arch, binary_name = TARGETS[target]
    directory = out / target
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / binary_name
    env = dict(os.environ, CGO_ENABLED='0', GOOS=goos, GOARCH=arch,
               GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off')
    flags = '-s -w' + (' -H windowsgui' if goos == 'windows' else '')
    subprocess.run([go, 'build', '-trimpath', f'-ldflags={flags}', '-o', str(binary), '.'],
                   cwd=ROOT / 'desktop-installer', env=env, check=True)
    binary.chmod(0o755)
    if goos == 'darwin':
        app = directory / 'SoulX Studio.app'
        macos = app / 'Contents' / 'MacOS'
        macos.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary, macos / 'SoulXStudio')
        plist = {
            'CFBundleDevelopmentRegion': 'zh_CN',
            'CFBundleDisplayName': 'SoulX Studio',
            'CFBundleName': 'SoulX Studio',
            'CFBundleIdentifier': 'local.soulx.studio',
            'CFBundleExecutable': 'SoulXStudio',
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': '3.0.1',
            'CFBundleVersion': '30101',
            'LSMinimumSystemVersion': '13.0',
            'LSApplicationCategoryType': 'public.app-category.productivity',
            'NSHighResolutionCapable': True,
            # No native window: the loopback setup center manages exit and service state.
            'LSUIElement': True,
        }
        (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(plist))
        (app / 'Contents' / 'PkgInfo').write_bytes(b'APPL????')
        archive = out / f'SoulX-Studio-{VERSION}-{target}.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for p in sorted(app.rglob('*')):
                if p.is_file():
                    add_zip(z, p, p.relative_to(directory).as_posix(), p.name == 'SoulXStudio')
            add_zip(z, ROOT / 'docs' / 'CROSS_PLATFORM_INSTALL.md', '先读我_安装说明.md')
            add_zip(z, ROOT / 'LICENSE', 'LICENSE')
            add_zip(z, ROOT / 'NOTICE', 'NOTICE')
            add_zip(z, ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md')
    elif goos == 'windows':
        archive = out / f'SoulX-Studio-{VERSION}-{target}.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            add_zip(z, binary, binary_name, True)
            add_zip(z, ROOT / 'docs' / 'CROSS_PLATFORM_INSTALL.md', '先读我_安装说明.md')
            add_zip(z, ROOT / 'LICENSE', 'LICENSE')
            add_zip(z, ROOT / 'NOTICE', 'NOTICE')
            add_zip(z, ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md')
    else:
        archive = binary
    return {'target': target, 'version': VERSION, 'binary': str(binary),
            'binary_sha256': sha(binary), 'archive': str(archive),
            'archive_bytes': archive.stat().st_size, 'archive_sha256': sha(archive),
            'cross_compiled': True, 'target_executed': False,
            'developer_signed': False, 'notarized': False}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT / 'dist-installers')
    p.add_argument('--targets', nargs='+', choices=list(TARGETS),
                   default=['windows-x64', 'mac-arm64', 'mac-intel'])
    p.add_argument('--payload-only', action='store_true')
    args = p.parse_args()
    meta = {'version': VERSION, 'payload': payload(), 'artifacts': []}
    if args.payload_only:
        print(json.dumps(meta, indent=2)); return 0
    go = shutil.which('go')
    if not go:
        raise RuntimeError('Go 1.23+ is required for building; end users do not need Go.')
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    for target in args.targets:
        meta['artifacts'].append(build(target, args.out, go))
    (args.out / 'build-manifest.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Build failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
