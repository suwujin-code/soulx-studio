"""Unified process launcher for the Gradio studio and FastAPI service."""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import threading
import webbrowser
from dataclasses import replace
from pathlib import Path

from .api import create_app
from .config import WorkbenchConfig
from .queue import JobWorker
from .store import WorkbenchStore
from .ui import build_ui


def _port_available(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host == "0.0.0.0" else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
            return True
        except OSError:
            return False


def _available_port(host: str, requested: int) -> int:
    for port in range(requested, requested + 50):
        if _port_available(host, port):
            return port
    raise RuntimeError("未找到可用端口，请关闭占用程序后重试")


def _lan_address() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "本机局域网 IP"


def main() -> None:
    parser = argparse.ArgumentParser(description="SoulX 粤语 AI 配音工作台")
    parser.add_argument("--host", default=None, help="127.0.0.1 仅本机；0.0.0.0 局域网")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--dry-run", action="store_true", help="不加载模型，仅验证全链路")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    config = WorkbenchConfig.from_env()
    host = args.host or config.host
    port = _available_port(host, args.port or config.port)
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else config.data_dir
    token = config.api_token
    lan_mode = host not in {"127.0.0.1", "localhost", "::1"}
    if lan_mode and not token:
        token_file = data_dir / ".api_token"
        data_dir.mkdir(parents=True, exist_ok=True)
        if token_file.is_file():
            token = token_file.read_text(encoding="utf-8").strip()
        else:
            token = secrets.token_urlsafe(24)
            token_file.write_text(token + "\n", encoding="utf-8")
            os.chmod(token_file, 0o600)
    config = replace(
        config,
        host=host,
        port=port,
        data_dir=data_dir,
        api_token=token,
        dry_run=bool(args.dry_run or config.dry_run),
    )
    store = WorkbenchStore(config)
    worker = JobWorker(store, config=config)
    app = create_app(config=config, store=store, worker=worker, start_worker=True)

    import gradio as gr
    import uvicorn

    ui = build_ui(store, worker)
    auth = ("soulx", token) if lan_mode else None
    app = gr.mount_gradio_app(app, ui, path="/studio", auth=auth)

    local_url = f"http://127.0.0.1:{port}/studio"
    print(f"SoulX Studio: {local_url}")
    print(f"OpenAPI: http://127.0.0.1:{port}/docs")
    if lan_mode:
        print(f"局域网地址: http://{_lan_address()}:{port}/studio")
        print("登录用户名: soulx")
        print(f"登录/API 密钥: {token}")
    elif not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(local_url)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
