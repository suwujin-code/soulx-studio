namespace SoulX {
  export type Page = 'editor'|'home'|'projects'|'voices'|'tasks'|'works'|'templates'|'settings'|'backup';
  export type JobStatus = 'queued'|'running'|'paused'|'completed'|'failed'|'cancelled';
  export interface Voice { id:string; name:string; region:string; dialect:string; style:string; tags:string[]; favorite:boolean; transcript:string; authorization:string; audio?:string; kind:'reference'|'placeholder'|'personal'; color:string; deleted?:boolean; serverIds:Record<string,string>; }
  export interface Take { id:string; jobId:string; origin:string; createdAt:string; status:JobStatus; text:string; voiceId:string; duration?:number; error?:string; stage?:string; test:boolean; accepted:boolean; cancelRequested?:boolean; }
  export interface Segment { id:string; text:string; sourceText:string; subtitleText:string; voiceId:string; role:string; pause:number; locked:boolean; takes:Take[]; selectedTakeId?:string; }
  export interface Project { id:string; name:string; category:string; mode:'single'|'dialogue'; color:string; createdAt:string; updatedAt:string; segments:Segment[]; archived:boolean; }
  export interface PendingSubmission { id:string; origin:string; projectId:string; createdAt:string; mapping:{segmentId:string;voiceId:string;text:string}[]; body:Record<string,unknown>; }
  export interface Workspace { schema:3; projects:Project[]; voices:Voice[]; activeProjectId:string; outbox:PendingSubmission[]; }
  export interface Health { dry_run:boolean; service:boolean; worker_running:boolean; dependencies:{name:string;installed:boolean}[]; model:{present:number;expected:number;complete:boolean;hash_verified:boolean;path:string}; capabilities:{immutable_jobs:boolean;zip_export:boolean;wav_merge:boolean;mp3:boolean}; queue:{counts:Record<string,number>;paused:boolean}; version:string; }
  export interface ServerJob { id:string;status:JobStatus;text:string;voice_ids:string[];result_metadata?:{duration_seconds?:number;mode?:string};stage?:string;error?:string;cancel_requested?:boolean;created_at:string; }
  export interface AppUI { page:Page; selectedId:string; editorTab:'script'|'dialogue'|'history'; voiceFilter:string; projectFilter:string; taskFilter:string; search:string; modal:string; modalData?:string; mobileNav:boolean; propsOpen:boolean; toast:string; toastError:boolean; connected:boolean; connecting:boolean; health?:Health; connectionError:string; baseUrl:string; token:string; saveStatus:'saved'|'saving'|'error'|'temporary'; saveError:string; storageMode:'browser'|'temporary'|'service'; busy:boolean; playback:{label:string;duration:number;current:number;playing:boolean;url?:string;kind?:string}; }
  export interface UndoItem { projectId:string; segments:Segment[]; }
  export const VERSION='3.0.0-preview.1';
}
