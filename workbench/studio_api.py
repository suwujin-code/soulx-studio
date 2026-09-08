"""v3 frontend integration. Legacy endpoints and data remain compatible.

New submissions are transactional and idempotent. Regenerations create new job
IDs; this module never calls the destructive legacy retry endpoint.
"""
from __future__ import annotations
import hashlib
import hmac
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import wave
import zipfile
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from .api import JobRequest
from .store import utc_now
from .media_tools import find_ffmpeg

VERSION = '3.0.1-preview.1'
REQUIRED_FILES = ['config.json', 'soulxpodcast_config.json', 'model.safetensors.index.json',
                  'model-00001-of-00003.safetensors','model-00002-of-00003.safetensors',
                  'model-00003-of-00003.safetensors','flow.pt','hift.pt','campplus.onnx',
                  'flow.decoder.estimator.fp32.onnx','tokenizer.json','tokenizer_config.json']

class WorkspaceWrite(BaseModel):
    expected_revision: int = Field(ge=0)
    data: dict

class SubmitItem(JobRequest):
    client_segment_id: str = Field(min_length=1, max_length=150)

class Submission(BaseModel):
    request_id: str = Field(min_length=10, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    project_name: str = Field(min_length=1, max_length=120)
    items: list[SubmitItem] = Field(min_length=1,max_length=1000)

class ExportItem(BaseModel):
    job_id: str = Field(min_length=1,max_length=100)
    text: str = Field(max_length=50000)
    pause: float = Field(default=0.35,ge=0,le=5)

class ExportRequest(BaseModel):
    name: str = Field(default='SoulX',max_length=120)
    format: Literal['zip','wav','mp3'] = 'zip'
    items: list[ExportItem] = Field(min_length=1,max_length=1000)


def attach_studio_api(app):
    store=app.state.store
    config=app.state.workbench_config
    worker=app.state.worker
    with store.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS studio_immutable_jobs(job_id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE IF NOT EXISTS studio_workspace(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data_json TEXT NOT NULL, updated_at TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS studio_submissions(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL)')

    def auth(authorization: str | None=Header(None),x_api_key: str | None=Header(None)):
        supplied=x_api_key or (authorization[7:] if authorization and authorization.lower().startswith('bearer ') else '')
        if config.api_token and not hmac.compare_digest(supplied,config.api_token):
            raise HTTPException(401,'API 密钥无效')
    r=APIRouter(prefix='/api/v3',dependencies=[Depends(auth)])

    @r.get('/workspace')
    def read_workspace():
        with store.connection() as db:
            row=db.execute("SELECT * FROM studio_workspace WHERE id='personal'").fetchone()
        return {'revision':row['revision'],'data':json.loads(row['data_json'])} if row else {'revision':0,'data':None}

    @r.put('/workspace')
    def write_workspace(request:WorkspaceWrite):
        if request.data.get('schema')!=3 or not isinstance(request.data.get('projects'),list):
            raise HTTPException(422,'工作区结构不正确')
        encoded=json.dumps(request.data,ensure_ascii=False,separators=(',',':'))
        if len(encoded.encode())>64*1024*1024:raise HTTPException(413,'服务工作区快照最大 64 MB，请拆分音色与项目')
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute("SELECT revision,data_json FROM studio_workspace WHERE id='personal'").fetchone()
                revision=row['revision'] if row else 0
                # Retry after a lost response is safe if the exact snapshot already exists.
                if row and row['data_json']==encoded and revision==request.expected_revision+1:
                    db.execute('ROLLBACK');return {'revision':revision}
                if revision!=request.expected_revision:raise HTTPException(409,'另一窗口已修改本机工作区，请先导出当前草稿再重新载入，不能覆盖')
                revision+=1
                db.execute("INSERT INTO studio_workspace VALUES('personal',?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,data_json=excluded.data_json,updated_at=excluded.updated_at",(revision,encoded,utc_now()))
                db.execute('COMMIT')
            except Exception:
                if db.in_transaction:db.execute('ROLLBACK')
                raise
        return {'revision':revision}

    @r.get('/studio/status')
    def status():
        deps=[]
        for name in ['torch','torchaudio','transformers','s3tokenizer','onnxruntime','scipy']:
            try: installed=importlib.util.find_spec(name) is not None
            except (ImportError,ValueError): installed=False
            deps.append({'name':name,'installed':installed})
        present=sum((config.model_path/f).is_file() for f in REQUIRED_FILES)
        return {'version':VERSION,'service':True,'dry_run':config.dry_run,'worker_running':worker.running,
                'dependencies':deps,'model':{'path':str(config.model_path),'present':present,'expected':len(REQUIRED_FILES),
                    'complete':present==len(REQUIRED_FILES),'hash_verified':False},
                'capabilities':{'immutable_jobs':True,'zip_export':True,'wav_merge':True,'mp3':bool(find_ffmpeg())},
                'queue':store.queue_stats(),
                'notice':'仅文件存在性与依赖导入路径检查；不代表哈希校验、模型已加载或真实粤语验收。'}

    @r.post('/segments/submit',status_code=202)
    def submit(request:Submission):
        encoded=json.dumps(request.model_dump(),sort_keys=True,ensure_ascii=False)
        digest=hashlib.sha256(encoded.encode()).hexdigest()
        # Check an existing receipt first. Repeating a completed submission must
        # not fail merely because a reference voice was subsequently archived.
        with store.connection() as db:
            old=db.execute('SELECT * FROM studio_submissions WHERE request_id=?',(request.request_id,)).fetchone()
        if old:
            if old['request_hash']!=digest: raise HTTPException(409,'相同请求编号对应不同内容，请保留原始待确认请求')
            return json.loads(old['response_json'])
        if not config.dry_run:
            env=status()
            if not env['model']['complete'] or not all(d['installed'] for d in env['dependencies']):
                raise HTTPException(503,'语音运行条件不完整。请在设置页补齐权重和依赖；没有降级到模拟语音。')
        # All business validation happens before any jobs are created.
        for item in request.items:
            for voice_id in item.voice_ids: store.get_voice(voice_id)
            if item.project_id: store.get_project(item.project_id)
            if '[S' in item.text or '<|' in item.text:
                raise HTTPException(422,'逐句编辑器不接收内部说话人/控制标记，请使用角色字段')
        batch_id='batch_'+uuid.uuid4().hex
        created=utc_now()
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old=db.execute('SELECT * FROM studio_submissions WHERE request_id=?',(request.request_id,)).fetchone()
                if old:
                    if old['request_hash']!=digest: raise HTTPException(409,'请求编号内容冲突')
                    db.execute('ROLLBACK')
                    return json.loads(old['response_json'])
                jobs=[]
                for item in request.items:
                    payload=item.store_payload();job_id='job_'+uuid.uuid4().hex
                    # Re-check references within transaction to close delete races.
                    for vid in item.voice_ids:
                        if not db.execute('SELECT 1 FROM voices WHERE id=? AND deleted_at IS NULL',(vid,)).fetchone():
                            raise HTTPException(409,'提交期间音色发生变更，请重新检查')
                    db.execute('''INSERT INTO jobs(id,batch_id,project_id,text,voice_ids_json,dialect,region,settings_json,
                        priority,status,progress,stage,max_attempts,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,'queued',0,'queued',2,?,?)''',
                        (job_id,batch_id,payload['project_id'],payload['text'],json.dumps(payload['voice_ids']),payload['dialect'],
                         payload['region'],json.dumps(payload['settings']),payload['priority'],created,created))
                    db.execute('INSERT INTO studio_immutable_jobs VALUES(?)',(job_id,))
                    jobs.append({'id':job_id,'client_segment_id':item.client_segment_id,'status':'queued','created_at':created})
                response={'request_id':request.request_id,'batch_id':batch_id,'jobs':jobs,'test':config.dry_run}
                db.execute('INSERT INTO studio_submissions VALUES(?,?,?,?)',(request.request_id,digest,json.dumps(response,ensure_ascii=False),created))
                db.execute('COMMIT')
            except Exception:
                if db.in_transaction: db.execute('ROLLBACK')
                raise
        worker.wake()
        return response

    @r.post('/voices/match')
    async def match_voice(audio:UploadFile=File(...)):
        """Look up an identical reference file; never overwrite transcript or rights."""
        sha=hashlib.sha256();size=0
        try:
            while chunk:=await audio.read(65536):
                size+=len(chunk)
                if size>20*1024*1024:raise HTTPException(413,'前端参考音频限 20 MB')
                sha.update(chunk)
            with store.connection() as db:
                row=db.execute('SELECT id FROM voices WHERE audio_sha256=? AND deleted_at IS NULL',(sha.hexdigest(),)).fetchone()
            return {'voice':store.get_voice(row['id']) if row else None}
        finally:await audio.close()

    @r.post('/exports')
    def export(request:ExportRequest):
        entries=[];total=0
        for item in request.items:
            job=store.get_job(item.job_id)
            if job['status']!='completed':raise HTTPException(409,'有片段尚未生成完成')
            if item.text!=job['text']:raise HTTPException(409,'文稿已改变，与音频版本不一致；请重做或选择对应旧版本')
            path=store.resolve_asset(job['result_path'])
            if not path or not path.is_file():raise HTTPException(404,'音频资源丢失')
            total+=path.stat().st_size
            if total>1024*1024*1024:raise HTTPException(413,'单次导出上限 1 GB，请分章节导出')
            entries.append((item,job,path))
        tmp=Path(tempfile.mkdtemp(prefix='soulx-export-',dir=store.data_dir/'exports'))
        try:
            is_test=any(j.get('result_metadata',{}).get('mode')=='dry-run' for _,j,_ in entries)
            manifest={'version':VERSION,'title':request.name,'test_audio_only':is_test,'created_at':utc_now(),
                      'subtitles':'未执行音频转写/强制对齐，未生成伪造字幕。','items':[]}
            for n,(item,job,path) in enumerate(entries,1):
                manifest['items'].append({'file':f'{n:03d}_{job["id"]}.wav','text':item.text,'pause_after':item.pause,
                    'job_id':job['id'],'duration':job.get('result_metadata',{}).get('duration_seconds'),
                    'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'metadata':job.get('result_metadata',{})})
            stem='SoulX_TEST_ONLY' if is_test else 'SoulX_audio'
            if request.format=='zip':
                output=tmp/(stem+'.zip')
                with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
                    for meta,(_,_,path) in zip(manifest['items'],entries):z.write(path,meta['file'])
                    z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                    z.writestr('文稿.txt','\n\n'.join(i.text for i,_,_ in entries))
                media='application/zip'
            else:
                output=tmp/(stem+'.wav');params=None
                try:
                    for _,_,source in entries:
                        with wave.open(str(source),'rb') as check:
                            check.getparams()
                except (wave.Error,EOFError) as exc:
                    raise HTTPException(422,'当前结果不是直接可拼接的 PCM WAV。请先导出逐句 ZIP，在音频软件中统一编码；不静默转码。') from exc
                with wave.open(str(output),'wb') as target:
                    for entry_index,(item,job,path) in enumerate(entries):
                        try:
                            with wave.open(str(path),'rb') as src:
                                p=(src.getnchannels(),src.getsampwidth(),src.getframerate(),src.getcomptype())
                                if params is None:params=p;target.setnchannels(p[0]);target.setsampwidth(p[1]);target.setframerate(p[2])
                                if params!=p:raise HTTPException(422,'片段编码不同，请先导出逐句 ZIP 并在音频软件中统一采样率')
                                while data:=src.readframes(65536):target.writeframesraw(data)
                                silence=b'\x80' if p[1]==1 else b'\x00'
                                if entry_index<len(entries)-1:target.writeframesraw(silence*int(item.pause*p[2])*p[0]*p[1])
                        except wave.Error as exc:raise HTTPException(422,'当前音频不是可直接拼接的 PCM WAV，请导出逐句 ZIP') from exc
                if request.format=='mp3':
                    ffmpeg=find_ffmpeg()
                    if not ffmpeg:raise HTTPException(503,'当前环境未安装 FFmpeg，可导出 WAV/ZIP')
                    mp3=tmp/(stem+'.mp3')
                    subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(output),'-codec:a','libmp3lame','-b:a','192k',str(mp3)],check=True,timeout=120,capture_output=True)
                    output=mp3;media='audio/mpeg'
                else:media='audio/wav'
            return FileResponse(output,media_type=media,filename=output.name,background=BackgroundTask(shutil.rmtree,tmp))
        except Exception:
            shutil.rmtree(tmp,ignore_errors=True)
            raise

    app.include_router(r)
    return app
