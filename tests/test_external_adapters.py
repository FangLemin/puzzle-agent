import pytest

from puzzle_ops import feishu
from puzzle_ops.adapters import MCPToolAdapter
from puzzle_ops.cms import MockCMSClient
from puzzle_ops.feishu import FeishuClientFactory, RealFeishuClient
from puzzle_ops.models import ToolResult


def test_mock_cms_queries_inventory_and_low_stock_tags(tmp_path):
    cms = MockCMSClient.with_synthetic_assets(tmp_path, "日本", weeks=1)

    result = cms.query_inventory("常规_日本_猫咪鲤鱼0401")
    low_stock = cms.low_stock_tags(threshold=5)

    assert result.success
    assert result.data["tag"] == "常规_日本_猫咪鲤鱼0401"
    assert "stock" in result.data
    assert all(item["stock"] < 5 for item in low_stock.data["items"])


def test_mcp_like_adapter_registers_cms_tools(tmp_path):
    cms = MockCMSClient.with_synthetic_assets(tmp_path, "日本", weeks=1)
    adapter = MCPToolAdapter()
    adapter.register_cms(cms)

    result = adapter.registry.call("cms.query_inventory", tag="常规_日本_猫咪鲤鱼0401")

    assert result.success
    assert result.data["tag"] == "常规_日本_猫咪鲤鱼0401"
    assert "cms.query_inventory" in adapter.manifest()["tools"]


def test_real_feishu_client_builds_official_values_append_request():
    captured = {}

    def transport(method, url, headers, json_body):
        captured.update(method=method, url=url, headers=headers, json_body=json_body)
        return {"code": 0, "msg": "success", "data": {"updates": {"updatedRows": 1}}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="sht_token",
        sheet_range="Sheet1!A1",
        access_token="t-token",
        transport=transport,
    )
    result = client.write_table("提需表", [{"运营tag": "常规_日本_猫咪鲤鱼0401", "张数": 7}])

    assert isinstance(result, ToolResult)
    assert result.success
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/open-apis/sheets/v2/spreadsheets/sht_token/values_append")
    assert captured["headers"]["Authorization"] == "Bearer t-token"
    assert captured["json_body"]["valueRange"]["range"] == "Sheet1!A1"
    assert captured["json_body"]["valueRange"]["values"][0] == ["运营tag", "张数"]


def test_real_feishu_client_fetches_tenant_access_token_when_token_missing():
    calls = []

    def transport(method, url, headers, json_body):
        calls.append({"method": method, "url": url, "headers": headers, "json_body": json_body})
        if url.endswith("/open-apis/auth/v3/tenant_access_token/internal"):
            return {"code": 0, "tenant_access_token": "tenant-token"}
        return {"code": 0, "msg": "success"}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="sht_token",
        sheet_range="Sheet1!A1",
        access_token="",
        transport=transport,
    )

    result = client.write_table("提需表", [{"运营tag": "常规_日本_猫咪鲤鱼0401"}])

    assert result.success
    assert calls[0]["json_body"] == {"app_id": "cli_xxx", "app_secret": "secret"}
    assert calls[1]["headers"]["Authorization"] == "Bearer tenant-token"


def test_real_feishu_client_uses_bitable_batch_create_when_range_is_table_id():
    captured = {}

    def transport(method, url, headers, json_body):
        captured.update(method=method, url=url, headers=headers, json_body=json_body)
        return {"code": 0, "msg": "success", "data": {"records": []}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
    )

    result = client.write_table("提需表", [{"运营tag": "常规_日本_猫咪鲤鱼0401", "张数": 7}])

    assert result.success
    assert captured["url"].endswith("/open-apis/bitable/v1/apps/app_token/tables/tbl_table_id/records/batch_create")
    assert captured["json_body"]["records"] == [{"fields": {"运营tag": "常规_日本_猫咪鲤鱼0401", "张数": 7}}]


