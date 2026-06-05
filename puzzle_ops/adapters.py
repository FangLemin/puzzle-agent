from __future__ import annotations

from puzzle_ops.cms import MockCMSClient
from puzzle_ops.runtime import ToolRegistry


class MCPToolAdapter:
    """MCP-like local adapter: protocol-shaped tools, local implementations."""

    def __init__(self):
        self.registry = ToolRegistry()
        self._tools: dict[str, str] = {}

    def register_cms(self, cms: MockCMSClient) -> None:
        self.registry.register("cms.query_inventory", cms.query_inventory)
        self.registry.register("cms.search_assets", cms.search_assets)
        self.registry.register("cms.low_stock_tags", cms.low_stock_tags)
        self._tools.update(
            {
                "cms.query_inventory": "查询 CMS 全局未分发素材库中某个运营 tag 的库存。",
                "cms.search_assets": "按国家/JS分类检索 CMS Mock 素材。",
                "cms.low_stock_tags": "返回库存低于阈值的运营 tag。",
            }
        )

    def manifest(self) -> dict[str, object]:
        return {
            "name": "puzzle_ops_mcp_like_adapter",
            "version": "0.1",
            "tools": self._tools,
        }
