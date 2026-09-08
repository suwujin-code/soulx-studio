#!/usr/bin/env python3
"""Fetch exactly the owner's reviewed public-release bundle from Drive.

No Drive login/cookies/tokens. Private/HTML responses fail closed; sharing the
source file does not share any other Drive file. The imported code is executed
only by the separate, pre-existing checksum-locked publication script.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

FILE_ID = '111_TG5g9z-rjl_G8UexhzEpp5Cg0RTIS'
EXPECTED_BYTES = 19_636_411
EXPECTED_SHA256 = '861c5fd0f81d9eab9cfe9a3d6f0cb345bf847cb367e348fa1f4759bb99ee50b7'
REPOSITORY = 'suwujin-code/soulx-studio'
BUNDLE_NAME = 'SoulX-Public-Import.zip'
PUBLIC_SOURCE = ('https://drive.usercontent.google.com/download?'
                 + urllib.parse.urlencode({'id': FILE_ID, 'export': 'download', 'confirm': 't'}))

class SourceNotReadable(RuntimeError):
    pass

class VerifiedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        host = (u.hostname or '').lower()
        if host == 'accounts.google.com':
            raise SourceNotReadable('Google Drive requires sign-in: the single source file is not publicly readable.')
        if (u.scheme != 'https' or u.username or u.password
            or not (host in {'drive.google.com', 'drive.usercontent.google.com'}
                    or host.endswith('.googleusercontent.com'))):
            raise SourceNotReadable('Unexpected Drive redirect; refusing to follow it.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(root: Path, opener=None):
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('Destination root must exist')
    dest = root / BUNDLE_NAME
    if dest.exists():
        if dest.is_symlink():
            raise ValueError('Refusing destination symlink')
        if dest.stat().st_size == EXPECTED_BYTES and hashlib.sha256(dest.read_bytes()).hexdigest() == EXPECTED_SHA256:
            return {'status': 'VERIFIED', 'reused': True, 'bytes': EXPECTED_BYTES, 'sha256': EXPECTED_SHA256}
        raise ValueError('Existing import package differs; not overwriting it')
    request = urllib.request.Request(PUBLIC_SOURCE, headers={'User-Agent': 'SoulX-Reviewed-Drive-Import/1.0'})
    open_url = opener or urllib.request.build_opener(VerifiedRedirects()).open
    temporary = None
    try:
        try:
            response = open_url(request, timeout=90)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise SourceNotReadable('Google Drive returned HTTP %s. The single source file needs read access for anyone with its link; no content was imported.' % exc.code) from exc
            raise
        with response:
            if response.status != 200:
                raise SourceNotReadable('Drive download did not return HTTP 200')
            mime = response.headers.get('Content-Type', '').lower()
            if 'text/html' in mime or 'application/json' in mime:
                raise SourceNotReadable('Drive returned an HTML/sign-in/confirmation page, not the ZIP. No content was imported.')
            size = response.headers.get('Content-Length')
            if size and int(size) != EXPECTED_BYTES:
                raise ValueError('Drive Content-Length does not match the reviewed package')
            digest, total = hashlib.sha256(), 0
            with tempfile.NamedTemporaryFile(prefix='.soulx-drive-', suffix='.partial', dir=root, delete=False) as out:
                temporary = Path(out.name)
                while block := response.read(1024 * 1024):
                    if total == 0 and not block.startswith(b'PK\x03\x04'):
                        raise SourceNotReadable('Response is not a ZIP archive; refusing import')
                    total += len(block)
                    if total > EXPECTED_BYTES:
                        raise ValueError('Drive download exceeds reviewed size')
                    out.write(block)
                    digest.update(block)
                out.flush()
                os.fsync(out.fileno())
            if total != EXPECTED_BYTES or digest.hexdigest() != EXPECTED_SHA256:
                raise ValueError('Drive byte count / SHA-256 mismatch; refusing import')
            if dest.exists():
                raise ValueError('Destination appeared during download')
            temporary.rename(dest)
            return {'status': 'VERIFIED', 'reused': False, 'bytes': total, 'sha256': digest.hexdigest()}
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Only the explicitly authorized repository main branch is allowed')
    try:
        result = download(Path.cwd())
    except Exception as exc:
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            Path(summary).write_text('# Drive import stopped safely\n\n' + str(exc)
                + '\n\nNo application-source import or Release publication was performed by this step.\n', encoding='utf-8')
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
