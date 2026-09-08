"""Real FastAPI/SQLite/worker integration. All synthesized fixtures are tones, NOT speech."""
from __future__ import annotations
import io,json,wave,time,uuid,threading,hashlib,zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from run_studio import build_app
from workbench.store import WorkbenchStore
from workbench.queue import JobWorker

REF=Path(__file__).parents[1]/'example/audios/cantonese_hk_male_s006.wav'

def make_voice(c):
    with REF.open('rb') as f:
        r=c.post('/api/v1/voices',files={'audio':('reference.wav',f,'audio/wav')},data={'name':'测试参考','transcript':'香港嘅夜景真係世界聞名。','source_authorization':'CC BY 4.0 / original archive reference / automated test only'})
    assert r.status_code in {200,201},r.text
    return r.json()['id']

def payload(vid,count=1,rid=None,text='早晨，大家好。'):
    return {'request_id':rid or 'request_'+uuid.uuid4().hex,'project_name':'自动化测试','items':[{'client_segment_id':f'seg_{i}','text':text,'voice_ids':[vid]} for i in range(count)]}

@pytest.fixture
def setup(tmp_path):
    app=build_app(tmp_path/'data',dry_run=True,start_worker=False)
    with TestClient(app) as c:yield app,c,make_voice(c)

def wait(c,ids,limit=20):
    end=time.monotonic()+limit
    while time.monotonic()<end:
        jobs=[c.get('/api/v1/tts/jobs/'+j).json() for j in ids]
        if all(j['status']=='completed' for j in jobs):return jobs
        time.sleep(.03)
    raise AssertionError(jobs)

def run_jobs(app,c,vid,count=2):
    app.state.worker.start()
    body=payload(vid,count)
    r=c.post('/api/v3/segments/submit',json=body)
    assert r.status_code==202,r.text
    jobs=wait(c,[j['id'] for j in r.json()['jobs']])
    app.state.worker.stop()
    return body,jobs

def test_static_frontend_and_status(setup):
    app,c,v=setup
    r=c.get('/app/');assert r.status_code==200 and 'studio.js' in r.text
    h=c.get('/api/v3/studio/status').json()
    assert h['dry_run'] and not h['model']['hash_verified'] and h['capabilities']['immutable_jobs']
    assert c.get('/app/studio.css').status_code==200
    assert c.get('/app/studio.js').status_code==200

def test_all_or_nothing_reference_validation(setup):
    app,c,v=setup;b=payload(v,2);b['items'][1]['voice_ids']=['missing']
    assert c.post('/api/v3/segments/submit',json=b).status_code==404
    assert c.get('/api/v1/tts/jobs').json()['items']==[]

@pytest.mark.parametrize('field,value',[('text',''),('text','[S1]hello'),('text','<|Yue|>测试'),('max_tokens',3)])
def test_bad_input_no_partial_jobs(setup,field,value):
    app,c,v=setup;b=payload(v,2);b['items'][1][field]=value
    assert c.post('/api/v3/segments/submit',json=b).status_code in {400,422}
    assert c.get('/api/v1/tts/jobs').json()['items']==[]

def test_idempotent_repeated_request(setup):
    app,c,v=setup;b=payload(v,3)
    a=c.post('/api/v3/segments/submit',json=b);z=c.post('/api/v3/segments/submit',json=b)
    assert a.status_code==202 and a.json()==z.json()
    assert len(c.get('/api/v1/tts/jobs').json()['items'])==3

def test_concurrent_idempotency(setup):
    app,c,v=setup;b=payload(v,4)
    with ThreadPoolExecutor(max_workers=5) as pool:responses=list(pool.map(lambda _:c.post('/api/v3/segments/submit',json=b),range(5)))
    assert all(r.status_code==202 for r in responses)
    assert all(r.json()==responses[0].json() for r in responses)
    assert len(c.get('/api/v1/tts/jobs').json()['items'])==4

def test_idempotency_payload_conflict(setup):
    app,c,v=setup;b=payload(v);assert c.post('/api/v3/segments/submit',json=b).status_code==202
    b['items'][0]['text']='另一句话'
    assert c.post('/api/v3/segments/submit',json=b).status_code==409
    assert len(c.get('/api/v1/tts/jobs').json()['items'])==1

