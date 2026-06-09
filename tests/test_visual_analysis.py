from io import BytesIO

from PIL import Image

from puzzle_ops.visual_analysis import LocalImageAnalyzer


def png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_analyzer_extracts_warm_horizontal_palette_and_readability():
    feature = LocalImageAnalyzer().analyze_bytes(png_bytes((120, 60), (220, 70, 60)), "warm.png")

    assert feature is not None
    assert feature.orientation == "横向构图"
    assert "暖红" in feature.palette
    assert feature.temperature == "暖色"
    assert feature.brightness_level in {"明亮", "中等明度"}
    assert "低对比/纯色风险" in feature.quality_tags
    assert "层次不足" in feature.puzzle_readability


def test_analyzer_extracts_cool_vertical_palette():
    feature = LocalImageAnalyzer().analyze_bytes(png_bytes((80, 180), (40, 170, 190)), "cool.png")

    assert feature is not None
    assert feature.orientation == "竖向构图"
    assert feature.temperature == "冷色"
    assert any(color in feature.palette for color in ("清透蓝", "自然绿色"))
    assert feature.saturation_level in {"中饱和", "高饱和"}


def test_analyzer_flags_dark_low_contrast_images():
    feature = LocalImageAnalyzer().analyze_bytes(png_bytes((100, 100), (20, 20, 20)), "dark.png")

    assert feature is not None
    assert feature.brightness_level == "偏暗"
    assert "过暗风险" in feature.quality_tags
    assert "低对比/纯色风险" in feature.quality_tags


def test_analyzer_returns_none_for_non_image_bytes():
    assert LocalImageAnalyzer().analyze_bytes(b"not an image", "broken.txt") is None


def test_analyzer_summarizes_multiple_images():
    summary = LocalImageAnalyzer().summarize_bytes(
        (
            png_bytes((120, 60), (220, 70, 60)),
            png_bytes((80, 180), (40, 170, 190)),
        )
    )

    assert "暖红" in summary.palette_summary
    assert "冷色" in summary.visual_summary or "暖色" in summary.visual_summary
    assert "横向构图" in summary.composition_summary
    assert "竖向构图" in summary.composition_summary
