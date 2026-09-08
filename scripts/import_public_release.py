#!/usr/bin/env python3
"""Import one checksum-locked reviewed package into the owner's public repository.
This is publication, not target-platform installation or TTS-quality acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
import zipfile

REPO = 'suwujin-code/soulx-studio'
TAG = 'v3.0.1-public-preview.1'
BUNDLE = 'SoulX-Public-Import.zip'
BUNDLE_SHA = '861c5fd0f81d9eab9cfe9a3d6f0cb345bf847cb367e348fa1f4759bb99ee50b7'
INITIAL_README = '9b2c72a5d38430250e162f3d384ee4c84ec7a51c'
SOURCE_PATH = 'source/soulx-studio-public-source.zip'
SOURCE_SHA = '17865efc1d9ce547a22a09d0501c33fe6b9e25f17c405d90caeab337e0fb9a72'
MARKER = '<!-- reviewed-import: ' + BUNDLE_SHA + ' -->'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_sha(data: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def safe_entries(blob: bytes, *, max_total: int) -> dict[str, bytes]:
    files = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for item in archive.infolist():
            name = item.filename
            path = PurePosixPath(name)
            if item.is_dir():
                continue
            if (not name or path.is_absolute() or '..' in path.parts
                    or '\\' in name or ':' in name or '\0' in name
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Unsafe archive entry: '+repr(name))
            if name.casefold() in {n.casefold() for n in files}:
                raise ValueError('Duplicate/case-colliding archive path')
            total += item.file_size
            if total > max_total:
                raise ValueError('Archive exceeds expanded-size limit')
            files[name] = archive.read(item)
    return files


def verify_bundle(path: Path) -> tuple[dict, dict[str, bytes], dict[str, bytes]]:
    if path.stat().st_size > 25_000_000:
        raise ValueError('Unexpected import package size')
    blob = path.read_bytes()
    if sha(blob) != BUNDLE_SHA:
        raise ValueError('Import SHA-256 mismatch; nothing will be published')
    outer = safe_entries(blob, max_total=35_000_000)
    manifest = json.loads(outer['manifest.json'])
    if (manifest.get('repository') != REPO or manifest.get('tag') != TAG
            or manifest.get('source_root') != 'soulx-studio/'):
        raise ValueError('Repository/tag/source-root mismatch')
    expected = {f['path'] for f in manifest['files']} | {'manifest.json'}
    if set(outer) != expected:
        raise ValueError('Import file inventory mismatch')
    for spec in manifest['files']:
        content = outer[spec['path']]
        if len(content) != spec['size'] or sha(content) != spec['sha256']:
            raise ValueError('Attachment integrity failure: '+spec['path'])
    if sha(outer[SOURCE_PATH]) != SOURCE_SHA:
        raise ValueError('Reviewed source archive mismatch')
    source = safe_entries(outer[SOURCE_PATH], max_total=10_000_000)
    if len(source) != 123:
        raise ValueError('Reviewed source file count mismatch')
    if any(not n.startswith('soulx-studio/') for n in source):
        raise ValueError('Invalid source root')
    source = {n[len('soulx-studio/'):]: b for n,b in source.items()}
    forbidden = {'.git','.venv','workbench_data','node_modules','__pycache__'}
    suffixes = {'.ttf','.otf','.woff','.woff2','.sqlite3','.db','.pem','.key','.safetensors','.onnx','.pt'}
    for name in source:
        if forbidden.intersection(PurePosixPath(name).parts) or Path(name).suffix.lower() in suffixes:
            raise ValueError('Unapproved private/runtime file: '+name)
    return manifest, outer, source


def run(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=True, text=True, **kwargs)


def gh_json(*args: str) -> object:
    return json.loads(run('gh',*args).stdout)


def commit_changes(message: str) -> str:
    run('git','add','--all')
    dirty = run('git','diff','--cached','--quiet',check=False)
    if dirty.returncode:
        run('git','-c','user.name=SoulX Release Bot',
            '-c','user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit','-m',message)
        # No force/rebase: concurrent owner edits must stop, not be overwritten.
        run('git','push','origin','HEAD:main')
    return run('git','rev-parse','HEAD').stdout.strip()


def publish(root: Path, bundle_path: Path) -> dict:
    if os.environ.get('GITHUB_REPOSITORY') != REPO:
        raise RuntimeError('Only the explicitly authorized owner repository is supported')
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Publishing is restricted to main')
    if not os.environ.get('GH_TOKEN'):
        raise RuntimeError('Missing repository-scoped Actions authentication')
    manifest, outer, source = verify_bundle(bundle_path)
    head = run('git','rev-parse','HEAD').stdout.strip()
    remote = run('git','ls-remote','origin','refs/heads/main').stdout.split()[0]
    if head != remote:
        raise RuntimeError('main moved after checkout; rerun on the latest commit')
    for name, data in source.items():
        dest = root/name
        if dest.is_symlink():
            raise ValueError('Refusing to overwrite a symlink: '+name)
        if dest.exists() and dest.read_bytes() != data:
            known_seed = name == 'README.md' and git_sha(dest.read_bytes()) == INITIAL_README
            own_status = name == 'PUBLICATION.md' and MARKER in dest.read_text(encoding='utf8')
            if not (known_seed or own_status):
                raise RuntimeError('Existing owner file differs; refusing overwrite: '+name)
        # Workflows are written through the connector, not added with a job token.
        if name.startswith('.github/'):
            if not dest.exists() or dest.read_bytes() != data:
                raise RuntimeError('Install the reviewed source-check workflow before importing')
            continue
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(data)
    audit = run('python','scripts/audit_public.py','--root',str(root))
    print(audit.stdout)
    run('python','-m','compileall','-q','api','workbench','soulxpodcast','scripts')
    pub = root/'PUBLICATION.md'
    pub.write_text('# Publication status\n\nReviewed source imported. Release attachment verification is in progress.\n\n'
                   'No new target-OS installation or genuine TTS acceptance is claimed.\n'+MARKER+'\n',encoding='utf8')
    # Keep the uploaded package until publishing completes so failed runs can resume.
    source_commit = commit_changes('release: import reviewed SoulX source without private runtime data')
    releases = gh_json('api',f'repos/{REPO}/releases?per_page=100')
    existing = next((r for r in releases if r['tag_name']==TAG),None)
    if existing and (MARKER not in (existing.get('body') or '')):
        raise RuntimeError('An unrelated release already owns this tag; refusing takeover')
    with tempfile.TemporaryDirectory(prefix='soulx-release-') as temp:
        tmp = Path(temp)
        assets=[]
        for name, content in outer.items():
            if name.startswith('assets/'):
                dest=tmp/Path(name).name;dest.write_bytes(content);assets.append(dest)
        src_asset=tmp/'SoulX-Studio-Reviewed-Public-Source.zip'
        src_asset.write_bytes(outer[SOURCE_PATH]);assets.append(src_asset)
        sums=tmp/'SHA256SUMS.txt'
        sums.write_text(''.join(f'{sha(p.read_bytes())}  {p.name}\n' for p in sorted(assets)),encoding='utf8')
        assets.append(sums)
        notes=tmp/'notes.md'
        notes.write_text('''# SoulX Studio · 公开安装预览版

下载适合电脑的 ZIP，不要把 Code → Download ZIP 当成安装程序。

- **mac-arm64**：Apple Silicon Mac；解压 .app 后打开本机安装中心。
- **windows-x64**：Windows 10/11 x64；解压后打开 SoulX-Studio-Setup.exe。
- **mac-intel**：Intel Mac，仅工作台，不开放本机语音安装。

**Developer Preview，未签名/未公证。** 首次安装需要联网下载独立 Python、依赖和模型；不是完整离线包。系统阻止运行时不要关闭安全机制。Mac/Windows 干净安装、实际粤语质量和性能仍待实机验收。

本次上传的是已核对的公开预览附件，不重新声称做过目标系统测试。来源记录中的 72 项 Python / 28 项 Go 测试属于此前 Linux 本地验收，不是本次 GitHub 的新增 TTS 或系统验收。

横版宣传片 48 秒：真实 UI 截图动效、原创配乐和音效，无旁白。版本/任务展示是标注的测试音，不是粤语效果证明。

保留上游 Apache-2.0 与参考音频独立署名；不要提交私人音频、工作区或密钥。

'''+MARKER,encoding='utf8')
        if not existing:
            run('gh','release','create',TAG,'--repo',REPO,'--target',source_commit,
                '--title','SoulX Studio 3.0.1 · Public Developer Preview',
                '--notes-file',str(notes),'--draft','--prerelease')
        release=gh_json('api',f'repos/{REPO}/releases/tags/{TAG}')
        published_assets={a['name']:a for a in release['assets']}
        if set(published_assets)-{p.name for p in assets}:
            raise RuntimeError('Unexpected release attachment; refusing to publish unknown files')
        for asset in assets:
            old=published_assets.get(asset.name)
            if old and old['size'] != asset.stat().st_size:
                raise RuntimeError('Existing attachment differs; refusing overwrite: '+asset.name)
            if not old:
                run('gh','release','upload',TAG,str(asset),'--repo',REPO)
        # Verify actual remote bytes before making a draft public.
        downloaded=tmp/'remote';downloaded.mkdir()
        run('gh','release','download',TAG,'--repo',REPO,'--dir',str(downloaded))
        for asset in assets:
            counterpart=downloaded/asset.name
            if not counterpart.is_file() or sha(counterpart.read_bytes()) != sha(asset.read_bytes()):
                raise RuntimeError('Remote attachment SHA-256 mismatch: '+asset.name)
        run('gh','release','edit',TAG,'--repo',REPO,'--draft=false','--prerelease','--latest=false')
        release=gh_json('api',f'repos/{REPO}/releases/tags/{TAG}')
        if release['draft'] or not release['prerelease']:
            raise RuntimeError('Unexpected release visibility/type')
        receipt={'status':'PUBLISHED_PREVIEW','repository':REPO,'tag':TAG,
                 'release_url':release['html_url'],'source_commit':source_commit,
                 'reviewed_package_sha256':BUNDLE_SHA,'target_system_acceptance':False,
                 'assets':[]}
        for asset in assets:
            remote_asset=next(a for a in release['assets'] if a['name']==asset.name)
            receipt['assets'].append({'name':asset.name,'size':asset.stat().st_size,
                'sha256':sha(asset.read_bytes()),'download_url':remote_asset['browser_download_url'],
                'remote_bytes_verified':True})
        receipt_path=root/'docs/publication-receipt.json'
        receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
        rows=[]
        for platform,key in [('Mac 苹果芯片','mac-arm64'),('Windows x64','windows-x64'),('Intel Mac（仅工作台）','mac-intel')]:
            item=next(a for a in receipt['assets'] if key in a['name'])
            rows.append(f'| {platform} | [下载安装预览包]({item["download_url"]}) |')
        block=('## 下载安装预览版\n\n[公开 Release 页面]('+release['html_url']+')\n\n'
               '| 电脑 | 安装文件 |\n|---|---|\n'+'\n'.join(rows)+'\n\n'
               '**未签名/未公证；首次安装需要联网。目标电脑真实安装及配音仍待验收。**\n\n')
        readme=root/'README.md'
        readme.write_text(readme.read_text(encoding='utf8').replace('## 开始使用',block+'## 开始使用',1),encoding='utf8')
        pub.write_text('# Publication status / 发布状态\n\n**PUBLISHED_PREVIEW**\n\n'
                      +release['html_url']+'\n\n所有附件已下载回读并核对 SHA-256。'
                      '发布成功不代表 Mac/Windows 实机、签名或真实粤语验收通过。\n\n'
                      '详细回执：`docs/publication-receipt.json`\n\n'+MARKER+'\n',encoding='utf8')
        bundle_path.unlink()
        commit_changes('docs: expose verified public installer downloads and publication receipt')
        print(json.dumps(receipt,ensure_ascii=False,indent=2))
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            Path(summary).write_text(block+'\n发布附件已核对；实机配音验收仍未完成。\n',encoding='utf8')
        return receipt


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--verify-only',type=Path)
    args=parser.parse_args()
    if args.verify_only:
        manifest,outer,source=verify_bundle(args.verify_only)
        print(json.dumps({'status':'VERIFIED_LOCAL_PACKAGE','files':len(source),
                          'tag':manifest['tag'],'published':False},indent=2))
        return
    root=Path.cwd().resolve()
    publish(root,root/BUNDLE)

if __name__=='__main__':
    main()
