from __future__ import annotations

import csv
import os
from pathlib import Path

import requests

from puzzle_ops.models import ToolResult


class MockFeishuClient:
    def __init__(self, export_dir: Path | str):
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.allow_real_sync = False

    @property
    def is_real(self) -> bool:
        return False

    def config_status(self) -> dict[str, object]:
        return {
            "mode": "mock",
            "missing": tuple(key for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_SPREADSHEET_TOKEN") if not os.getenv(key)),
            "export_dir": str(self.export_dir),
        }

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

    @property
    def is_real(self) -> bool:
        return True

    def config_status(self) -> dict[str, object]:
        return {
            "mode": "real",
            "missing": (),
            "spreadsheet_token": self.spreadsheet_token,
            "sheet_range": self.sheet_range,
        }

    def write_table(self, table_name: str, rows: list[dict[str, object]]) -> ToolResult:
        if self.sheet_range.startswith("tbl"):
            return self._write_bitable(table_name, rows)
        return self._write_sheet(table_name, rows)

    def _write_sheet(self, table_name: str, rows: list[dict[str, object]]) -> ToolResult:
        headers = _ordered_fields(rows)
        values = [headers] + [[row.get(header, "") for header in headers] for row in rows]
        token = self.access_token or self._fetch_tenant_access_token()
        url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values_append"
        response = self.transport(
            "POST",
            url,
            {
                "Authorization": f"Bearer {token}",
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

    def _write_bitable(self, table_name: str, rows: list[dict[str, object]]) -> ToolResult:
        token = self.access_token or self._fetch_tenant_access_token()
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.spreadsheet_token}/tables/{self.sheet_range}/records/batch_create"
        response = self.transport(
            "POST",
            url,
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            {"records": [{"fields": row} for row in rows]},
        )
        success = response.get("code") == 0
        return ToolResult(
            success,
            {"mode": "real_bitable", "table_name": table_name, "row_count": len(rows), "response": response},
            f"飞书多维表格真实同步{'成功' if success else '失败'}：{table_name}",
            error=None if success else str(response),
        )

    def _fetch_tenant_access_token(self) -> str:
        response = self.transport(
            "POST",
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            {"Content-Type": "application/json; charset=utf-8"},
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = response.get("tenant_access_token", "")
        if response.get("code") != 0 or not token:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败：{response}")
        self.access_token = str(token)
        return self.access_token


class FeishuClientFactory:
    @staticmethod
    def create(export_dir: Path | str = "exports/feishu_mock"):
        _load_env_file(Path.cwd() / ".env")
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        spreadsheet_token = os.getenv("FEISHU_SPREADSHEET_TOKEN")
        sheet_range = os.getenv("FEISHU_SHEET_RANGE", "Sheet1!A1")
        access_token = os.getenv("FEISHU_ACCESS_TOKEN")
        if app_id and app_secret and spreadsheet_token and access_token:
            return RealFeishuClient(app_id, app_secret, spreadsheet_token, sheet_range, access_token)
        if app_id and app_secret and spreadsheet_token:
            return RealFeishuClient(app_id, app_secret, spreadsheet_token, sheet_range, "")
        return MockFeishuClient(export_dir)


def _default_transport(method: str, url: str, headers: dict[str, str], json_body: dict[str, object]) -> dict[str, object]:
    try:
        response = requests.request(method, url, headers=headers, json=json_body, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        text = getattr(getattr(exc, "response", None), "text", "") or getattr(locals().get("response", None), "text", "")
        raise RuntimeError(f"飞书 HTTP 请求失败：{exc}; {text[:500]}") from exc
    return response.json()


def _ordered_fields(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return ["empty"]
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
