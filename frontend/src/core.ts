namespace SoulX {
  export const uid=(p='id')=>p+'_'+(globalThis.crypto?.randomUUID?.()||Date.now().toString(36)+Math.random().toString(36).slice(2));
  export const now=()=>new Date().toISOString();
  export const escape=(v:unknown)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
  export const clone=<T>(v:T):T=>JSON.parse(JSON.stringify(v));
  export const fmtTime=(n=0)=>`${Math.floor(n/60).toString().padStart(2,'0')}:${Math.floor(n%60).toString().padStart(2,'0')}`;
  export const shortDate=(d:string)=>new Date(d).toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'});
  export const fmtSize=(n:number)=>n>1024*1024?(n/1024/1024).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';
  export const splitText=(t:string)=>t.trim().split(/(?<=[。！？!?])\s*|\n+/u).map(x=>x.trim()).filter(Boolean);
  export function newSegment(text:string,voiceId:string,role='旁白'):Segment { return {id:uid('seg'),text,sourceText:text,subtitleText:text,voiceId,role,pause:0.35,locked:false,takes:[]}; }
  export function newProject(name='未命名配音项目',mode:'single'|'dialogue'='single',texts:string[]=['']):Project {
    if(state?.projects.length>=500)throw new Error('当前工作区项目数达到 500，请备份并使用独立工作区');
    const voiceId=state?.voices.find(v=>!v.deleted&&v.kind!=='placeholder')?.id||'reference-hk';
    return {id:uid('project'),name,mode,category:mode==='dialogue'?'双人对话':'短视频',color:'blue',createdAt:now(),updatedAt:now(),archived:false,segments:texts.map((t,i)=>newSegment(t,voiceId,mode==='dialogue'?(i%2?'嘉宾':'主持人'):'旁白'))};
  }
  export function seed():Workspace {
    const voices:Voice[]=[
      {id:'reference-hk',name:'阿朗 · 粤语参考',region:'香港粤语',dialect:'yue',style:'自然叙述',tags:['参考音频','CC BY 4.0'],favorite:true,transcript:'香港嘅夜景真係世界聞名。',authorization:'CC BY 4.0；eduhk-compling/11601706_QIUZHILIANG，s006.wav。归档中的测试参考声音，不代表原创商用音色。',audio:REFERENCE_WAV,kind:'reference',color:'blue',serverIds:{}},
      {id:'placeholder-warm',name:'知性女声',region:'广州粤语',dialect:'yue',style:'温暖 · 亲切',tags:['待添加参考声音'],favorite:false,transcript:'',authorization:'',kind:'placeholder',color:'rose',serverIds:{}},
      {id:'placeholder-steady',name:'沉稳男声',region:'粤语',dialect:'yue',style:'沉稳 · 讲解',tags:['待添加参考声音'],favorite:false,transcript:'',authorization:'',kind:'placeholder',color:'purple',serverIds:{}},
      {id:'placeholder-bright',name:'活泼女声',region:'粤语',dialect:'yue',style:'轻快 · 有活力',tags:['待添加参考声音'],favorite:false,transcript:'',authorization:'',kind:'placeholder',color:'amber',serverIds:{}}
    ];
    const texts=['各位朋友，大家好！今日帶大家行入廣州嘅街頭巷尾。','一杯熱奶茶，一籠新鮮出爐嘅點心，就係熟悉嘅廣州味道。','唔使趕時間，慢慢行、慢慢食，總有一間小店令你念念不忘。','美食背後，係一座城市嘅溫度，同埋每個認真生活嘅人。','下一站，你想同我哋去邊度？留言話我知啦。'];
    const project:Project={id:'project-first',name:'广州街角 · 美食探店',mode:'single',category:'短视频',color:'blue',createdAt:now(),updatedAt:now(),segments:texts.map(t=>newSegment(t,'reference-hk')),archived:false};
    return {schema:3,projects:[project],voices,activeProjectId:project.id,outbox:[]};
  }
  export let state:Workspace;
  export const ui:AppUI={page:'editor',selectedId:'',editorTab:'script',voiceFilter:'全部',projectFilter:'全部',taskFilter:'全部',search:'',modal:'',mobileNav:false,propsOpen:false,toast:'',toastError:false,connected:false,connecting:false,connectionError:'',baseUrl:'',token:'',saveStatus:'saved',saveError:'',storageMode:'browser',busy:false,playback:{label:'尚未选择音频',duration:0,current:0,playing:false}};
  export const undoStack:UndoItem[]=[];
  export const redoStack:UndoItem[]=[];
  export function project():Project { return state.projects.find(p=>p.id===state.activeProjectId)||state.projects[0]; }
  export function segment():Segment|undefined { return project()?.segments.find(s=>s.id===ui.selectedId); }
  export function voice(id:string):Voice|undefined{return state.voices.find(v=>v.id===id);}
  export function take(s:Segment):Take|undefined{return s.takes.find(t=>t.id===s.selectedTakeId)||s.takes[s.takes.length-1];}
  export function currentTakeMatches(s:Segment,t?:Take):boolean {return !!t&&s.text===t.text&&s.voiceId===t.voiceId;}
  export function allTakes(){return state.projects.flatMap(p=>p.segments.flatMap(s=>s.takes.map(t=>({project:p,segment:s,take:t}))));}
  export function remember(){undoStack.push({projectId:project().id,segments:clone(project().segments)});if(undoStack.length>60)undoStack.shift();redoStack.length=0;}
  export function history(dir:'undo'|'redo'){const from=dir==='undo'?undoStack:redoStack,to=dir==='undo'?redoStack:undoStack;const item=from.pop();if(!item)return;const p=state.projects.find(p=>p.id===item.projectId);if(!p)return;to.push({projectId:p.id,segments:clone(p.segments)});const latest=new Map(p.segments.map(s=>[s.id,s]));
    const next=item.segments.map(s=>{const current=latest.get(s.id);if(current){const merged=new Map([...s.takes,...current.takes].map(t=>[t.id,t]));s.takes=[...merged.values()];}return s;});
    for(const current of p.segments){if(!next.some(s=>s.id===current.id)&&current.takes.length)next.push(current);}
    p.segments=next;state.activeProjectId=p.id;ui.selectedId=p.segments[0]?.id||'';changed();render();}
  let db:IDBDatabase|undefined; let revision=0;let serviceOrigin=''; let saving:Promise<void>=Promise.resolve();let editTimer=0;let writeTicket=0;
  export async function loadWorkspace():Promise<void>{
    try {
      db=await new Promise<IDBDatabase>((resolve,reject)=>{const r=indexedDB.open('soulx-studio-v3',1);r.onupgradeneeded=()=>r.result.createObjectStore('workspace');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
      const record=await new Promise<any>((resolve,reject)=>{const r=db!.transaction('workspace').objectStore('workspace').get('main');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
      if(record){state=validateWorkspace(record.data);revision=record.revision||0;}else state=seed();
    }catch(e){db=undefined;state=seed();ui.saveStatus='temporary';ui.storageMode='temporary';ui.saveError='此预览环境不提供持久存储。请从本机服务地址打开；当前改动可导出工作区备份。';}
    ui.selectedId=project().segments[0]?.id||'';
    try {const c=JSON.parse(sessionStorage.getItem('soulx-connection')||'{}');ui.baseUrl=c.baseUrl||'';ui.token=c.token||'';}catch{}
    const preset=(window as any).__SOULX_API_BASE__;
    if(preset)ui.baseUrl=preset;
    if(!ui.baseUrl&&location.protocol.startsWith('http'))ui.baseUrl=location.origin;
  }
  export async function useServiceStorage(confirmed=false):Promise<void>{
    if(!ui.connected)throw new Error('请先连接本机服务');
    await saving.catch(()=>{});
    const record=await api<{revision:number;data:unknown}>('/api/v3/workspace');
    if(record.data&&!confirmed){ui.modal='switch-workspace';render();return;}
    if(record.data)state=validateWorkspace(record.data);
    undoStack.length=0;redoStack.length=0;
    revision=record.revision;serviceOrigin=apiBase();ui.storageMode='service';ui.saveStatus='saved';ui.saveError='';
    ui.selectedId=project().segments[0]?.id||'';ui.modal='';
    await persist();notify('已使用本机服务的 SQLite 工作区；没有上传到云端。');render();
  }
  export function persist():Promise<void>{
    clearTimeout(editTimer);editTimer=0;const ticket=++writeTicket;
    if(ui.storageMode==='service'){
      if(!ui.connected||apiBase()!==serviceOrigin){ui.saveStatus='error';updateSaveIndicator();return Promise.reject(new Error('工作区属于原本机服务，请重新连接后保存'));}
      const snapshot=clone(state);ui.saveStatus='saving';updateSaveIndicator();
      const next=saving.catch(()=>{}).then(async()=>{try{const result=await api<{revision:number}>('/api/v3/workspace',{method:'PUT',body:JSON.stringify({expected_revision:revision,data:snapshot})});revision=result.revision;ui.saveStatus=ticket===writeTicket&&!editTimer?'saved':'saving';ui.saveError='';updateSaveIndicator();}catch(e){ui.saveStatus='error';ui.saveError=(e as Error).message;updateSaveIndicator();throw e;}});
      saving=next;return next;
    }

    if(!db){ui.saveStatus='temporary';updateSaveIndicator();return Promise.reject(new Error(ui.saveError||'浏览器存储不可用'));}
    const snapshot=clone(state);ui.saveStatus='saving';updateSaveIndicator();
    const next=saving.catch(()=>{}).then(()=>new Promise<void>((resolve,reject)=>{
      const tx=db!.transaction('workspace','readwrite');const store=tx.objectStore('workspace');let reason='';
      const r=store.get('main');r.onsuccess=()=>{const existing=r.result;if((existing?.revision||0)!==revision){reason='另一个窗口已修改工作区。请先导出本窗口草稿，再重新载入，避免覆盖。';tx.abort();return;}store.put({revision:revision+1,data:snapshot},'main');};
      tx.oncomplete=()=>{revision++;ui.saveStatus=ticket===writeTicket&&!editTimer?'saved':'saving';ui.saveError='';updateSaveIndicator();resolve();};
      tx.onabort=tx.onerror=()=>{ui.saveStatus='error';ui.saveError=reason||'保存失败：存储空间不足或浏览器拒绝写入。请导出备份。';updateSaveIndicator();reject(new Error(ui.saveError));};
    }));saving=next;return next;
  }
  export function changed(){project().updatedAt=now();if(!db&&ui.storageMode!=='service'){updateSaveIndicator();return;}ui.saveStatus='saving';updateSaveIndicator();clearTimeout(editTimer);editTimer=window.setTimeout(()=>{editTimer=0;persist().catch(e=>notify(String(e.message),true));},350);}
  export function updateSaveIndicator(){const el=document.querySelector('[data-save-indicator]');if(el){el.className='save-indicator '+ui.saveStatus;el.innerHTML=icon(ui.saveStatus==='saved'?'cloudcheck':'alert',15)+escape(ui.saveStatus==='saved'?(ui.storageMode==='service'?'已保存到本机工作区':'已保存到此浏览器'):ui.saveStatus==='saving'?'正在保存…':ui.saveStatus==='temporary'?'临时预览 · 请导出备份':'保存失败，请导出备份');el.setAttribute('title',ui.saveError);}}
  export function validateWorkspace(input:unknown):Workspace{
    if(!input||typeof input!=='object')throw new Error('不是有效的工作区文件');const d=clone(input) as Workspace;
    if(d.schema!==3||!Array.isArray(d.projects)||!Array.isArray(d.voices)||!Array.isArray(d.outbox))throw new Error('备份版本不匹配：需要 SoulX v3 工作区');
    if(!d.projects.length||d.projects.length>500||d.voices.length>2000||d.outbox.length>100)throw new Error('项目、音色或请求数量不合法');
    const safe=(s:unknown,max=50000):s is string=>typeof s==='string'&&s.length<=max;
    const identifier=(s:unknown):s is string=>safe(s,150)&&/^[a-zA-Z0-9_-]+$/.test(s);
    const origin=(s:unknown):s is string=>{try{return safe(s,1000)&&normalizeBase(s)===s;}catch{return false;}};
    const date=(s:unknown)=>safe(s,100)&&Number.isFinite(Date.parse(s));
    const colors=['blue','rose','purple','amber'];const ids=new Set<string>();
    for(const v of d.voices){
      if(!v||!identifier(v.id)||!safe(v.name,120)||!safe(v.transcript)||!safe(v.authorization)||!safe(v.region,100)||!safe(v.dialect,30)||!safe(v.style,200)||!Array.isArray(v.tags)||v.tags.length>100||!v.tags.every(t=>safe(t,100))||!['reference','placeholder','personal'].includes(v.kind)||!colors.includes(v.color)||typeof v.favorite!=='boolean'||!v.serverIds||typeof v.serverIds!=='object'||Array.isArray(v.serverIds))throw new Error('音色字段无效');
      if(ids.has(v.id))throw new Error('音色 ID 重复');ids.add(v.id);
      for(const [key,value] of Object.entries(v.serverIds))if(!origin(key)||!identifier(value))throw new Error('音色服务引用无效');
      if(v.audio&&(typeof v.audio!=='string'||v.audio.length>30*1024*1024||!/^data:audio\/[a-zA-Z0-9.+-]+;base64,[a-zA-Z0-9+/=]+$/.test(v.audio)))throw new Error('音频只能是备份内的合法音频数据');
      if(v.deleted!==undefined&&typeof v.deleted!=='boolean')throw new Error('音色删除状态无效');
    }
    const pids=new Set<string>(),sids=new Set<string>(),tids=new Set<string>();
    for(const p of d.projects){
      if(!p||!identifier(p.id)||pids.has(p.id)||!safe(p.name,120)||!safe(p.category,120)||!date(p.createdAt)||!date(p.updatedAt)||!colors.includes(p.color)||!['single','dialogue'].includes(p.mode)||typeof p.archived!=='boolean'||!Array.isArray(p.segments)||p.segments.length>1000)throw new Error('项目字段或段落数量无效');pids.add(p.id);
      for(const s of p.segments){
        if(!s||!identifier(s.id)||sids.has(s.id)||!safe(s.text)||!safe(s.sourceText)||!safe(s.subtitleText)||!safe(s.role,60)||!ids.has(s.voiceId)||!Number.isFinite(s.pause)||s.pause<0||s.pause>5||typeof s.locked!=='boolean'||!Array.isArray(s.takes)||s.takes.length>500)throw new Error('段落字段无效，或引用的音色不存在');sids.add(s.id);
        for(const t of s.takes){if(t){for(const key of ['error','stage','duration','cancelRequested'] as const){if(t[key]===null)delete t[key];}}if(!t||!identifier(t.id)||tids.has(t.id)||!identifier(t.jobId)||!origin(t.origin)||!safe(t.text)||!ids.has(t.voiceId)||!date(t.createdAt)||!['queued','running','paused','completed','failed','cancelled'].includes(t.status)||typeof t.test!=='boolean'||typeof t.accepted!=='boolean'||(t.duration!==undefined&&(!Number.isFinite(t.duration)||t.duration<0))||(t.error!==undefined&&!safe(t.error))||(t.stage!==undefined&&!safe(t.stage,500)))throw new Error('生成版本字段无效');tids.add(t.id);}
        if(s.selectedTakeId&&!s.takes.some(t=>t.id===s.selectedTakeId))s.selectedTakeId=s.takes[s.takes.length-1]?.id;
      }
    }
    const requestIds=new Set<string>();
    for(const o of d.outbox){
      if(!o||!identifier(o.id)||requestIds.has(o.id)||!origin(o.origin)||!pids.has(o.projectId)||!date(o.createdAt)||!Array.isArray(o.mapping)||!o.mapping.length||o.mapping.length>1000||!o.body||o.body.request_id!==o.id||!Array.isArray(o.body.items)||o.body.items.length!==o.mapping.length)throw new Error('未确认提交字段无效');requestIds.add(o.id);
      const p=d.projects.find(p=>p.id===o.projectId)!;
      for(const m of o.mapping){if(!m||!p.segments.some(s=>s.id===m.segmentId)||!ids.has(m.voiceId)||!safe(m.text))throw new Error('未确认提交引用无效');}
      for(const item of o.body.items as any[]){const m=o.mapping.find(m=>m.segmentId===item.client_segment_id);if(!m||m.text!==item.text||!Array.isArray(item.voice_ids)||item.voice_ids.length!==1||!item.voice_ids.every(identifier))throw new Error('待提交文稿与记录不匹配');}
    }
    if(!pids.has(d.activeProjectId))d.activeProjectId=d.projects[0].id;
    return d;
  }
  export function exportJSON(){const b=new Blob([JSON.stringify({...state,exportedAt:now()},null,2)],{type:'application/json'});downloadBlob(b,`SoulX工作区_${new Date().toISOString().slice(0,10)}.json`);notify('已导出完整浏览器工作区，包含此浏览器已导入的参考音频；仅服务端音色及生成音频需另备份。');}
  export function downloadBlob(blob:Blob,name:string){const u=URL.createObjectURL(blob);const a=document.createElement('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),15000);}
  export async function importWorkspace(file:File){if(file.size>80*1024*1024)throw new Error('备份超过 80 MB，请在桌面服务中处理');const incoming=validateWorkspace(JSON.parse(await file.text()));const vm=new Map<string,string>();for(const v of incoming.voices){const id=uid('voice');vm.set(v.id,id);v.id=id;v.serverIds={};state.voices.push(v);}for(const p of incoming.projects){p.id=uid('project');p.name=(p.name+' · 恢复').slice(0,120);for(const s of p.segments){s.id=uid('seg');s.voiceId=vm.get(s.voiceId)!;for(const t of s.takes){const oldId=t.id;t.id=uid('take');if(s.selectedTakeId===oldId)s.selectedTakeId=t.id;t.voiceId=vm.get(t.voiceId)||t.voiceId;}}state.projects.push(p);}state.activeProjectId=state.projects[state.projects.length-1].id;ui.selectedId=project().segments[0]?.id||'';await persist();notify('备份已恢复为新项目，未覆盖任何现有项目；未确认提交不会自动重发。');render();}
  export const templates=[
    {id:'food',name:'街角美食探店',tag:'短视频',icon:'utensils',color:'amber',desc:'用熟悉的粤语，讲一间小店的故事。',texts:['今日帶大家去一間我私藏咗好耐嘅小店。','唔單止好食，更加有一份熟悉嘅人情味。','記得收藏，下次一齊嚟試下。']},
    {id:'course',name:'知识讲解 · 课程',tag:'知识科普',icon:'book',color:'blue',desc:'先提出问题，再把复杂的知识讲清楚。',texts:['今日我哋一齊學一個好實用嘅方法。','先諗清楚你想解決嘅問題，再揀適合嘅工具。','而家輪到你試下，用自己嘅例子練習一次。']},
    {id:'dialogue',name:'双人对话 · 播客',tag:'双人对话',icon:'messages',color:'purple',desc:'角色与台词分开管理，逐句制作与审听。',texts:['歡迎嚟到今日嘅節目，我哋傾下人工智能。','你好！我覺得最重要嘅，係先搵到真正嘅需求。','可唔可以分享一個你自己用過嘅例子？','當然可以，我哋由一件細事開始講起。']},
    {id:'brand',name:'品牌故事 · 产品',tag:'品牌介绍',icon:'sparkles',color:'rose',desc:'把产品特点变成真实、自然的表达。',texts:['有啲好設計，係喺日常生活入面慢慢發現嘅。','每一個細節，都係為咗令你用得更加順手。','由今日開始，俾生活多一個好選擇。']}
  ];
  export const icons:Record<string,string>={
    wave:'<path d="M3 10v4m4-8v12m5-16v20m5-15v10m4-7v4"/>',home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
    edit:'<path d="m16 3 5 5-12 12H4v-5Z"/><path d="m13 6 5 5"/>',folder:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    mic:'<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',tasks:'<rect x="4" y="3" width="16" height="19" rx="3"/><path d="m8 9 1 1 2-2m3 1h3m-9 6 1 1 2-2m3 1h3"/>',
    works:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 5V3h8v2m-6 6 5 3-5 3Z"/>',grid:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    settings:'<path d="m9 3-1 3-3 1-2 4 2 2v4l4 3 3-1 3 1 4-3v-4l2-2-2-4-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',link:'<path d="m9 15 6-6m-4-3 2-2a5 5 0 0 1 7 7l-2 2m-7 5-2 2a5 5 0 0 1-7-7l2-2"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',play:'<path fill="currentColor" stroke="none" d="m9 5 11 7-11 7Z"/>',pause:'<path d="M8 5v14M16 5v14" stroke-width="4"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>',down:'<path d="m6 9 6 6 6-6"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>',check:'<path d="m4 12 5 5L20 6"/>',
    cloudcheck:'<path d="M5 17a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5 5 0 0 1 3 9m-11 3 3 3 6-6"/>',shield:'<path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6Z"/><path d="m8 11 3 3 5-5"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    undo:'<path d="m8 4-5 5 5 5M3 9h11a6 6 0 0 1 0 12"/>',redo:'<path d="m16 4 5 5-5 5m5-5H10a6 6 0 0 0 0 12"/>',lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4"/>',
    unlock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8-1"/>',copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16H3V3h13v1"/>',trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    volume:'<path d="m11 4-6 5H2v6h3l6 5Zm4 4a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',headphones:'<path d="M4 14V9a8 8 0 0 1 16 0v5"/><rect x="2" y="12" width="5" height="9" rx="2"/><rect x="17" y="12" width="5" height="9" rx="2"/>',
    star:'<path d="m12 2 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z"/>',heart:'<path d="M12 21 3 12a6 6 0 0 1 9-8 6 6 0 0 1 9 8Z"/>',
    sparkles:'<path d="m12 3 2 6 6 3-6 2-2 7-2-7-7-2 7-3Zm8-2v4m-2-2h4"/>',menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',alert:'<path d="m12 3 10 18H2Z"/><path d="M12 9v4m0 4h.01"/>',
    refresh:'<path d="M20 8a8 8 0 0 0-14-3L3 8m0-6v6h6m-5 8a8 8 0 0 0 14 3l3-3m0 6v-6h-6"/>',book:'<path d="M12 5v16M3 3c5 0 6 2 9 2 3 0 4-2 9-2v16c-5 0-6 2-9 2-3 0-4-2-9-2Z"/>',
    messages:'<path d="M3 3h15v12H8l-5 4Zm15 5h4v14l-5-3h-6v-4"/>',utensils:'<path d="M5 2v8m-3-8v6a3 3 0 0 0 6 0V2M5 11v11M20 2c-5 3-5 8 0 9V2Zm0 9v11"/>',
    split:'<path d="M12 21V11m0 0L5 4m7 7 7-7M2 4h5v5m10 0V4h5"/>',grip:'<circle cx="9" cy="5" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="19" r="1"/>',
    up:'<path d="m6 15 6-6 6 6"/>',bolt:'<path d="m14 2-10 12h7l-1 8 10-12h-7Z"/>',external:'<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',stop:'<rect x="6" y="6" width="12" height="12" rx="2"/>',filter:'<path d="M3 5h18M6 12h12m-8 7h4"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>'
  };
  export function icon(name:string,size=18){return `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${icons[name]||icons.wave}</svg>`;}
  export function btn(action:string,label:string,ico='',classes='',extra=''){return `<button type="button" data-action="${action}" class="btn ${classes}" ${extra}>${ico?icon(ico):''}${label?`<span>${escape(label)}</span>`:''}</button>`;}
  export function ibtn(action:string,label:string,ico:string,extra=''){return btn(action,'',ico,'icon-btn',`aria-label="${escape(label)}" title="${escape(label)}" ${extra}`);}
  export function avatar(v?:Voice,size=''){return `<span class="avatar ${v?.color||'blue'} ${size}">${v?.kind==='placeholder'?icon('mic',size==='large'?28:16):icon('wave',size==='large'?30:18)}</span>`;}
  export function miniwave(color='blue'){return `<div class="mini-wave ${color}" aria-hidden="true">${Array.from({length:34},(_,i)=>`<i style="height:${8+Math.abs(Math.sin(i*1.7)*Math.cos(i*.33))*24}px"></i>`).join('')}</div>`;}
}
