from __future__ import annotations

import csv
import os
from pathlib import Path

from puzzle_ops.models import ToolResult


class MockFeishuClient:
    def __init__(self, export_dir: Path | str):
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def write_table(self, table_name: str, rows: list[dict[str, object]]) -> ToolResult:
        path = self.export_dir / f"{table_name}.csv"
        fieldnames = sorted({key for row in rows for key in row}) if rows else ["empty"]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return ToolResult(True, {"path": str(path), "mode": "mock"}, f"已写入飞书 Mock：{table_name}")


class RealFeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    def write_table(self, table_name: str, rows: list[dict[str, object]]) -> ToolResult:
        return ToolResult(
            False,
            {"mode": "real", "table_name": table_name, "row_count": len(rows)},
            "真实飞书客户端已配置，但当前原型未执行外网写入。",
            error="REAL_FEISHU_WRITE_NOT_IMPLEMENTED",
        )


class FeishuClientFactory:
    @staticmethod
    def create(export_dir: Path | str = "exports/feishu_mock"):
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        if app_id and app_secret:
            return RealFeishuClient(app_id, app_secret)
        return MockFeishuClient(export_dir)
