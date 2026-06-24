from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import uuid

from puzzle_ops.models import DemandRow
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient, QwenVisionLLMClient, VisionLLMClientFactory
from puzzle_ops.visual_analysis import LocalImageAnalyzer


class TrialImageUploadService:
    def __init__(
        self,
        upload_dir: Path | str,
        vision_client: OpenAIVisionLLMClient | QwenVisionLLMClient | None = None,
        vision_config_error: MissingVisionLLMConfig | None = None,
    ):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.analyzer = LocalImageAnalyzer()
        self.vision_config_error = vision_config_error
        self.vision_client = vision_client
        if self.vision_client is None and self.vision_config_error is None:
            try:
                self.vision_client = VisionLLMClientFactory.create()
            except MissingVisionLLMConfig as exc:
                self.vision_config_error = exc

    def parse(self, row: DemandRow, files: list[dict[str, object]], mode: str) -> tuple[DemandRow, tuple[dict[str, str], ...]]:
        saved = tuple(self._save(file) for file in files if file.get("filename"))
        if not saved:
            return row.edited(remark=(row.remark + "；" if row.remark else "") + "未选择图片，无法解析。"), ()

        names = tuple(item["filename"] for item in saved)
        subject = _subject_from_names(names) or row.subject
        visual = self.analyzer.summarize_bytes(tuple(item["content"] for item in saved))
        semantic = self.vision_client.analyze(list(saved), row.country, row.js_category, visual) if self.vision_client else None
        if semantic and semantic.subject:
            subject = semantic.subject
        operation_tag = _trial_operation_tag(row.operation_tag, row.country, subject)
        semantic_remark = _semantic_remark(semantic) if semantic else _missing_semantic_remark(self.vision_config_error)
        if mode == "derive":
            image_name = f"{names[0]} + 衍生方向"
            remark = (
                "本地图片解析完成：衍生方向为保留参考图的"
                f"{visual.palette_summary}、{visual.composition_summary}，加强{row.country}市场文化元素；"
                f"质量提示：{visual.quality_summary}；拼图友好度：{visual.readability_summary}；"
                f"视觉解析尺寸{visual.size_summary}；{semantic_remark}；当前仅输出衍生方向，不生成新参考图。"
            )
        else:
            image_name = " + ".join(names[:3])
            remark = (
                f"本地图片解析完成：已读取{len(saved)}张参考图；视觉解析尺寸{visual.size_summary}；"
                f"明暗/饱和/冷暖：{visual.visual_summary}；质量提示：{visual.quality_summary}；"
                f"拼图友好度：{visual.readability_summary}；{semantic_remark}。"
            )
        parsed = row.edited(
            image_name=image_name,
            operation_tag=operation_tag,
            subject=subject,
            subject_description=_business_description(subject, row.country, visual, semantic),
            remark=(row.remark + "；" if row.remark else "") + remark,
            reference_image_url=str(saved[0]["url"]),
            reference_image_path=str(saved[0]["path"]),
            reference_image_content_type=str(saved[0]["content_type"]),
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


def _trial_operation_tag(current_tag: str, country: str, subject: str) -> str:
    cleaned = _compact_tag_subject(subject)
    suffix = date.today().strftime("%m%d")
    if current_tag.startswith("试新_"):
        return f"试新_{country}_{cleaned}{suffix}"
    return re.sub(r"\d{4}$", suffix, current_tag)


def _compact_tag_subject(subject: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", subject).strip()
    if not cleaned:
        return "待确认主体"
    if len(cleaned) <= 8:
        return cleaned
    priority = (
        "3D渲染动物拟人化",
        "传统浴袍美女",
        "日式火车店铺少女",
        "薰衣草风车",
        "鲜花手推车",
        "海滩野餐",
        "蕾丝桌旗",
        "宫廷礼服",
        "古典喷泉",
        "法式花园",
        "火车店铺少女",
        "游客塔楼",
        "多层塔楼",
        "寿司拼盘",
        "抹茶甜点",
        "日式店铺",
        "观景步道",
        "背包行人",
        "游客",
        "塔楼",
        "少女",
        "寿司",
        "抹茶",
    )
    hits = [word for word in priority if word in cleaned]
    if "游客" in hits and "塔楼" in hits:
        return "游客塔楼"
    flower_terms = ("鲜花", "花", "玫瑰", "百合", "雏菊")
    if any(term in cleaned for term in flower_terms) and "手推车" in cleaned:
        return "鲜花手推车"
    if "薰衣草" in cleaned and "风车" in cleaned:
        return "薰衣草风车"
    if "海滩" in cleaned and "野餐" in cleaned:
        return "海滩野餐"
    if "蕾丝" in cleaned and ("桌旗" in cleaned or "桌布" in cleaned):
        return "蕾丝桌旗"
    for word in hits:
        if len(word) <= 8:
            return word
    return cleaned[:8]


def _business_description(subject: str, country: str, visual, semantic) -> str:
    color = visual.palette_summary
    if semantic and semantic.style:
        color = f"{visual.palette_summary}，整体风格为{semantic.style}"
    scene = semantic.scene if semantic and semantic.scene else visual.composition_summary
    culture = "、".join(semantic.culture_elements) if semantic and semantic.culture_elements else f"{country}市场文化元素待确认"
    return f"主体内容：{subject}；色彩氛围：{color}；构图环境：{scene}，结合{culture}。"


def _semantic_remark(semantic) -> str:
    return f"视觉LLM：真实{semantic.provider}，置信度{semantic.confidence:.2f}，{semantic.raw_text}"


def _missing_semantic_description(error: MissingVisionLLMConfig | None) -> str:
    missing = "、".join(error.missing) if error else "QWEN_API_KEY"
    return f"语义主体：待真实视觉 LLM 解析；场景：待解析；文化元素：待解析；风格：待解析；语义风险：缺少配置 {missing}"


def _missing_semantic_remark(error: MissingVisionLLMConfig | None) -> str:
    missing = "、".join(error.missing) if error else "QWEN_API_KEY"
    return f"视觉LLM：未运行，缺少真实模型配置 {missing}；请配置后重新上传解析"
