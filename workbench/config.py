"""Runtime configuration shared by the UI, REST API, queue and MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class WorkbenchConfig:
    project_root: Path
    data_dir: Path
    model_path: Path
    host: str
    port: int
    api_token: str
    dry_run: bool
    worker_poll_seconds: float

    @classmethod
    def from_env(cls) -> "WorkbenchConfig":
        project_root = Path(os.getenv("SOULX_PROJECT_ROOT", str(PROJECT_ROOT))).expanduser().resolve()
        data_dir = Path(
            os.getenv("SOULX_DATA_DIR", str(project_root / "workbench_data"))
        ).expanduser().resolve()
        model_path = Path(
            os.getenv(
                "MODEL_PATH",
                str(project_root / "pretrained_models" / "SoulX-Podcast-1.7B-dialect"),
            )
        ).expanduser().resolve()
        return cls(
            project_root=project_root,
            data_dir=data_dir,
            model_path=model_path,
            host=os.getenv("SOULX_HOST", "127.0.0.1"),
            port=int(os.getenv("SOULX_PORT", "8000")),
            api_token=os.getenv("SOULX_API_TOKEN", "").strip(),
            dry_run=os.getenv("SOULX_DRY_RUN", "false").lower() in {"1", "true", "yes"},
            worker_poll_seconds=max(0.05, float(os.getenv("SOULX_WORKER_POLL", "0.4"))),
        )

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.data_dir / "voices",
            self.data_dir / "results",
            self.data_dir / "projects",
            self.data_dir / "exports",
            self.data_dir / "trash",
        ):
            directory.mkdir(parents=True, exist_ok=True)
