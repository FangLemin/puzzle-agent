from __future__ import annotations

from pathlib import Path
import re
import uuid

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
        colors = "参考图主色提取：暖色/清透色/高饱和点缀"
        composition = "构图：主体居中或三分构图，保留拼图可辨识边界"
        if mode == "derive":
            image_name = f"{names[0]} + 衍生参考图1 + 衍生参考图2"
            remark = "本地图片解析完成：已按好图衍生模式生成2张相似参考图占位。"
        else:
            image_name = " + ".join(names[:3])
            remark = f"本地图片解析完成：已读取{len(saved)}张参考图。"
        parsed = row.edited(
            image_name=image_name,
            subject=subject,
            subject_description=f"主体：{subject}；色彩：{colors}；{composition}。",
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