def test_real_feishu_client_omits_plain_text_attachment_fields_for_bitable():
    captured = {}

    def transport(method, url, headers, json_body):
        captured.update(json_body=json_body)
        return {"code": 0, "msg": "success", "data": {"records": []}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
    )

    result = client.write_table("提需表", [{"图片本身": "温泉街传统浴袍美女", "运营tag": "常规_日本_传统浴袍美女0604"}])

    assert result.success
    assert captured["json_body"]["records"] == [{"fields": {"运营tag": "常规_日本_传统浴袍美女0604"}}]


def test_real_feishu_client_omits_link_style_image_field_for_bitable_attachment():
    captured = {}

    def transport(method, url, headers, json_body):
        captured.update(json_body=json_body)
        return {"code": 0, "msg": "success", "data": {"records": []}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
    )

    result = client.write_table(
        "提需表",
        [
            {
                "图片本身": [{"text": "sushi.png", "link": "/uploads/sushi.png"}],
                "图片链接": "/uploads/sushi.png",
                "运营tag": "试新_日本_寿司拼盘0609",
            }
        ],
    )

    assert result.success
    assert captured["json_body"]["records"] == [{"fields": {"运营tag": "试新_日本_寿司拼盘0609"}}]


def test_real_feishu_client_omits_unknown_bitable_fields_to_match_existing_schema():
    captured = {}

    def transport(method, url, headers, json_body):
        captured.update(json_body=json_body)
        return {"code": 0, "msg": "success", "data": {"records": []}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
    )

    result = client.write_table(
        "提需表",
        [{"运营tag": "试新_日本_寿司0609", "图片链接": "/uploads/sushi.png", "不存在字段": "x"}],
    )

    assert result.success
    assert captured["json_body"]["records"] == [{"fields": {"运营tag": "试新_日本_寿司0609"}}]


def test_real_feishu_client_omits_bitable_fields_missing_from_remote_schema():
    captured = {}

    def transport(method, url, headers, json_body):
        if url.endswith("/fields?page_size=200"):
            return {
                "code": 0,
                "data": {
                    "items": [
                        {"field_name": "运营tag"},
                        {"field_name": "主体描述"},
                    ]
                },
            }
        captured.update(json_body=json_body)
        return {"code": 0, "msg": "success", "data": {"records": []}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
        bitable_app_token="app_token",
    )

    result = client.write_table(
        "提需表",
        [
            {
                "运营tag": "试新_日本_寿司0609",
                "主体描述": "主体内容：寿司",
                "价值观匹配度": "符合日本市场价值观",
            }
        ],
    )

    assert result.success
    assert captured["json_body"]["records"] == [
        {"fields": {"运营tag": "试新_日本_寿司0609", "主体描述": "主体内容：寿司"}}
    ]


def test_real_feishu_client_keeps_real_attachment_file_tokens_for_bitable():
    captured = {}

    def transport(method, url, headers, json_body):
        captured.update(json_body=json_body)
        return {"code": 0, "msg": "success", "data": {"records": []}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
    )

    result = client.write_table(
        "提需表",
        [{"图片本身": [{"file_token": "boxcnxxxx"}], "运营tag": "试新_日本_寿司拼盘0609"}],
    )

    assert result.success
    assert captured["json_body"]["records"] == [
        {"fields": {"图片本身": [{"file_token": "boxcnxxxx"}], "运营tag": "试新_日本_寿司拼盘0609"}}
    ]


def test_real_feishu_client_uploads_bitable_image_and_returns_file_token(tmp_path):
    image_path = tmp_path / "sushi.png"
    image_path.write_bytes(b"fake-png")
    captured = {}

    def media_transport(method, url, headers, data, files):
        captured.update(method=method, url=url, headers=headers, data=data, files=files)
        return {"code": 0, "data": {"file_token": "boxcn_sushi"}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        media_transport=media_transport,
        bitable_app_token="app_token",
    )

    file_token = client.upload_bitable_attachment(image_path, "sushi.png", "image/png")

    assert file_token == "boxcn_sushi"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/open-apis/drive/v1/medias/upload_all")
    assert captured["data"]["parent_type"] == "bitable_image"
    assert captured["data"]["parent_node"] == "app_token"
    assert captured["data"]["file_name"] == "sushi.png"
    assert captured["data"]["size"] == str(len(b"fake-png"))
    assert captured["files"]["file"][0] == "sushi.png"


