"""Versioned REST API for voices, persistent TTS jobs and portable bundles."""

from __future__ import annotations

import csv
import hmac
import json
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .config import WorkbenchConfig
from .queue import JobWorker
from .store import (
    ConflictError,
    NotFoundError,
    StoreError,
    ValidationError,
    WorkbenchStore,
)


class VoiceUpdate(BaseModel):
    name: str | None = None
    transcript: str | None = None
    dialect: str | None = None
    region: str | None = None
    gender: str | None = None
    style: str | None = None
    dialect_prompt: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    source_authorization: str | None = None
    favorite: bool | None = None


class JobRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    voice_ids: list[str] = Field(min_length=1, max_length=4)
    dialect: str = "yue"
    region: str = "香港粤语"
    priority: int = Field(default=0, ge=-100, le=100)
    project_id: str | None = None
    seed: int = 1988
    max_tokens: int = Field(default=512, ge=64, le=1500)
    temperature: float = Field(default=0.6, ge=0.1, le=2.0)
    top_k: int = Field(default=100, ge=1, le=500)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    repetition_penalty: float = Field(default=1.25, ge=1.0, le=2.0)
    output_format: Literal["wav"] = "wav"

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("配音文本不能为空")
        return value

    def store_payload(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "voice_ids": self.voice_ids,
            "dialect": self.dialect,
            "region": self.region,
            "priority": self.priority,
            "project_id": self.project_id,
            "settings": {
                "seed": self.seed,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "top_k": self.top_k,
                "top_p": self.top_p,
                "repetition_penalty": self.repetition_penalty,
                "output_format": self.output_format,
            },
        }


class BatchRequest(BaseModel):
    items: list[JobRequest] = Field(min_length=1, max_length=1000)


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    data: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = None
    data: dict[str, Any] | None = None
    archived: bool | None = None