def test_regeneration_never_overwrites_old_asset(setup):
    app,c,v=setup;body,jobs=run_jobs(app,c,v,1);old=jobs[0]
    before=c.get('/api/v1/tts/jobs/'+old['id']+'/audio').content
    newbody,newjobs=run_jobs(app,c,v,1)
    assert old['id']!=newjobs[0]['id']
    assert c.get('/api/v1/tts/jobs/'+old['id']+'/audio').content==before
    assert c.get('/api/v1/tts/jobs/'+old['id']).json()['status']=='completed'

def test_queue_pause_cancel_resume(setup):
    app,c,v=setup;b=payload(v,3);js=c.post('/api/v3/segments/submit',json=b).json()['jobs']
    j=js[0]['id'];assert c.post(f'/api/v1/tts/jobs/{j}/pause').json()['status']=='paused'
    assert c.post(f'/api/v1/tts/jobs/{j}/resume').json()['status']=='queued'
    assert c.post(f'/api/v1/tts/jobs/{j}/cancel').json()['status']=='cancelled'
    assert c.post('/api/v1/queue/pause').json()['paused']
    app.state.worker.start();time.sleep(.05)
    assert c.get('/api/v1/tts/jobs/'+js[1]['id']).json()['status']=='queued'
    assert not c.post('/api/v1/queue/resume').json()['paused']
    wait(c,[j['id'] for j in js[1:]]);app.state.worker.stop()

def test_opening_store_does_not_reset_live_job(setup):
    app,c,v=setup;store=app.state.store
    store.create_job(text='正在执行',voice_ids=[v]);job=store.claim_next_job()
    assert job['status']=='running'
    WorkbenchStore(config=store.config)
    assert store.get_job(job['id'])['status']=='running'

def test_restart_recovers_interrupted_jobs_under_lock(setup):
    app,c,v=setup;store=app.state.store
    a=store.create_job(text='模拟上次中断',voice_ids=[v]);store.claim_next_job()
    store.set_queue_paused(True);app.state.worker.start()
    assert store.get_job(a['id'])['status']=='queued'
    app.state.worker.stop()

def test_worker_does_not_release_ownership_before_finish(setup):
    app,c,v=setup;started=threading.Event();release=threading.Event()
    class Slow:
        def synthesize(self,job,voices,path,progress):
            from workbench.synthesizer import DryRunSynthesizer
            started.set();release.wait(5)
            return DryRunSynthesizer().synthesize(job,voices,path,progress)
    a=JobWorker(app.state.store,Slow());b=JobWorker(app.state.store)
    app.state.store.create_job(text='锁定执行',voice_ids=[v]);a.start();assert started.wait(2)
    a.stop(.1);b.start();assert a.running and not b.running
    release.set();a.stop(3);b.start();assert b.running;b.stop()

def test_export_zip_hashes_and_test_markers(setup):
    app,c,v=setup;body,jobs=run_jobs(app,c,v)
    r=c.post('/api/v3/exports',json={'name':'test','format':'zip','items':[{'job_id':j['id'],'text':j['text'],'pause':.4} for j in jobs]})
    assert r.status_code in {200,201},r.text
    assert 'TEST_ONLY' in r.headers['content-disposition']
    z=zipfile.ZipFile(io.BytesIO(r.content));m=json.loads(z.read('manifest.json'))
    assert m['test_audio_only'] and len(m['items'])==2
    for i in m['items']:assert hashlib.sha256(z.read(i['file'])).hexdigest()==i['sha256']
    assert list((app.state.store.data_dir/'exports').iterdir())==[]

def test_export_wav_pause_math(setup):
    app,c,v=setup;body,jobs=run_jobs(app,c,v)
    r=c.post('/api/v3/exports',json={'format':'wav','items':[{'job_id':j['id'],'text':j['text'],'pause':.4} for j in jobs]})
    assert r.status_code in {200,201},r.text
    with wave.open(io.BytesIO(r.content)) as f:
        duration=f.getnframes()/f.getframerate()
    assert abs(duration-(sum(j['result_metadata']['duration_seconds'] for j in jobs)+.4))<.003

@pytest.mark.parametrize('format',['zip','wav','mp3'])
def test_export_refuses_stale_text(setup,format):
    app,c,v=setup;body,jobs=run_jobs(app,c,v,1)
    r=c.post('/api/v3/exports',json={'format':format,'items':[{'job_id':jobs[0]['id'],'text':'修改后的不同文稿'}]})
    assert r.status_code==409

def test_reference_match_does_not_mutate_transcript(setup):
    app,c,v=setup
    r=c.post('/api/v3/voices/match',files={'audio':('reference.wav',REF.read_bytes(),'audio/wav')})
    assert r.json()['voice']['id']==v
    assert c.get('/api/v1/voices/'+v).json()['transcript']=='香港嘅夜景真係世界聞名。'