def test_real_feishu_client_upload_uses_canonical_bitable_app_token(tmp_path):
    image_path = tmp_path / "sushi.png"
    image_path.write_bytes(b"fake-png")
    captured = {}

    def transport(method, url, headers, json_body):
        assert method == "GET"
        assert url.endswith("/open-apis/bitable/v1/apps/wiki_node_token")
        return {"code": 0, "data": {"app": {"app_token": "app_token"}}}

    def media_transport(method, url, headers, data, files):
        captured.update(data=data)
        return {"code": 0, "data": {"file_token": "boxcn_sushi"}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="wiki_node_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
        media_transport=media_transport,
        bitable_app_token="app_token",
    )

    file_token = client.upload_bitable_attachment(image_path, "sushi.png", "image/png")

    assert file_token == "boxcn_sushi"
    assert captured["data"]["parent_node"] == "app_token"


def test_real_feishu_client_returns_failure_when_bitable_attachment_upload_fails(tmp_path):
    image_path = tmp_path / "sushi.png"
    image_path.write_bytes(b"fake-png")

    def transport(method, url, headers, json_body):
        return {"code": 0, "data": {"app": {"app_token": "app_token"}}}

    def media_transport(method, url, headers, data, files):
        raise RuntimeError("飞书素材上传 HTTP 请求失败：parent node not exist")

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="wiki_node_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
        media_transport=media_transport,
        bitable_app_token="app_token",
    )

    result = client.write_table(
        "提需表",
        [
            {
                "图片本身": "sushi.png",
                "_reference_image_path": str(image_path),
                "_reference_image_content_type": "image/png",
                "运营tag": "试新_日本_寿司0609",
            }
        ],
    )

    assert not result.success
    assert "parent node not exist" in str(result.error)


def test_real_feishu_client_uploads_local_image_before_bitable_create(tmp_path):
    image_path = tmp_path / "sushi.png"
    image_path.write_bytes(b"fake-png")
    calls = []

    def transport(method, url, headers, json_body):
        calls.append({"kind": "json", "url": url, "json_body": json_body})
        if url.endswith("/fields?page_size=200"):
            return {"code": 0, "data": {"items": [{"field_name": "图片本身"}, {"field_name": "运营tag"}]}}
        return {"code": 0, "msg": "success", "data": {"records": []}}

    def media_transport(method, url, headers, data, files):
        calls.append({"kind": "media", "url": url, "data": data, "files": files})
        return {"code": 0, "data": {"file_token": "boxcn_sushi"}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
        media_transport=media_transport,
        bitable_app_token="app_token",
    )

    result = client.write_table(
        "提需表",
        [
            {
                "图片本身": "sushi.png",
                "_reference_image_path": str(image_path),
                "_reference_image_content_type": "image/png",
                "运营tag": "试新_日本_寿司0609",
            }
        ],
    )

    assert result.success
    assert [call["kind"] for call in calls] == ["media", "json", "json"]
    assert calls[1]["url"].endswith("/fields?page_size=200")
    assert calls[2]["json_body"]["records"] == [
        {"fields": {"图片本身": [{"file_token": "boxcn_sushi"}], "运营tag": "试新_日本_寿司0609"}}
    ]


