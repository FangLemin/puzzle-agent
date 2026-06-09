from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import colorsys

from PIL import Image, ImageStat


@dataclass(frozen=True)
class LocalVisualFeature:
    filename: str
    size: str
    orientation: str
    palette: tuple[str, ...]
    palette_summary: str
    brightness_level: str
    saturation_level: str
    temperature: str
    quality_tags: tuple[str, ...]
    puzzle_readability: str
    average_brightness: float


@dataclass(frozen=True)
class LocalVisualSummary:
    valid_count: int
    palette_summary: str
    visual_summary: str
    composition_summary: str
    quality_summary: str
    readability_summary: str
    size_summary: str
    features: tuple[LocalVisualFeature, ...]


class LocalImageAnalyzer:
    def analyze_path(self, path: str | Path) -> LocalVisualFeature | None:
        data = Path(path).read_bytes()
        return self.analyze_bytes(data, Path(path).name)

    def analyze_bytes(self, content: bytes, filename: str = "uploaded.png") -> LocalVisualFeature | None:
        try:
            with Image.open(BytesIO(content)) as image:
                rgb = image.convert("RGB")
                width, height = rgb.size
                palette = _palette(rgb)
                brightness = ImageStat.Stat(rgb.convert("L")).mean[0]
                contrast = ImageStat.Stat(rgb.convert("L")).stddev[0]
                saturation = _average_saturation(rgb)
        except Exception:
            return None

        quality_tags = _quality_tags(brightness, contrast)
        return LocalVisualFeature(
            filename=filename,
            size=f"{width}x{height}",
            orientation=_orientation(width, height),
            palette=palette,
            palette_summary="、".join(palette),
            brightness_level=_brightness_level(brightness),
            saturation_level=_saturation_level(saturation),
            temperature=_temperature(palette),
            quality_tags=quality_tags,
            puzzle_readability=_puzzle_readability(quality_tags, len(palette), contrast),
            average_brightness=round(brightness, 1),
        )

    def summarize_bytes(self, contents: tuple[bytes, ...]) -> LocalVisualSummary:
        features = tuple(
            feature for feature in (self.analyze_bytes(content, f"image-{index}") for index, content in enumerate(contents, 1)) if feature
        )
        return self.summarize_features(features)

    def summarize_features(self, features: tuple[LocalVisualFeature, ...]) -> LocalVisualSummary:
        if not features:
            return LocalVisualSummary(
                valid_count=0,
                palette_summary="图片文件已保存，但本地解析器无法读取主色",
                visual_summary="未能读取图片像素信息",
                composition_summary="需要人工确认主体位置和画面层次",
                quality_summary="无法判断明暗、饱和度和拼图可读性",
                readability_summary="需要人工确认主体边界和拼图层次",
                size_summary="无可读尺寸",
                features=(),
            )
        palette = _unique(color for feature in features for color in feature.palette)
        orientations = _unique(feature.orientation for feature in features)
        temperatures = _unique(feature.temperature for feature in features)
        brightness = _unique(feature.brightness_level for feature in features)
        saturation = _unique(feature.saturation_level for feature in features)
        quality = _unique(tag for feature in features for tag in feature.quality_tags) or ("未发现明显本地质量风险",)
        readability = _unique(feature.puzzle_readability for feature in features)
        sizes = _unique(feature.size for feature in features)
        return LocalVisualSummary(
            valid_count=len(features),
            palette_summary=f"本地视觉解析主色为{'、'.join(palette)}",
            visual_summary=f"{'、'.join(temperatures)}；{'、'.join(brightness)}；{'、'.join(saturation)}",
            composition_summary=f"{'、'.join(orientations)}，建议保留主体清晰边界和前中后景层次",
            quality_summary="、".join(quality),
            readability_summary="；".join(readability),
            size_summary="、".join(sizes),
            features=features,
        )


def _palette(image: Image.Image) -> tuple[str, ...]:
    sample = image.resize((32, 32))
    buckets = Counter()
    for r, g, b in sample.getdata():
        buckets[_color_name(r, g, b)] += 1
    return tuple(name for name, _ in buckets.most_common(3))


def _orientation(width: int, height: int) -> str:
    if width > height * 1.15:
        return "横向构图"
    if height > width * 1.15:
        return "竖向构图"
    return "方形构图"


def _average_saturation(image: Image.Image) -> float:
    sample = image.resize((24, 24))
    values = []
    for r, g, b in sample.getdata():
        _, saturation, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        values.append(saturation)
    return sum(values) / max(len(values), 1)


def _brightness_level(value: float) -> str:
    if value >= 185:
        return "明亮"
    if value < 95:
        return "偏暗"
    return "中等明度"


def _saturation_level(value: float) -> str:
    if value >= 0.55:
        return "高饱和"
    if value < 0.22:
        return "低饱和"
    return "中饱和"


def _temperature(palette: tuple[str, ...]) -> str:
    warm = sum(1 for color in palette if color in {"暖红", "暖黄色", "粉紫色", "暖米白"})
    cool = sum(1 for color in palette if color in {"清透蓝", "自然绿色", "深青色"})
    if warm > cool:
        return "暖色"
    if cool > warm:
        return "冷色"
    return "中性色"


def _quality_tags(brightness: float, contrast: float) -> tuple[str, ...]:
    tags = []
    if brightness < 70:
        tags.append("过暗风险")
    if brightness > 235:
        tags.append("过亮风险")
    if contrast < 18:
        tags.append("低对比/纯色风险")
    return tuple(tags)


def _puzzle_readability(quality_tags: tuple[str, ...], palette_count: int, contrast: float) -> str:
    if "低对比/纯色风险" in quality_tags or palette_count <= 1:
        return "拼图友好度偏低：画面层次不足，建议增加主体边界、材质纹理和前中后景"
    if contrast >= 45 and palette_count >= 2:
        return "拼图友好度较高：色块和明暗层次有助于切片识别"
    return "拼图友好度中等：建议强化主体边界和局部细节"


def _color_name(r: int, g: int, b: int) -> str:
    if max(r, g, b) < 70:
        return "深色"
    if min(r, g, b) > 220:
        return "浅白"
    if r >= g + 45 and r >= b + 45:
        return "暖红"
    if g >= r + 35 and g >= b + 20:
        return "自然绿色"
    if b >= r + 30 and b >= g + 20:
        return "清透蓝"
    if g >= 120 and b >= 130 and r < 90:
        return "深青色"
    if r >= 180 and g >= 150 and b < 130:
        return "暖黄色"
    if r >= 150 and b >= 140 and g < 140:
        return "粉紫色"
    if r >= 180 and g >= 160 and b >= 130:
        return "暖米白"
    return "综合色"


def _unique(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))
