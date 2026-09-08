#!/usr/bin/env python3
"""Small, deterministic pre-publication check; not a substitute for human review.

Only approved source/documentation directories are eligible for publishing.
Local workspaces, caches, installer outputs and environment data are excluded.
Do not log detected secrets; output path/category only.
"""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

TOP_DIRS={'api','cli','soulxpodcast','workbench','frontend','desktop-installer','tests',
          'examples','runtime','scripts','packaging','example','docs','.github'}
TOP_FILES={'LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','README.md','SECURITY.md',
 'CONTRIBUTING.md','.gitignore','preview.py','run_studio.py','desktop_server.py',
 'run_api.py','run_workbench.py','PUBLICATION.md'}
SKIP_PARTS={'__pycache__','.pytest_cache','.git','node_modules','workbench_data',
 'workbench_test_data','dist-installers','release-assets','logs','cache','.venv'}
ALLOWED_AUDIO={'example/audios/cantonese_hk_male_s006.wav'}
REQUIRED={'LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','README.md','SECURITY.md',
 'example/audios/ATTRIBUTION.md','frontend/dist/index.html','desktop_server.py'}
SECRET_PATTERNS=[
 ('github_token',re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})')),
 ('private_key',re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
 ('url_password',re.compile(r'https?://[^/\s:@]+:[^/\s@]{6,}@')),
]
BANNED_SUFFIXES={'.db','.sqlite3','.sqlite','.pem','.key','.p12','.pfx','.ttf','.otf','.woff','.woff2','.safetensors','.onnx','.pt','.pth'}


def selected_files(root:Path)->list[Path]:
    root=root.resolve();out=[]
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root)
        if any(x in SKIP_PARTS or x.startswith('.venv') for x in rel.parts):continue
        if rel.as_posix() in {'desktop-installer/payload.zip','api/temp','api/outputs'} or rel.as_posix().startswith(('api/temp/','api/outputs/')):continue
        if p.suffix in {'.pyc','.pyo'}:continue
        if rel.parts[0] not in TOP_DIRS and rel.name not in TOP_FILES and not (len(rel.parts)==1 and rel.name.startswith('requirements.') and rel.suffix=='.txt'):continue
        if p.is_file() or p.is_symlink():out.append(p)
    return out


def audit_source(root:Path)->dict:
    root=root.resolve();files=selected_files(root);issues=[];inventory=[]
    for p in files:
        rel=p.relative_to(root).as_posix()
        if p.is_symlink():issues.append({'path':rel,'category':'symlink'});continue
        if p.suffix.lower() in BANNED_SUFFIXES or p.name.startswith('.env'):
            issues.append({'path':rel,'category':'private_or_unapproved_binary'})
        if p.suffix.lower() in {'.wav','.mp3','.m4a','.flac','.ogg','.mp4','.webm'} and rel not in ALLOWED_AUDIO:
            issues.append({'path':rel,'category':'unreviewed_media'})
        if p.suffix.lower() in {'.zip','.exe','.dmg','.msi','.pkg'}:
            issues.append({'path':rel,'category':'binary_should_be_release_asset'})
        data=p.read_bytes()
        if len(data)>10*1024*1024:issues.append({'path':rel,'category':'oversized_git_file'})
        try:text=data.decode('utf8')
        except UnicodeError:text=''
        for label,pattern in SECRET_PATTERNS:
            if pattern.search(text):issues.append({'path':rel,'category':label})
        if '/Users/' in text and not rel.startswith('scripts/audit_public'):
            issues.append({'path':rel,'category':'private_macos_path'})
        inventory.append({'path':rel,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    names={x['path'] for x in inventory}
    for missing in sorted(REQUIRED-names):issues.append({'path':missing,'category':'required_file_missing'})
    return {'status':'PASS' if not issues else 'FAIL','files_checked':len(inventory),'issues':issues,'files':inventory,
            'scope':'Allowlisted source and reviewed demo media; pattern-based review, not comprehensive security certification.'}

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a.add_argument('--out',type=Path);args=a.parse_args()
    result=audit_source(args.root)
    if args.out:args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='files'},ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='PASS' else 1)