def create_app(
    *,
    config: WorkbenchConfig | None = None,
    store: WorkbenchStore | None = None,
    worker: JobWorker | None = None,
    start_worker: bool = True,
) -> FastAPI:
    runtime_config = config or WorkbenchConfig.from_env()
    runtime_store = store or WorkbenchStore(runtime_config)
    runtime_worker = worker or JobWorker(runtime_store, config=runtime_config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            runtime_worker.start()
        yield
        if start_worker:
            runtime_worker.stop()

    app = FastAPI(
        title="SoulX 粤语 AI 配音工作台 API",
        description="本地优先的音色库、批量队列、粤语配音与多 Mac 协作接口",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.store = runtime_store
    app.state.worker = runtime_worker
    app.state.workbench_config = runtime_config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def authorize(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> None:
        expected = runtime_config.api_token
        if not expected:
            return
        bearer = ""
        if authorization and authorization.lower().startswith("bearer "):
            bearer = authorization[7:].strip()
        supplied = x_api_key or bearer
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="API 密钥无效")

    protected = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])

    @app.exception_handler(StoreError)
    async def store_error_handler(_, exc: StoreError):
        from fastapi.responses import JSONResponse

        if isinstance(exc, NotFoundError):
            status_code = 404
        elif isinstance(exc, ConflictError):
            status_code = 409
        else:
            status_code = 400
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    @app.get("/")
    async def root():
        return {
            "name": "SoulX 粤语 AI 配音工作台",
            "version": "2.0.0",
            "studio": "/studio",
            "api_docs": "/docs",
            "mcp": "python -m workbench.mcp_server",
        }

    @app.get("/health")
    async def public_health():
        return {"status": "ok", "version": "2.0.0"}

    @protected.get("/health")
    async def health():
        model_path = runtime_config.model_path
        return {
            "status": "ok",
            "version": "2.0.0",
            "dry_run": runtime_config.dry_run,
            "model_ready": runtime_config.dry_run
            or (model_path / "soulxpodcast_config.json").is_file(),
            "model_path": str(model_path),
            "worker_running": runtime_worker.running,
            "queue": runtime_store.queue_stats(),
            "data_dir": str(runtime_store.data_dir),
            "api_token_enabled": bool(runtime_config.api_token),
        }

    @protected.post("/voices", status_code=201)
    async def create_voice(
        audio: UploadFile = File(...),
        name: str = Form(...),
        transcript: str = Form(...),
        dialect: str = Form("yue"),
        region: str = Form("香港粤语"),
        gender: str = Form(""),
        style: str = Form(""),
        dialect_prompt: str = Form(""),
        tags: str = Form(""),
        notes: str = Form(""),
        source_authorization: str = Form(...),
        favorite: bool = Form(False),
    ):
        extension = Path(audio.filename or "voice.wav").suffix.lower()
        if extension not in {".wav", ".mp3", ".flac", ".m4a", ".aac"}:
            raise HTTPException(status_code=400, detail="仅支持 WAV、MP3、FLAC、M4A、AAC")
        with tempfile.NamedTemporaryFile(
            suffix=extension,
            prefix="voice-upload-",
            dir=runtime_store.data_dir / "trash",
            delete=False,
        ) as temporary_handle:
            temporary = Path(temporary_handle.name)
        try:
            with temporary.open("wb") as handle:
                shutil.copyfileobj(audio.file, handle)
            return runtime_store.create_voice(
                name=name,
                audio_file=temporary,
                transcript=transcript,
                dialect=dialect,
                region=region,
                gender=gender,
                style=style,
                dialect_prompt=dialect_prompt,
                tags=[value.strip() for value in tags.replace("，", ",").split(",") if value.strip()],
                notes=notes,
                source_authorization=source_authorization,
                favorite=favorite,
            )
        finally:
            temporary.unlink(missing_ok=True)
            await audio.close()

    @protected.get("/voices")
    async def list_voices(
        search: str = "",
        favorite: bool = False,
        include_deleted: bool = False,
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        return {
            "items": runtime_store.list_voices(
                search=search,
                favorites_only=favorite,
                include_deleted=include_deleted,
                limit=limit,
            )
        }

    @protected.get("/voices/{voice_id}")
    async def get_voice(voice_id: str, include_deleted: bool = False):
        return runtime_store.get_voice(voice_id, include_deleted=include_deleted)

    @protected.patch("/voices/{voice_id}")
    async def update_voice(voice_id: str, request: VoiceUpdate):
        changes = request.model_dump(exclude_none=True)
        return runtime_store.update_voice(voice_id, **changes)

    @protected.delete("/voices/{voice_id}")
    async def delete_voice(voice_id: str):
        return runtime_store.delete_voice(voice_id)

    @protected.post("/voices/{voice_id}/restore")
    async def restore_voice(voice_id: str):
        return runtime_store.restore_voice(voice_id)

    @protected.get("/voices/{voice_id}/audio")
    async def voice_audio(voice_id: str):
        voice = runtime_store.get_voice(voice_id)
        path = runtime_store.resolve_asset(voice["audio_path"])
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="音色文件不存在")
        return FileResponse(path, filename=path.name)

    @protected.post("/tts/jobs", status_code=202)
    async def create_job(request: JobRequest):
        job = runtime_store.create_job(**request.store_payload())
        runtime_worker.wake()
        return job

    @protected.post("/tts/batches", status_code=202)
    async def create_batch(request: BatchRequest):
        result = runtime_store.create_batch(
            item.store_payload() for item in request.items
        )
        runtime_worker.wake()
        return result

    @protected.post("/tts/batches/import-csv", status_code=202)
    async def import_batch_csv(
        file: UploadFile = File(...),
        default_voice_ids: str = Form(""),
    ):
        content = (await file.read()).decode("utf-8-sig")
        reader = csv.DictReader(content.splitlines())
        if not reader.fieldnames or "text" not in reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV 必须包含 text 列")
        fallback = [value.strip() for value in default_voice_ids.split("|") if value.strip()]
        items = []
        for row_number, row in enumerate(reader, start=2):
            text = (row.get("text") or "").strip()
            if not text:
                continue
            voice_ids = [
                value.strip()
                for value in (row.get("voice_ids") or "").replace(",", "|").split("|")
                if value.strip()
            ] or fallback
            if not voice_ids:
                raise HTTPException(status_code=400, detail=f"CSV 第 {row_number} 行未提供 voice_ids")
            request = JobRequest(
                text=text,
                voice_ids=voice_ids,
                dialect=(row.get("dialect") or "yue").strip(),
                region=(row.get("region") or "香港粤语").strip(),
                priority=int(row.get("priority") or 0),
                seed=int(row.get("seed") or 1988),
                max_tokens=int(row.get("max_tokens") or 512),
            )
            items.append(request.store_payload())
        result = runtime_store.create_batch(items)
        runtime_worker.wake()
        return result

    @protected.get("/tts/jobs")
    async def list_jobs(
        status: str | None = None,
        batch_id: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        return {"items": runtime_store.list_jobs(status=status, batch_id=batch_id, limit=limit)}

    @protected.get("/tts/jobs/{job_id}")
    async def get_job(job_id: str):
        return runtime_store.get_job(job_id)

    @protected.post("/tts/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        return runtime_store.cancel_job(job_id)

    @protected.post("/tts/jobs/{job_id}/retry")
    async def retry_job(job_id: str):
        result = runtime_store.retry_job(job_id)
        runtime_worker.wake()
        return result

    @protected.post("/tts/jobs/{job_id}/pause")
    async def pause_job(job_id: str):
        return runtime_store.pause_job(job_id)

    @protected.post("/tts/jobs/{job_id}/resume")
    async def resume_job(job_id: str):
        result = runtime_store.resume_job(job_id)
        runtime_worker.wake()
        return result

    @protected.get("/tts/jobs/{job_id}/audio")
    async def job_audio(job_id: str):
        job = runtime_store.get_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(status_code=409, detail="任务尚未完成")
        path = runtime_store.resolve_asset(job["result_path"])
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="结果文件不存在")
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    @protected.get("/queue")
    async def queue_status():
        return runtime_store.queue_stats()

    @protected.post("/queue/pause")
    async def pause_queue():
        return runtime_worker.pause()

    @protected.post("/queue/resume")
    async def resume_queue():
        return runtime_worker.resume()

    @protected.post("/projects", status_code=201)
    async def create_project(request: ProjectRequest):
        return runtime_store.create_project(request.name, request.data)

    @protected.get("/projects")
    async def list_projects(include_archived: bool = True):
        return {"items": runtime_store.list_projects(include_archived=include_archived)}

    @protected.get("/projects/{project_id}")
    async def get_project(project_id: str):
        return runtime_store.get_project(project_id)

    @protected.patch("/projects/{project_id}")
    async def update_project(project_id: str, request: ProjectUpdate):
        return runtime_store.update_project(
            project_id,
            name=request.name,
            data=request.data,
            archived=request.archived,
        )

    @protected.get("/system/export")
    async def export_bundle():
        path = runtime_store.export_portable_bundle()
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @protected.post("/system/import-preview")
    async def import_preview(file: UploadFile = File(...)):
        suffix = Path(file.filename or "bundle.zip").suffix or ".zip"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            shutil.copyfileobj(file.file, temporary)
            path = Path(temporary.name)
        try:
            return runtime_store.inspect_portable_bundle(path)
        finally:
            path.unlink(missing_ok=True)
            await file.close()

    app.include_router(protected)
    return app


app = create_app()
