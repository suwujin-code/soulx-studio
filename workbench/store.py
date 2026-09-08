"""SQLite-backed voice, project and generation job storage.

The store intentionally keeps paths relative to ``data_dir`` so a portable
bundle can be opened on another Mac without rewriting absolute paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Sequence

from .config import WorkbenchConfig


VOICE_FIELDS = {
    "name",
    "transcript",
    "dialect",
    "region",
    "gender",
    "style",
    "dialect_prompt",
    "notes",
    "source_authorization",
    "favorite",
    "tags",
}
JOB_STATUSES = {"queued", "paused", "running", "completed", "failed", "cancelled"}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


class StoreError(RuntimeError):
    pass


class ValidationError(StoreError):
    pass


class NotFoundError(StoreError):
    pass


class ConflictError(StoreError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _safe_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class WorkbenchStore:
    def __init__(self, config: WorkbenchConfig | None = None, data_dir: str | Path | None = None):
        self.config = config or WorkbenchConfig.from_env()
        self.data_dir = Path(data_dir).resolve() if data_dir else self.config.data_dir
        self.db_path = self.data_dir / "workbench.sqlite3"
        for name in ("voices", "results", "projects", "exports", "trash"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self.connection() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS voices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    audio_sha256 TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    dialect TEXT NOT NULL DEFAULT 'yue',
                    region TEXT NOT NULL DEFAULT '香港粤语',
                    gender TEXT NOT NULL DEFAULT '',
                    style TEXT NOT NULL DEFAULT '',
                    dialect_prompt TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    source_authorization TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_voices_active_hash
                    ON voices(audio_sha256) WHERE deleted_at IS NULL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT,
                    project_id TEXT,
                    text TEXT NOT NULL,
                    voice_ids_json TEXT NOT NULL,
                    dialect TEXT NOT NULL DEFAULT 'yue',
                    region TEXT NOT NULL DEFAULT '香港粤语',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 2,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    result_path TEXT,
                    result_metadata_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                    ON jobs(status, priority DESC, created_at ASC);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            voice_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(voices)").fetchall()
            }
            if "dialect_prompt" not in voice_columns:
                db.execute(
                    "ALTER TABLE voices ADD COLUMN dialect_prompt TEXT NOT NULL DEFAULT ''"
                )
            db.execute(
                "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES('queue_paused', 'false', ?)",
                (utc_now(),),
            )


    def recover_interrupted_jobs(self) -> None:
        """Caller MUST hold the cross-process worker lock before invoking this."""
        with self.connection() as db:
            db.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END, "
                       "stage='recovered',progress=0,updated_at=? WHERE status='running'", (utc_now(),))

    def _relative_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.data_dir.resolve()).as_posix()
        except ValueError as exc:
            raise ValidationError("文件必须位于工作台数据目录内") from exc

    def resolve_asset(self, relative_path: str | None) -> Path | None:
        if not relative_path:
            return None
        candidate = (self.data_dir / relative_path).resolve()
        try:
            candidate.relative_to(self.data_dir.resolve())
        except ValueError as exc:
            raise ValidationError("无效的资源路径") from exc
        return candidate

    @staticmethod
    def _voice_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["favorite"] = bool(data["favorite"])
        data["tags"] = _decode(data.pop("tags_json"), [])
        data["deleted"] = bool(data.get("deleted_at"))
        return data

    def create_voice(
        self,
        *,
        name: str,
        audio_file: str | Path,
        transcript: str,
        dialect: str = "yue",
        region: str = "香港粤语",
        gender: str = "",
        style: str = "",
        dialect_prompt: str = "",
        tags: Sequence[str] | None = None,
        notes: str = "",
        source_authorization: str,
        favorite: bool = False,
    ) -> dict[str, Any]:
        name = " ".join(name.split())
        transcript = transcript.strip()
        source_authorization = source_authorization.strip()
        source = Path(audio_file).expanduser().resolve()
        if not name:
            raise ValidationError("音色名称不能为空")
        if not transcript:
            raise ValidationError("参考文本不能为空")
        if not source_authorization:
            raise ValidationError("必须填写声音授权来源或用途说明")
        if not source.is_file():
            raise ValidationError("参考音频不存在")
        if source.stat().st_size > 100 * 1024 * 1024:
            raise ValidationError("参考音频不能超过 100MB")
        sha256 = _file_sha256(source)
        with self.connection() as db:
            duplicate = db.execute(
                "SELECT id, name FROM voices WHERE audio_sha256=? AND deleted_at IS NULL",
                (sha256,),
            ).fetchone()
        if duplicate:
            raise ConflictError(f"该参考音频已保存为音色：{duplicate['name']}")
        voice_id = _safe_id("voice")
        extension = source.suffix.lower() if source.suffix.lower() in {".wav", ".mp3", ".flac", ".m4a", ".aac"} else ".wav"
        target = self.data_dir / "voices" / f"{voice_id}{extension}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
        now = utc_now()
        normalized_tags = sorted({str(tag).strip() for tag in (tags or []) if str(tag).strip()})
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO voices(
                    id,name,audio_path,audio_sha256,transcript,dialect,region,gender,style,
                    dialect_prompt,tags_json,notes,source_authorization,favorite,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        voice_id, name, self._relative_path(target), sha256, transcript,
                        dialect.strip() or "yue", region.strip() or "香港粤语", gender.strip(),
                        style.strip(), dialect_prompt.strip(), _json(normalized_tags), notes.strip(), source_authorization,
                        int(bool(favorite)), now, now,
                    ),
                )
                db.execute("COMMIT")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return self.get_voice(voice_id)

    def get_voice(self, voice_id: str, include_deleted: bool = False) -> dict[str, Any]:
        query = "SELECT * FROM voices WHERE id=?"
        params: list[Any] = [voice_id]
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        with self.connection() as db:
            row = db.execute(query, params).fetchone()
        if not row:
            raise NotFoundError("音色不存在")
        return self._voice_dict(row)

    def list_voices(
        self,
        *,
        search: str = "",
        favorites_only: bool = False,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        conditions = []
        values: list[Any] = []
        if not include_deleted:
            conditions.append("deleted_at IS NULL")
        if favorites_only:
            conditions.append("favorite=1")
        if search.strip():
            needle = f"%{search.strip()}%"
            conditions.append("(name LIKE ? OR transcript LIKE ? OR region LIKE ? OR tags_json LIKE ?)")
            values.extend([needle, needle, needle, needle])
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(max(1, min(int(limit), 1000)))
        with self.connection() as db:
            rows = db.execute(
                f"SELECT * FROM voices{where} ORDER BY favorite DESC, updated_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._voice_dict(row) for row in rows]

    def update_voice(self, voice_id: str, **changes: Any) -> dict[str, Any]:
        self.get_voice(voice_id, include_deleted=True)
        unknown = set(changes) - VOICE_FIELDS
        if unknown:
            raise ValidationError(f"不支持更新字段：{', '.join(sorted(unknown))}")
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            column = "tags_json" if key == "tags" else key
            if key == "tags":
                value = _json(sorted({str(tag).strip() for tag in (value or []) if str(tag).strip()}))
            elif key == "favorite":
                value = int(bool(value))
            elif isinstance(value, str):
                value = value.strip()
            if key == "name" and not value:
                raise ValidationError("音色名称不能为空")
            assignments.append(f"{column}=?")
            values.append(value)
        if not assignments:
            return self.get_voice(voice_id, include_deleted=True)
        assignments.append("updated_at=?")
        values.extend([utc_now(), voice_id])
        with self.connection() as db:
            db.execute(f"UPDATE voices SET {', '.join(assignments)} WHERE id=?", values)
        return self.get_voice(voice_id, include_deleted=True)

    def delete_voice(self, voice_id: str) -> dict[str, Any]:
        voice = self.get_voice(voice_id)
        with self.connection() as db:
            active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','paused','running') AND voice_ids_json LIKE ?",
                (f"%{voice_id}%",),
            ).fetchone()[0]
            if active:
                raise ConflictError("该音色仍被未完成任务引用，请先完成或取消相关任务")
            now = utc_now()
            db.execute("UPDATE voices SET deleted_at=?, updated_at=? WHERE id=?", (now, now, voice_id))
        voice["deleted_at"] = now
        voice["deleted"] = True
        return voice

    def restore_voice(self, voice_id: str) -> dict[str, Any]:
        voice = self.get_voice(voice_id, include_deleted=True)
        with self.connection() as db:
            duplicate = db.execute(
                "SELECT id FROM voices WHERE audio_sha256=? AND deleted_at IS NULL AND id<>?",
                (voice["audio_sha256"], voice_id),
            ).fetchone()
            if duplicate:
                raise ConflictError("已有相同参考音频的有效音色，不能恢复重复项")
            now = utc_now()
            db.execute("UPDATE voices SET deleted_at=NULL, updated_at=? WHERE id=?", (now, voice_id))
        return self.get_voice(voice_id)

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["voice_ids"] = _decode(data.pop("voice_ids_json"), [])
        data["settings"] = _decode(data.pop("settings_json"), {})
        data["result_metadata"] = _decode(data.pop("result_metadata_json"), {})
        data["cancel_requested"] = bool(data["cancel_requested"])
        return data

    def create_job(
        self,
        *,
        text: str,
        voice_ids: Sequence[str],
        dialect: str = "yue",
        region: str = "香港粤语",
        settings: dict[str, Any] | None = None,
        priority: int = 0,
        project_id: str | None = None,
        batch_id: str | None = None,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        text = text.strip()
        normalized_voice_ids = list(dict.fromkeys(str(value).strip() for value in voice_ids if str(value).strip()))
        if not text:
            raise ValidationError("配音文本不能为空")
        if not 1 <= len(normalized_voice_ids) <= 4:
            raise ValidationError("每个任务必须选择 1–4 个音色")
        for voice_id in normalized_voice_ids:
            self.get_voice(voice_id)
        if project_id:
            self.get_project(project_id)
        job_id = _safe_id("job")
        now = utc_now()
        with self.connection() as db:
            db.execute(
                """INSERT INTO jobs(
                id,batch_id,project_id,text,voice_ids_json,dialect,region,settings_json,
                priority,status,progress,stage,max_attempts,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'queued',0,'queued',?,?,?)""",
                (
                    job_id, batch_id, project_id, text, _json(normalized_voice_ids),
                    dialect.strip() or "yue", region.strip() or "香港粤语",
                    _json(settings or {}), max(-100, min(int(priority), 100)),
                    max(1, min(int(max_attempts), 10)), now, now,
                ),
            )
        return self.get_job(job_id)

    def create_batch(self, items: Iterable[dict[str, Any]]) -> dict[str, Any]:
        batch_id = _safe_id("batch")
        jobs = []
        for item in items:
            jobs.append(self.create_job(batch_id=batch_id, **item))
        if not jobs:
            raise ValidationError("批量任务不能为空")
        return {"batch_id": batch_id, "jobs": jobs, "count": len(jobs)}

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise NotFoundError("任务不存在")
        return self._job_dict(row)

    def list_jobs(
        self, *, status: str | None = None, batch_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if status:
            if status not in JOB_STATUSES:
                raise ValidationError("无效任务状态")
            conditions.append("status=?")
            values.append(status)
        if batch_id:
            conditions.append("batch_id=?")
            values.append(batch_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(max(1, min(int(limit), 1000)))
        with self.connection() as db:
            rows = db.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ?", values
            ).fetchall()
        return [self._job_dict(row) for row in rows]

    def claim_next_job(self) -> dict[str, Any] | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            paused = db.execute("SELECT value FROM settings WHERE key='queue_paused'").fetchone()
            if paused and paused["value"] == "true":
                db.execute("COMMIT")
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY priority DESC, created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                db.execute("COMMIT")
                return None
            now = utc_now()
            db.execute(
                "UPDATE jobs SET status='running',stage='preparing',progress=5,attempts=attempts+1,"
                "started_at=COALESCE(started_at,?),updated_at=? WHERE id=? AND status='queued'",
                (now, now, row["id"]),
            )
            db.execute("COMMIT")
        return self.get_job(row["id"])

    def update_job_progress(self, job_id: str, progress: int, stage: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] != "running":
            return job
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET progress=?,stage=?,updated_at=? WHERE id=?",
                (max(0, min(int(progress), 99)), stage[:80], utc_now(), job_id),
            )
        return self.get_job(job_id)

    def complete_job(self, job_id: str, result_path: str | Path, metadata: dict[str, Any]) -> dict[str, Any]:
        target = Path(result_path).resolve()
        relative = self._relative_path(target)
        if not target.is_file():
            raise ValidationError("生成结果不存在")
        now = utc_now()
        with self.connection() as db:
            current = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not current:
                raise NotFoundError("任务不存在")
            if current["cancel_requested"]:
                target.unlink(missing_ok=True)
                db.execute(
                    "UPDATE jobs SET status='cancelled',stage='cancelled',progress=0,completed_at=?,updated_at=? WHERE id=?",
                    (now, now, job_id),
                )
            else:
                db.execute(
                    "UPDATE jobs SET status='completed',stage='completed',progress=100,result_path=?,"
                    "result_metadata_json=?,error=NULL,completed_at=?,updated_at=? WHERE id=?",
                    (relative, _json(metadata), now, now, job_id),
                )
        return self.get_job(job_id)

    def fail_job(self, job_id: str, error: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        now = utc_now()
        retry = job["attempts"] < job["max_attempts"] and not job["cancel_requested"]
        status = "queued" if retry else ("cancelled" if job["cancel_requested"] else "failed")
        stage = "retrying" if retry else status
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status=?,stage=?,progress=0,error=?,completed_at=?,updated_at=? WHERE id=?",
                (status, stage, str(error)[:1000], None if retry else now, now, job_id),
            )
        return self.get_job(job_id)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] in TERMINAL_JOB_STATUSES:
            return job
        now = utc_now()
        with self.connection() as db:
            if job["status"] == "running":
                db.execute(
                    "UPDATE jobs SET cancel_requested=1,stage='cancelling',updated_at=? WHERE id=?",
                    (now, job_id),
                )
            else:
                db.execute(
                    "UPDATE jobs SET status='cancelled',stage='cancelled',completed_at=?,updated_at=? WHERE id=?",
                    (now, now, job_id),
                )
        return self.get_job(job_id)

    def pause_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] != "queued":
            raise ConflictError("只有等待中的任务可以暂停")
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='paused',stage='paused',updated_at=? WHERE id=?", (utc_now(), job_id))
        return self.get_job(job_id)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] != "paused":
            raise ConflictError("只有已暂停任务可以恢复")
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='queued',stage='queued',updated_at=? WHERE id=?", (utc_now(), job_id))
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with self.connection() as db:
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='studio_immutable_jobs'").fetchone():
                if db.execute("SELECT 1 FROM studio_immutable_jobs WHERE job_id=?",(job_id,)).fetchone():
                    raise ConflictError("v3 历史版本不可覆盖，请通过逐句编辑器新建一次生成")
        job = self.get_job(job_id)
        if job["status"] not in {"failed", "cancelled", "completed"}:
            raise ConflictError("只有失败、取消或已完成任务可以重新排队")
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status='queued',stage='queued',progress=0,error=NULL,cancel_requested=0,"
                "result_path=NULL,result_metadata_json='{}',started_at=NULL,completed_at=NULL,updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )
        return self.get_job(job_id)

    def set_queue_paused(self, paused: bool) -> bool:
        self.set_setting("queue_paused", "true" if paused else "false")
        return paused

    def pause_when_idle(self) -> bool:
        """Atomically stop new claims only when no inference is running.

        The pause check in claim_next_job uses the same write transaction.
        This closes the check-then-pause race during desktop shutdown.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
            if active:
                db.execute("ROLLBACK")
                return False
            db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES('queue_paused','true',?) "
                "ON CONFLICT(key) DO UPDATE SET value='true',updated_at=excluded.updated_at",
                (utc_now(),),
            )
            db.execute("COMMIT")
        return True

    def queue_stats(self) -> dict[str, Any]:
        with self.connection() as db:
            rows = db.execute("SELECT status,COUNT(*) count FROM jobs GROUP BY status").fetchall()
        counts = {status: 0 for status in JOB_STATUSES}
        counts.update({row["status"]: row["count"] for row in rows})
        return {"paused": self.get_setting("queue_paused", "false") == "true", "counts": counts}

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, utc_now()),
            )

    def create_project(self, name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        name = " ".join(name.split())
        if not name:
            raise ValidationError("项目名称不能为空")
        project_id = _safe_id("project")
        now = utc_now()
        with self.connection() as db:
            db.execute(
                "INSERT INTO projects(id,name,data_json,created_at,updated_at) VALUES(?,?,?,?,?)",
                (project_id, name, _json(data or {}), now, now),
            )
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM projects WHERE id=? AND deleted_at IS NULL", (project_id,)).fetchone()
        if not row:
            raise NotFoundError("项目不存在")
        data = dict(row)
        data["data"] = _decode(data.pop("data_json"), {})
        data["archived"] = bool(data["archived"])
        return data

    def list_projects(self, include_archived: bool = True) -> list[dict[str, Any]]:
        condition = "deleted_at IS NULL" + ("" if include_archived else " AND archived=0")
        with self.connection() as db:
            rows = db.execute(f"SELECT * FROM projects WHERE {condition} ORDER BY updated_at DESC").fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["data"] = _decode(data.pop("data_json"), {})
            data["archived"] = bool(data["archived"])
            result.append(data)
        return result

    def update_project(self, project_id: str, *, name: str | None = None, data: dict[str, Any] | None = None, archived: bool | None = None) -> dict[str, Any]:
        self.get_project(project_id)
        assignments = ["updated_at=?"]
        values: list[Any] = [utc_now()]
        if name is not None:
            name = " ".join(name.split())
            if not name:
                raise ValidationError("项目名称不能为空")
            assignments.append("name=?")
            values.append(name)
        if data is not None:
            assignments.append("data_json=?")
            values.append(_json(data))
        if archived is not None:
            assignments.append("archived=?")
            values.append(int(bool(archived)))
        values.append(project_id)
        with self.connection() as db:
            db.execute(f"UPDATE projects SET {', '.join(assignments)} WHERE id=?", values)
        return self.get_project(project_id)

    def export_portable_bundle(self, destination: str | Path | None = None) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = Path(destination).resolve() if destination else self.data_dir / "exports" / f"soulx_workbench_{timestamp}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="soulx-export-") as temporary_directory:
            snapshot = Path(temporary_directory) / "workbench.sqlite3"
            with self.connection() as source, sqlite3.connect(snapshot) as backup:
                source.backup(backup)
            manifest = {
                "format": "soulx-workbench-portable",
                "schema_version": 1,
                "created_at": utc_now(),
                "includes": ["database", "voices", "results", "projects"],
            }
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, "workbench.sqlite3")
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                for directory_name in ("voices", "results", "projects"):
                    directory = self.data_dir / directory_name
                    for file_path in directory.rglob("*"):
                        if file_path.is_file():
                            archive.write(file_path, file_path.relative_to(self.data_dir).as_posix())
        return target

    @staticmethod
    def inspect_portable_bundle(bundle_path: str | Path) -> dict[str, Any]:
        bundle = Path(bundle_path).expanduser().resolve()
        if not bundle.is_file():
            raise ValidationError("迁移包不存在")
        with zipfile.ZipFile(bundle) as archive:
            for member in archive.namelist():
                pure = PurePosixPath(member)
                if pure.is_absolute() or ".." in pure.parts:
                    raise ValidationError("迁移包包含不安全路径")
            if "manifest.json" not in archive.namelist() or "workbench.sqlite3" not in archive.namelist():
                raise ValidationError("不是有效的 SoulX 工作台迁移包")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != "soulx-workbench-portable":
                raise ValidationError("迁移包格式不兼容")
            return {**manifest, "file_count": len(archive.namelist()), "size_bytes": bundle.stat().st_size}

    @classmethod
    def restore_portable_bundle(cls, bundle_path: str | Path, destination: str | Path) -> "WorkbenchStore":
        cls.inspect_portable_bundle(bundle_path)
        target = Path(destination).expanduser().resolve()
        if target.exists() and any(target.iterdir()):
            raise ConflictError("恢复目标目录必须为空，避免覆盖另一台 Mac 的现有数据")
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(Path(bundle_path).expanduser().resolve()) as archive:
            archive.extractall(target)
        return cls(data_dir=target)
