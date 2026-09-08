"""Install the pinned model on all desktop platforms; no curl or shell required.
Only download after an explicit --download. Existing directories are read-only
in verification mode. Incomplete transfers are resumable and never final files.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import quote

MANIFEST = Path(__file__).resolve().parents[1] / 'packaging/OFFLINE_MODEL_MANIFEST.json'
TOKENIZER_NAME = 'speech_tokenizer_v2_25hz.onnx'
TOKENIZER_SHA = 'd43342aa12163a80bf07bffb94c9de2e120a8df2f9917cd2f642e7f4219c6f71'
TOKENIZER_URL = 'https://www.modelscope.cn/models/iic/CosyVoice2-0.5B/resolve/master/speech_tokenizer_v2.onnx'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4*1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def safe_path(root: Path, name: str) -> Path:
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts:
        raise ValueError('Unsafe model path: '+name)
    target = (root / str(p)).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('Model path escapes directory')
    return target


def valid_file(path: Path, size: int | None, sha: str) -> bool:
    return (path.is_file() and (size is None or path.stat().st_size == size)
            and (not sha or digest(path) == sha))


def download(url: str, target: Path, size: int | None, sha: str,
             opener=urllib.request.urlopen) -> None:
    if valid_file(target, size, sha):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name+'.partial')
    # A full-sized partial is not necessarily valid; never request an invalid EOF range.
    if partial.is_file() and size is not None and partial.stat().st_size >= size:
        if valid_file(partial, size, sha):
            os.replace(partial, target)
            return
        partial.unlink()
    for attempt in range(3):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            request = urllib.request.Request(url, headers={'User-Agent':'SoulX-Desktop-Installer/3.0.1',
                                              **({'Range':f'bytes={offset}-'} if offset else {})})
            with opener(request, timeout=60) as response:
                code = response.status
                if code not in (200,206):
                    raise OSError(f'Download HTTP {code}')
                append = offset > 0 and code == 206
                if code == 206:
                    cr = response.headers.get('Content-Range','')
                    if not cr.startswith(f'bytes {offset}-'):
                        raise ValueError('Unexpected Content-Range; refusing corrupt resume')
                with partial.open('ab' if append else 'wb') as f:
                    total = offset if append else 0
                    for block in iter(lambda: response.read(1024*1024), b''):
                        total += len(block)
                        if total > (size if size is not None else 2*1024**3):
                            raise ValueError('Download exceeds expected size')
                        f.write(block)
            if not valid_file(partial, size, sha):
                # A wrong checksum must not be reused in another resume.
                if size is None or partial.stat().st_size >= size:
                    partial.unlink(missing_ok=True)
                raise OSError('Download incomplete or SHA-256 mismatch: '+target.name)
            os.replace(partial, target)
            return
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(attempt+1)


def prepare(root: Path, allow_download: bool = False) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding='utf8'))
    root = root.expanduser().resolve()
    verified = []
    for i,item in enumerate(manifest['files'],1):
        path = safe_path(root,item['path'])
        print(f'[{i}/{len(manifest["files"])}] {item["path"]}',flush=True)
        if not valid_file(path,item['size'],item.get('sha256','')):
            if not allow_download:
                raise RuntimeError('模型缺失或校验失败：'+item['path'])
            url=f'https://huggingface.co/{manifest["repo"]}/resolve/{manifest["revision"]}/{quote(item["path"],safe="/")}'
            download(url,path,item['size'],item.get('sha256',''))
        verified.append({'path':item['path'],'check':'sha256+size' if item.get('sha256') else 'size-only'})
    return {'repo':manifest['repo'],'revision':manifest['revision'],'model_dir':str(root),
            'files':verified,'model_loaded':False,'speech_test_passed':False}


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--cache-dir',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--download',action='store_true')
    a=p.parse_args()
    result=prepare(a.model_dir,a.download)
    target=a.cache_dir/'s3tokenizer'/TOKENIZER_NAME
    if not valid_file(target,None,TOKENIZER_SHA):
        if not a.download:
            raise SystemExit('辅助 tokenizer 未缓存；请允许下载或放入 '+str(target))
        print('Downloading auxiliary speech tokenizer (separate from main model)',flush=True)
        download(TOKENIZER_URL,target,None,TOKENIZER_SHA)
    result['tokenizer_sha256']=TOKENIZER_SHA
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('资源校验完成；尚未加载大模型，也不代表真实粤语质量验收。',flush=True)
