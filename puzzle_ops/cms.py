from __future__ import annotations

from collections import Counter
from pathlib import Path

from puzzle_ops.models import HistoricalRecord, ToolResult
from puzzle_ops.synthetic_data import SyntheticDataGenerator


class MockCMSClient:
    """Local CMS substitute for the global undistributed asset library."""

    def __init__(self, assets: tuple[HistoricalRecord, ...]):
        self.assets = assets

    @classmethod
    def with_synthetic_assets(cls, output_dir: Path | str, country: str, weeks: int = 1) -> "MockCMSClient":
        assets = SyntheticDataGenerator(output_dir).generate_country_history(country, weeks)
        return cls(tuple(assets))

    def query_inventory(self, tag: str) -> ToolResult:
        matched = [asset for asset in self.assets if asset.operation_tag == tag]
        return ToolResult(
            True,
            {
                "tag": tag,
                "stock": len(matched),
                "items": [self._asset_payload(asset) for asset in matched],
            },
            f"CMS Mock 查询到 {len(matched)} 张库存图",
            evidence=tuple(asset.image_id for asset in matched[:5]),
        )

    def search_assets(self, country: str | None = None, js_category: str | None = None, limit: int = 20) -> ToolResult:
        assets = [
            asset
            for asset in self.assets
            if (country is None or asset.country == country) and (js_category is None or asset.js_category == js_category)
        ][:limit]
        return ToolResult(
            True,
            {"items": [self._asset_payload(asset) for asset in assets]},
            f"CMS Mock 返回 {len(assets)} 张素材",
            evidence=tuple(asset.image_id for asset in assets[:5]),
        )

    def low_stock_tags(self, threshold: int = 5) -> ToolResult:
        counts = Counter(asset.operation_tag for asset in self.assets)
        items = [{"tag": tag, "stock": stock} for tag, stock in sorted(counts.items()) if stock < threshold]
        return ToolResult(True, {"items": items}, f"发现 {len(items)} 个低库存 tag")

    def _asset_payload(self, asset: HistoricalRecord) -> dict[str, object]:
        return {
            "image_id": asset.image_id,
            "image_url": asset.image_url,
            "local_image_path": asset.local_image_path,
            "tag": asset.operation_tag,
            "country": asset.country,
            "js_category": asset.js_category,
            "grade": asset.grade,
            "source": asset.source,
        }
