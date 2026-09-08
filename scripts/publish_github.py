#!/usr/bin/env python3
"""Publish this reviewed project in an authenticated GitHub CLI environment.

Default is offline plan-only. --execute --ack-public explicitly permits creating
one NEW public repository, pushing main (no force), and publishing a prerelease.
No token is read, requested or printed by this script. Never edits global config.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path
from typing import Sequence
from audit_public import audit_source, selected_files

DEFAULT_REPO='suwujin-code/soulx-studio'
DEFAULT_TAG='v3.0.1-public-preview.1'

class PublishError(RuntimeError):pass


def run(argv:Sequence[str],cwd:Path,ok:bool=True)->subprocess.CompletedProcess:
    p=subprocess.run(list(argv),cwd=cwd,text=True,capture_output=True)
    if ok and p.returncode:
        # These commands have no tokens in argv. Do not dump full environment/config.
        raise PublishError(f'{argv[0]} {argv[1] if len(argv)>1 else ""} failed: '+(p.stderr.strip() or p.stdout.strip())[-1800:])
    return p


def validate_repo(repo:str)->tuple[str,str]:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',repo):raise PublishError('Use one explicit owner/repository name.')
    owner,name=repo.split('/')
    if name.endswith('.git') or name in {'.','..'}:raise PublishError('Invalid repository name.')
    return owner,name


def asset_inventory(folder:Path)->list[dict]:
    manifest=folder/'ASSETS.json'
    if not manifest.is_file():raise PublishError('Release asset manifest ASSETS.json is missing.')
    raw=json.loads(manifest.read_text(encoding='utf8'))
    if not isinstance(raw,list) or not raw:raise PublishError('Release asset list is empty.')
    names=set();out=[]
    for item in raw:
        name=item.get('name','')
        if not name or Path(name).name!=name or '/' in name or '\\' in name or name.startswith('.') or name in names:raise PublishError('Unsafe/duplicate release asset filename.')
        names.add(name);path=folder/name
        if path.is_symlink() or not path.is_file():raise PublishError('Missing/non-regular release asset: '+name)
        data=path.read_bytes();sha=hashlib.sha256(data).hexdigest()
        if len(data)!=item.get('bytes') or sha!=item.get('sha256'):raise PublishError('Release asset checksum mismatch: '+name)
        out.append({'name':name,'bytes':len(data),'sha256':sha,'path':str(path.resolve())})
    return out


def verify_remote_assets(expected:list[dict],remote:list[dict])->None:
    actual={a['name']:a for a in remote}
    if set(actual)!={a['name'] for a in expected}:raise PublishError('Remote draft asset names differ. Refusing to publish.')
    for item in expected:
        got=actual[item['name']]
        if got.get('size')!=item['bytes']:raise PublishError('Remote asset size mismatch: '+item['name'])
        if got.get('digest') and got['digest']!='sha256:'+item['sha256']:raise PublishError('Remote asset digest mismatch: '+item['name'])
        if got.get('state')!='uploaded':raise PublishError('Remote asset not completely uploaded: '+item['name'])


def read_state(path:Path,repo:str)->dict:
    if not path.exists():return {'repository':repo,'phase':'NOT_STARTED'}
    data=json.loads(path.read_text(encoding='utf8'))
    if data.get('repository')!=repo:raise PublishError('Saved publication receipt belongs to a different repository.')
    return data


def save_state(path:Path,data:dict):
    tmp=path.with_suffix('.json.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');os.replace(tmp,path)


def ensure_local_git(root:Path):
    top=run(['git','rev-parse','--show-toplevel'],root,ok=False)
    if top.returncode==0 and Path(top.stdout.strip()).resolve()!=root.resolve():raise PublishError('Source folder is nested in another Git repository; refusing to modify it.')
    if top.returncode!=0:run(['git','init','-b','main'],root)
    files=[str(p.relative_to(root)) for p in selected_files(root)]
    for i in range(0,len(files),80):run(['git','add','--',*files[i:i+80]],root)
    staged=run(['git','diff','--cached','--quiet'],root,ok=False)
    if staged.returncode==1:
        run(['git','-c','user.name=OING','-c','user.email=release@users.noreply.github.com','commit','-m','Prepare SoulX Studio public developer preview'],root)
    elif staged.returncode!=0:raise PublishError('Cannot inspect staged changes.')
    if run(['git','branch','--show-current'],root).stdout.strip()!='main':raise PublishError('Please select main intentionally; script will not change or force-reset your branch.')
    clean=run(['git','diff','HEAD','--name-only'],root).stdout.strip()
    if clean:raise PublishError('Tracked source has unstaged changes. Review them first.')
    tracked=set(filter(None,run(['git','ls-files','-z'],root).stdout.split('\0')))
    if tracked!=set(files):raise PublishError('Tracked file set differs from the approved public source list.')


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--assets',type=Path)
    parser.add_argument('--repo',default=DEFAULT_REPO)
    parser.add_argument('--tag',default=DEFAULT_TAG)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--ack-public',action='store_true')
    args=parser.parse_args();root=args.root.resolve();owner,_=validate_repo(args.repo)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',args.tag):raise PublishError('Invalid release tag.')
    assets=asset_inventory((args.assets or root.parent/'release-assets').resolve())
    audit=audit_source(root)
    if audit['status']!='PASS':raise PublishError('Public source audit failed: '+json.dumps(audit['issues'],ensure_ascii=False))
    notes=root/'docs/RELEASE_NOTES.md'
    if not notes.is_file():raise PublishError('Release notes missing.')
    plan={'mode':'EXECUTE' if args.execute else 'DRY_RUN_NO_NETWORK','repository':args.repo,'visibility':'public',
          'release_tag':args.tag,'prerelease':True,'source_files':audit['files_checked'],
          'assets':[{k:v for k,v in a.items() if k!='path'} for a in assets],
          'operations':['verify authenticated owner','create new public repository or resume only the recorded repository',
                        'push main without force','create draft prerelease','upload and verify assets','publish prerelease','read back repository and release']}
    print(json.dumps(plan,ensure_ascii=False,indent=2))
    if not args.execute:return 0
    if not args.ack_public:raise PublishError('Public posting requires both --execute and --ack-public.')
    for command in ['git','gh']:
        if not shutil.which(command):raise PublishError(command+' is missing. Install it outside this script; no account changes were made.')
    run(['gh','auth','status','--hostname','github.com'],root)
    actual=run(['gh','api','user','--jq','.login'],root).stdout.strip()
    if actual.lower()!=owner.lower():raise PublishError(f'Authenticated as {actual}, expected {owner}; refusing publication to another account.')
    ensure_local_git(root)
    state_path=root/'.publish-state.json';state=read_state(state_path,args.repo)
    remote=run(['gh','api','repos/'+args.repo],root,ok=False)
    if remote.returncode==0:
        data=json.loads(remote.stdout)
        if state.get('repository_id')!=data.get('id'):raise PublishError('Repository already exists without a matching local creation receipt. It will not be modified.')
        if data.get('private') is not False:raise PublishError('Recorded repository is not public; refusing visibility changes.')
    else:
        if '404' not in (remote.stderr+remote.stdout):raise PublishError('Cannot verify whether repository exists; refusing to guess.')
        run(['gh','repo','create',args.repo,'--public','--description','Independent Cantonese voice workbench: sentence editing, batch jobs and local voice assets. Developer preview.'],root)
        data=json.loads(run(['gh','api','repos/'+args.repo],root).stdout)
        state.update(repository_id=data['id'],phase='REPOSITORY_CREATED',repository_url=data['html_url'])
        save_state(state_path,state)
    expected_url='https://github.com/'+args.repo+'.git'
    origin=run(['git','remote','get-url','origin'],root,ok=False)
    if origin.returncode==0 and origin.stdout.strip()!=expected_url:raise PublishError('Local origin points elsewhere. Refusing to replace it.')
    if origin.returncode!=0:run(['git','remote','add','origin',expected_url],root)
    run(['git','-c','credential.helper=','-c','credential.helper=!gh auth git-credential','push','-u','origin','main'],root)
    commit=run(['git','rev-parse','HEAD'],root).stdout.strip()
    remote_commit=run(['gh','api','repos/'+args.repo+'/commits/main','--jq','.sha'],root).stdout.strip()
    if remote_commit!=commit:raise PublishError('Remote commit does not match; release not published.')
    state.update(phase='SOURCE_PUSHED',commit=commit);save_state(state_path,state)
    endpoint='repos/'+args.repo+'/releases/tags/'+args.tag
    current=run(['gh','api',endpoint],root,ok=False)
    if current.returncode==0:
        release=json.loads(current.stdout)
        if release.get('id')!=state.get('release_id'):raise PublishError('Release tag already exists without this execution receipt. It will not be changed.')
    else:
        if '404' not in current.stdout+current.stderr:raise PublishError('Cannot inspect release; refusing to guess.')
        run(['gh','release','create',args.tag,'--repo',args.repo,'--draft','--prerelease','--target',commit,'--title','SoulX Studio 3.0.1 · Public developer preview','--notes-file',str(notes)],root)
        release=json.loads(run(['gh','api',endpoint],root).stdout)
        state.update(release_id=release['id'],phase='DRAFT_RELEASE_CREATED');save_state(state_path,state)
    existing={a['name']:a for a in release.get('assets',[])}
    for asset in assets:
        if asset['name'] in existing:
            if existing[asset['name']].get('size')!=asset['bytes']:raise PublishError('An existing release asset differs; no overwrite is performed.')
        else:
            if not release['draft']:raise PublishError('Published release is missing an asset; script will not mutate it.')
            run(['gh','release','upload',args.tag,asset['path'],'--repo',args.repo],root)
    release=json.loads(run(['gh','api',endpoint],root).stdout)
    verify_remote_assets(assets,release['assets'])
    if release['draft']:run(['gh','release','edit',args.tag,'--repo',args.repo,'--draft=false','--prerelease'],root)
    release=json.loads(run(['gh','api',endpoint],root).stdout)
    if release['draft'] or not release['prerelease']:raise PublishError('Release state verification failed.')
    state.update(phase='PUBLISHED',release_url=release['html_url'],assets_verified=True)
    save_state(state_path,state);save_state(root/'publication-receipt.json',state)
    print(json.dumps(state,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (PublishError,OSError,ValueError,json.JSONDecodeError) as exc:
        print('Publication not completed: '+str(exc),file=sys.stderr);raise SystemExit(1)
