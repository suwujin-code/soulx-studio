from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_python_files_parse(self):
        for path in list((ROOT / "workbench").glob("*.py")) + [ROOT / "run_workbench.py"]:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_rest_api_contract(self):
        source = (ROOT / "workbench" / "api.py").read_text(encoding="utf-8")
        for route in (
            "/api/v1",
            "/voices",
            "/tts/jobs",
            "/tts/batches",
            "/cancel",
            "/retry",
            "/pause",
            "/resume",
            "/projects",
            "/system/export",
            "/system/import-preview",
        ):
            self.assertIn(route, source)

    def test_mcp_contract(self):
        source = (ROOT / "workbench" / "mcp_server.py").read_text(encoding="utf-8")
        for tool_name in (
            "list_voices",
            "submit_tts",
            "submit_batch",
            "get_job",
            "list_jobs",
            "cancel_job",
            "retry_job",
            "queue_status",
            "pause_queue",
            "resume_queue",
        ):
            self.assertIn(f"def {tool_name}", source)

    def test_frontend_contract(self):
        source = (ROOT / "workbench" / "ui.py").read_text(encoding="utf-8")
        for label in ("立即配音", "音色中心", "批量与队列", "项目与跨 Mac", "接口与系统"):
            self.assertIn(label, source)
        for event in (".click(", ".change(", ".submit(", ".tick("):
            self.assertIn(event, source)


if __name__ == "__main__":
    unittest.main()