def test_real_feishu_client_does_not_upload_unsyncable_placeholder_image(tmp_path):
    image_path = tmp_path / "mock-derivative.png"
    image_path.write_bytes(b"placeholder-png")
    calls = []

    def transport(method, url, headers, json_body):
        calls.append({"kind": "json", "url": url, "json_body": json_body})
        if url.endswith("/fields?page_size=200"):
            return {"code": 0, "data": {"items": [{"field_name": "图片本身"}, {"field_name": "运营tag"}, {"field_name": "备注"}]}}
        return {"code": 0, "msg": "success", "data": {"records": []}}

    def media_transport(method, url, headers, data, files):
        raise AssertionError("placeholder/mock images must not be uploaded to Feishu attachment field")

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
        media_transport=media_transport,
        bitable_app_token="app_token",
    )

    result = client.write_table(
        "提需表",
        [
            {
                "图片本身": [{"text": "衍生参考图1_mock.png", "link": "/uploads/mock-derivative.png"}],
                "_reference_image_path": str(image_path),
                "_reference_image_content_type": "image/png",
                "_reference_image_syncable": False,
                "运营tag": "试新_日本_日式塔楼游客0614",
                "备注": "Mock provider 仅生成占位记录，不能作为真实飞书附件。",
            }
        ],
    )

    assert result.success
    assert [call["kind"] for call in calls] == ["json", "json"]
    assert calls[1]["json_body"]["records"] == [
        {"fields": {"运营tag": "试新_日本_日式塔楼游客0614", "备注": "Mock provider 仅生成占位记录，不能作为真实飞书附件。"}}
    ]


def test_real_feishu_client_bitable_web_url_reuses_cached_canonical_token():
    def transport(method, url, headers, json_body):
        raise AssertionError("web_url should not call Feishu API after sync")

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
        bitable_app_token="app_token",
    )

    assert client.web_url() == "https://feishu.cn/base/app_token?table=tbl_table_id"


def test_real_feishu_client_bitable_web_url_uses_configured_canonical_app_token():
    def transport(method, url, headers, json_body):
        raise AssertionError("configured canonical token should avoid remote lookup")

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="wiki_node_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
        bitable_app_token="app_token",
    )

    assert client.web_url() == "https://feishu.cn/base/app_token?table=tbl_table_id"


def test_real_feishu_client_bitable_web_url_resolves_canonical_app_token_when_needed():
    calls = []

    def transport(method, url, headers, json_body):
        calls.append(url)
        return {"code": 0, "data": {"app": {"app_token": "app_token"}}}

    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="wiki_node_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
        transport=transport,
    )

    assert client.web_url() == "https://feishu.cn/base/app_token?table=tbl_table_id"
    assert calls == ["https://open.feishu.cn/open-apis/bitable/v1/apps/wiki_node_token"]


def test_real_feishu_client_normalizes_configured_web_url(monkeypatch):
    monkeypatch.setenv("FEISHU_WEB_URL", "feishu.cn/base/app_token?table=tbl_table_id")
    client = RealFeishuClient(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="app_token",
        sheet_range="tbl_table_id",
        access_token="t-token",
    )

    assert client.web_url() == "https://feishu.cn/base/app_token?table=tbl_table_id"


def test_feishu_factory_reports_missing_real_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_SPREADSHEET_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    client = FeishuClientFactory.create(export_dir=tmp_path)

    assert not client.is_real
    assert "FEISHU_APP_ID" in client.config_status()["missing"]


def test_default_transport_uses_requests_json_post(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            captured["raised"] = False

        def json(self):
            return {"code": 0, "msg": "ok"}

    def fake_request(method, url, headers, json, timeout):
        captured.update(method=method, url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(feishu.requests, "request", fake_request)

    response = feishu._default_transport("POST", "https://open.feishu.cn/test", {"A": "B"}, {"x": 1})

    assert response == {"code": 0, "msg": "ok"}
    assert captured["method"] == "POST"
    assert captured["json"] == {"x": 1}
    assert captured["timeout"] == 10


def test_default_transport_reports_non_json_http_errors(monkeypatch):
    class FakeResponse:
        text = "bad gateway"

        def raise_for_status(self):
            raise feishu.requests.HTTPError("502")

        def json(self):
            raise AssertionError("json should not be read after HTTP error")

    monkeypatch.setattr(feishu.requests, "request", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError) as exc:
        feishu._default_transport("POST", "https://open.feishu.cn/test", {}, {})

    assert "飞书 HTTP 请求失败" in str(exc.value)
