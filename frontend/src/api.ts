namespace SoulX {
  let polling=false;
  export class APIError extends Error {constructor(message:string,public status:number){super(message);}}
  export function apiBase(){return ui.baseUrl.replace(/\/$/,'');}
  export function normalizeBase(value:string){const u=new URL(value);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash||u.pathname!=='/')throw new Error('请填写服务根地址，例如 http://127.0.0.1:18781，不含路径或密钥');return u.origin;}
  export async function api<T=any>(path:string,options:RequestInit={}):Promise<T>{
    if(!ui.baseUrl)throw new Error('尚未连接本机服务');
    const headers=new Headers(options.headers);if(ui.token)headers.set('Authorization','Bearer '+ui.token);
    if(options.body&&typeof options.body==='string')headers.set('Content-Type','application/json');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{const r=await fetch(apiBase()+path,{...options,headers,signal:controller.signal});if(!r.ok){let error='请求失败 '+r.status;try{const b=await r.json();error=typeof b.detail==='string'?b.detail:JSON.stringify(b.detail);}catch{}throw new APIError(error,r.status);}return await r.json() as T;}
    catch(e){if((e as Error).name==='AbortError')throw new Error('连接超时。任务可能已经接收，请通过“恢复未确认提交”核对，不要重复新建。');throw e;}
    finally{clearTimeout(timer);}
  }
  export async function connect(){
    ui.connecting=true;ui.connectionError='';render();
    try{ui.baseUrl=normalizeBase(ui.baseUrl);const h=await api<Health>('/api/v3/studio/status');if(!h.capabilities?.immutable_jobs)throw new Error('当前服务缺少 v3 接口，请运行新版 run_studio.py');ui.health=h;ui.connected=true;
      try{sessionStorage.setItem('soulx-connection',JSON.stringify({baseUrl:ui.baseUrl,token:ui.token}));}catch{}
      if(ui.storageMode!=='service')await syncVoices();notify(h.dry_run?'已连接测试后端：只生成测试音，不是粤语语音。':'已连接本机服务；生成前仍需通过运行条件检查。');
    }catch(e){ui.connected=false;ui.health=undefined;ui.connectionError=(e as Error).message;notify('未连接：'+ui.connectionError,true);}finally{ui.connecting=false;render();}
  }
  export async function syncVoices(){
    const result=await api<{items:any[]}>('/api/v1/voices?limit=1000');
    for(const sv of result.items){let local=state.voices.find(v=>v.serverIds[apiBase()]===sv.id);if(local){local.name=sv.name;continue;}
      if(state.voices.some(v=>v.transcript===sv.transcript&&v.name===sv.name&&v.serverIds[apiBase()]))continue;
      state.voices.push({id:uid('voice'),name:sv.name,region:sv.region,dialect:sv.dialect,style:sv.style||'自定义',tags:sv.tags||[],favorite:sv.favorite,transcript:sv.transcript,authorization:sv.source_authorization,kind:'personal',color:'purple',serverIds:{[apiBase()]:sv.id}});
    }
    if(result.items.length&&ui.storageMode!=='temporary')await persist();
  }
  export function canGenerate(){return ui.saveStatus!=='temporary'&&ui.saveStatus!=='error'&&ui.connected&&!!ui.health?.worker_running&&(ui.health.dry_run||(ui.health.model.complete&&ui.health.dependencies.every(d=>d.installed)));}
  export function generateReason(){return ui.saveStatus==='temporary'?'预览环境不支持持久保存，请通过本机服务地址打开':ui.saveStatus==='error'?'请先解决保存错误或备份恢复':!ui.connected?'请先连接本机语音服务':!ui.health?.worker_running?'任务执行器未运行':!canGenerate()?'模型权重或依赖未完整，请查看设置':'每句创建独立任务，保留所有旧版本';}
  export async function ensureVoice(v:Voice):Promise<string>{
    const key=apiBase();if(v.serverIds[key])return v.serverIds[key];
    if(v.kind==='placeholder'||!v.audio||!v.transcript.trim()||!v.authorization.trim())throw new Error(`${v.name} 缺少参考音频、对应文字或授权依据`);
    const blob=await(await fetch(v.audio)).blob();const match=new FormData();match.append('audio',blob,'reference.wav');
    const existing=await api<{voice:any}>('/api/v3/voices/match',{method:'POST',body:match});
    if(existing.voice){if(existing.voice.transcript.trim()!==v.transcript.trim())throw new Error('相同参考声音已存在，但转写不同。请在音色库选择已有音色或核对转写，不能静默覆盖。');v.serverIds[key]=existing.voice.id;await persist();return existing.voice.id;}
    const f=new FormData();f.append('audio',blob,'reference.'+(blob.type.includes('mpeg')?'mp3':blob.type.includes('mp4')?'m4a':blob.type.includes('flac')?'flac':blob.type.includes('aac')?'aac':'wav'));f.append('name',v.name);f.append('transcript',v.transcript);f.append('source_authorization',v.authorization);f.append('dialect',v.dialect);f.append('region',v.region);f.append('style',v.style);f.append('tags',v.tags.join(','));
    const result=await api<any>('/api/v1/voices',{method:'POST',body:f});v.serverIds[key]=result.id;await persist();return result.id;
  }
  export async function generateSegments(ids:string[]){
    if(ui.busy)return;if(!canGenerate()){notify(generateReason(),true);ui.page='settings';render();return;}
    const p=project();const target=p.segments.filter(s=>ids.includes(s.id)&&!s.locked);
    if(!target.length)throw new Error('没有可生成的未锁定段落');
    if(state.outbox.some(o=>o.projectId===p.id&&o.origin===apiBase()))throw new Error('此项目有未确认提交，请先在任务中心恢复核对，避免重复生成');
    const active=target.find(s=>s.takes.some(t=>t.origin===apiBase()&&['queued','running','paused'].includes(t.status)));
    if(active)throw new Error('部分段落已有排队或运行中的任务，请先等待完成或取消');
    for(const s of target){if(!s.text.trim())throw new Error(`第 ${p.segments.indexOf(s)+1} 句为空`);if(s.text.length>1500)throw new Error('单句超过 1500 字，请先拆分以降低截断风险');if(/\[S\d+\]|<\|/.test(s.text))throw new Error('请移除内部说话人或控制标记，使用角色字段');if(!voice(s.voiceId)||voice(s.voiceId)!.kind==='placeholder')throw new Error('请先为每个角色选择真实参考音色');}
    ui.busy=true;render();
    try{
      const mapping=target.map(s=>({segmentId:s.id,voiceId:s.voiceId,text:s.text}));const items=[];
      for(const s of mapping){const v=voice(s.voiceId)!;const sid=await ensureVoice(v);items.push({client_segment_id:s.segmentId,text:s.text,voice_ids:[sid],dialect:v.dialect,region:v.region,seed:1988+Math.floor(Math.random()*10000),max_tokens:1200});}
      const id=uid('request');const pending:PendingSubmission={id,origin:apiBase(),projectId:p.id,createdAt:now(),mapping,body:{request_id:id,project_name:p.name,items}};
      state.outbox.push(pending);await persist();await sendPending(pending);
    }finally{ui.busy=false;render();}
  }
  export async function sendPending(o:PendingSubmission){
    if(o.origin!==apiBase())throw new Error('未确认请求属于另一服务，请连接原服务后核对');
    let result:any;
    try{result=await api<any>('/api/v3/segments/submit',{method:'POST',body:JSON.stringify(o.body)});}
    catch(e){if(e instanceof APIError&&[400,401,404,413,422,503].includes(e.status)){state.outbox=state.outbox.filter(x=>x.id!==o.id);await persist();}throw e;}
    const p=state.projects.find(p=>p.id===o.projectId);if(p){for(const j of result.jobs){const map=o.mapping.find(m=>m.segmentId===j.client_segment_id);const s=p.segments.find(s=>s.id===j.client_segment_id);if(s&&map&&!s.takes.some(t=>t.jobId===j.id&&t.origin===o.origin)){const t:Take={id:uid('take'),jobId:j.id,origin:o.origin,createdAt:j.created_at,status:'queued',text:map.text,voiceId:map.voiceId,test:!!result.test,accepted:false};s.takes.push(t);s.selectedTakeId=t.id;}}}
    state.outbox=state.outbox.filter(x=>x.id!==o.id);await persist();notify(`已提交 ${result.jobs.length} 句${result.test?'测试音任务':'配音任务'}，每句独立保存版本。`);render();await pollJobs();
  }
  export async function pollJobs(){
    if(!ui.connected||polling||ui.busy)return;polling=true;
    try{
      const active=allTakes().filter(x=>x.take.origin===apiBase()&&['queued','running','paused'].includes(x.take.status));
      const h=await api<Health>('/api/v3/studio/status');ui.health=h;
      if(active.length){const {items}=await api<{items:ServerJob[]}>('/api/v1/tts/jobs?limit=1000');let dirty=false;for(const {take:t} of active){const j=items.find(j=>j.id===t.jobId)||await api<ServerJob>('/api/v1/tts/jobs/'+encodeURIComponent(t.jobId));const before=JSON.stringify(t);t.status=j.status;t.stage=j.stage||undefined;t.error=j.error||undefined;t.cancelRequested=j.cancel_requested;t.duration=j.result_metadata?.duration_seconds;t.test=j.result_metadata?.mode==='dry-run'||t.test;if(before!==JSON.stringify(t))dirty=true;}
        if(dirty){await persist();if(!['TEXTAREA','INPUT','SELECT'].includes(document.activeElement?.tagName||'')&&!ui.modal)render();}
      }
    }catch(e){ui.connectionError=(e as Error).message;ui.connected=false;notify('与服务连接中断：本地草稿和任务记录已保留。',true);if(!['TEXTAREA','INPUT'].includes(document.activeElement?.tagName||''))render();}finally{polling=false;}
  }
  export async function jobAction(t:Take,action:'cancel'|'pause'|'resume'){
    if(!ui.connected||t.origin!==apiBase())throw new Error('请先连接此任务原来的语音服务');
    const j=await api<ServerJob>(`/api/v1/tts/jobs/${encodeURIComponent(t.jobId)}/${action}`,{method:'POST'});t.status=j.status;t.cancelRequested=j.cancel_requested;t.stage=j.stage;await persist();notify(j.cancel_requested&&j.status==='running'?'已请求取消：当前引擎可能完成本句后才停止，尚未声称已中止。':'任务状态已更新');render();
  }
  export async function requestBlob(path:string,options:RequestInit={}):Promise<Blob>{
    const headers=new Headers(options.headers);if(ui.token)headers.set('Authorization','Bearer '+ui.token);if(typeof options.body==='string')headers.set('Content-Type','application/json');
    const r=await fetch(apiBase()+path,{...options,headers});if(!r.ok){let message='下载失败 '+r.status;try{const body=await r.json();message=String(body.detail||message);}catch{}throw new Error(message);}return r.blob();
  }
  export async function exportAudio(format:'zip'|'wav'|'mp3'){
    const segments=project().segments.filter(s=>s.text.trim());if(!segments.length)throw new Error('项目没有文稿');
    const choices=segments.map(s=>({s,t:take(s)}));for(const {s,t} of choices){if(!t||t.status!=='completed')throw new Error('有句子尚未生成完成，请先完成逐句生成');if(t.origin!==apiBase()||!ui.connected)throw new Error('请连接这些音频原来的服务');if(!currentTakeMatches(s,t))throw new Error('文稿或音色已修改，有旧音频未更新，不能错误交付');if(!t.test&&!t.accepted)throw new Error('请先试听并验收所有片段；测试音可导出为明确标记的测试包');}
    const body={name:project().name,format,items:choices.map(({s,t})=>({job_id:t!.jobId,text:s.text,pause:s.pause}))};
    const blob=await requestBlob('/api/v3/exports',{method:'POST',body:JSON.stringify(body)});downloadBlob(blob,`${project().name}${choices.some(x=>x.t!.test)?'_仅测试音':''}.${format}`);notify('已导出当前选定版本；原始片段和旧版本均保留。');ui.modal='';render();
  }
}
