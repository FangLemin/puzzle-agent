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

    def web_url(self) -> str:
        return str(self.export_dir)


class RealFeishuClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        spreadsheet_token: str,
        sheet_range: str,
        access_token: str,
        transport=None,
        media_transport=None,
        bitable_app_token: str = "",
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.spreadsheet_token = spreadsheet_token
        self.sheet_range = sheet_range
        self.access_token = access_token
        self.transport = transport or _default_transport
        self.media_transport = media_transport or _default_media_transport
        self._canonical_app_token = bitable_app_token or os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
        self._bitable_field_names: set[str] | None = None

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
        try:
            if self.sheet_range.startswith("tbl"):
                return self._write_bitable(table_name, rows)
            return self._write_sheet(table_name, rows)
        except RuntimeError as exc:
            return ToolResult(
                False,
                {"mode": "real", "table_name": table_name, "row_count": len(rows)},
                f"飞书真实同步失败：{table_name}",
                error=str(exc),
            )

    def web_url(self) -> str:
        configured = os.getenv("FEISHU_WEB_URL")
        if configured:
            return _ensure_url_scheme(configured)
        if self.sheet_range.startswith("tbl"):
            return f"https://feishu.cn/base/{self._canonical_bitable_app_token()}?table={self.sheet_range}"
        return f"https://feishu.cn/sheets/{self.spreadsheet_token}"

    def _canonical_bitable_app_token(self) -> str:
        if self._canonical_app_token:
            return self._canonical_app_token
        token = self.access_token or self._fetch_tenant_access_token()
        response = self.transport(
            "GET",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.spreadsheet_token}",
            {"Authorization": f"Bearer {token}"},
            {},
        )
        app = response.get("data", {}).get("app", {})
        self._canonical_app_token = str(app.get("app_token") or self.spreadsheet_token)
        return self._canonical_app_token

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
        app_token = self._canonical_bitable_app_token()
        rows = [self._prepare_bitable_attachments(row, token) for row in rows]
        field_names = self._remote_bitable_field_names(app_token, token)
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{self.sheet_range}/records/batch_create"
        response = self.transport(
            "POST",
            url,
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            {"records": [{"fields": _bitable_fields(row, field_names)} for row in rows]},
        )
        success = response.get("code") == 0
        return ToolResult(
            success,
            {"mode": "real_bitable", "table_name": table_name, "row_count": len(rows), "response": response},
            f"飞书多维表格真实同步{'成功' if success else '失败'}：{table_name}",
            error=None if success else str(response),
        )

    def upload_bitable_attachment(self, file_path: Path | str, file_name: str | None = None, content_type: str = "application/octet-stream") -> str:
        path = Path(file_path)
        token = self.access_token or self._fetch_tenant_access_token()
        name = file_name or path.name
        data = {
            "file_name": name,
            "parent_type": "bitable_image" if content_type.startswith("image/") else "bitable_file",
            "parent_node": self._canonical_bitable_app_token(),
            "size": str(path.stat().st_size),
        }
        with path.open("rb") as handle:
            response = self.media_transport(
                "POST",
                "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
                {"Authorization": f"Bearer {token}"},
                data,
                {"file": (name, handle, content_type)},
            )
        file_token = str(response.get("data", {}).get("file_token") or "")
        if response.get("code") != 0 or not file_token:
            raise RuntimeError(f"上传飞书多维表格附件失败：{response}")
        return file_token

    def _remote_bitable_field_names(self, app_token: str, token: str) -> set[str] | None:
        if self._bitable_field_names is not None:
            return self._bitable_field_names
        response = self.transport(
            "GET",
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{self.sheet_range}/fields?page_size=200",
            {"Authorization": f"Bearer {token}"},
            {},
        )
        items = response.get("data", {}).get("items", [])
        names = {
            str(item.get("field_name") or item.get("name") or "")
            for item in items
            if isinstance(item, dict) and (item.get("field_name") or item.get("name"))
        }
        self._bitable_field_names = names or None
        return self._bitable_field_names

    def _prepare_bitable_attachments(self, row: dict[str, object], token: str) -> dict[str, object]:
        if row.get("_reference_image_syncable") is False:
            return row
        path = row.get("_reference_image_path")
        if path and Path(str(path)).exists() and not _is_bitable_attachment(row.get("图片本身")):
            file_token = self.upload_bitable_attachment(
                str(path),
                _attachment_file_name(row, str(path)),
                str(row.get("_reference_image_content_type") or "image/png"),
            )
            row = dict(row)
            row["图片本身"] = [{"file_token": file_token}]
        return row

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


def _default_media_transport(method: str, url: str, headers: dict[str, str], data: dict[str, str], files: dict[str, object]) -> dict[str, object]:
    try:
        response = requests.request(method, url, headers=headers, data=data, files=files, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        text = getattr(getattr(exc, "response", None), "text", "") or getattr(locals().get("response", None), "text", "")
        raise RuntimeError(f"飞书素材上传 HTTP 请求失败：{exc}; {text[:500]}") from exc
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


def _bitable_fields(row: dict[str, object], remote_field_names: set[str] | None = None) -> dict[str, object]:
    fields = {}
    for key, value in row.items():
        if key not in BITABLE_FIELD_ALLOWLIST:
            continue
        if remote_field_names is not None and key not in remote_field_names:
            continue
        if key == "图片本身" and not _is_bitable_attachment(value):
            continue
        fields[key] = value
    return fields


def _is_bitable_attachment(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) and item.get("file_token") for item in value)


def _attachment_file_name(row: dict[str, object], path: str) -> str:
    image_name = row.get("图片名称")
    if isinstance(image_name, str) and image_name:
        return image_name
    value = row.get("图片本身")
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        text = value[0].get("text")
        if isinstance(text, str) and text:
            return text
    return Path(path).name


def _ensure_url_scheme(url: str) -> str:
    value = url.strip()
    if value.startswith(("http://", "https://")):
        return value
    return "https://" + value


BITABLE_FIELD_ALLOWLIST = {
    "提需分类",
    "国家",
    "JS分类",
    "图片本身",
    "运营tag",
    "主体内容",
    "张数",
    "需求等级",
    "加工方式",
    "交付日期",
    "主体描述",
    "备注",
    "价值观匹配度",
}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
