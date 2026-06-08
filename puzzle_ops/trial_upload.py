from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
import uuid

from PIL import Image, ImageStat

from puzzle_ops.models import DemandRow


class TrialImageUploadService:
    def __init__(self, upload_dir: Path | str):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def parse(self, row: DemandRow, files: list[dict[str, object]], mode: str) -> tuple[DemandRow, tuple[dict[str, str], ...]]:
        saved = tuple(self._save(file) for file in files if file.get("filename"))
        if not saved:
            return row.edited(remark=(row.remark + "；" if row.remark else "") + "未选择图片，无法解析。"), ()

        names = tuple(item["filename"] for item in saved)
        subject = _subject_from_names(names) or row.subject
        visual = _visual_summary(tuple(item["content"] for item in saved))
        if mode == "derive":
            image_name = f"{names[0]} + 衍生参考图1 + 衍生参考图2"
            remark = f"本地图片解析完成：已按好图衍生模式生成2张相似参考图占位；{visual['remark']}。"
        else:
            image_name = " + ".join(names[:3])
            remark = f"本地图片解析完成：已读取{len(saved)}张参考图；{visual['remark']}。"
        parsed = row.edited(
            image_name=image_name,
            subject=subject,
            subject_description=f"主体：{subject}；色彩：{visual['colors']}；构图：{visual['composition']}。",
            remark=(row.remark + "；" if row.remark else "") + remark,
        )
        return parsed, saved

    def _save(self, file: dict[str, object]) -> dict[str, str]:
        filename = _safe_filename(str(file["filename"]))
        suffix = Path(filename).suffix.lower() or ".png"
        saved_name = f"{uuid.uuid4().hex}{suffix}"
        path = self.upload_dir / saved_name
        path.write_bytes(bytes(file.get("content", b"")))
        return {
            "filename": filename,
            "url": f"/uploads/{saved_name}",
            "path": str(path),
            "content_type": str(file.get("content_type", "application/octet-stream")),
            "content": bytes(file.get("content", b"")),
        }


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "uploaded.png"
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", name)


def _subject_from_names(names: tuple[str, ...]) -> str:
    text = " ".join(names).lower()
    mapping = {
        "cat": "猫咪",
        "koi": "猫咪鲤鱼",
        "flower": "花卉",
        "lily": "铃兰花",
        "lavender": "薰衣草",
        "house": "房屋建筑",
        "travel": "旅行踏青",
    }
    for key, value in mapping.items():
        if key in text:
            return value
    return ""


def _visual_summary(contents: tuple[bytes, ...]) -> dict[str, str]:
    features = [_image_feature(content) for content in contents]
    valid = [feature for feature in features if feature]
    if not valid:
        return {
            "colors": "图片文件已保存，但本地解析器无法读取主色",
            "composition": "需要人工确认主体位置和画面层次",
            "remark": "未能读取图片像素信息",
        }
    colors = "、".join(dict.fromkeys(feature["color"] for feature in valid))
    compositions = "、".join(dict.fromkeys(feature["composition"] for feature in valid))
    sizes = "、".join(feature["size"] for feature in valid)
    brightness = round(sum(float(feature["brightness"]) for feature in valid) / len(valid))
    lightness = "明亮" if brightness >= 170 else "偏暗" if brightness < 95 else "中等明度"
    return {
        "colors": f"本地视觉解析主色为{colors}，整体{lightness}",
        "composition": f"{compositions}，建议保留主体清晰边界和可拼层次",
        "remark": f"视觉解析尺寸{sizes}，平均亮度{brightness}",
    }


def _image_feature(content: bytes) -> dict[str, str] | None:
    try:
        with Image.open(BytesIO(content)) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            tiny = rgb.resize((1, 1))
            r, g, b = tiny.getpixel((0, 0))
            stat = ImageStat.Stat(rgb.convert("L"))
            brightness = stat.mean[0]
    except Exception:
        return None
    if width > height * 1.15:
        composition = "横向构图"
    elif height > width * 1.15:
        composition = "竖向构图"
    else:
        composition = "方形构图"
    return {
        "color": _color_name(r, g, b),
        "composition": composition,
        "size": f"{width}x{height}",
        "brightness": f"{brightness:.1f}",
    }


def _color_name(r: int, g: int, b: int) -> str:
    if max(r, g, b) < 70:
        return "深色"
    if min(r, g, b) > 210:
        return "浅白"
    if r >= g + 45 and r >= b + 45:
        return "暖红"
    if g >= r + 35 and g >= b + 35:
        return "自然绿色"
    if b >= r + 35 and b >= g + 35:
        return "清透蓝"
    if r >= 180 and g >= 150 and b < 120:
        return "暖黄色"
    if r >= 150 and b >= 140 and g < 130:
        return "粉紫色"
    return "综合色"