def test_auth_enforced(tmp_path,monkeypatch):
    monkeypatch.setenv('SOULX_API_TOKEN','test-secret')
    with TestClient(build_app(tmp_path/'secure',dry_run=True,start_worker=False)) as c:
        assert c.get('/api/v3/studio/status').status_code==401
        assert c.get('/api/v3/studio/status',headers={'Authorization':'Bearer test-secret'}).status_code==200
        assert c.get('/api/v1/voices').status_code==401

def test_real_mode_does_not_fallback_to_test_tone(tmp_path):
    with TestClient(build_app(tmp_path/'real',dry_run=False,start_worker=False)) as c:
        v=make_voice(c);r=c.post('/api/v3/segments/submit',json=payload(v))
        assert r.status_code==503
        assert c.get('/api/v1/tts/jobs').json()['items']==[]

def test_100_sentence_durability(setup):
    app,c,v=setup;body,jobs=run_jobs(app,c,v,100)
    assert len(jobs)==100 and len({j['id'] for j in jobs})==100
    assert all(j['result_metadata']['mode']=='dry-run' for j in jobs)
    copied=WorkbenchStore(config=app.state.store.config)
    assert all(copied.get_job(j['id'])['status']=='completed' for j in jobs)

def test_workspace_compare_and_swap_conflict(setup):
    app,c,v=setup;d={'schema':3,'projects':[]}
    assert c.get('/api/v3/workspace').json()=={'revision':0,'data':None}
    a=c.put('/api/v3/workspace',json={'expected_revision':0,'data':d})
    assert a.status_code==200 and a.json()['revision']==1
    # Retry after response loss does not duplicate or overwrite.
    assert c.put('/api/v3/workspace',json={'expected_revision':0,'data':d}).json()['revision']==1
    different={'schema':3,'projects':[],'title':'newer'}
    assert c.put('/api/v3/workspace',json={'expected_revision':1,'data':different}).status_code==200
    assert c.put('/api/v3/workspace',json={'expected_revision':1,'data':d}).status_code==409
    assert c.get('/api/v3/workspace').json()['data']==different

def test_workspace_rejects_wrong_schema(setup):
    app,c,v=setup
    assert c.put('/api/v3/workspace',json={'expected_revision':0,'data':{'schema':2,'projects':[]}}).status_code==422

def test_untrusted_web_origin_rejected(setup):
    app,c,v=setup
    assert c.get('/api/v3/studio/status',headers={'Origin':'https://untrusted.example'}).status_code==403
    assert c.get('/api/v3/studio/status',headers={'Origin':'http://127.0.0.1:18782'}).status_code==200

def test_old_retry_cannot_overwrite_v3_take(setup):
    app,c,v=setup;_,jobs=run_jobs(app,c,v,1)
    r=c.post('/api/v1/tts/jobs/'+jobs[0]['id']+'/retry')
    assert r.status_code==409
    assert c.get('/api/v1/tts/jobs/'+jobs[0]['id']).json()['status']=='completed'

def test_repeated_segment_export_keeps_intermediate_pause(setup):
    app,c,v=setup;_,jobs=run_jobs(app,c,v,1);j=jobs[0]
    item={'job_id':j['id'],'text':j['text'],'pause':.7}
    r=c.post('/api/v3/exports',json={'format':'wav','items':[item,item]})
    assert r.status_code==200
    with wave.open(io.BytesIO(r.content)) as f:d=f.getnframes()/f.getframerate()
    assert abs(d-(2*j['result_metadata']['duration_seconds']+.7))<.002

def test_invalid_wav_export_is_clean_422(setup):
    app,c,v=setup;_,jobs=run_jobs(app,c,v,1);j=jobs[0]
    app.state.store.resolve_asset(j['result_path']).write_bytes(b'not a wav file')
    r=c.post('/api/v3/exports',json={'format':'wav','items':[{'job_id':j['id'],'text':j['text']}]})
    assert r.status_code==422
    assert list((app.state.store.data_dir/'exports').iterdir())==[]

def test_mp3_export_real_transcode_of_test_tone(setup):
    import shutil
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg unavailable')
    app,c,v=setup;_,jobs=run_jobs(app,c,v,1);j=jobs[0]
    r=c.post('/api/v3/exports',json={'format':'mp3','items':[{'job_id':j['id'],'text':j['text']}]})
    assert r.status_code==200 and len(r.content)>1000
    assert r.headers['content-type']=='audio/mpeg'
