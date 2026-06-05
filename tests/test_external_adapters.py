from puzzle_ops.adapters import MCPToolAdapter
from puzzle_ops.cms import MockCMSClient
from puzzle_ops.feishu import RealFeishuClient
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
