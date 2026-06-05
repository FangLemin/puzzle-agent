from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from puzzle_ops.models import ToolResult


class MockFeishuClient:
    def __init__(self, export_dir: Path | str):
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def write_table(self, table_name: str, rows: list[dict[str, object]]) -> ToolResult:
        path = self.export_dir / f"{table_name}.csv"
        fieldnames = _ordered_fields(rows)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return ToolResult(True, {"path": str(path), "mode": "mock"}, f"已写入飞书 Mock：{table_name}")


class RealFeishuClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        spreadsheet_token: str,
        sheet_range: str,
        access_token: str,
        transport=None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.spreadsheet_token = spreadsheet_token
        self.sheet_range = sheet_range
        self.access_token = access_token
        self.transport = transport or _default_transport

    def write_table(self, table_name: str, rows: list[dict[str, object]]) -> ToolResult:
        headers = _ordered_fields(rows)
        values = [headers] + [[row.get(header, "") for header in headers] for row in rows]
        url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values_append"
        response = self.transport(
            "POST",
            url,
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            {"valueRange": {"range": self.sheet_range, "values": values}},
        )
        success = response.get("code") == 0
        return ToolResult(
            success,
            {"mode": "real", "table_name": table_name, "row_count": len(rows), "response": response},
            f"飞书真实同步{'成功' if success else '失败'}：{table_name}",
            error=None if success else str(response),
        )


class FeishuClientFactory:
    @staticmethod
    def create(export_dir: Path | str = "exports/feishu_mock"):
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        spreadsheet_token = os.getenv("FEISHU_SPREADSHEET_TOKEN")
        sheet_range = os.getenv("FEISHU_SHEET_RANGE", "Sheet1!A1")
        access_token = os.getenv("FEISHU_ACCESS_TOKEN")
        if app_id and app_secret and spreadsheet_token and access_token:
            return RealFeishuClient(app_id, app_secret, spreadsheet_token, sheet_range, access_token)
        return MockFeishuClient(export_dir)


def _default_transport(method: str, url: str, headers: dict[str, str], json_body: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(json_body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _ordered_fields(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return ["empty"]
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields
