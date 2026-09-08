"""MCP server exposing the same persistent voices and job queue as the API."""

from __future__ import annotations

from typing import Any

from .config import WorkbenchConfig
from .queue import JobWorker
from .store import WorkbenchStore


def build_mcp(
    store: WorkbenchStore | None = None,
    worker: JobWorker | None = None,
):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP 依赖未安装，请运行 pip install -r requirements.workbench.txt"
        ) from exc

    runtime_store = store or WorkbenchStore()
    runtime_worker = worker or JobWorker(runtime_store)
    mcp = FastMCP("SoulX Cantonese TTS")

    @mcp.tool()
    def list_voices(search: str = "", favorites_only: bool = False) -> list[dict[str, Any]]:
        """搜索可用音色；返回音色 ID、名称、地区、标签和授权摘要。"""
        return runtime_store.list_voices(search=search, favorites_only=favorites_only)

    @mcp.tool()
    def submit_tts(
        text: str,
        voice_ids: list[str],
        dialect: str = "yue",
        region: str = "香港粤语",
        priority: int = 0,
        seed: int = 1988,
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        """提交一个粤语配音任务并返回可轮询的 job id。"""
        job = runtime_store.create_job(
            text=text,
            voice_ids=voice_ids,
            dialect=dialect,
            region=region,
            priority=priority,
            settings={"seed": seed, "max_tokens": max_tokens, "output_format": "wav"},
        )
        runtime_worker.start()
        runtime_worker.wake()
        return job

    @mcp.tool()
    def submit_batch(items: list[dict[str, Any]]) -> dict[str, Any]:
        """批量提交任务；每项需要 text 和 voice_ids，可包含 priority、dialect、settings。"""
        result = runtime_store.create_batch(items)
        runtime_worker.start()
        runtime_worker.wake()
        return result

    @mcp.tool()
    def get_job(job_id: str) -> dict[str, Any]:
        """查询任务状态、进度、失败原因和结果相对路径。"""
        return runtime_store.get_job(job_id)

    @mcp.tool()
    def list_jobs(status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """列出最近任务，可按 queued/running/completed/failed/cancelled 筛选。"""
        return runtime_store.list_jobs(status=status or None, limit=limit)

    @mcp.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """取消等待任务；运行中的模型任务会在当前推理结束后丢弃结果。"""
        return runtime_store.cancel_job(job_id)

    @mcp.tool()
    def retry_job(job_id: str) -> dict[str, Any]:
        """用完全相同参数重新排队。"""
        result = runtime_store.retry_job(job_id)
        runtime_worker.start()
        runtime_worker.wake()
        return result

    @mcp.tool()
    def queue_status() -> dict[str, Any]:
        """查看队列暂停状态及各状态任务数量。"""
        return runtime_store.queue_stats()

    @mcp.tool()
    def pause_queue() -> dict[str, Any]:
        """暂停领取新任务，不中断当前模型推理。"""
        return runtime_worker.pause()

    @mcp.tool()
    def resume_queue() -> dict[str, Any]:
        """恢复任务队列。"""
        runtime_worker.start()
        return runtime_worker.resume()

    return mcp


def main() -> None:
    build_mcp().run()


if __name__ == "__main__":
    main()
