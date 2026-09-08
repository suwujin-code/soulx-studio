from __future__ import annotations
import errno
import io
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch
import pytest
from workbench.process_lock import ProcessFileLock
from scripts.prepare_desktop_model import safe_path,valid_file,digest,download


def test_native_lock_is_exclusive_and_reusable(tmp_path):
    a,b=ProcessFileLock(tmp_path/'worker.lock'),ProcessFileLock(tmp_path/'worker.lock')
    assert a.acquire() and a.acquire()
    assert not b.acquire()
    a.release();assert b.acquire();b.release();b.release()


def test_lock_real_subprocess(tmp_path):
    lock=ProcessFileLock(tmp_path/'真实工作区.lock');assert lock.acquire()
    code='from pathlib import Path; from workbench.process_lock import ProcessFileLock; import sys; l=ProcessFileLock(Path(sys.argv[1])); print(l.acquire(),flush=True)'
    run=lambda:subprocess.run([sys.executable,'-c',code,str(lock.path)],capture_output=True,text=True,check=True).stdout.strip()
    assert run()=='False';lock.release();assert run()=='True'


def test_lock_released_when_owner_exits(tmp_path):
    path=tmp_path/'worker.lock'
    code='from pathlib import Path; from workbench.process_lock import ProcessFileLock; import sys; l=ProcessFileLock(Path(sys.argv[1])); assert l.acquire()'
    subprocess.run([sys.executable,'-c',code,str(path)],check=True)
    l=ProcessFileLock(path);assert l.acquire();l.release()


def test_permission_error_not_disguised_as_contention(tmp_path):
    with patch('workbench.process_lock.os.open',side_effect=OSError(errno.ENOSPC,'disk full')):
        with pytest.raises(OSError):ProcessFileLock(tmp_path/'x').acquire()


def test_windows_branch_locks_byte_zero(tmp_path,monkeypatch):
    import workbench.process_lock as mod
    calls=[]
    fake=types.SimpleNamespace(LK_NBLCK=2,LK_UNLCK=0,locking=lambda fd,mode,n:calls.append((os.lseek(fd,0,os.SEEK_CUR),mode,n)))
    proxy=types.SimpleNamespace(**{k:getattr(os,k) for k in ['open','fdopen','fstat','O_RDWR','O_CREAT']},name='nt')
    monkeypatch.setattr(mod,'os',proxy);monkeypatch.setitem(sys.modules,'msvcrt',fake)
    l=ProcessFileLock(tmp_path/'中文.lock');assert l.acquire();l.release()
    assert calls==[(0,2,1),(0,0,1)]


def test_windows_branch_contended(tmp_path,monkeypatch):
    import workbench.process_lock as mod
    def locked(*args):raise OSError(errno.EACCES,'locked')
    fake=types.SimpleNamespace(LK_NBLCK=2,LK_UNLCK=0,locking=locked)
    proxy=types.SimpleNamespace(**{k:getattr(os,k) for k in ['open','fdopen','fstat','O_RDWR','O_CREAT']},name='nt')
    monkeypatch.setattr(mod,'os',proxy);monkeypatch.setitem(sys.modules,'msvcrt',fake)
    assert not ProcessFileLock(tmp_path/'x').acquire()


@pytest.mark.parametrize('path',['../x','C:/x','a\\b','/x','a/../../x'])
def test_model_path_traversal(tmp_path,path):
    with pytest.raises(ValueError):safe_path(tmp_path,path)


def test_model_digest_and_size(tmp_path):
    f=tmp_path/'model';f.write_bytes(b'weights')
    assert valid_file(f,7,digest(f));assert not valid_file(f,8,digest(f));assert not valid_file(f,7,'a'*64)


class Response(io.BytesIO):
    def __init__(self,data,status=200,headers=None):super().__init__(data);self.status=status;self.headers=headers or {}


def test_resume_when_server_ignores_range(tmp_path):
    t=tmp_path/'model';t.with_name('model.partial').write_bytes(b'wrong')
    import hashlib
    data=b'complete-valid-weights'
    download('https://example.test/m',t,len(data),hashlib.sha256(data).hexdigest(),opener=lambda *a,**k:Response(data))
    assert t.read_bytes()==data


def test_resume_checks_content_range(tmp_path,monkeypatch):
    monkeypatch.setattr('scripts.prepare_desktop_model.time.sleep',lambda s:None)
    t=tmp_path/'model';t.with_name('model.partial').write_bytes(b'ab')
    with pytest.raises(ValueError):
        download('https://example.test/m',t,4,'',opener=lambda *a,**k:Response(b'cd',206,{'Content-Range':'bytes 0-1/4'}))
    assert not t.exists()


def test_valid_partial_promoted_without_download(tmp_path):
    t=tmp_path/'model';t.with_name('model.partial').write_bytes(b'abcd')
    download('unused',t,4,'',opener=lambda *a,**k:pytest.fail('unexpected network'))
    assert t.read_bytes()==b'abcd'


def test_bad_hash_never_promoted(tmp_path,monkeypatch):
    monkeypatch.setattr('scripts.prepare_desktop_model.time.sleep',lambda s:None)
    t=tmp_path/'model'
    with pytest.raises(OSError):download('https://example.test/m',t,4,'f'*64,opener=lambda *a,**k:Response(b'bad!'))
    assert not t.exists()


def test_shadowing_audio_shim_removed():
    assert not (Path(__file__).resolve().parents[1]/'torchaudio').exists()


def test_managed_shutdown_token_and_no_real_inference(tmp_path):
    from desktop_server import build_managed_app
    from fastapi.testclient import TestClient
    app,box=build_managed_app(tmp_path/'data','test-instance','only-for-test',19001)
    box['server']=types.SimpleNamespace(should_exit=False)
    with TestClient(app) as client:
        assert client.get('/api/desktop/instance').status_code==401
        r=client.get('/api/desktop/instance',headers={'X-SoulX-Control':'only-for-test'})
        assert r.status_code==200 and r.json()['dry_run'] is False
        assert client.post('/api/desktop/shutdown',headers={'X-SoulX-Control':'only-for-test'}).status_code==200
        assert box['server'].should_exit


def test_shutdown_pause_prevents_new_claims(tmp_path):
    from workbench.config import WorkbenchConfig
    from workbench.store import WorkbenchStore
    s=WorkbenchStore(WorkbenchConfig.from_env(),tmp_path/'data')
    assert s.pause_when_idle()
    assert s.queue_stats()['paused']
    assert s.claim_next_job() is None


def test_running_job_refuses_shutdown_without_pausing(tmp_path):
    from workbench.config import WorkbenchConfig
    from workbench.store import WorkbenchStore
    from tests.test_workbench import make_wav
    s=WorkbenchStore(WorkbenchConfig.from_env(),tmp_path/'data')
    wav=tmp_path/'ref.wav';make_wav(wav)
    v=s.create_voice(name='reference',audio_file=wav,transcript='测试',source_authorization='unit-test')
    s.create_job(text='test',voice_ids=[v['id']]);assert s.claim_next_job()
    assert not s.pause_when_idle()
    assert not s.queue_stats()['paused']
