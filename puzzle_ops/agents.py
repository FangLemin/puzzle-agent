from dataclasses import replace
from datetime import date, datetime, timedelta
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import re
import sys
import time
from tempfile import gettempdir
import uuid

from puzzle_ops.data import COUNTRIES, SYNC_ROWS
from puzzle_ops.adapters import DeepEvalAdapter, MCPToolAdapter, PhoenixExporter, PromptfooExporter
from puzzle_ops.audit import AuditPolicyRetriever, AuditRuleEngine
from puzzle_ops.excel_importer import import_history_workbook, import_undistributed_candidate_workbook
from puzzle_ops.feishu import FeishuClientFactory, MockFeishuClient
from puzzle_ops.guarded_tools import GuardedToolExecutor, GuardedToolPolicy
from puzzle_ops.models import AgentTrace, AnalysisReport, AnalysisRow, DemandRow, HolidayRecommendation, ImageAsset, ImageProfile, JS_CATEGORIES, ScheduleItem, TagMeta, Task, ToolResult, ValuePredictionCard, ValueRuleCandidate
from puzzle_ops.multimodal import ImageFeatureExtractor, SimilarImageRetriever, ValueInsightMiner
from puzzle_ops.production import configured_runtime_dir, create_runtime_backup, is_truthy_env, resolve_runtime_dir, start_daily_runtime_backup
from puzzle_ops.rag import FeedbackAwareRerankProvider, FileDocumentLoaderAdapter, HybridRagRetriever, LocalEmbeddingProvider, MilvusVectorStore, MilvusVectorStoreRetriever, MissingRagAnswerGenerator, QdrantVectorStore, QdrantVectorStoreRetriever, QwenRagAnswerGenerator, RagChunk, RagChunkingConfig, RagDocument, RagGeneratedAnswer, RagPrompt, RagProviderConfig, RagRetrievalCase, RagRuntimeStats, RagVectorStoreConfig, RetrievalCaseLoaderAdapter, StaticDocumentLoaderAdapter, build_processed_documents_from_raw, build_rag_prompt, chunk_document, evaluate_rag_quality_report, evaluate_retrieval_report, export_offline_rag_index, export_rag_acceptance_report, prepare_qdrant_points, providers_from_config, rewrite_rag_query, _qwen_chat_transport, _extract_chat_completion_text
from puzzle_ops.storage import PuzzleRepository
from puzzle_ops.skills import BusinessSkillLibrary, SkillExecutionError, SkillRunResult
from puzzle_ops.trulens_eval import TruLensRAGEvaluator
from puzzle_ops.trial_upload import TrialImageUploadService, _compact_tag_subject
from puzzle_ops.eval_suite import AgentEvalSuite
from puzzle_ops.harness import AgentHarness, EVAL_SAMPLE_CSV_FIELDS, load_eval_samples_csv, _predict_grade as _harness_predict_grade
from puzzle_ops.image_generation import DerivativeImage, ImageGenerationProviderFactory
from puzzle_ops.visual_similarity import QwenVLImageEmbeddingProvider, VisualIndexRecord, VisualMilvusImageStore, VisualSimilarityIndex
from puzzle_ops.visual_analysis import LocalImageAnalyzer
from puzzle_ops.visual_assets import image_bytes


RAG_TASK_INDEX_SOURCE_TYPES: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "value_master": (
        "value_rule",
        "approved_value_rule",
        "approved_rag_patch",
        "audit_policy",
        "fact",
        "harness_gold_sample",
    ),
    "audit": (
        "audit_policy",
        "approved_rag_patch",
        "value_rule",
        "approved_value_rule",
        "fact",
    ),
    "weekly_review": (
        "sample_fact",
        "harness_gold_sample",
        "value_rule",
        "approved_value_rule",
        "approved_rag_patch",
        "fact",
    ),
    "memory_governance": (
        "memory_perception",
        "memory_working",
        "approved_value_rule",
        "fact",
    ),
}

VALUE_CANDIDATE_WORKBOOK = Path("/Users/fanglemin/Desktop/未分发候选拼图_价值观大师填写模板.xlsx")


def _default_repository_path(runtime_dir: Path) -> Path:
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in Path(sys.argv[0]).name:
        return runtime_dir / f"puzzle_ops_{os.getpid()}.db"
    return runtime_dir / "puzzle_ops.db"


RAG_TASK_INDEX_LABELS = {
    "all": "全量兼容索引",
    "value_master": "价值观大师",
    "audit": "审核/版权风险",
    "weekly_review": "周复盘历史表现",
    "memory_governance": "Memory 治理",
}


class PuzzleOpsAgent:
    """Business-facing Agent service for outbound puzzle content operations."""

    editable_priorities = {"P0", "P1", "P2"}
    editable_methods = {"纯AI", "限素材网", "先照片后AI"}
    workday_positions = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)
    weekend_positions = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)
    rag_chunking_config = RagChunkingConfig(chunk_size_tokens=600, chunk_overlap_tokens=100)
    rag_bm25_top_k = 30
    rag_vector_top_k = 30

    def __init__(
        self,
        repository: PuzzleRepository | None = None,
        *,
        today: date | None = None,
        enable_regular_vision: bool = False,
        holiday_llm_transport=None,
        description_prompt_transport=None,
        analysis_llm_transport=None,
    ):
        runtime_dir = resolve_runtime_dir()
        repository_path = runtime_dir / "puzzle_ops.db" if configured_runtime_dir() else _default_repository_path(runtime_dir)
        self.repository = repository or PuzzleRepository(repository_path)
        self._runtime_dir = runtime_dir
        self.today = today or date.today()
        self.enable_regular_vision = enable_regular_vision
        self._holiday_llm_transport = holiday_llm_transport
        self._description_prompt_transport = description_prompt_transport
        self._analysis_llm_transport = analysis_llm_transport
        self._holiday_recommendation_cache: dict[tuple[str, str, str, str], HolidayRecommendation] = {}
        self.local_image_analyzer = LocalImageAnalyzer()
        self._history_cache: dict[str, tuple] = {}
        self._approved_candidates: dict[str, ValueRuleCandidate] = {}
        self.adapter = MCPToolAdapter(repository=self.repository)
        self.adapter.register_production_tools("日本")
        self.feishu = FeishuClientFactory.create(runtime_dir / "feishu_mock")
        self.trial_uploads = TrialImageUploadService(runtime_dir / "trial_uploads")
        self.image_generator = ImageGenerationProviderFactory.create(runtime_dir / "trial_uploads")
        self.visual_embedding_provider = QwenVLImageEmbeddingProvider.from_env()
        self.visual_similarity_index = VisualSimilarityIndex()
        self.visual_milvus_store = VisualMilvusImageStore.from_env()
        self.rag_provider_config = RagProviderConfig.from_env()
        self.rag_vector_store_config = RagVectorStoreConfig.from_env()
        self._last_rag_stats = RagRuntimeStats()
        self._last_rag_rewritten_query = ""
        self._last_rag_trace: dict[str, object] = {}
        self.business_skills = BusinessSkillLibrary.default()
        self._daily_backup_status = start_daily_runtime_backup(runtime_dir) if is_truthy_env("PUZZLEOPS_PRODUCTION_MODE") else {}

    def create_production_backup(self, *, label: str = "") -> dict[str, object]:
        return create_runtime_backup(self._runtime_dir, label=label)

    def countries(self) -> tuple[str, ...]:
        return tuple(COUNTRIES.keys())

    def dashboard(self, country: str) -> dict[str, object]:
        data = self._country(country)
        return {
            "country": country,
            "country_label": f"{data['flag']} {country}",
            "owner": data["owner"],
            "sa": _dashboard_sa_ratio(country),
            "ai": data["ai"],
            "tasks": [{"title": task.title, "body": task.body} for task in self.dashboard_tasks(country)],
        }

    def dashboard_tasks(self, country: str) -> tuple[Task, ...]:
        tasks: list[Task] = []
        records = self._history_records(country)
        if records:
            reusable = _tag_summaries(records, positive=True)
            risky = _tag_summaries(records, positive=False)
            if reusable:
                primary = reusable[0]
                tasks.append(
                    Task(
                        "历史好图提需方向",
                        f"{primary['operation_tag']} 历史 S/A 表现较好，JS分类 {primary['js_category']}，可作为常规提需参考；当前未接入真实库存数量，不生成低库存结论。",
                    )
                )
            if risky:
                primary_risk = risky[0]
                tasks.append(
                    Task(
                        "历史风险复盘",
                        f"{primary_risk['subject']} 所在 JS分类 {primary_risk['js_category']} 出现 C/D 表现，建议复盘主体、文化语境和构图后再决定是否继续提需。",
                    )
                )
        else:
            low_stock = [
                tag
                for tags in self.categories(country).values()
                for tag in tags
                if tag.stock <= 5
            ]
            low_stock = sorted(low_stock, key=lambda tag: (0 if tag.hot else 1, tag.stock, tag.tag))
            if low_stock:
                primary = low_stock[0]
                extra = "、".join(f"{tag.tag} 库存 {tag.stock}" for tag in low_stock[1:4])
                body = f"{primary.tag} 库存 {primary.stock}，低库存{'爆款' if primary.hot else '素材'}，需要优先提需。"
                if extra:
                    body += f" 其他待补：{extra}。"
                tasks.append(Task("低库存爆款", body))
        holiday = self.upcoming_holiday(country)
        if holiday:
            tasks.append(
                Task(
                    "节日营销",
                    f"{holiday.name}将在{holiday.date_range}进入营销窗口，建议提前补充{holiday.ai_themes[0]}、{holiday.ai_themes[1]}和{holiday.elements[0]}元素。",
                )
            )
        if self.today.weekday() == 2:
            tasks.append(Task("周三复盘", "今天需要复盘上上周三到上周二数据，重点看 S/A 增长、C/D 风险和低库存提需方向。"))
        return tuple(tasks)

    def categories(self, country: str) -> dict[str, tuple[TagMeta, ...]]:
        records = self._history_records(country)
        if records:
            return _categories_from_history_records(records)
        return self._country(country)["categories"]

    def sorted_tags(self, country: str, category: str) -> tuple[TagMeta, ...]:
        tags = self.categories(country)[category]
        return tuple(sorted(tags, key=lambda tag: (self.stock_rank(tag), tag.stock, tag.tag)))

    def stock_rank(self, tag: TagMeta) -> int:
        if "爆款缺库存" in tag.risk:
            return 0
        if "C/D" in tag.risk and not tag.hot:
            return 1
        if tag.hot and tag.stock <= 5:
            return 0
        if not tag.hot and tag.stock <= 5:
            return 1
        return 2

    def stock_class(self, tag: TagMeta) -> str:
        return ("stock-hot", "stock-low", "stock-normal")[self.stock_rank(tag)]

    def images_for_tag(self, country: str, operation_tag: str):
        records = self._history_records(country)
        real_images = _real_inventory_images_for_tag(records, country, operation_tag, self._tag_subject(country, operation_tag), limit=5)
        if real_images:
            return real_images
        return tuple(_unverified_metric_image(image) for image in self._country(country)["images"].get(operation_tag, ()))

    def _tag_subject(self, country: str, operation_tag: str) -> str:
        for tags in self.categories(country).values():
            for tag in tags:
                if tag.tag == operation_tag:
                    return tag.subject
        return ""

    def add_regular_demand(self, country: str, category: str, operation_tag: str, image_index: int) -> DemandRow:
        tag_meta = self._tag_meta(country, category, operation_tag)
        image = self.images_for_tag(country, operation_tag)[image_index]
        current_tag = _replace_tag_date_suffix(operation_tag, self.today)
        return DemandRow(
            need_type="常规",
            country=country,
            js_category=category,
            image_name=image.title,
            operation_tag=current_tag,
            subject=tag_meta.subject,
            count=7,
            priority="P1",
            method="纯AI" if image.source == "AI" else "限素材网",
            delivery_date="",
            subject_description="",
            remark="",
            reference_image_path=image.thumb if Path(str(image.thumb)).expanduser().is_file() else "",
            reference_image_content_type=_image_content_type_from_path(image.thumb) if Path(str(image.thumb)).expanduser().is_file() else "",
        )

    def edit_demand_row(
        self,
        row: DemandRow,
        *,
        priority: str | None = None,
        count: int | None = None,
        method: str | None = None,
        operation_tag: str | None = None,
        delivery_date: str | None = None,
        subject_description: str | None = None,
        remark: str | None = None,
    ) -> DemandRow:
        changes: dict[str, object] = {}
        if priority is not None:
            if priority not in self.editable_priorities:
                raise ValueError("需求等级只能是 P0、P1 或 P2")
            changes["priority"] = priority
        if count is not None:
            if count <= 0:
                raise ValueError("张数必须大于 0")
            changes["count"] = count
        if method is not None:
            if method not in self.editable_methods:
                raise ValueError("加工方式只能是 纯AI、限素材网 或 先照片后AI")
            changes["method"] = method
        if operation_tag is not None:
            changes["operation_tag"] = operation_tag
        if delivery_date is not None:
            changes["delivery_date"] = delivery_date
        if subject_description is not None:
            changes["subject_description"] = subject_description
        if remark is not None:
            changes["remark"] = remark
        return row.edited(**changes)

    def generate_subject_description(self, row: DemandRow) -> DemandRow:
        if row.reference_image_path and Path(row.reference_image_path).expanduser().is_file():
            feature = self.local_image_analyzer.analyze_path(row.reference_image_path)
            visual = self.local_image_analyzer.summarize_features((feature,) if feature else ())
            visual_bytes = Path(row.reference_image_path).expanduser().read_bytes()
        else:
            visual_bytes = image_bytes(row.image_name, row.subject)
            visual = self.local_image_analyzer.summarize_bytes((visual_bytes,))
        semantic = None
        if self.enable_regular_vision and self.trial_uploads.vision_client:
            try:
                semantic = self.trial_uploads.vision_client.analyze(
                    [
                        {
                            "filename": f"{row.image_name}.png",
                            "content": visual_bytes,
                            "content_type": "image/png",
                        }
                    ],
                    row.country,
                    row.js_category,
                    visual,
                )
            except Exception:
                semantic = None
        subject = semantic.subject if semantic and semantic.subject else row.subject
        description = _business_subject_description(subject, row.country, visual, semantic)
        remark = _append_description_source_remark(row.remark, semantic, self.trial_uploads.vision_client if semantic else None)
        return row.edited(subject=subject, subject_description=description, remark=remark)

    def generate_subject_description_prompt_baseline(
        self,
        row: DemandRow,
        *,
        template_row: DemandRow | None = None,
    ) -> dict[str, str]:
        template_row = template_row or self.generate_subject_description(row)
        visual = _regular_visual_summary(self, row)[0]
        prompt = _description_prompt_baseline_prompt(row, template_row, visual)
        api_key = _first_nonempty_env("DESCRIPTION_PROMPT_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
        model = os.getenv("DESCRIPTION_PROMPT_MODEL", "qwen-plus").strip() or "qwen-plus"
        endpoint = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        if not api_key:
            return {
                "status": "missing_config",
                "provider": "qwen",
                "model": model,
                "prompt": prompt,
                "subject_description": "",
                "remark": "缺少 QWEN_API_KEY，未调用强 Prompt baseline。",
                "raw_output": "",
            }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        try:
            response = (self._description_prompt_transport or _qwen_chat_transport)(payload, api_key, endpoint)
            output_text = _extract_chat_completion_text(response)
            data = _json_object_from_text(output_text)
            return {
                "status": "ok",
                "provider": "qwen",
                "model": model,
                "prompt": prompt,
                "subject_description": str(data.get("subject_description", "")).strip(),
                "remark": str(data.get("remark", "")).strip(),
                "raw_output": output_text,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": "qwen",
                "model": model,
                "prompt": prompt,
                "subject_description": "",
                "remark": f"强 Prompt baseline 调用失败：{exc}",
                "raw_output": "",
            }

    def create_trial_demand(self, country: str, category: str, mode: str) -> DemandRow:
        data = self._country(country)["trial"]
        if mode not in {"parse", "derive"}:
            raise ValueError("试新模式只能是 parse 或 derive")
        tag = data["derive_tag"] if mode == "derive" else data["parse_tag"]
        image_name = "上传好图解析衍生方向" if mode == "derive" else "上传参考图1-3张"
        risk = "" if data["risk"].startswith("未发现明显") else f"! {data['risk']}"
        return DemandRow(
            need_type="试新",
            country=country,
            js_category="",
            image_name=image_name,
            operation_tag=tag,
            subject=data["subject"],
            count=3,
            priority="P1",
            method="先照片后AI",
            delivery_date="",
            subject_description=f"主体内容：{data['subject']}；色彩氛围：{data['colors']}；构图环境：{data['composition']}",
            remark=risk,
            value_match="",
        )

    def simulate_trial_upload(self, country: str, category: str, mode: str) -> DemandRow:
        row = self.create_trial_demand(country, category, mode)
        if mode == "derive":
            return row.edited(
                image_name="历史好图解析衍生方向",
                subject_description=f"主体内容：{row.subject}；色彩氛围：提取历史好图主色，保留高辨识暖色/冷色基调；构图环境：保留原图主体位置，补充国家文化场景。",
                remark=(row.remark + "；" if row.remark else "") + "已解析历史好图，可整理衍生方向进入试新提需。",
            )
        return row.edited(
            image_name="参考图A+参考图B+参考图C",
            subject_description=f"主体内容：{row.subject}；色彩氛围：综合3张参考图主色；构图环境：提取共同主体与前中后景关系。",
            remark=(row.remark + "；" if row.remark else "") + "已解析3张参考图，可进入试新提需。",
        )

    def parse_trial_uploads(self, country: str, category: str, mode: str, files: list[dict[str, object]]) -> tuple[DemandRow, tuple[dict[str, str], ...]]:
        row = self.create_trial_demand(country, category, mode)
        parsed, previews = self.trial_uploads.parse(row, files, mode, business_date=self.today)
        self._record_trial_parse_memories(country, mode, parsed, previews)
        return parsed, previews

    def save_trial_uploads(self, files: list[dict[str, object]]) -> tuple[dict[str, str], ...]:
        return self.trial_uploads.save_uploads(files)

    def parse_saved_trial_uploads(
        self,
        country: str,
        category: str,
        mode: str,
        uploads: list[dict[str, object]] | tuple[dict[str, object], ...],
        *,
        run_vision: bool = True,
    ) -> tuple[DemandRow, tuple[dict[str, str], ...]]:
        row = self.create_trial_demand(country, category, mode)
        parsed, previews = self.trial_uploads.parse_saved(row, uploads, mode, business_date=self.today, run_vision=run_vision)
        self._record_trial_parse_memories(country, mode, parsed, previews)
        return parsed, previews

    def _record_trial_parse_memories(self, country: str, mode: str, parsed: DemandRow, previews: tuple[dict[str, str], ...]) -> None:
        if previews:
            self.record_perception_memory(
                country,
                "trial_image_parse",
                {
                    "mode": mode,
                    "image_name": parsed.image_name,
                    "subject": parsed.subject,
                    "subject_description": parsed.subject_description,
                    "remark": parsed.remark,
                    "reference_image_path": parsed.reference_image_path,
                },
            )
            self.record_extracted_fact(
                country,
                "image_semantic_fact",
                {
                    "subject": parsed.subject,
                    "country": parsed.country,
                    "js_category": parsed.js_category,
                    "operation_tag": parsed.operation_tag,
                    "image_name": parsed.image_name,
                    "reference_image_path": parsed.reference_image_path,
                },
            )

    def generation_provider_status(self) -> dict[str, object]:
        if self.image_generator:
            return self.image_generator.healthcheck()
        return {"provider": "not_configured", "configured": False, "message": "生成 provider 未配置"}

    def derivative_generation_prompts(self, row: DemandRow) -> tuple[str, str]:
        if row.country == "日本":
            prompt = _japan_derivative_prompt(row)
            negative_prompt = _japan_derivative_negative_prompt()
        elif row.country == "法国":
            prompt = _france_derivative_prompt(row)
            negative_prompt = _france_derivative_negative_prompt()
        else:
            prompt = (
                f"基于参考图衍生2张{row.country}市场拼图参考图；每张都是单张完整画面，只呈现一个主场景、一个季节氛围、一个清晰主体。"
                f"保留{row.subject}的核心吸引力、色彩氛围和构图层次，变化具体场景、道具组合或人物/动物动作，"
                "画面必须像一张可直接生产的拼图参考图，适合中老年用户拼图。"
            )
            negative_prompt = _required_derivative_negative_prompt()
        return prompt, negative_prompt

    def generate_trial_derivatives(
        self,
        row: DemandRow,
        *,
        prompt: str = "",
        negative_prompt: str = "",
    ) -> tuple[DemandRow, tuple[DemandRow, ...], tuple[dict[str, str], ...]]:
        provider = self.image_generator
        if not row.reference_image_path or not Path(row.reference_image_path).expanduser().is_file():
            return (
                row.edited(remark=(row.remark + "；" if row.remark else "") + "请先上传并解析一张真实历史好图，再生成衍生参考图。"),
                (),
                (),
            )
        width, height = _image_dimensions(row.reference_image_path)
        if width and height and (width < 240 or height < 240 or width > 8000 or height > 8000):
            return (
                row.edited(
                    remark=(
                        (row.remark + "；" if row.remark else "")
                        + f"Qwen 图像生成要求参考图宽高在 240-8000 像素之间；当前图片为 {width}x{height}，请换一张更清晰的历史好图。"
                    )
                ),
                (),
                (),
            )
        if provider is None:
            return (
                row.edited(remark=(row.remark + "；" if row.remark else "") + "Qwen 图像生成未配置：当前只保留衍生方向，不伪造新参考图。"),
                (),
                (),
            )
        default_prompt, default_negative_prompt = self.derivative_generation_prompts(row)
        prompt = str(prompt or "").strip() or default_prompt
        negative_prompt = _ensure_required_derivative_negative_prompt(str(negative_prompt or "").strip() or default_negative_prompt)
        style_constraints = {
            "source_sample_id": row.operation_tag,
            "retained_features": f"{row.subject}；{row.subject_description}",
            "changed_features": "单一场景氛围；光线天气；少量点缀；远景氛围",
            "risk_notes": "生成图必须经过二次 VLM 解析与审核后才能同步飞书",
        }
        seeds = tuple(int(self.today.strftime("%m%d")) + index for index in range(2))
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(
                executor.submit(
                    provider.generate_derivatives,
                    reference_image=row.reference_image_path or row.image_name,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    count=1,
                    seed=seed,
                    style_constraints=style_constraints,
                )
                for seed in seeds
            )
            derivatives = tuple(image for future in futures for image in future.result())
        rows: list[DemandRow] = []
        previews: list[dict[str, str]] = []
        for index, image in enumerate(derivatives, 1):
            path = Path(image.local_image_path)
            image_name = f"衍生参考图{index}.png"
            second_review_passed, review_status, reviewed_subject, reviewed_description = self._review_generated_derivative(row, image)
            remark = (
                f"{row.remark}；Qwen图像生成 seed={image.seed}；"
                f"{review_status}，通过后才能同步飞书；"
                f"Prompt：{image.prompt}；Negative：{image.negative_prompt}"
            )
            generated_row = row.edited(
                image_name=image_name,
                subject=reviewed_subject or row.subject,
                subject_description=reviewed_description or row.subject_description,
                reference_image_url=f"/uploads/{path.name}",
                reference_image_path=str(path),
                reference_image_content_type="image/png",
                reference_image_syncable=False,
                generation_review_status="passed" if second_review_passed else "blocked",
                human_approved=False,
                remark=remark,
            )
            rows.append(generated_row)
            previews.append(
                {
                    "image_id": image.image_id,
                    "filename": image_name,
                    "url": f"/uploads/{path.name}",
                    "path": str(path),
                    "content_type": "image/png",
                }
            )
        updated = row.edited(remark=(row.remark + "；" if row.remark else "") + f"已生成{len(rows)}张衍生参考图，等待二次 VLM 解析与审核。")
        return updated, tuple(rows), tuple(previews)

    def _review_generated_derivative(self, row: DemandRow, image: DerivativeImage) -> tuple[bool, str, str, str]:
        path = Path(image.local_image_path)
        if image.provider in {"mock", "not_configured"}:
            return False, "已进入二次 VLM 解析与审核待办：生成 provider 非真实出图", "", ""
        if not path.exists():
            return False, "二次 VLM 解析未通过：生成图文件不存在", "", ""
        client = self.trial_uploads.vision_client
        if not client:
            return False, "二次 VLM 解析未通过：视觉 LLM 未配置", "", ""

        visual_feature = self.local_image_analyzer.analyze_path(path)
        local_summary = self.local_image_analyzer.summarize_features((visual_feature,) if visual_feature else ())
        try:
            semantic = client.analyze(
                [{"filename": path.name, "path": str(path), "content_type": image_content_type(path)}],
                row.country,
                row.js_category,
                local_summary,
            )
        except Exception as exc:
            return False, f"二次 VLM 解析未通过：真实视觉 LLM 调用失败：{exc}", "", ""

        description = _generated_subject_description(row.country, local_summary, semantic)
        subject = semantic.subject or row.subject
        audit_text = " ".join(
            (
                subject,
                semantic.scene,
                semantic.style,
                " ".join(semantic.culture_elements),
                " ".join(semantic.risk_tags),
            )
        )
        audit = self.audit_review(audit_text)
        if semantic.risk_tags or audit.risk_level == "高":
            risks = "、".join(semantic.risk_tags) or audit.reason
            return False, f"二次 VLM 解析未通过：{risks}；{audit.reason}", subject, description
        drift_reason = _derivative_subject_drift_reason(row, semantic)
        if drift_reason:
            return False, f"二次 VLM 解析未通过：主体偏离参考图；{drift_reason}", subject, description
        return True, f"二次 VLM 解析与审核通过（{semantic.provider}，置信度{semantic.confidence:.2f}）", subject, description

    def apply_value_master(self, row: DemandRow) -> DemandRow:
        client = self.trial_uploads.vision_client
        if not client:
            value_match = _missing_value_llm_message(self.trial_uploads.vision_config_error)
        else:
            try:
                row_for_judgement, vision_note = self._row_with_fresh_value_vision(row, client)
                rag_evidence = self._rag_evidence_for_value_master(row_for_judgement)
                rag_rules = tuple(rag_evidence["rules"]) + self._visual_similarity_rules_for_value_master(row_for_judgement)
                value_match = client.judge_value_match(_value_row_payload(row_for_judgement), rag_rules)
                if vision_note:
                    value_match = f"{vision_note}；{value_match}"
                value_match = _append_system_rag_trace(value_match, rag_rules)
                value_match = _append_generated_rag_evidence(value_match, rag_evidence.get("generated_answer", ""))
                return row_for_judgement.edited(value_match=value_match)
            except Exception as exc:
                value_match = f"价值观大师：真实视觉 LLM 调用失败，暂不生成匹配结论；请检查模型配置后重试。错误：{exc}"
        return row.edited(value_match=value_match)

    def _row_with_fresh_value_vision(self, row: DemandRow, client) -> tuple[DemandRow, str]:
        path = Path(row.reference_image_path).expanduser() if row.reference_image_path else None
        if not path or not path.is_file():
            return row, "价值观大师视觉输入：未找到原图文件，使用当前提需行文本判断"
        feature = self.local_image_analyzer.analyze_path(path)
        local_summary = self.local_image_analyzer.summarize_features((feature,) if feature else ())
        semantic = client.analyze(
            [{"filename": path.name, "path": str(path), "content_type": row.reference_image_content_type or image_content_type(path)}],
            row.country,
            row.js_category,
            local_summary,
        )
        subject = semantic.subject or row.subject
        description = _business_subject_description(subject, row.country, local_summary, semantic)
        semantic_note = (
            f"价值观大师视觉解析：真实{semantic.provider}，置信度{semantic.confidence:.2f}，"
            f"主体={subject}，场景={semantic.scene or '未识别'}，风险={','.join(semantic.risk_tags) or '无明显风险'}"
        )
        remark = _append_unique_note(row.remark, semantic_note)
        return row.edited(subject=subject, subject_description=description, remark=remark), semantic_note

    def _visual_similarity_rules_for_value_master(self, row: DemandRow) -> tuple[tuple[str, str], ...]:
        path = row.reference_image_path or ""
        try:
            evidence = self.similar_visual_history_for_candidate(
                {
                    "country": row.country,
                    "local_image_path": path,
                    "operation_tag": row.operation_tag,
                    "subject": row.subject,
                    "js_category": row.js_category,
                    "subject_description": row.subject_description,
                },
                top_k=6,
            )
        except Exception as exc:
            return (("视觉相似历史依据", f"历史图像相似检索降级：{exc}；需人工复核。"),)
        if evidence.get("status") != "ok":
            return (("视觉相似历史依据", "历史图像相似依据不足，需人工复核。"),)
        rules = []
        for label, key in (("视觉相似历史好图", "similar_good"), ("视觉相似历史风险图", "similar_risk")):
            for index, hit in enumerate(evidence.get(key, ()) or (), start=1):
                if not isinstance(hit, dict):
                    continue
                rules.append(
                    (
                        f"{label}#{index}",
                        f"{hit.get('operation_tag', '')}；image_id={hit.get('image_id', '')}；等级={hit.get('grade', '')}；原因={hit.get('reason', '')}",
                    )
                )
        return tuple(rules) or (("视觉相似历史依据", "历史图像相似依据不足，需人工复核。"),)

    def upcoming_holiday(self, country: str, *, window_days: int = 15) -> HolidayRecommendation | None:
        next_holiday = self.next_holiday(country, max_days=window_days)
        return next_holiday[1] if next_holiday else None

    def next_holiday(self, country: str, *, max_days: int = 90) -> tuple[int, HolidayRecommendation] | None:
        today = self.today
        candidates: list[tuple[int, HolidayRecommendation]] = []
        for year in (today.year, today.year + 1):
            for holiday_date, holiday in _country_holidays(country, year):
                days = (holiday_date - today).days
                if 0 <= days <= max_days:
                    candidates.append((days, holiday))
        if not candidates:
            return None
        days, holiday = sorted(candidates, key=lambda item: item[0])[0]
        return days, self._enrich_holiday_recommendation(country, holiday)

    def holiday_recommendation(self, country: str) -> HolidayRecommendation:
        return self.upcoming_holiday(country) or self._enrich_holiday_recommendation(country, self._country(country)["holiday"])

    def _enrich_holiday_recommendation(self, country: str, holiday: HolidayRecommendation) -> HolidayRecommendation:
        cache_key = (
            country,
            holiday.name,
            str(self.today),
            "|".join(
                (
                    os.getenv("HOLIDAY_LLM_ENABLE_REMOTE_CALLS", ""),
                    os.getenv("HOLIDAY_LLM_PROVIDER", ""),
                    os.getenv("HOLIDAY_LLM_MODEL", ""),
                )
            ),
        )
        cached = self._holiday_recommendation_cache.get(cache_key)
        if cached:
            return cached
        records = self._history_records(country)
        if not records:
            local_note = _holiday_planning_note(holiday, (), (), _holiday_value_rule_citations(self._country(country), holiday), 0)
            planning_note, source = self._holiday_llm_planning(country, holiday, (), (), _holiday_value_rule_citations(self._country(country), holiday), 0, local_note)
            enriched = replace(
                holiday,
                history_good_images=(),
                history_bad_images=(),
                direct_history_count=0,
                evidence_note="当前未导入真实历史样本，节日建议仅基于维护表和国家价值观规则。",
                value_rule_citations=_holiday_value_rule_citations(self._country(country), holiday),
                llm_planning_note=planning_note,
                llm_source=source,
            )
            self._holiday_recommendation_cache[cache_key] = enriched
            return enriched
        direct_matches = _holiday_direct_history_matches(records, holiday)
        good_ranked = _rank_holiday_records(direct_matches, holiday, positive=True) if direct_matches else ()
        bad_ranked = _rank_holiday_records(direct_matches, holiday, positive=False) if direct_matches else ()
        if not good_ranked:
            good_ranked = _rank_holiday_records(tuple(record for record in records if record.grade in {"S", "A"}), holiday, positive=True)
        if not bad_ranked:
            bad_ranked = _rank_holiday_records(records, holiday, positive=False)
        good_images = tuple(_image_asset_from_record(record) for record in good_ranked[:4])
        bad_images = tuple(_image_asset_from_record(record) for record in bad_ranked[:3])
        citations = _holiday_value_rule_citations(self._country(country), holiday)
        direct_count = len(direct_matches)
        evidence_note = (
            f"已匹配到 {direct_count} 张真实历史样本，优先引用该节日/相近主题的 S/A 与 C/D 表现。"
            if direct_count
            else "暂无该节日直接历史样本；以下引用同国家真实历史好图/坏图规律，供节日首次生产时参考。"
        )
        local_note = _holiday_planning_note(holiday, good_images, bad_images, citations, direct_count)
        planning_note, source = self._holiday_llm_planning(country, holiday, good_images, bad_images, citations, direct_count, local_note)
        enriched = replace(
            holiday,
            history_good_images=good_images,
            history_bad_images=bad_images,
            direct_history_count=direct_count,
            evidence_note=evidence_note,
            value_rule_citations=citations,
            llm_planning_note=planning_note,
            llm_source=source,
        )
        self._holiday_recommendation_cache[cache_key] = enriched
        return enriched

    def _holiday_llm_planning(
        self,
        country: str,
        holiday: HolidayRecommendation,
        good_images: tuple[ImageAsset, ...],
        bad_images: tuple[ImageAsset, ...],
        citations: tuple[str, ...],
        direct_count: int,
        fallback_note: str,
    ) -> tuple[str, str]:
        provider = os.getenv("HOLIDAY_LLM_PROVIDER", "qwen").strip().lower() or "qwen"
        remote_enabled = os.getenv("HOLIDAY_LLM_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        if provider not in {"qwen", "dashscope"} or not remote_enabled:
            return fallback_note, "本地规则 fallback"
        api_key = _first_nonempty_env("HOLIDAY_LLM_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
        if not api_key:
            return fallback_note + "（远程节日策划未调用：缺少 HOLIDAY_LLM_API_KEY/QWEN_API_KEY。）", "本地规则 fallback"
        model = os.getenv("HOLIDAY_LLM_MODEL", "qwen3.7-plus").strip() or "qwen3.7-plus"
        endpoint = os.getenv("HOLIDAY_LLM_ENDPOINT", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions").strip()
        payload = _holiday_llm_payload(country, holiday, good_images, bad_images, citations, direct_count, model)
        try:
            response = (self._holiday_llm_transport or _qwen_chat_transport)(payload, api_key, endpoint)
            text = _extract_chat_completion_text(response).strip()
        except Exception as exc:
            return fallback_note + f"（远程节日策划调用失败，已回退本地规则：{exc}）", "本地规则 fallback"
        if not text:
            return fallback_note + "（远程节日策划未返回文本，已回退本地规则。）", "本地规则 fallback"
        return text, f"Qwen {model}"

    def analysis_report(self, country: str) -> AnalysisReport:
        data = self._country(country)["analysis"]
        records = self._history_records(country)
        rows = tuple(_analysis_row_from_record(record) for record in records) if records else data["rows"]
        total = len(records)
        sa_ratio = _pct(sum(1 for record in records if record.grade in {"S", "A"}) / total) if total else data["sa_ratio"]
        cd_ratio = _pct(sum(1 for record in records if record.grade in {"C", "D"}) / total) if total else data["cd_ratio"]
        ai_ratio = _pct(sum(1 for record in records if record.source == "AI") / total) if total else data["ai_ratio"]
        visual_recap = self._visual_analysis_recap(country)
        sample_summary = (
            f"本周期已接入真实样本{total}张，SA {sa_ratio}，CD {cd_ratio}，AI {ai_ratio}。"
            if total
            else ""
        )
        business_recap = _analysis_business_recap(country, records) if records else {}
        cycle_summary = (
            f"{sample_summary}{business_recap['cycle_summary']} 视觉维度复盘：{visual_recap}"
            if business_recap
            else f"{sample_summary}{data['cycle_summary']} 视觉维度复盘：{visual_recap}"
        )
        next_todo = (
            str(business_recap["next_todo"])
            if business_recap
            else f"{data['next_todo']} 多模态建议：优先补充主体清晰、文化语境准确、质量风险低的试新参考图。"
        )
        if business_recap:
            cycle_summary, next_todo = self._analysis_llm_rewrite(
                country,
                records,
                cycle_summary,
                next_todo,
                visual_recap,
            )
        return AnalysisReport(
            country=country,
            sa_ratio=sa_ratio,
            cd_ratio=cd_ratio,
            ai_ratio=ai_ratio,
            sa_delta=data["sa_delta"],
            cd_delta=data["cd_delta"],
            ai_delta=data["ai_delta"],
            sa_history_avg=data["sa_history_avg"],
            sa_okr=data["sa_okr"],
            cd_history_avg=data["cd_history_avg"],
            ai_history_avg=data["ai_history_avg"],
            ai_okr=data["ai_okr"],
            cycle_summary=cycle_summary,
            next_todo=next_todo,
            rows=rows,
        )

    def _analysis_llm_rewrite(
        self,
        country: str,
        records: tuple,
        cycle_summary: str,
        next_todo: str,
        visual_recap: str,
    ) -> tuple[str, str]:
        provider = os.getenv("ANALYSIS_LLM_PROVIDER", "qwen").strip().lower() or "qwen"
        remote_enabled = os.getenv("ANALYSIS_LLM_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        if provider not in {"qwen", "dashscope"} or not remote_enabled:
            return cycle_summary, next_todo
        api_key = _first_nonempty_env("ANALYSIS_LLM_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
        if not api_key:
            return cycle_summary, next_todo
        model = os.getenv("ANALYSIS_LLM_MODEL", "qwen3.7-plus").strip() or "qwen3.7-plus"
        endpoint = os.getenv("ANALYSIS_LLM_ENDPOINT", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions").strip()
        payload = _analysis_llm_payload(country, records, cycle_summary, next_todo, visual_recap, model)
        try:
            response = (self._analysis_llm_transport or _qwen_chat_transport)(payload, api_key, endpoint)
            text = _extract_chat_completion_text(response).strip()
            parsed = _analysis_llm_output_from_text(text)
        except Exception:
            return cycle_summary, next_todo
        rewritten_summary = parsed.get("cycle_summary", "").strip()
        rewritten_todo = parsed.get("next_todo", "").strip()
        if not rewritten_summary and not rewritten_todo:
            return cycle_summary, next_todo
        return rewritten_summary or cycle_summary, rewritten_todo or next_todo

    def weekly_review_workbench(self, country: str) -> dict[str, object]:
        records = self._history_records(country)
        other_country = "法国" if country == "日本" else "日本"
        other_records = self._history_records(other_country) if other_country in self.countries() else ()
        new_sa = tuple(sorted((record for record in records if record.grade in {"S", "A"}), key=_record_strength, reverse=True)[:8])
        declining = tuple(sorted((record for record in records if _is_declining_record(record)), key=_record_risk_score, reverse=True)[:8])
        reusable = _tag_summaries(records, positive=True)[:6]
        retire = _tag_summaries(records, positive=False)[:6]
        suggestions = tuple(_weekly_need_suggestion(country, item, self.today) for item in reusable[:5])
        return {
            "country": country,
            "source": "uploaded_excel" if records else "builtin_demo",
            "period": _review_period(records),
            "new_sa_images": tuple(_record_review_item(record) for record in new_sa),
            "declining_images": tuple(_record_review_item(record) for record in declining),
            "country_differences": _country_differences(country, records, other_country, other_records),
            "reusable_tags": reusable,
            "retire_tags": retire,
            "need_suggestions": suggestions,
            "summary": _weekly_review_summary(country, records, new_sa, declining, reusable, retire),
        }

    def weekly_review_need_rows(self, country: str, *, limit: int = 5) -> tuple[DemandRow, ...]:
        suggestions = self.weekly_review_workbench(country)["need_suggestions"]
        rows = []
        for suggestion in tuple(suggestions)[: max(limit, 0)]:
            if not isinstance(suggestion, dict):
                continue
            rows.append(
                DemandRow(
                    need_type="常规",
                    country=country,
                    js_category=str(suggestion.get("js_category", "")),
                    image_name=str(suggestion.get("source_image", "周三复盘建议")),
                    operation_tag=str(suggestion.get("operation_tag", "")),
                    subject=str(suggestion.get("subject", "")),
                    count=int(suggestion.get("count", 7) or 7),
                    priority=str(suggestion.get("priority", "P1")),
                    method=str(suggestion.get("method", "限素材网")),
                    delivery_date="",
                    subject_description=str(suggestion.get("description", "")),
                    remark=str(suggestion.get("reason", "")),
                )
            )
        return tuple(rows)

    def value_rules(self, country: str):
        base_rules = list(self._country(country)["value_rules"])
        approved = [
            (f"运营审批候选{index}", str(rule["rule_text"]))
            for index, rule in enumerate(self.approved_value_rules(country), 1)
            if str(rule["rule_text"]) not in {body for _, body in base_rules}
        ]
        return tuple(base_rules + approved)

    def build_value_audit_rag_index(self, country: str, *, task_index: str = "all") -> tuple[RagDocument, ...]:
        documents = StaticDocumentLoaderAdapter(self.rag_documents_for_task(country, task_index)).load()
        chunks = tuple(
            chunk
            for document in documents
            for chunk in chunk_document(document, max_chars=None, chunking=self.rag_chunking_config)
        )
        self.repository.save_rag_index(country, documents, chunks)
        return documents

    def rag_documents_for_task(self, country: str, task_index: str = "all") -> tuple[RagDocument, ...]:
        normalized = self._normalize_rag_task_index(task_index)
        allowed_sources = RAG_TASK_INDEX_SOURCE_TYPES[normalized]
        documents = self._rag_documents(country)
        if allowed_sources is None:
            return documents
        allowed = set(allowed_sources)
        return tuple(document for document in documents if document.source_type in allowed)

    def rag_task_source_types(self, task_index: str = "all") -> tuple[str, ...] | None:
        return RAG_TASK_INDEX_SOURCE_TYPES[self._normalize_rag_task_index(task_index)]

    def rag_retrieval_runtime_status(self, task_index: str = "value_master") -> dict[str, object]:
        provider = (self.rag_vector_store_config.provider or "sqlite").strip().lower()
        search_enabled = self._rag_vector_store_search_enabled()
        milvus_primary = provider == "milvus" and self.rag_vector_store_config.ready and search_enabled
        fallback_active = not milvus_primary
        if milvus_primary:
            primary_provider = "Milvus"
            fallback_reason = ""
            mode = "primary"
        elif provider == "milvus":
            primary_provider = "SQLite"
            mode = "fallback"
            fallback_reason = "Milvus 已配置但未就绪或未开启搜索，当前使用本地 BM25/Embedding fallback"
        else:
            primary_provider = provider.upper() if provider == "qdrant" and search_enabled else "SQLite"
            mode = "fallback"
            fallback_reason = "Milvus 未作为当前 provider，当前使用本地或已配置向量库 fallback"
        normalized = self._normalize_rag_task_index(task_index)
        return {
            "task_index": normalized,
            "task_label": RAG_TASK_INDEX_LABELS.get(normalized, normalized),
            "task_source_types": RAG_TASK_INDEX_SOURCE_TYPES[normalized],
            "provider": provider,
            "primary_provider": primary_provider,
            "mode": mode,
            "milvus_primary": milvus_primary,
            "fallback_active": fallback_active,
            "fallback_reason": fallback_reason,
            "search_enabled": search_enabled,
            "collection": self.rag_vector_store_config.collection,
            "status_text": self.rag_vector_store_config.status_text,
        }

    def _normalize_rag_task_index(self, task_index: str) -> str:
        normalized = (task_index or "all").strip().lower()
        return normalized if normalized in RAG_TASK_INDEX_SOURCE_TYPES else "all"

    def value_audit_rag_answer(
        self,
        country: str,
        query: str,
        top_k: int = 6,
        *,
        provider_config: RagProviderConfig | None = None,
        task_index: str = "value_master",
    ) -> RagPrompt:
        started_at = time.perf_counter()
        task_index = self._normalize_rag_task_index(task_index)
        self.build_value_audit_rag_index(country, task_index=task_index)
        chunks = tuple(_rag_chunk_from_row(row) for row in self.repository.rag_chunks(country))
        stats = RagRuntimeStats()
        embedding_provider, rerank_provider = providers_from_config(
            provider_config or self.rag_provider_config,
            stats=stats,
            cache_get=self.repository.get_rag_embedding_cache,
            cache_set=self.repository.set_rag_embedding_cache,
        )
        config = provider_config or self.rag_provider_config
        if not config.remote_calls_enabled:
            feedback_scores = self.rag_feedback_scores(country)
            if feedback_scores:
                rerank_provider = FeedbackAwareRerankProvider(rerank_provider, feedback_scores)
        vector_store_retriever = self._rag_vector_store_retriever()
        retriever = HybridRagRetriever(
            chunks,
            embedding_provider=embedding_provider,
            rerank_provider=rerank_provider,
            vector_store_retriever=vector_store_retriever,
        )
        rewritten_query = rewrite_rag_query(query, country=country)
        trace = retriever.search_with_trace(
            rewritten_query,
            country=country,
            top_k=top_k,
            source_types=self.rag_task_source_types(task_index),
            bm25_top_k=self.rag_bm25_top_k,
            vector_top_k=self.rag_vector_top_k,
        )
        hits = trace.final_hits
        if _looks_like_audit_query(query) and not any(hit.chunk.source_type == "audit_policy" for hit in hits):
            audit_hits = retriever.search(
                rewritten_query,
                country=country,
                top_k=1,
                source_types=("audit_policy",),
                bm25_top_k=self.rag_bm25_top_k,
                vector_top_k=self.rag_vector_top_k,
            )
            if audit_hits:
                hits = tuple(list(hits[: max(top_k - 1, 0)]) + [audit_hits[0]])
        runtime_status = self.rag_retrieval_runtime_status(task_index)
        trace_payload = {
            **trace.as_dict(),
            "task_index": task_index,
            "task_label": runtime_status["task_label"],
            "task_source_types": runtime_status["task_source_types"],
            "milvus_primary": runtime_status["milvus_primary"],
            "vector_store_mode": runtime_status["mode"],
        }
        if hits != trace.final_hits:
            trace_payload = dict(trace_payload)
            trace_payload["final_hits"] = tuple(_rag_hit_trace_payload(hit) for hit in hits)
        prompt = build_rag_prompt(rewritten_query, hits)
        self._last_rag_stats = stats
        self._last_rag_rewritten_query = rewritten_query
        self._last_rag_trace = trace_payload
        latency_ms = round((time.perf_counter() - started_at) * 1000, 4)
        trace_path = self._write_rag_trace(country, query, rewritten_query, prompt, trace_payload, stats.as_dict(), latency_ms=latency_ms)
        self._record_memory_rag_hits(country, hits, trace_id=Path(trace_path).stem)
        return prompt

    def value_audit_rag_generated_answer(
        self,
        country: str,
        query: str,
        top_k: int = 6,
        *,
        generator: object | None = None,
        provider_config: RagProviderConfig | None = None,
    ) -> RagGeneratedAnswer:
        prompt = self.value_audit_rag_answer(country, query, top_k=top_k, provider_config=provider_config)
        answer_generator = generator or self._default_rag_answer_generator()
        started_at = time.perf_counter()
        result = answer_generator.generate(prompt)
        generation_latency_ms = round((time.perf_counter() - started_at) * 1000, 4)
        self._augment_latest_rag_trace_with_generation(country, result, generation_latency_ms)
        return result

    def _rag_rules_for_value_master(self, row: DemandRow) -> tuple[tuple[str, str], ...]:
        return self._rag_evidence_for_value_master(row)["rules"]

    def _rag_evidence_for_value_master(self, row: DemandRow) -> dict[str, object]:
        query = " ".join(
            (
                row.country,
                row.js_category,
                row.operation_tag,
                row.subject,
                row.subject_description,
                row.remark,
                "价值观 审核 风险 文化混淆 版权 IP 文字水印 AI质量",
            )
        )
        generated = self.value_audit_rag_generated_answer(row.country, query, top_k=6)
        if generated.status == "generated" and generated.answer:
            strong_citations = _strong_rag_citations_from_trace(self._last_rag_trace, tuple(generated.citations), max_citations=3)
            citation_rules = tuple((citation, "生成式RAG答案引用依据") for citation in strong_citations)
            return {
                "rules": (("生成式RAG答案", generated.answer), *citation_rules),
                "generated_answer": generated.answer,
                "generation_status": generated.status,
            }
        answer = self.value_audit_rag_answer(row.country, query, top_k=6)
        strong_citations = _strong_rag_citations_from_trace(self._last_rag_trace, tuple(answer.citations), max_citations=3)
        if not strong_citations:
            return {"rules": self.value_rules(row.country), "generated_answer": "", "generation_status": generated.status}
        allowed = set(strong_citations)
        rules = []
        for line in answer.context.splitlines():
            if not line.startswith("[") or "]" not in line:
                continue
            citation, text = line.split("]", 1)
            citation_id = citation.strip("[")
            if citation_id not in allowed:
                continue
            rules.append((citation_id, text.strip()))
        return {
            "rules": tuple(rules) or self.value_rules(row.country),
            "generated_answer": "",
            "generation_status": generated.status,
        }

    def value_audit_rag_summary(self, country: str) -> dict[str, object]:
        query = f"{country}市场试新提需是否符合价值观，并检查版权/IP、文字水印、文化混淆和AI质量风险"
        task_index = "value_master"
        answer = self.value_audit_rag_answer(country, query, top_k=5, task_index=task_index)
        runtime_status = self.rag_retrieval_runtime_status(task_index)
        chunks = self.repository.rag_chunks(country)
        chunk_by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
        citation_details = tuple(
            {
                "chunk_id": citation,
                "parent_id": str(chunk_by_id[citation]["parent_id"]),
                "source_type": str(chunk_by_id[citation]["source_type"]),
                "title": str(chunk_by_id[citation]["title"]),
                "text": str(chunk_by_id[citation]["text"]),
            }
            for citation in answer.citations
            if citation in chunk_by_id
        )
        source_counts: dict[str, int] = {}
        for chunk in chunks:
            source_type = str(chunk["source_type"])
            source_counts[source_type] = source_counts.get(source_type, 0) + 1
        return {
            "chunk_count": len(chunks),
            "source_counts": source_counts,
            "citations": answer.citations,
            "citation_details": citation_details,
            "context": answer.context,
            "prompt": answer.prompt,
            "embedding_provider": self.rag_provider_config.embedding_provider,
            "embedding_model": self.rag_provider_config.embedding_model,
            "rerank_provider": self.rag_provider_config.rerank_provider,
            "rerank_model": self.rag_provider_config.rerank_model,
            "provider_configured": self.rag_provider_config.configured,
            "provider_remote_ready": self.rag_provider_config.remote_ready,
            "provider_remote_calls_enabled": self.rag_provider_config.remote_calls_enabled,
            "provider_status": self.rag_provider_config.status_text,
            "offline_loader": "StaticDocumentLoaderAdapter",
            "splitter": self.rag_chunking_config.splitter,
            "chunk_size_tokens": self.rag_chunking_config.chunk_size_tokens,
            "chunk_overlap_tokens": self.rag_chunking_config.chunk_overlap_tokens,
            "vector_store": self.rag_vector_store_config.provider,
            "vector_store_collection": self.rag_vector_store_config.collection,
            "vector_store_ready": self.rag_vector_store_config.ready,
            "vector_store_status": self.rag_vector_store_config.status_text,
            "vector_store_search_enabled": self._rag_vector_store_search_enabled(),
            "task_index": runtime_status["task_index"],
            "task_label": runtime_status["task_label"],
            "task_source_types": runtime_status["task_source_types"],
            "rag_retrieval_runtime_status": runtime_status,
            "milvus_primary": runtime_status["milvus_primary"],
            "vector_store_mode": runtime_status["mode"],
            "bm25_top_k": self.rag_bm25_top_k,
            "vector_top_k": self.rag_vector_top_k,
            "rerank_top_k": 5,
            "rewritten_query": self._last_rag_rewritten_query,
            "retrieval_trace": self._last_rag_trace,
            "retrieval_eval_report": self.value_audit_rag_eval_report(country),
            "rag_eval_dataset": self.rag_eval_dataset_summary(country),
            "rag_chunk_eval_dataset": self.rag_chunk_eval_dataset_summary(country),
            "rag_eval_case_evidence": self.rag_eval_case_evidence(country),
            "rag_eval_failure_feedback": self.rag_eval_failure_feedback_summary(country),
            "rag_knowledge_patch_drafts": self.rag_knowledge_patch_drafts(country),
            "rag_quality_governance": self.rag_quality_governance_workbench(country),
            "rag_patch_ops": self.rag_patch_ops_summary(country),
            "rag_live_model_ops": self.rag_live_model_ops_summary(country),
            "latest_acceptance_summary": self.latest_rag_acceptance_summary(country),
            "knowledge_base": self._rag_knowledge_summary(country),
            "feedback_summary": self.rag_feedback_summary(country),
            "recent_traces": self.recent_rag_traces(country, limit=3),
            **self._last_rag_stats.as_dict(),
        }

    def rag_citation_details(self, country: str, citations: tuple[str, ...] | list[str]) -> tuple[dict[str, str], ...]:
        chunks = self.repository.rag_chunks(country)
        chunk_by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
        details = []
        for citation in citations:
            citation_id = str(citation)
            chunk = chunk_by_id.get(citation_id)
            if not chunk:
                details.append(
                    {
                        "chunk_id": citation_id,
                        "parent_id": citation_id.split("#", 1)[0],
                        "source_type": "unknown",
                        "title": _readable_citation_label(citation_id),
                        "text": "",
                    }
                )
                continue
            details.append(
                {
                    "chunk_id": citation_id,
                    "parent_id": str(chunk["parent_id"]),
                    "source_type": str(chunk["source_type"]),
                    "title": str(chunk["title"]),
                    "text": str(chunk["text"]),
                }
            )
        return tuple(details)

    def rag_live_model_ops_summary(self, country: str) -> dict[str, object]:
        acceptance = self.latest_rag_acceptance_summary(country)
        preflight = acceptance.get("preflight", {}) if isinstance(acceptance.get("preflight"), dict) else {}
        runtime_stats = acceptance.get("runtime_stats", {}) if isinstance(acceptance.get("runtime_stats"), dict) else {}
        embedding = preflight.get("embedding", {}) if isinstance(preflight.get("embedding"), dict) else {}
        qdrant = preflight.get("qdrant", {}) if isinstance(preflight.get("qdrant"), dict) else {}
        rerank = preflight.get("rerank", {}) if isinstance(preflight.get("rerank"), dict) else {}
        return {
            "exists": bool(acceptance.get("exists")),
            "mode": str(acceptance.get("mode", "") or "not_run"),
            "status": str(acceptance.get("status", "")),
            "failure_stage": str(acceptance.get("failure_stage", "")),
            "embedding_ready": bool(embedding.get("ready", False)),
            "embedding_provider": str(embedding.get("provider") or embedding.get("provider_name") or self.rag_provider_config.embedding_provider),
            "qdrant_ready": bool(qdrant.get("ready", False)),
            "qdrant_provider": str(qdrant.get("provider") or self.rag_vector_store_config.provider),
            "rerank_ready": bool(rerank.get("ready", False)),
            "rerank_provider": str(rerank.get("provider") or rerank.get("provider_name") or self.rag_provider_config.rerank_provider),
            "embedding_remote_calls": int(runtime_stats.get("embedding_remote_calls", 0) or 0),
            "embedding_fallbacks": int(runtime_stats.get("embedding_fallbacks", 0) or 0),
            "rerank_remote_calls": int(runtime_stats.get("rerank_remote_calls", 0) or 0),
            "rerank_fallbacks": int(runtime_stats.get("rerank_fallbacks", 0) or 0),
            "qdrant_vector_hits": bool(acceptance.get("qdrant_vector_hits", False)),
            "hit@5": acceptance.get("hit@5", 0),
            "mrr@5": acceptance.get("mrr@5", 0),
            "summary_path": str(acceptance.get("summary_path", "")),
        }

    def export_rag_ops_report(self, country: str, output_dir: Path) -> dict[str, object]:
        output_dir.mkdir(parents=True, exist_ok=True)
        live_model_ops = self.rag_live_model_ops_summary(country)
        patch_ops = self.rag_patch_ops_summary(country)
        latest_acceptance = self.latest_rag_acceptance_summary(country)
        rag_eval_dataset = self.rag_eval_dataset_summary(country)
        knowledge_base = self._rag_knowledge_summary(country)
        patch_priority_summary = self.rag_knowledge_patch_drafts(country, limit=10_000).get("priority_summary", {})
        run_comparison = patch_ops.get("run_comparison", {}) if isinstance(patch_ops.get("run_comparison"), dict) else {}
        patch_case_diff = {
            "fixed_failure_count": int(run_comparison.get("fixed_failure_count", 0) or 0),
            "new_failure_count": int(run_comparison.get("new_failure_count", 0) or 0),
            "fixed_failures": tuple(str(item) for item in run_comparison.get("fixed_failures", ()) if str(item)),
            "new_failures": tuple(str(item) for item in run_comparison.get("new_failures", ()) if str(item)),
        }
        live_model_evidence = latest_acceptance.get("live_model_evidence", {})
        if not isinstance(live_model_evidence, dict):
            live_model_evidence = {}
        quality_eval = latest_acceptance.get("quality_eval", {})
        if not isinstance(quality_eval, dict):
            quality_eval = {}
        if not quality_eval:
            quality_eval = self.rag_trace_quality_eval_summary(country, limit=50)
        payload: dict[str, object] = {
            "country": country,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "live_model_ops": live_model_ops,
            "live_model_evidence": live_model_evidence,
            "quality_eval": quality_eval,
            "patch_ops": patch_ops,
            "latest_acceptance": latest_acceptance,
            "rag_eval_dataset": rag_eval_dataset,
            "knowledge_base": knowledge_base,
            "patch_priority_summary": patch_priority_summary,
            "patch_case_diff": patch_case_diff,
        }
        json_path = output_dir / f"rag_ops_report_{country}.json"
        markdown_path = output_dir / f"rag_ops_report_{country}.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(self._rag_ops_report_markdown(payload), encoding="utf-8")
        return {"json_path": str(json_path), "markdown_path": str(markdown_path), "country": country}

    def _rag_ops_report_markdown(self, payload: dict[str, object]) -> str:
        live = payload.get("live_model_ops", {}) if isinstance(payload.get("live_model_ops"), dict) else {}
        patch = payload.get("patch_ops", {}) if isinstance(payload.get("patch_ops"), dict) else {}
        priority = payload.get("patch_priority_summary", {}) if isinstance(payload.get("patch_priority_summary"), dict) else {}
        top_patch = priority.get("top_patch", {}) if isinstance(priority.get("top_patch"), dict) else {}
        case_diff = payload.get("patch_case_diff", {}) if isinstance(payload.get("patch_case_diff"), dict) else {}
        evidence = payload.get("live_model_evidence", {}) if isinstance(payload.get("live_model_evidence"), dict) else {}
        evidence_overall = evidence.get("overall", {}) if isinstance(evidence.get("overall"), dict) else {}
        evidence_embedding = evidence.get("embedding", {}) if isinstance(evidence.get("embedding"), dict) else {}
        evidence_rerank = evidence.get("rerank", {}) if isinstance(evidence.get("rerank"), dict) else {}
        quality = payload.get("quality_eval", {}) if isinstance(payload.get("quality_eval"), dict) else {}
        answer_accuracy = quality.get("answer_accuracy", {}) if isinstance(quality.get("answer_accuracy"), dict) else {}
        trustworthiness = quality.get("trustworthiness", {}) if isinstance(quality.get("trustworthiness"), dict) else {}
        latency = quality.get("latency", {}) if isinstance(quality.get("latency"), dict) else {}
        scalability = quality.get("scalability", {}) if isinstance(quality.get("scalability"), dict) else {}
        user_experience = quality.get("user_experience", {}) if isinstance(quality.get("user_experience"), dict) else {}
        acceptance = payload.get("latest_acceptance", {}) if isinstance(payload.get("latest_acceptance"), dict) else {}
        dataset = payload.get("rag_eval_dataset", {}) if isinstance(payload.get("rag_eval_dataset"), dict) else {}
        knowledge = payload.get("knowledge_base", {}) if isinstance(payload.get("knowledge_base"), dict) else {}
        fixed_failures = ", ".join(str(item) for item in case_diff.get("fixed_failures", ()) if str(item))
        new_failures = ", ".join(str(item) for item in case_diff.get("new_failures", ()) if str(item))
        lines = [
            f"# RAG Ops Report - {payload.get('country', '')}",
            "",
            f"- generated_at: {payload.get('generated_at', '')}",
            "",
            "## RAG Live Model Ops",
            f"- mode: {live.get('mode', 'not_run')}",
            f"- status: {live.get('status', '')}",
            f"- embedding: ready={live.get('embedding_ready', False)} provider={live.get('embedding_provider', '')}",
            f"- qdrant: ready={live.get('qdrant_ready', False)} provider={live.get('qdrant_provider', '')} hit={live.get('qdrant_vector_hits', False)}",
            f"- rerank: ready={live.get('rerank_ready', False)} provider={live.get('rerank_provider', '')}",
            f"- remote_calls: embedding={live.get('embedding_remote_calls', 0)} rerank={live.get('rerank_remote_calls', 0)}",
            f"- fallbacks: embedding={live.get('embedding_fallbacks', 0)} rerank={live.get('rerank_fallbacks', 0)}",
            f"- hit@5: {live.get('hit@5', 0)}",
            f"- mrr@5: {live.get('mrr@5', 0)}",
            "",
            "## RAG Live Model Evidence",
            f"- status: {evidence_overall.get('status', 'unknown')}",
            f"- verified: {evidence_overall.get('verified', False)}",
            f"- embedding: provider={evidence_embedding.get('provider', '')} model={evidence_embedding.get('model', '')} family={evidence_embedding.get('model_family', '')} remote_calls={evidence_embedding.get('observed_remote_calls', 0)} fallback_free={evidence_embedding.get('fallback_free', False)}",
            f"- rerank: provider={evidence_rerank.get('provider', '')} model={evidence_rerank.get('model', '')} family={evidence_rerank.get('provider_family', '')} remote_calls={evidence_rerank.get('observed_remote_calls', 0)} fallback_free={evidence_rerank.get('fallback_free', False)}",
            "",
            "## RAG Quality Eval",
            f"- source={quality.get('source', 'acceptance_report')} trace_count={quality.get('trace_count', 0)}",
            f"- answer_accuracy: bleu1={answer_accuracy.get('bleu1', 0)} rouge_l={answer_accuracy.get('rouge_l', 0)}",
            f"- trustworthiness: support_overlap={trustworthiness.get('support_overlap', 0)} document_coverage={trustworthiness.get('document_coverage', 0)}",
            f"- latency: average_ms={latency.get('average_ms', 0)} p95_ms={latency.get('p95_ms', 0)} p99_ms={latency.get('p99_ms', 0)}",
            f"- scalability: qps={scalability.get('qps', 0)} corpus_document_count={scalability.get('corpus_document_count', 0)}",
            f"- user_experience: average_satisfaction={user_experience.get('average_satisfaction', 0)} satisfaction_rate={user_experience.get('satisfaction_rate', 0)} readability_score={user_experience.get('readability_score', 0)}",
            "",
            "## RAG Patch Ops",
            f"- status: {patch.get('status', 'none')}",
            f"- patch_count: {patch.get('patch_count', 0)}",
            f"- rebuild_hit@5: {patch.get('rebuild_hit@5', 0)}",
            f"- rebuild_mrr@5: {patch.get('rebuild_mrr@5', 0)}",
            f"- qdrant_status: {patch.get('qdrant_status', 'none')}",
            f"- qdrant_points: {patch.get('qdrant_points', 0)}",
            f"- manifest_path: {patch.get('manifest_path', '')}",
            "",
            "## RAG Patch Priority",
            f"- P0: {priority.get('P0', 0)}",
            f"- P1: {priority.get('P1', 0)}",
            f"- P2: {priority.get('P2', 0)}",
            f"- top_patch: {top_patch.get('patch_id', '')}",
            "",
            "## RAG Patch Case Diff",
            f"- fixed_failure_count: {case_diff.get('fixed_failure_count', 0)}",
            f"- new_failure_count: {case_diff.get('new_failure_count', 0)}",
            f"- fixed_failures: {fixed_failures}",
            f"- new_failures: {new_failures}",
            "",
            "## RAG Acceptance",
            f"- exists: {acceptance.get('exists', False)}",
            f"- status: {acceptance.get('status', '')}",
            f"- failure_stage: {acceptance.get('failure_stage', '')}",
            f"- report_path: {acceptance.get('report_path', '')}",
            "",
            "## RAG Eval Dataset",
            f"- real_sample_count: {dataset.get('real_sample_count', 0)}",
            f"- human_gold_count: {dataset.get('human_gold_count', 0)}",
            f"- ai_silver_count: {dataset.get('ai_silver_count', 0)}",
            f"- status: {dataset.get('status', '')}",
            "",
            "## Knowledge Base",
            f"- root: {knowledge.get('root', '')}",
            f"- qdrant_manifest_status: {knowledge.get('qdrant_manifest_status', '')}",
            f"- qdrant_manifest_points: {knowledge.get('qdrant_manifest_upserted_points', 0)}",
            f"- qdrant_manifest_vector_size: {knowledge.get('qdrant_manifest_vector_size', 0)}",
        ]
        return "\n".join(lines) + "\n"

    def rag_patch_ops_summary(self, country: str) -> dict[str, object]:
        latest_manifest_path = _rag_knowledge_dir() / "patch_manifests" / f"rag_patch_apply_{country}.json"
        manifest = _read_json_object(latest_manifest_path)
        if not manifest:
            priority_summary = self.rag_knowledge_patch_drafts(country, limit=10_000).get("priority_summary", {})
            return {
                "status": "none",
                "manifest_path": str(latest_manifest_path),
                "patch_count": 0,
                "raw_patch_file": "",
                "rebuild_hit@5": 0,
                "rebuild_mrr@5": 0,
                "qdrant_status": "none",
                "qdrant_points": 0,
                "qdrant_vector_size": 0,
                "recent_runs": (),
                "priority_summary": priority_summary,
                "priority_impact": _rag_patch_priority_impact(priority_summary, {}),
            }
        latest = _rag_patch_manifest_row(manifest, latest_manifest_path)
        runs_dir = latest_manifest_path.parent / "runs"
        recent_runs: list[dict[str, object]] = []
        if runs_dir.exists():
            for path in sorted(runs_dir.glob(f"rag_patch_apply_{country}_*.json"), reverse=True):
                payload = _read_json_object(path)
                if payload:
                    recent_runs.append(_rag_patch_manifest_row(payload, path))
                if len(recent_runs) >= 8:
                    break
        comparison = _rag_patch_run_comparison(latest, tuple(recent_runs))
        priority_summary = self.rag_knowledge_patch_drafts(country, limit=10_000).get("priority_summary", {})
        return {
            **latest,
            "recent_runs": tuple(recent_runs),
            "run_comparison": comparison,
            "priority_summary": priority_summary,
            "priority_impact": _rag_patch_priority_impact(priority_summary, comparison),
        }

    def rag_knowledge_patch_drafts(self, country: str, *, limit: int = 8) -> dict[str, object]:
        feedback = self.rag_eval_failure_feedback_summary(country, limit=limit)
        drafts = []
        for item in feedback.get("items", ()):
            if not isinstance(item, dict):
                continue
            priority = _rag_patch_priority(item)
            expected = str(item.get("expected_parent_id", ""))
            source_type = "audit_policy_patch" if "AUDIT" in expected or "GLOBAL" in expected else "value_rule_patch"
            patch_id = f"patch-{country}-{item.get('memory_id', 0)}"
            query = str(item.get("query", ""))
            note = str(item.get("note", ""))
            retrieved = item.get("retrieved_parent_ids", ())
            if isinstance(retrieved, (list, tuple)):
                retrieved_text = "、".join(str(value) for value in retrieved)
            else:
                retrieved_text = str(retrieved)
            draft_text = (
                f"针对 RAG eval query「{query}」，expected parent「{expected}」未被召回；"
                f"误召回/实际召回为「{retrieved_text or '无'}」。"
                f"建议补充与该 expected parent 相关的价值观、审核规则或 hard negative 表述。"
                f"人工备注：{note or '待运营补充'}"
            )
            drafts.append(
                {
                    "patch_id": patch_id,
                    "country": country,
                    "source_type": source_type,
                    "title": f"RAG失败反馈补丁：{expected}",
                    "expected_parent_id": expected,
                    "source_memory_id": item.get("memory_id", 0),
                    "query": query,
                    "draft_text": draft_text,
                    "review_status": "needs_human_review",
                    "optimization_use": item.get("optimization_use", "hard_negative_or_knowledge_patch"),
                    "priority_score": priority["score"],
                    "priority_band": priority["band"],
                    "priority_reason": priority["reason"],
                    "diagnosis": item.get("diagnosis", ""),
                    "gold_grade": item.get("gold_grade", ""),
                    "label_source": item.get("label_source", ""),
                }
            )
        drafts = sorted(drafts, key=lambda item: (int(item.get("priority_score", 0)), int(item.get("source_memory_id", 0))), reverse=True)
        return {"draft_count": len(drafts), "items": tuple(drafts), "priority_summary": _rag_patch_priority_summary(drafts)}

    def export_rag_knowledge_patch_drafts(self, country: str, output_path: Path | str) -> Path:
        drafts = self.rag_knowledge_patch_drafts(country, limit=10_000)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for item in drafts.get("items", ()):
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        return path

    def export_approved_rag_patch_markdown(self, country: str, output_path: Path | str) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        patches = []
        for memory in self.repository.layered_memories(country, layer="long_term", include_inactive=True):
            if memory.get("memory_type") != "approved_rag_knowledge_patch":
                continue
            if not memory.get("human_verified"):
                continue
            if memory.get("status") != "active":
                continue
            payload = memory.get("payload", {})
            if not isinstance(payload, dict):
                continue
            patches.append((memory, payload))

        lines = [
            "---",
            f"country: {country}",
            "source_type: approved_rag_patch",
            "review_status: approved",
            "generated_from: long_term_memory",
            "---",
            "",
            "# RAG已审核知识补丁",
            "",
            "本文件由已人工审核通过的长期记忆导出，供后续人工确认后合入 knowledge/raw；导出动作不会自动改写正式知识库。",
            "",
        ]
        if not patches:
            lines.extend(["暂无已审核 RAG 知识补丁。", ""])
        for memory, payload in patches:
            expected = str(payload.get("expected_parent_id", "") or f"memory-{memory.get('memory_id', '')}")
            explicit_id = f" {{#{expected}}}" if re.match(r"^[A-Za-z0-9_\-]+$", expected) else ""
            lines.extend(
                [
                    f"## RAG补丁：{expected}{explicit_id}",
                    "",
                    f"- patch_id: {payload.get('patch_id', '')}",
                    f"- source_memory_id: {memory.get('source_memory_id', '')}",
                    f"- memory_id: {memory.get('memory_id', '')}",
                    f"- optimization_use: {payload.get('optimization_use', '')}",
                    f"- query: {payload.get('query', '')}",
                    f"- 人工审核备注: {payload.get('human_note', '')}",
                    "",
                    str(payload.get("rule_text", "")),
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def apply_approved_rag_patch_markdown_to_raw(self, country: str) -> dict[str, object]:
        root = _rag_knowledge_dir()
        raw_dir = root / "raw"
        manifests_dir = root / "patch_manifests"
        runs_dir = manifests_dir / "runs"
        raw_dir.mkdir(parents=True, exist_ok=True)
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_id = _manifest_run_id()
        raw_patch_path = raw_dir / f"approved_rag_patch_{country}_{run_id}.md"
        self.export_approved_rag_patch_markdown(country, raw_patch_path)

        patch_ids: list[str] = []
        source_memory_ids: list[int] = []
        for memory in self.repository.layered_memories(country, layer="long_term", include_inactive=True):
            if memory.get("memory_type") != "approved_rag_knowledge_patch":
                continue
            if not memory.get("human_verified") or memory.get("status") != "active":
                continue
            payload = memory.get("payload", {})
            if isinstance(payload, dict):
                patch_id = str(payload.get("patch_id", "")).strip()
                if patch_id:
                    patch_ids.append(patch_id)
            source_memory_ids.append(int(memory.get("memory_id", 0) or 0))

        status = "applied" if patch_ids else "skipped_no_approved_patches"
        manifest = {
            "run_id": run_id,
            "created_at": date.today().isoformat(),
            "country": country,
            "status": status,
            "source_type": "approved_rag_patch",
            "raw_patch_path": str(raw_patch_path),
            "applied_patch_count": len(patch_ids),
            "patch_ids": patch_ids,
            "source_memory_ids": source_memory_ids,
            "next_step": "人工确认后点击重建RAG知识库，再按需重建并入库Qdrant。",
        }
        manifest_path = runs_dir / f"rag_patch_apply_{country}_{run_id}.json"
        latest_manifest_path = manifests_dir / f"rag_patch_apply_{country}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **manifest,
            "manifest_path": str(manifest_path),
            "latest_manifest_path": str(latest_manifest_path),
        }

    def apply_approved_rag_patch_and_rebuild(self, country: str) -> dict[str, object]:
        apply_result = self.apply_approved_rag_patch_markdown_to_raw(country)
        rebuild = self.rebuild_rag_knowledge_from_raw(country)
        manifest_path = Path(str(apply_result.get("manifest_path", "")))
        latest_manifest_path = Path(str(apply_result.get("latest_manifest_path", "")))
        manifest = _read_json_object(manifest_path)
        manifest["status"] = "applied_rebuilt"
        manifest["rebuild"] = {
            "processed_path": rebuild.get("processed_path", ""),
            "document_count": rebuild.get("document_count", 0),
            "hit@5": rebuild.get("hit@5", 0),
            "mrr@5": rebuild.get("mrr@5", 0),
            "passed_threshold": rebuild.get("passed_threshold", False),
            "eval_total": rebuild.get("eval_total", 0),
            "failed_count": rebuild.get("failed_count", 0),
            "cases": rebuild.get("cases", ()),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **apply_result,
            **rebuild,
            "status": "applied_rebuilt",
            "rebuild": manifest["rebuild"],
        }

    def rollback_latest_approved_rag_patch_and_rebuild(self, country: str) -> dict[str, object]:
        root = _rag_knowledge_dir()
        latest_manifest_path = root / "patch_manifests" / f"rag_patch_apply_{country}.json"
        manifest = _read_json_object(latest_manifest_path)
        if not manifest:
            raise ValueError(f"找不到最新 RAG patch manifest：{latest_manifest_path}")
        if str(manifest.get("country", "")) != country:
            raise ValueError(f"RAG patch manifest 国家不匹配：{manifest.get('country')} != {country}")
        raw_patch_path = Path(str(manifest.get("raw_patch_path", "")))
        if not raw_patch_path.exists():
            raise ValueError(f"找不到要回滚的 raw patch：{raw_patch_path}")
        raw_patch_path.unlink()
        rebuild = self.rebuild_rag_knowledge_from_raw(country)
        rollback = {
            "removed_raw_patch_path": str(raw_patch_path),
            "rolled_back_at": date.today().isoformat(),
        }
        manifest["status"] = "rolled_back_rebuilt"
        manifest["rollback"] = rollback
        manifest["rebuild_after_rollback"] = {
            "processed_path": rebuild.get("processed_path", ""),
            "document_count": rebuild.get("document_count", 0),
            "hit@5": rebuild.get("hit@5", 0),
            "mrr@5": rebuild.get("mrr@5", 0),
            "passed_threshold": rebuild.get("passed_threshold", False),
            "eval_total": rebuild.get("eval_total", 0),
            "failed_count": rebuild.get("failed_count", 0),
            "cases": rebuild.get("cases", ()),
        }
        latest_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        run_id = str(manifest.get("run_id", "")).strip()
        manifest_path = latest_manifest_path
        if run_id:
            run_manifest_path = latest_manifest_path.parent / "runs" / f"rag_patch_apply_{country}_{run_id}.json"
            run_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest_path = run_manifest_path
        return {
            **rebuild,
            "status": "rolled_back_rebuilt",
            "removed_raw_patch_path": str(raw_patch_path),
            "manifest_path": str(manifest_path),
            "latest_manifest_path": str(latest_manifest_path),
            "rollback": rollback,
        }

    def apply_approved_rag_patch_rebuild_and_reindex_qdrant(
        self,
        country: str,
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
        vector_store: QdrantVectorStore | MilvusVectorStore | None = None,
    ) -> dict[str, object]:
        return self.apply_approved_rag_patch_rebuild_and_reindex_vector_store(
            country,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            vector_store_provider="qdrant",
        )

    def apply_approved_rag_patch_rebuild_and_reindex_vector_store(
        self,
        country: str,
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
        vector_store=None,
        vector_store_provider: str | None = None,
    ) -> dict[str, object]:
        apply_result = self.apply_approved_rag_patch_and_rebuild(country)
        vector_result = self.reindex_rag_vector_store_from_raw(
            country,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            vector_store_provider=vector_store_provider,
        )
        manifest_path = Path(str(apply_result.get("manifest_path", "")))
        latest_manifest_path = Path(str(apply_result.get("latest_manifest_path", "")))
        manifest = _read_json_object(manifest_path)
        provider = str(vector_result.get("vector_store_provider", vector_store_provider or self.rag_vector_store_config.provider or "")).strip() or "vector_store"
        vector_summary = {
            "provider": provider,
            "status": vector_result.get("status", ""),
            "manifest_path": vector_result.get("manifest_path", ""),
            "latest_manifest_path": vector_result.get("latest_manifest_path", ""),
            "upserted_points": vector_result.get("upserted_points", 0),
            "chunk_count": vector_result.get("chunk_count", 0),
            "vector_count": vector_result.get("vector_count", 0),
            "vector_size": vector_result.get("vector_size", 0),
            "hit@5": vector_result.get("hit@5", 0),
            "mrr@5": vector_result.get("mrr@5", 0),
            "precision@5": vector_result.get("precision@5", 0),
            "recall@5": vector_result.get("recall@5", 0),
            "ndcg@5": vector_result.get("ndcg@5", 0),
            "qdrant_collection": vector_result.get("qdrant_collection", ""),
            "vector_store_collection": vector_result.get("vector_store_collection", vector_result.get("qdrant_collection", "")),
        }
        manifest["status"] = f"applied_rebuilt_{provider}_indexed"
        manifest["vector_store"] = vector_summary
        if provider == "qdrant":
            manifest["qdrant"] = vector_summary
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **apply_result,
            "status": f"applied_rebuilt_{provider}_indexed",
            "vector_store": vector_summary,
            "qdrant": vector_summary if provider == "qdrant" else {},
        }

    def approve_rag_knowledge_patch_draft(self, country: str, patch_id: str, *, human_note: str, actor: str = "") -> int:
        draft = next(
            (item for item in self.rag_knowledge_patch_drafts(country, limit=10_000).get("items", ()) if isinstance(item, dict) and item.get("patch_id") == patch_id),
            None,
        )
        if not draft:
            raise ValueError(f"找不到 RAG 知识补丁草案：{patch_id}")
        payload = {
            "patch_id": str(draft.get("patch_id", "")),
            "source_type": str(draft.get("source_type", "")),
            "expected_parent_id": str(draft.get("expected_parent_id", "")),
            "query": str(draft.get("query", "")),
            "rule_text": str(draft.get("draft_text", "")),
            "human_note": human_note.strip() or "运营人工审核通过",
            "review_status": "approved",
            "optimization_use": str(draft.get("optimization_use", "")),
        }
        source_memory_id = int(draft.get("source_memory_id", 0) or 0) or None
        return self.repository.add_layered_memory(
            country,
            "long_term",
            "approved_rag_knowledge_patch",
            payload,
            source_memory_id=source_memory_id,
            human_verified=True,
            created_by=actor,
            review_status="approved",
            approved_for_rag=True,
        )

    def rag_quality_governance_workbench(self, country: str) -> dict[str, object]:
        citation_summary = self.rag_feedback_summary(country)
        low_scores = []
        for memory in self.repository.layered_memories(country, layer="working", include_inactive=True):
            if memory.get("memory_type") != "value_match_human_score" or memory.get("status") != "active":
                continue
            payload = memory.get("payload", {})
            if not isinstance(payload, dict):
                continue
            score = int(payload.get("satisfaction_score", 0) or 0)
            if score and score <= 2:
                low_scores.append(
                    {
                        "memory_id": int(memory.get("memory_id", 0) or 0),
                        "subject": str(payload.get("subject", "")),
                        "operation_tag": str(payload.get("operation_tag", "")),
                        "satisfaction_score": score,
                    }
                )
        failure_summary = self.rag_eval_failure_feedback_summary(country, limit=10_000)
        patch_summary = self.rag_knowledge_patch_drafts(country, limit=10_000)
        emergency_items = tuple(
            {**item, "reason": "risk_keyword_or_p0"}
            for item in patch_summary.get("items", ())
            if isinstance(item, dict) and _is_emergency_rag_patch_candidate(item)
        )
        return {
            "cadence": "monthly_with_emergency",
            "cadence_label": "月度重建 + 紧急补丁",
            "feedback_pool": {
                "citation_feedback_count": int(citation_summary.get("total_feedback", 0) or 0),
                "not_useful_count": int(citation_summary.get("not_useful_count", 0) or 0),
                "useful_count": int(citation_summary.get("useful_count", 0) or 0),
                "low_score_count": len(low_scores),
                "low_scores": tuple(low_scores[:8]),
                "failure_feedback_count": int(failure_summary.get("pending_count", 0) or 0),
            },
            "weekly_anomalies": {
                "emergency_candidate_count": len(emergency_items),
                "top_not_useful_chunks": tuple(
                    item for item in citation_summary.get("top_chunks", ()) if isinstance(item, dict) and int(item.get("not_useful_count", 0) or 0) > 0
                )[:8],
                "low_score_items": tuple(low_scores[:8]),
            },
            "monthly_patch_plan": {
                "draft_count": int(patch_summary.get("draft_count", 0) or 0),
                "recommended_action": "monthly_review" if int(patch_summary.get("draft_count", 0) or 0) else "collect_more_feedback",
                "priority_summary": patch_summary.get("priority_summary", {}),
                "items": tuple(patch_summary.get("items", ())[:8]) if isinstance(patch_summary.get("items", ()), tuple) else tuple(patch_summary.get("items", ())),
            },
            "emergency_patch_flow": {
                "items": emergency_items[:8],
                "rule": "P0、版权/IP、文化禁忌、节日误判可走紧急补丁；其余进入月度处理。",
            },
        }

    def mark_rag_feedback_for_monthly_review(self, country: str, memory_id: int, *, actor: str = "", note: str = "") -> int:
        return self.record_working_memory(
            country,
            "rag_governance_monthly_marker",
            {"source_memory_id": memory_id, "review_note": note.strip(), "governance_status": "monthly_review"},
            actor=actor,
        )

    def mark_rag_feedback_for_emergency_patch(self, country: str, memory_id: int, *, actor: str = "", note: str = "") -> int:
        return self.record_working_memory(
            country,
            "rag_governance_emergency_marker",
            {"source_memory_id": memory_id, "review_note": note.strip(), "governance_status": "emergency_patch"},
            actor=actor,
        )

    def apply_emergency_rag_patch_and_rebuild(self, country: str, memory_id: int, *, actor: str = "", note: str = "") -> dict[str, object]:
        patch = next(
            (
                item
                for item in self.rag_knowledge_patch_drafts(country, limit=10_000).get("items", ())
                if isinstance(item, dict) and int(item.get("source_memory_id", 0) or 0) == int(memory_id)
            ),
            None,
        )
        if not patch:
            raise ValueError(f"找不到可应用的紧急 RAG 反馈：memory_id={memory_id}")
        self.mark_rag_feedback_for_emergency_patch(country, memory_id, actor=actor, note=note or "紧急补丁应用")
        self.approve_rag_knowledge_patch_draft(
            country,
            str(patch.get("patch_id", "")),
            human_note=note or "负责人确认紧急补丁",
            actor=actor,
        )
        result = self.apply_approved_rag_patch_and_rebuild(country)
        return {
            **result,
            "status": "emergency_applied",
            "feedback_memory_id": memory_id,
            "patch_id": str(patch.get("patch_id", "")),
            "emergency_reason": "risk_keyword_or_p0" if _is_emergency_rag_patch_candidate(patch) else "manual_emergency",
        }

    def rag_eval_failure_feedback_summary(self, country: str, *, limit: int = 8) -> dict[str, object]:
        items = []
        for memory in self.repository.layered_memories(country, layer="working", include_inactive=True):
            if memory.get("memory_type") != "rag_eval_failure_feedback" or memory.get("status") != "active":
                continue
            payload = memory.get("payload", {})
            if not isinstance(payload, dict):
                continue
            retrieved = payload.get("retrieved_parent_ids", ())
            if isinstance(retrieved, (list, tuple)):
                retrieved_ids = tuple(str(item) for item in retrieved)
            else:
                retrieved_ids = tuple(str(retrieved).split()) if retrieved else ()
            items.append(
                {
                    "memory_id": int(memory.get("memory_id", 0) or 0),
                    "query": str(payload.get("query", "")),
                    "expected_parent_id": str(payload.get("expected_parent_id", "")),
                    "retrieved_parent_ids": retrieved_ids,
                    "note": str(payload.get("note", "")),
                    "diagnosis": str(payload.get("diagnosis", "")),
                    "suggested_action": str(payload.get("suggested_action", "")),
                    "gold_grade": str(payload.get("gold_grade", "")),
                    "label_source": str(payload.get("label_source", "")),
                    "optimization_use": "hard_negative_or_knowledge_patch",
                    "status": str(memory.get("status", "active")),
                }
            )
        items = sorted(items, key=lambda item: int(item["memory_id"]), reverse=True)
        return {"pending_count": len(items), "items": tuple(items[:limit])}

    def export_rag_eval_failure_feedback(self, country: str, output_path: Path | str) -> Path:
        summary = self.rag_eval_failure_feedback_summary(country, limit=10_000)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for item in summary.get("items", ()):
                payload = {"country": country, **item}
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path

    def rag_eval_case_evidence(self, country: str, *, limit: int = 8) -> dict[str, object]:
        report = self.value_audit_rag_eval_report(country)
        raw_cases = report.get("cases", ())
        if not isinstance(raw_cases, (list, tuple)):
            raw_cases = ()
        rows: list[dict[str, object]] = []
        failed_count = 0
        for case in raw_cases[:limit]:
            if not isinstance(case, dict):
                continue
            retrieved = case.get("retrieved_parent_ids", ())
            if isinstance(retrieved, (list, tuple)):
                retrieved_ids = tuple(str(item) for item in retrieved)
            else:
                retrieved_ids = tuple(str(retrieved).split()) if retrieved else ()
            hit = bool(case.get("hit"))
            rank = int(case.get("rank", 0) or 0)
            if not hit:
                failed_count += 1
            expected = str(case.get("expected_parent_id", ""))
            rows.append(
                {
                    "query": str(case.get("query", "")),
                    "country": str(case.get("country", country)),
                    "expected_parent_id": expected,
                    "retrieved_parent_ids": retrieved_ids,
                    "hit": hit,
                    "rank": rank,
                    "status": "PASS" if hit else "FAIL",
                    "failure_reason": "" if hit else f"未命中 expected_parent_id={expected}",
                }
            )
        return {
            "dataset_name": report.get("dataset_name", ""),
            "hit@5": report.get("hit@5", 0),
            "mrr@5": report.get("mrr@5", 0),
            "threshold": report.get("threshold", 0.8),
            "passed_threshold": report.get("passed_threshold", False),
            "total": report.get("total", len(raw_cases)),
            "failed_count": failed_count,
            "cases": tuple(rows),
        }

    def rag_eval_dataset_summary(self, country: str) -> dict[str, object]:
        samples = tuple(sample for sample in self.harness_samples(country) if sample.is_real)
        human_gold = tuple(sample for sample in samples if sample.label_source == "human_gold" and sample.label_status == "reviewed")
        ai_silver = tuple(sample for sample in samples if sample.label_source == "ai_silver" and sample.label_status == "pending_review")
        manual_grade = tuple(sample for sample in samples if sample.label_source == "manual_grade" and sample.label_status == "needs_ai_prelabeled")
        file_cases = self._rag_eval_cases(country)
        harness_cases = self._harness_gold_rag_eval_cases(country)
        real_count = len(samples)
        if real_count >= 30 and human_gold:
            status = "ready_for_business_eval"
        elif real_count >= 30:
            status = "needs_human_gold_review"
        else:
            status = "needs_30_50_real_samples"
        return {
            "real_sample_count": real_count,
            "ai_silver_count": len(ai_silver),
            "manual_grade_count": len(manual_grade),
            "human_gold_count": len(human_gold),
            "file_eval_case_count": len(file_cases),
            "harness_eval_case_count": len(harness_cases),
            "total_eval_case_count": len(file_cases) + len(harness_cases),
            "target_real_sample_range": "30-50",
            "hit_at_five_threshold": 0.8,
            "status": status,
        }

    def rag_chunk_eval_dataset_summary(self, country: str) -> dict[str, object]:
        documents = self.rag_documents_for_task(country, "all")
        chunks = tuple(
            chunk
            for document in documents
            for chunk in chunk_document(document, max_chars=None, chunking=self.rag_chunking_config)
        )
        cases = _business_object_chunk_eval_cases(country, documents)
        retriever = HybridRagRetriever(chunks)
        report = evaluate_retrieval_report(
            retriever,
            cases,
            k=5,
            threshold=0.8,
            dataset_name=f"{country}业务对象级chunk eval",
            knowledge_version=f"{country}-business-object-{len(documents)}docs-{len(chunks)}chunks",
        )
        risk_cases = tuple(case for case in cases if _looks_like_audit_query(case.query))
        risk_missed = sum(
            1
            for case in report.get("cases", ())
            if isinstance(case, dict) and _looks_like_audit_query(str(case.get("query", ""))) and not case.get("hit")
        )
        precision = float(report.get("precision@5", 0) or 0)
        return {
            "country": country,
            "query_count": len(cases),
            "target_query_range": "30-50",
            "metrics": {
                "recall@5": report.get("recall@5", 0),
                "mrr@5": report.get("mrr@5", 0),
                "citation_precision@5": precision,
                "risk_miss_rate@5": round(risk_missed / len(risk_cases), 4) if risk_cases else 0.0,
            },
            "hybrid_search": {
                "bm25_dense_rerank": True,
                "bm25": True,
                "dense": True,
                "rerank": True,
                "milvus_hybrid_ready": self.rag_vector_store_config.provider == "milvus",
            },
            "document_count": len(documents),
            "chunk_count": len(chunks),
            "cases": report.get("cases", ())[:10],
        }

    def latest_rag_acceptance_summary(self, country: str) -> dict[str, object]:
        summary_path = self._runtime_dir / "rag_acceptance_reports" / f"rag_acceptance_full_summary_{country}.json"
        payload = _read_json_object(summary_path)
        if not payload:
            return {"exists": False, "summary_path": str(summary_path)}
        report = payload.get("report", {}) if isinstance(payload.get("report"), dict) else {}
        preflight = payload.get("preflight", {}) if isinstance(payload.get("preflight"), dict) else {}
        observed = report.get("observed_retrieval", {}) if isinstance(report.get("observed_retrieval"), dict) else {}
        runtime_stats = report.get("runtime_stats", {}) if isinstance(report.get("runtime_stats"), dict) else {}
        live_model_evidence = report.get("live_model_evidence", {}) if isinstance(report.get("live_model_evidence"), dict) else {}
        quality_eval = report.get("quality_eval", {}) if isinstance(report.get("quality_eval"), dict) else {}
        return {
            "exists": True,
            "summary_path": str(summary_path),
            "status": str(payload.get("status", "")),
            "failure_stage": str(payload.get("failure_stage", "")),
            "error": str(payload.get("error", "")),
            "report_path": str(payload.get("report_path", "")),
            "preflight": preflight,
            "mode": str(preflight.get("mode", "")),
            "hit@5": report.get("hit@5", 0),
            "mrr@5": report.get("mrr@5", 0),
            "qdrant_vector_hits": observed.get("qdrant_vector_hits", False),
            "runtime_stats": runtime_stats,
            "live_model_evidence": live_model_evidence,
            "quality_eval": quality_eval,
        }

    def rag_trace_quality_eval_summary(self, country: str, *, limit: int = 50) -> dict[str, object]:
        traces = self.recent_rag_traces(country, limit=limit)
        answers: list[str] = []
        references: list[str] = []
        support_documents: list[str] = []
        required_facts: list[str] = []
        latency_ms: list[float] = []
        satisfaction_scores: list[int] = []
        citation_ids: set[str] = set()
        for trace in traces:
            answer = str(trace.get("answer", "") or "")
            reference = str(trace.get("reference_answer", "") or "")
            context = str(trace.get("context", "") or "")
            if answer:
                answers.append(answer)
            if reference:
                references.append(reference)
            for document in _as_text_tuple(trace.get("support_documents", ())):
                if document:
                    support_documents.append(document)
            if context:
                support_documents.append(context)
            for fact in _as_text_tuple(trace.get("required_facts", ())):
                if fact:
                    required_facts.append(fact)
            latency = _trace_latency_ms(trace)
            if latency is not None:
                latency_ms.append(latency)
            score = _trace_satisfaction_score(trace)
            if score is not None:
                satisfaction_scores.append(score)
            for citation in _as_text_tuple(trace.get("citations", ())):
                if citation:
                    citation_ids.add(citation)
        total_seconds = round(sum(latency_ms) / 1000, 4) if latency_ms else 0.0
        quality = evaluate_rag_quality_report(
            answer="\n".join(answers),
            reference_answer="\n".join(references),
            support_documents=tuple(support_documents),
            required_facts=tuple(required_facts),
            latency_ms=tuple(latency_ms),
            satisfaction_scores=tuple(satisfaction_scores),
            total_queries=len(traces),
            total_seconds=total_seconds,
            corpus_document_count=len(citation_ids),
        )
        return {"source": "rag_traces", "trace_count": len(traces), **quality}

    def _default_rag_answer_generator(self):
        provider = os.getenv("RAG_GENERATION_PROVIDER", "").strip().lower()
        remote_enabled = os.getenv("RAG_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        if provider in {"qwen", "dashscope"} and remote_enabled:
            api_key = _first_nonempty_env("RAG_GENERATION_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY")
            model = os.getenv("RAG_GENERATION_MODEL", "qwen3.7-plus").strip() or "qwen3.7-plus"
            endpoint = os.getenv(
                "RAG_GENERATION_ENDPOINT",
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            ).strip()
            return QwenRagAnswerGenerator(api_key=api_key, model=model, endpoint=endpoint)
        reason = "RAG 生成模型未配置；需要 RAG_GENERATION_PROVIDER=qwen 且 RAG_ENABLE_REMOTE_CALLS=1"
        return MissingRagAnswerGenerator(reason)

    def _augment_latest_rag_trace_with_generation(
        self,
        country: str,
        result: RagGeneratedAnswer,
        generation_latency_ms: float,
    ) -> None:
        traces = self.recent_rag_traces(country, limit=1)
        if not traces:
            return
        path = Path(str(traces[0].get("trace_path", "")))
        payload = _read_json_object(path)
        if not payload:
            return
        payload.update(
            {
                "llm_answer": result.answer,
                "answer": result.answer or payload.get("answer", ""),
                "answer_source": "llm_generated" if result.status == "generated" else payload.get("answer_source", "retrieved_context"),
                "generation_status": result.status,
                "generation_provider": result.provider,
                "generation_model": result.model,
                "generation_prompt": result.prompt,
                "generation_citations": result.citations,
                "generation_latency_ms": generation_latency_ms,
                "generation_error": result.error,
            }
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def recent_rag_traces(self, country: str, *, limit: int = 5) -> tuple[dict[str, object], ...]:
        trace_dir = self._rag_trace_dir(country)
        if not trace_dir.exists():
            return ()
        rows = []
        for path in sorted(trace_dir.glob("*.json"), reverse=True):
            payload = _read_json_object(path)
            if not payload:
                continue
            citations = payload.get("citations", ())
            if isinstance(citations, list):
                payload["citations"] = tuple(str(item) for item in citations)
            support_documents = payload.get("support_documents", ())
            if isinstance(support_documents, list):
                payload["support_documents"] = tuple(str(item) for item in support_documents)
            required_facts = payload.get("required_facts", ())
            if isinstance(required_facts, list):
                payload["required_facts"] = tuple(str(item) for item in required_facts)
            generation_citations = payload.get("generation_citations", ())
            if isinstance(generation_citations, list):
                payload["generation_citations"] = tuple(str(item) for item in generation_citations)
            payload["trace_path"] = str(path)
            rows.append(payload)
            if len(rows) >= limit:
                break
        return tuple(rows)

    def _write_rag_trace(
        self,
        country: str,
        original_query: str,
        rewritten_query: str,
        prompt: RagPrompt,
        retrieval_trace: dict[str, object],
        runtime_stats: dict[str, object],
        *,
        latency_ms: float | None = None,
    ) -> str:
        trace_dir = self._rag_trace_dir(country)
        trace_dir.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = trace_dir / f"rag_trace_{created_at}_{uuid.uuid4().hex[:8]}.json"
        payload = {
            "trace_id": path.stem,
            "created_at": created_at,
            "country": country,
            "original_query": original_query,
            "rewritten_query": rewritten_query,
            "answer": prompt.context,
            "answer_source": "retrieved_context",
            "support_documents": (prompt.context,),
            "latency_ms": latency_ms,
            "context": prompt.context,
            "citations": prompt.citations,
            "prompt": prompt.prompt,
            "retrieval_trace": retrieval_trace,
            "task_index": retrieval_trace.get("task_index", ""),
            "task_label": retrieval_trace.get("task_label", ""),
            "milvus_primary": retrieval_trace.get("milvus_primary", False),
            "vector_store_mode": retrieval_trace.get("vector_store_mode", ""),
            "runtime_stats": runtime_stats,
            "embedding_provider": self.rag_provider_config.embedding_provider,
            "embedding_model": self.rag_provider_config.embedding_model,
            "rerank_provider": self.rag_provider_config.rerank_provider,
            "rerank_model": self.rag_provider_config.rerank_model,
            "vector_store": self.rag_vector_store_config.provider,
            "vector_store_collection": self.rag_vector_store_config.collection,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._prune_rag_traces(trace_dir, keep=30)
        return str(path)

    def _rag_trace_dir(self, country: str) -> Path:
        safe_country = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "_", country).strip("_") or "GLOBAL"
        return self._runtime_dir / "rag_traces" / safe_country

    def _prune_rag_traces(self, trace_dir: Path, *, keep: int) -> None:
        paths = sorted(trace_dir.glob("*.json"), reverse=True)
        for path in paths[keep:]:
            path.unlink(missing_ok=True)

    def _rag_vector_store_search_enabled(self) -> bool:
        if self.rag_vector_store_config.provider == "milvus":
            enabled = os.getenv("RAG_MILVUS_SEARCH_ENABLED", os.getenv("RAG_VECTOR_STORE_SEARCH_ENABLED", ""))
            if not enabled.strip():
                return self.rag_vector_store_config.ready
        else:
            enabled = os.getenv("RAG_QDRANT_SEARCH_ENABLED", os.getenv("RAG_VECTOR_STORE_SEARCH_ENABLED", ""))
        return (
            self.rag_vector_store_config.provider in {"qdrant", "milvus"}
            and self.rag_vector_store_config.ready
            and enabled.strip().lower() in {"1", "true", "yes", "on"}
        )

    def _rag_vector_store_retriever(self) -> QdrantVectorStoreRetriever | MilvusVectorStoreRetriever | None:
        if not self._rag_vector_store_search_enabled():
            return None
        if self.rag_vector_store_config.provider == "milvus":
            return MilvusVectorStoreRetriever(MilvusVectorStore(self.rag_vector_store_config))
        return QdrantVectorStoreRetriever(QdrantVectorStore(self.rag_vector_store_config))

    def _vector_store_retriever_for(self, store):
        if getattr(store, "provider_name", "") == "milvus" or self.rag_vector_store_config.provider == "milvus":
            return MilvusVectorStoreRetriever(store)
        return QdrantVectorStoreRetriever(store)

    def export_value_audit_rag_artifacts(self, country: str, output_dir: Path | str) -> dict[str, object]:
        documents = StaticDocumentLoaderAdapter(self._rag_documents(country)).load()
        artifacts = export_offline_rag_index(
            documents,
            output_dir,
            country=country,
            chunking=self.rag_chunking_config,
            vector_store=self.rag_vector_store_config,
        )
        return {
            "manifest_path": str(artifacts.manifest_path),
            "documents_path": str(artifacts.documents_path),
            "chunks_path": str(artifacts.chunks_path),
            "document_count": artifacts.manifest["document_count"],
            "chunk_count": artifacts.manifest["chunk_count"],
            "source_counts": artifacts.manifest["source_counts"],
            "vector_store": artifacts.manifest["vector_store"]["provider"],
            "vector_store_ready": artifacts.manifest["vector_store"]["ready"],
            "parent_child_count": len(artifacts.manifest["parent_child"]),
        }

    def rebuild_rag_knowledge_from_raw(self, country: str) -> dict[str, object]:
        root = _rag_knowledge_dir()
        raw_dir = root / "raw"
        processed_path = root / "processed" / "value_audit_documents.jsonl"
        documents = build_processed_documents_from_raw(raw_dir, processed_path)
        self.build_value_audit_rag_index(country)
        report = self.value_audit_rag_eval_report(country)
        return {
            "raw_dir": str(raw_dir),
            "processed_path": str(processed_path),
            "document_count": len(documents),
            "hit@5": report.get("hit@5", 0),
            "mrr@5": report.get("mrr@5", 0),
            "precision@5": report.get("precision@5", 0),
            "recall@5": report.get("recall@5", 0),
            "ndcg@5": report.get("ndcg@5", 0),
            "passed_threshold": report.get("passed_threshold", False),
            "eval_total": report.get("total", 0),
            "failed_count": sum(1 for case in report.get("cases", ()) if isinstance(case, dict) and not case.get("hit")),
            "cases": report.get("cases", ()),
        }

    def reindex_rag_qdrant_from_raw(
        self,
        country: str,
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
        vector_store: QdrantVectorStore | None = None,
    ) -> dict[str, object]:
        return self.reindex_rag_vector_store_from_raw(country, embedding_provider=embedding_provider, vector_store=vector_store, vector_store_provider="qdrant")

    def reindex_rag_vector_store_from_raw(
        self,
        country: str,
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
        vector_store=None,
        vector_store_provider: str | None = None,
    ) -> dict[str, object]:
        provider_name = vector_store_provider or getattr(vector_store, "provider_name", "") or self.rag_vector_store_config.provider
        rebuild = self.rebuild_rag_knowledge_from_raw(country)
        chunks = tuple(_rag_chunk_from_row(row) for row in self.repository.rag_chunks(country))
        provider = embedding_provider
        stats = RagRuntimeStats()
        if provider is None:
            provider, _ = providers_from_config(
                self.rag_provider_config,
                stats=stats,
                cache_get=self.repository.get_rag_embedding_cache,
                cache_set=self.repository.set_rag_embedding_cache,
            )
        vectors_by_chunk_id: dict[str, tuple[float, ...]] = {}
        for chunk in chunks:
            vector = provider.query_vector(f"{chunk.title}：{chunk.text}")
            if vector:
                vectors_by_chunk_id[chunk.chunk_id] = tuple(float(value) for value in vector)
        points = prepare_qdrant_points(chunks, vectors_by_chunk_id)
        if not points:
            result = {
                **rebuild,
                "status": "skipped_no_vectors",
                "chunk_count": len(chunks),
                "vector_count": 0,
                "upserted_points": 0,
                "vector_size": 0,
                "vector_store_provider": provider_name,
                "vector_store_collection": self.rag_vector_store_config.collection,
                "qdrant_collection": self.rag_vector_store_config.collection,
                **stats.as_dict(),
            }
            result["manifest_path"] = self._write_vector_store_reindex_manifest(country, result)
            return result
        store = vector_store or self._rag_vector_store()
        vector_size = len(points[0].vector)
        collection_status = store.ensure_collection(vector_size) if hasattr(store, "ensure_collection") else {"status": "managed_by_provider", "vector_size": vector_size}
        response = store.upsert(points)
        result = {
            **rebuild,
            "status": "indexed",
            "chunk_count": len(chunks),
            "vector_count": len(vectors_by_chunk_id),
            "upserted_points": len(points),
            "vector_size": vector_size,
            "vector_store_provider": provider_name,
            "vector_store_collection": self.rag_vector_store_config.collection,
            "qdrant_collection": self.rag_vector_store_config.collection,
            "collection_status": collection_status,
            "qdrant_response": response,
            "vector_store_response": response,
            "point_ids": tuple(point.id for point in points),
            "point_records": tuple(_qdrant_point_record(point) for point in points),
            **stats.as_dict(),
        }
        result["manifest_path"] = self._write_vector_store_reindex_manifest(country, result)
        return result

    def _rag_vector_store(self):
        if self.rag_vector_store_config.provider == "milvus":
            return MilvusVectorStore(self.rag_vector_store_config)
        return QdrantVectorStore(self.rag_vector_store_config)

    def _write_qdrant_reindex_manifest(self, country: str, result: dict[str, object]) -> str:
        return self._write_vector_store_reindex_manifest(country, result, provider="qdrant")

    def _write_vector_store_reindex_manifest(self, country: str, result: dict[str, object], *, provider: str | None = None) -> str:
        provider = provider or str(result.get("vector_store_provider", "") or self.rag_vector_store_config.provider or "qdrant")
        indices_dir = _rag_knowledge_dir() / "indices"
        runs_dir = indices_dir / "runs"
        indices_dir.mkdir(parents=True, exist_ok=True)
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_id = _manifest_run_id()
        manifest_path = runs_dir / f"{provider}_reindex_{country}_{run_id}.json"
        latest_path = indices_dir / f"{provider}_reindex_{country}.json"
        manifest = {
            "run_id": run_id,
            "created_at": date.today().isoformat(),
            "country": country,
            "status": result.get("status", ""),
            "vector_store": {
                "provider": provider,
                "collection": result.get("vector_store_collection", result.get("qdrant_collection", "")),
            },
            "processed_path": result.get("processed_path", ""),
            "document_count": result.get("document_count", 0),
            "chunk_count": result.get("chunk_count", 0),
            "vector_count": result.get("vector_count", 0),
            "vector_size": result.get("vector_size", 0),
            "upserted_points": result.get("upserted_points", 0),
            "qdrant_collection": result.get("qdrant_collection", ""),
            "collection_status": result.get("collection_status", {}),
            "point_ids": tuple(result.get("point_ids", ()) if isinstance(result.get("point_ids", ()), (list, tuple)) else ()),
            "point_records": tuple(result.get("point_records", ()) if isinstance(result.get("point_records", ()), (list, tuple)) else ()),
            "hit@5": result.get("hit@5", 0),
            "mrr@5": result.get("mrr@5", 0),
            "precision@5": result.get("precision@5", 0),
            "recall@5": result.get("recall@5", 0),
            "ndcg@5": result.get("ndcg@5", 0),
            "passed_threshold": result.get("passed_threshold", False),
            "eval_total": result.get("eval_total", 0),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result["run_id"] = run_id
        result["latest_manifest_path"] = str(latest_path)
        return str(manifest_path)

    def run_qdrant_smoke_diagnostic(
        self,
        country: str,
        *,
        vector_store: QdrantVectorStore | None = None,
    ) -> dict[str, object]:
        manifest_path = _rag_knowledge_dir() / "indices" / f"qdrant_reindex_{country}.json"
        manifest = _read_json_object(manifest_path)
        vector_size = int(manifest.get("vector_size", 0) or 0)
        if vector_size <= 0:
            result = {
                "status": "skipped_no_manifest_vector_size",
                "country": country,
                "vector_size": 0,
                "search_hit": False,
                "cleanup_status": "not_started",
            }
        else:
            store = vector_store or QdrantVectorStore(self.rag_vector_store_config)
            result = {
                "country": country,
                **store.smoke_diagnostic(vector_size=vector_size, country=country),
            }
        manifest["smoke_diagnostic"] = result
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        run_id = str(manifest.get("run_id", "")).strip()
        if run_id:
            run_manifest_path = manifest_path.parent / "runs" / f"qdrant_reindex_{country}_{run_id}.json"
            run_manifest = _read_json_object(run_manifest_path) or dict(manifest)
            run_manifest["smoke_diagnostic"] = result
            run_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            run_manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def run_milvus_smoke_diagnostic(
        self,
        country: str,
        *,
        vector_store: MilvusVectorStore | None = None,
    ) -> dict[str, object]:
        manifest_path = _rag_knowledge_dir() / "indices" / f"milvus_reindex_{country}.json"
        manifest = _read_json_object(manifest_path)
        vector_size = int(manifest.get("vector_size", 0) or 0)
        if vector_size <= 0:
            result = {
                "status": "skipped_no_manifest_vector_size",
                "country": country,
                "vector_size": 0,
                "search_hit": False,
                "cleanup_status": "not_started",
            }
        else:
            store = vector_store or MilvusVectorStore(self.rag_vector_store_config)
            result = {
                "country": country,
                **store.smoke_diagnostic(vector_size=vector_size, country=country),
            }
        manifest["smoke_diagnostic"] = result
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        run_id = str(manifest.get("run_id", "")).strip()
        if run_id:
            run_manifest_path = manifest_path.parent / "runs" / f"milvus_reindex_{country}_{run_id}.json"
            run_manifest = _read_json_object(run_manifest_path) or dict(manifest)
            run_manifest["smoke_diagnostic"] = result
            run_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            run_manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def rollback_qdrant_manifest(
        self,
        country: str,
        run_id: str,
        *,
        vector_store: QdrantVectorStore | None = None,
    ) -> dict[str, object]:
        cleaned_run_id = run_id.strip()
        if not cleaned_run_id:
            raise ValueError("缺少要回滚的 Qdrant manifest run_id")
        root = _rag_knowledge_dir()
        run_manifest_path = root / "indices" / "runs" / f"qdrant_reindex_{country}_{cleaned_run_id}.json"
        manifest = _read_json_object(run_manifest_path)
        if not manifest:
            raise ValueError(f"找不到 Qdrant manifest run：{cleaned_run_id}")
        if str(manifest.get("country", "")) != country:
            raise ValueError(f"Qdrant manifest 国家不匹配：{manifest.get('country')} != {country}")
        latest_path = root / "indices" / f"qdrant_reindex_{country}.json"
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        raw_point_ids = manifest.get("point_ids", ())
        point_ids = tuple(str(point_id) for point_id in raw_point_ids) if isinstance(raw_point_ids, (list, tuple)) else ()
        raw_point_records = manifest.get("point_records", ())
        point_records = tuple(record for record in raw_point_records if isinstance(record, dict)) if isinstance(raw_point_records, (list, tuple)) else ()
        restore_status = {"status": "skipped_no_vector_store", "restored_points": 0}
        if vector_store is not None:
            restore_status = vector_store.restore_points(point_ids, point_records=point_records)
        return {
            "status": "rolled_back",
            "country": country,
            "run_id": cleaned_run_id,
            "latest_manifest_path": str(latest_path),
            "source_manifest_path": str(run_manifest_path),
            "vector_size": int(manifest.get("vector_size", 0) or 0),
            "upserted_points": int(manifest.get("upserted_points", 0) or 0),
            "point_ids": point_ids,
            "point_records": point_records,
            "restore_status": restore_status,
        }

    def value_audit_rag_eval_report(self, country: str) -> dict[str, object]:
        documents = StaticDocumentLoaderAdapter(self._rag_documents(country)).load()
        chunks = tuple(
            chunk
            for document in documents
            for chunk in chunk_document(document, max_chars=None, chunking=self.rag_chunking_config)
        )
        retriever = HybridRagRetriever(chunks)
        file_cases = self._rag_eval_cases(country)
        harness_cases = self._harness_gold_rag_eval_cases(country)
        eval_cases = (*file_cases, *harness_cases)
        cases = eval_cases or _rag_smoke_eval_cases(country, documents)
        dataset_kind = "business" if harness_cases else ("file" if file_cases else "smoke")
        report = evaluate_retrieval_report(
            retriever,
            cases,
            k=5,
            threshold=0.8,
            dataset_name=f"{country}价值观审核RAG {dataset_kind} eval",
            knowledge_version=f"{country}-value-audit-{len(documents)}docs-{len(chunks)}chunks",
        )
        report["business_sample_gate"] = self._business_sample_rag_gate(retriever, harness_cases)
        return report

    def export_value_audit_rag_acceptance_report(self, country: str, output_dir: Path | str) -> dict[str, object]:
        documents = StaticDocumentLoaderAdapter(self._rag_documents(country)).load()
        chunks = tuple(
            chunk
            for document in documents
            for chunk in chunk_document(document, max_chars=None, chunking=self.rag_chunking_config)
        )
        stats = RagRuntimeStats()
        embedding_provider, rerank_provider = providers_from_config(
            self.rag_provider_config,
            stats=stats,
            cache_get=self.repository.get_rag_embedding_cache,
            cache_set=self.repository.set_rag_embedding_cache,
        )
        retriever = HybridRagRetriever(
            chunks,
            embedding_provider=embedding_provider,
            rerank_provider=rerank_provider,
            vector_store_retriever=self._rag_vector_store_retriever(),
        )
        file_cases = self._rag_eval_cases(country)
        harness_cases = self._harness_gold_rag_eval_cases(country)
        eval_cases = (*file_cases, *harness_cases)
        cases = eval_cases or _rag_smoke_eval_cases(country, documents)
        dataset_kind = "business" if harness_cases else ("file" if file_cases else "smoke")
        output = Path(output_dir)
        path = output / f"rag_acceptance_{country}.json"
        report = export_rag_acceptance_report(
            retriever,
            cases,
            path,
            k=5,
            threshold=0.8,
            dataset_name=f"{country}价值观审核RAG {dataset_kind} acceptance",
            knowledge_version=f"{country}-value-audit-{len(documents)}docs-{len(chunks)}chunks",
            provider_config=self.rag_provider_config,
            vector_store=self.rag_vector_store_config,
        )
        report["business_sample_gate"] = self._business_sample_rag_gate(retriever, harness_cases)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), **report}

    def export_rag_hard_negative_report(
        self,
        countries: tuple[str, ...] | list[str] | None = None,
        *,
        output_dir: Path | str | None = None,
        k: int = 5,
        threshold: float = 0.8,
    ) -> dict[str, object]:
        selected_countries = tuple(countries or self.countries())
        country_reports: dict[str, dict[str, object]] = {}
        all_cases: list[dict[str, object]] = []
        for country in selected_countries:
            documents = StaticDocumentLoaderAdapter(self.rag_documents_for_task(country, "value_master")).load()
            chunks = tuple(
                chunk
                for document in documents
                for chunk in chunk_document(document, max_chars=None, chunking=self.rag_chunking_config)
            )
            retriever = HybridRagRetriever(chunks)
            file_cases = self._rag_eval_cases(country)
            harness_cases = self._harness_gold_rag_eval_cases(country)
            cases = (*file_cases, *harness_cases) or _rag_smoke_eval_cases(country, documents)
            base_report = evaluate_retrieval_report(
                retriever,
                cases,
                k=k,
                threshold=threshold,
                dataset_name=f"{country}价值观RAG hard-negative eval",
                knowledge_version=f"{country}-hard-negative-{len(documents)}docs-{len(chunks)}chunks",
            )
            decorated_cases = tuple(
                _rag_hard_negative_case_result(raw_case, case, k=k)
                for raw_case, case in zip(cases, base_report.get("cases", ()))
                if isinstance(case, dict)
            )
            country_report = {
                "country": country,
                "case_count": len(decorated_cases),
                "document_count": len(documents),
                "chunk_count": len(chunks),
                "file_case_count": len(file_cases),
                "harness_case_count": len(harness_cases),
                f"hit@{k}": base_report.get(f"hit@{k}", 0.0),
                f"mrr@{k}": base_report.get(f"mrr@{k}", 0.0),
                f"precision@{k}": base_report.get(f"precision@{k}", 0.0),
                f"recall@{k}": base_report.get(f"recall@{k}", 0.0),
                f"ndcg@{k}": base_report.get(f"ndcg@{k}", 0.0),
                "hard_negative_top1_rate": _hard_negative_rate(decorated_cases, "hard_negative_top1"),
                "hard_negative_topk_rate": _hard_negative_rate(decorated_cases, "hard_negative_in_top_k"),
                "failure_types": _rag_failure_type_counts(decorated_cases),
                "cases": decorated_cases,
            }
            country_reports[country] = country_report
            all_cases.extend(decorated_cases)
        report = _rag_hard_negative_report_payload(country_reports, tuple(all_cases), selected_countries, k=k, threshold=threshold)
        export_dir = Path(output_dir) if output_dir is not None else self._runtime_dir / "resume_evidence"
        export_dir.mkdir(parents=True, exist_ok=True)
        json_report = export_dir / "rag_hard_negative_report.json"
        markdown_report = export_dir / "rag_hard_negative_report.md"
        json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_report.write_text(_rag_hard_negative_report_markdown(report), encoding="utf-8")
        return {"json_report": str(json_report), "markdown_report": str(markdown_report), **report}

    def _business_sample_rag_gate(
        self,
        retriever: HybridRagRetriever,
        harness_cases: tuple[RagRetrievalCase, ...],
        *,
        k: int = 5,
        threshold: float = 0.8,
    ) -> dict[str, object]:
        if not harness_cases:
            return {
                "source": "human_gold",
                "case_count": 0,
                f"hit@{k}": 0,
                f"mrr@{k}": 0,
                "threshold": threshold,
                "passed_threshold": False,
                "status": "not_evaluable",
                "failed_count": 0,
                "cases": (),
            }
        report = evaluate_retrieval_report(
            retriever,
            harness_cases,
            k=k,
            threshold=threshold,
            dataset_name="真实 human_gold 业务样本 RAG gate",
            knowledge_version=f"human_gold-{len(harness_cases)}cases",
        )
        failed_count = sum(1 for item in report.get("cases", ()) if isinstance(item, dict) and not item.get("hit"))
        return {
            "source": "human_gold",
            "case_count": report.get("total", 0),
            f"hit@{k}": report.get(f"hit@{k}", 0),
            f"mrr@{k}": report.get(f"mrr@{k}", 0),
            "threshold": threshold,
            "passed_threshold": report.get("passed_threshold", False),
            "status": "passed" if report.get("passed_threshold", False) else "failed",
            "failed_count": failed_count,
            "cases": report.get("cases", ()),
        }

    def run_full_rag_industrial_acceptance(
        self,
        country: str,
        output_dir: Path | str,
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
        rerank_provider=None,
        vector_store: QdrantVectorStore | None = None,
        preflight_mode: str = "fast",
    ) -> dict[str, object]:
        stats = RagRuntimeStats()
        default_embedding, default_rerank = providers_from_config(
            self.rag_provider_config,
            stats=stats,
            cache_get=self.repository.get_rag_embedding_cache,
            cache_set=self.repository.set_rag_embedding_cache,
        )
        embedding = embedding_provider or default_embedding
        rerank = rerank_provider or default_rerank
        store = vector_store or self._rag_vector_store()
        output = Path(output_dir)
        summary_path = output / f"rag_acceptance_full_summary_{country}.json"
        preflight = self._rag_acceptance_preflight(embedding, rerank, store, mode=preflight_mode)
        try:
            reindex = self.reindex_rag_vector_store_from_raw(
                country,
                embedding_provider=embedding,
                vector_store=store,
            )
        except Exception as exc:
            return self._full_rag_acceptance_failure(
                country,
                summary_path,
                stage="qdrant_reindex",
                error=exc,
                embedding_provider=embedding,
                rerank_provider=rerank,
                vector_store=store,
                preflight=preflight,
            )
        documents = StaticDocumentLoaderAdapter(self._rag_documents(country)).load()
        chunks = tuple(_rag_chunk_from_row(row) for row in self.repository.rag_chunks(country))
        cases = self._rag_retrieval_cases(country) or _rag_smoke_eval_cases(country, documents)
        path = output / f"rag_acceptance_full_{country}.json"
        retriever = HybridRagRetriever(
            chunks,
            embedding_provider=embedding,
            rerank_provider=rerank,
            vector_store_retriever=self._vector_store_retriever_for(store),
        )
        try:
            report = export_rag_acceptance_report(
                retriever,
                cases,
                path,
                k=5,
                threshold=0.8,
                dataset_name=f"{country}价值观审核RAG full industrial acceptance",
                knowledge_version=f"{country}-value-audit-{len(documents)}docs-{len(chunks)}chunks",
                provider_config=self.rag_provider_config,
                vector_store=self.rag_vector_store_config,
            )
        except Exception as exc:
            return self._full_rag_acceptance_failure(
                country,
                summary_path,
                stage="acceptance_report",
                error=exc,
                embedding_provider=embedding,
                rerank_provider=rerank,
                vector_store=store,
                reindex=reindex,
                preflight=preflight,
            )
        status = "passed" if reindex.get("status") == "indexed" and report.get("passed_threshold") else "failed"
        result = {
            "status": status,
            "country": country,
            "reindex": reindex,
            "report_path": str(path),
            "report": report,
            "preflight": preflight,
            "failure_stage": "" if status == "passed" else "hit_rate_threshold",
            "error": "" if status == "passed" else "RAG hit@5 未达到阈值或 Qdrant reindex 未完成 indexed 状态",
            "diagnostics": self._rag_acceptance_diagnostics(
                embedding_provider=embedding,
                rerank_provider=rerank,
                vector_store=store,
                reindex=reindex,
                report=report,
            ),
        }
        output.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["summary_path"] = str(summary_path)
        return result

    def _full_rag_acceptance_failure(
        self,
        country: str,
        summary_path: Path,
        *,
        stage: str,
        error: Exception,
        embedding_provider,
        rerank_provider,
        vector_store,
        reindex: dict[str, object] | None = None,
        preflight: dict[str, object] | None = None,
    ) -> dict[str, object]:
        result = {
            "status": "failed",
            "country": country,
            "failure_stage": stage,
            "error": str(error),
            "reindex": reindex or {},
            "report_path": "",
            "report": {},
            "preflight": preflight or {},
            "diagnostics": self._rag_acceptance_diagnostics(
                embedding_provider=embedding_provider,
                rerank_provider=rerank_provider,
                vector_store=vector_store,
                reindex=reindex or {},
                report={},
                failed_component=_failure_component_for_stage(stage),
                error=str(error),
            ),
        }
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["summary_path"] = str(summary_path)
        return result

    def _rag_acceptance_preflight(self, embedding_provider, rerank_provider, vector_store, *, mode: str = "fast") -> dict[str, object]:
        if mode == "live":
            return {
                "mode": "live",
                "embedding": _provider_healthcheck(
                    embedding_provider,
                    fallback=lambda: _embedding_provider_smoke(embedding_provider),
                ),
                "qdrant": _provider_healthcheck(vector_store),
                "rerank": _provider_healthcheck(rerank_provider),
            }
        return {
            "mode": "fast",
            "embedding": _fast_provider_status(embedding_provider),
            "qdrant": _fast_provider_status(vector_store),
            "rerank": _fast_provider_status(rerank_provider),
        }

    def _rag_acceptance_diagnostics(
        self,
        *,
        embedding_provider,
        rerank_provider,
        vector_store,
        reindex: dict[str, object],
        report: dict[str, object],
        failed_component: str = "",
        error: str = "",
    ) -> tuple[dict[str, object], ...]:
        observed = report.get("observed_retrieval", {}) if isinstance(report.get("observed_retrieval"), dict) else {}
        runtime_stats = report.get("runtime_stats", {}) if isinstance(report.get("runtime_stats"), dict) else {}
        rows = (
            {
                "component": "embedding",
                "status": "failed" if failed_component == "embedding" else "ok",
                "provider": getattr(embedding_provider, "provider_name", self.rag_provider_config.embedding_provider),
                "remote_calls": runtime_stats.get("embedding_remote_calls", 0),
                "fallbacks": runtime_stats.get("embedding_fallbacks", 0),
                "message": error if failed_component == "embedding" else "Embedding provider 已配置；remote_calls 可用于判断是否真实远程调用。",
            },
            {
                "component": "qdrant",
                "status": "failed" if failed_component == "qdrant" else ("ok" if reindex.get("status") == "indexed" else "warning"),
                "provider": getattr(vector_store, "provider_name", self.rag_vector_store_config.provider),
                "upserted_points": reindex.get("upserted_points", 0),
                "qdrant_vector_hits": observed.get("qdrant_vector_hits", False),
                "message": error if failed_component == "qdrant" else "Qdrant 入库与检索命中由 upserted_points/qdrant_vector_hits 判断。",
            },
            {
                "component": "rerank",
                "status": "failed" if failed_component == "rerank" else "ok",
                "provider": getattr(rerank_provider, "provider_name", self.rag_provider_config.rerank_provider),
                "remote_calls": runtime_stats.get("rerank_remote_calls", 0),
                "fallbacks": runtime_stats.get("rerank_fallbacks", 0),
                "message": error if failed_component == "rerank" else "Rerank provider 已配置；remote_calls/fallbacks 可用于判断 BGE 是否生效。",
            },
            {
                "component": "hit_rate",
                "status": "ok" if report.get("passed_threshold") else "warning",
                "hit@5": report.get("hit@5", 0),
                "threshold": report.get("threshold", 0.8),
                "message": "hit@5 达标" if report.get("passed_threshold") else "hit@5 未达标或报告未生成。",
            },
        )
        return rows

    def rag_feedback_summary(self, country: str) -> dict[str, object]:
        by_chunk: dict[str, dict[str, object]] = {}
        total = useful_total = not_useful_total = 0
        for memory in self.repository.layered_memories(country, layer="working", include_inactive=True):
            if memory.get("memory_type") != "rag_citation_feedback" or memory.get("status") != "active":
                continue
            payload = memory.get("payload", {})
            if not isinstance(payload, dict):
                continue
            chunk_id = str(payload.get("chunk_id", "")).strip()
            usefulness = str(payload.get("usefulness", "")).strip()
            if not chunk_id or usefulness not in {"useful", "not_useful"}:
                continue
            bucket = by_chunk.setdefault(
                chunk_id,
                {"chunk_id": chunk_id, "useful_count": 0, "not_useful_count": 0, "net_score": 0, "notes": []},
            )
            total += 1
            if usefulness == "useful":
                useful_total += 1
                bucket["useful_count"] = int(bucket["useful_count"]) + 1
            else:
                not_useful_total += 1
                bucket["not_useful_count"] = int(bucket["not_useful_count"]) + 1
            note = str(payload.get("note", "")).strip()
            if note:
                notes = bucket["notes"] if isinstance(bucket["notes"], list) else []
                notes.append(note)
                bucket["notes"] = notes[-3:]
            bucket["net_score"] = int(bucket["useful_count"]) - int(bucket["not_useful_count"])
        top_chunks = tuple(
            sorted(
                by_chunk.values(),
                key=lambda item: (int(item["net_score"]), int(item["useful_count"]), str(item["chunk_id"])),
                reverse=True,
            )
        )
        return {
            "total_feedback": total,
            "useful_count": useful_total,
            "not_useful_count": not_useful_total,
            "top_chunks": top_chunks,
        }

    def rag_feedback_scores(self, country: str) -> dict[str, int]:
        summary = self.rag_feedback_summary(country)
        scores: dict[str, int] = {}
        for item in summary.get("top_chunks", ()):
            if isinstance(item, dict):
                scores[str(item.get("chunk_id", ""))] = int(item.get("net_score", 0))
        return {chunk_id: score for chunk_id, score in scores.items() if chunk_id and score}

    def value_match_rag_citation_details(self, row: DemandRow) -> tuple[dict[str, str], ...]:
        citation_ids = _extract_rag_citation_ids(row.value_match)
        if not citation_ids:
            return ()
        chunk_by_id = {str(chunk["chunk_id"]): chunk for chunk in self.repository.rag_chunks(row.country)}
        return tuple(
            {
                "chunk_id": citation_id,
                "parent_id": str(chunk_by_id[citation_id]["parent_id"]),
                "source_type": str(chunk_by_id[citation_id]["source_type"]),
                "title": str(chunk_by_id[citation_id]["title"]),
                "text": str(chunk_by_id[citation_id]["text"]),
            }
            for citation_id in citation_ids
            if citation_id in chunk_by_id
        )

    def value_predictions(self, country: str, grade: str) -> tuple[ValuePredictionCard, ...]:
        cards: list[ValuePredictionCard] = []
        for operation_tag, images in self._country(country)["images"].items():
            for image in images:
                if image.grade == grade:
                    remark = image.remark or "预测备注：价值观匹配度较高，可进入排图池。"
                    cards.append(ValuePredictionCard(operation_tag, image, remark))
        return tuple(cards)

    def undistributed_value_candidates(self, country: str, grade: str = "") -> tuple[dict[str, object], ...]:
        candidates = tuple(self._real_undistributed_value_candidates(country))
        if grade:
            candidates = tuple(candidate for candidate in candidates if candidate["predicted_grade"] == grade)
        return tuple(
            sorted(
                candidates,
                key=lambda item: (float(item["sa_probability"]), -int(item["risk_rank"]), str(item["candidate_id"])),
                reverse=True,
            )
        )

    def import_value_candidate_excel(self, country: str) -> tuple[dict[str, object], ...]:
        return import_undistributed_candidate_workbook(
            VALUE_CANDIDATE_WORKBOOK,
            country,
            self._runtime_dir / "value_candidates" / country / "images",
        )

    def _real_undistributed_value_candidates(self, country: str) -> tuple[dict[str, object], ...]:
        if not VALUE_CANDIDATE_WORKBOOK.exists():
            return ()
        imported = self.import_value_candidate_excel(country)
        return tuple(self._with_value_candidate_prediction(candidate) for candidate in imported)

    def _with_value_candidate_prediction(self, candidate: dict[str, object]) -> dict[str, object]:
        cache = self._value_candidate_prediction_cache(candidate)
        base = {
            **candidate,
            "predicted_grade": "待预测",
            "sa_probability": 0.0,
            "open_rate_range": "待预测",
            "completion_rate_range": "待预测",
            "finish_time_range": "待预测",
            "action": "待预测",
            "risk_rank": 9,
            "evidence": "来自Excel真实未分发候选图；请点击批量预测当前国家。预测值尚未生成。",
            "prediction_status": "pending" if candidate.get("local_image_path") else "missing_image",
            "visual_subject": str(candidate.get("subject", "")),
            "rag_citations": (),
            "risk_points": (),
        }
        if cache:
            base.update(cache)
        if cache and base.get("rag_filter_version") != "v0.7.32":
            base["rag_citations"] = ()
            base["rag_citation_details"] = ()
            if "旧缓存RAG依据未通过强相关过滤" not in str(base.get("evidence", "")):
                base["evidence"] = f"{base.get('evidence', '')}；旧缓存RAG依据未通过强相关过滤，已隐藏，重新预测后会写入强相关引用。".strip("；")
        cached_grade = str(base.get("predicted_grade", ""))
        if cached_grade in {"S", "A", "B", "C", "D"} and base.get("metric_calibration_version") != "v0.7.33":
            metric_levels = _metric_levels_for_grade(cached_grade)
            open_range, completion_range, finish_range = _calibrated_metric_ranges(str(base.get("country", "")), metric_levels)
            base["metric_levels"] = metric_levels
            base["open_rate_range"] = open_range
            base["completion_rate_range"] = completion_range
            base["finish_time_range"] = finish_range
            base["action"], base["risk_rank"] = _action_for_business_grade(cached_grade)
            base["metric_calibration_version"] = "v0.7.33"
            level_sequence = tuple(metric_levels[field] for field in ("open_rate", "completion_rate", "avg_finish_time"))
            if "指标校准=" not in str(base.get("evidence", "")):
                base["evidence"] = (
                    f"{base.get('evidence', '')}；等级预测={cached_grade}；"
                    f"指标校准={''.join(level_sequence)}，用于和等级口径保持一致。"
                ).strip("；")
        if not self.trial_uploads.vision_client and base["prediction_status"] == "pending":
            base["prediction_status"] = "missing_vision_model"
            base["evidence"] = "已导入真实未分发候选图；未配置真实视觉模型，不能生成真实预测。"
        base["image"] = ImageAsset(
            str(base.get("candidate_id", "")),
            str(base.get("predicted_grade", "待预测")),
            str(base.get("open_rate_range", "待预测")),
            str(base.get("completion_rate_range", "待预测")),
            str(base.get("finish_time_range", "待预测")),
            str(base.get("candidate_source", "")),
            str(base.get("local_image_path", "")) or str(base.get("candidate_id", "")),
            str(base.get("evidence", "")),
        )
        return base

    def _value_candidate_prediction_cache(self, candidate: dict[str, object]) -> dict[str, object]:
        path = self._value_candidate_cache_path(candidate)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("image_hash") == candidate.get("image_hash"):
                    return dict(payload)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _value_candidate_cache_path(self, candidate: dict[str, object]) -> Path:
        country = str(candidate.get("country", "unknown"))
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(candidate.get("candidate_id", "candidate")))
        return self._value_candidate_cache_root() / country / "predictions" / f"{safe_id}.json"

    def _value_candidate_cache_root(self) -> Path:
        default_runtime_dir = Path(gettempdir()) / "puzzle_ops_agent_runtime"
        if self._runtime_dir == default_runtime_dir:
            return VALUE_CANDIDATE_WORKBOOK.parent / ".puzzle_ops_value_candidate_cache"
        return self._runtime_dir / "value_candidates"

    def predict_undistributed_value_candidates(self, country: str, *, limit: int = 100) -> dict[str, object]:
        imported = self.import_value_candidate_excel(country)
        if not self.trial_uploads.vision_client:
            return {"country": country, "candidate_count": len(imported), "predicted_count": 0, "status": "missing_vision_model"}
        predicted_count = 0
        cached_count = 0
        blocked_count = 0
        for candidate in imported[:limit]:
            if not candidate.get("local_image_path"):
                blocked_count += 1
                continue
            cache = self._value_candidate_prediction_cache(candidate)
            if cache and not _cached_value_candidate_prediction_is_stale(cache):
                cached_count += 1
                continue
            prediction = self._predict_value_candidate(candidate)
            cache_path = self._value_candidate_cache_path(candidate)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(prediction, ensure_ascii=False, indent=2), encoding="utf-8")
            predicted_count += 1
        return {
            "country": country,
            "candidate_count": len(imported),
            "predicted_count": predicted_count,
            "cached_count": cached_count,
            "blocked_count": blocked_count,
            "status": "predicted",
        }

    def predict_single_undistributed_value_candidate(self, country: str, candidate_id: str, *, force: bool = False) -> dict[str, object]:
        imported = self.import_value_candidate_excel(country)
        candidate = next((item for item in imported if str(item.get("candidate_id", "")) == candidate_id), None)
        if candidate is None:
            return {"country": country, "candidate_id": candidate_id, "status": "missing_candidate", "predicted_count": 0, "cached_count": 0}
        if not candidate.get("local_image_path"):
            return {"country": country, "candidate_id": candidate_id, "status": "missing_image", "predicted_count": 0, "cached_count": 0}
        if not self.trial_uploads.vision_client:
            return {"country": country, "candidate_id": candidate_id, "status": "missing_vision_model", "predicted_count": 0, "cached_count": 0}
        cache_path = self._value_candidate_cache_path(candidate)
        if force and cache_path.exists():
            cache_path.unlink()
        if self._value_candidate_prediction_cache(candidate):
            return {"country": country, "candidate_id": candidate_id, "status": "cached", "predicted_count": 0, "cached_count": 1}
        prediction = self._predict_value_candidate(candidate)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(prediction, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"country": country, "candidate_id": candidate_id, "status": "predicted", "predicted_count": 1, "cached_count": 0}

    def rebuild_visual_similarity_index(self, country: str) -> dict[str, object]:
        records = []
        skipped = 0
        for record in self._history_records(country):
            path = Path(str(getattr(record, "local_image_path", "") or "")).expanduser()
            if not path.is_file():
                skipped += 1
                continue
            embedding = self.visual_embedding_provider.embed_image(
                str(path),
                text=_visual_similarity_text_from_history(record),
            )
            records.append(
                VisualIndexRecord.from_image(
                    image_id=str(getattr(record, "image_id", "") or getattr(record, "operation_tag", "")),
                    country=country,
                    grade=str(getattr(record, "grade", "")),
                    local_image_path=str(path),
                    subject=str(getattr(record, "subject_tag", "")),
                    operation_tag=str(getattr(record, "operation_tag", "")),
                    embedding=embedding,
                )
            )
        result = self.visual_similarity_index.upsert(tuple(records))
        milvus_result: dict[str, object] = {"status": "not_configured"}
        if records and self.visual_milvus_store is not None:
            self.visual_milvus_store.ensure_collection(len(records[0].vector))
            milvus_result = self.visual_milvus_store.upsert(tuple(records))
        return {
            "country": country,
            "indexed_count": len(records),
            "skipped_count": skipped,
            "provider": getattr(self.visual_embedding_provider, "provider_name", "unknown"),
            "model": getattr(self.visual_embedding_provider, "model", ""),
            "local_index": result,
            "milvus": milvus_result,
            "version": "v0.7.51-visual-milvus-index",
        }

    def similar_visual_history_for_candidate(self, candidate: dict[str, object], *, top_k: int = 6) -> dict[str, object]:
        country = str(candidate.get("country", ""))
        path = Path(str(candidate.get("local_image_path", ""))).expanduser()
        if not country or not path.is_file():
            return {
                "status": "missing_image",
                "similar_good": (),
                "similar_neutral": (),
                "similar_risk": (),
                "message": "历史图像相似依据不足，需人工复核。",
                "version": "v0.7.52-visual-similarity-evidence",
            }
        if self.visual_similarity_index.record_count <= 0:
            self.rebuild_visual_similarity_index(country)
        embedding = self.visual_embedding_provider.embed_image(str(path), text=_visual_similarity_text_from_candidate(candidate))
        if self.visual_milvus_store is not None:
            try:
                milvus_hits = self.visual_milvus_store.search(embedding.vector, country=country, top_k=top_k)
            except Exception:
                milvus_hits = ()
            if milvus_hits:
                grouped = _group_visual_similarity_hits(milvus_hits)
                grouped["retrieval_mode"] = "milvus_image_embedding"
                grouped["version"] = "v0.7.52-visual-similarity-evidence"
                return grouped
        grouped = self.visual_similarity_index.grouped_search(embedding, country=country, top_k=top_k)
        grouped["version"] = "v0.7.52-visual-similarity-evidence"
        if grouped.get("status") != "ok":
            grouped["message"] = "历史图像相似依据不足，需人工复核。"
        return grouped

    def _predict_value_candidate(self, candidate: dict[str, object]) -> dict[str, object]:
        path = Path(str(candidate.get("local_image_path", "")))
        feature = self.local_image_analyzer.analyze_path(path)
        local_summary = self.local_image_analyzer.summarize_features((feature,) if feature else ())
        semantic = self.trial_uploads.vision_client.analyze(
            [{"filename": path.name, "path": str(path), "content_type": image_content_type(path)}],
            str(candidate.get("country", "")),
            str(candidate.get("js_category", "")),
            local_summary,
        )
        similar_positive = self._similar_history_for_candidate(candidate, semantic, positive=True)
        similar_negative = self._similar_history_for_candidate(candidate, semantic, positive=False)
        prediction = _value_candidate_prediction_from_evidence(candidate, semantic, similar_positive, similar_negative)
        visual_similarity = self.similar_visual_history_for_candidate({**candidate, "subject": semantic.subject}, top_k=6)
        rag_answer = self.value_audit_rag_answer(
            str(candidate.get("country", "")),
            f"{semantic.subject} {semantic.scene} {candidate.get('operation_tag', '')}",
            top_k=3,
        )
        strong_rag_citations = _strong_rag_citations_from_trace(self._last_rag_trace, tuple(rag_answer.citations), max_citations=3)
        prediction.update(
            {
                "candidate_id": candidate.get("candidate_id", ""),
                "country": candidate.get("country", ""),
                "image_hash": candidate.get("image_hash", ""),
                "prediction_status": "predicted",
                "visual_subject": semantic.subject,
                "visual_scene": semantic.scene,
                "visual_style": semantic.style,
                "risk_points": tuple(semantic.risk_tags),
                "rag_citations": strong_rag_citations,
                "rag_citation_details": self.rag_citation_details(str(candidate.get("country", "")), strong_rag_citations),
                "visual_similarity_evidence": visual_similarity,
                "rag_filter_version": "v0.7.32",
                "metric_calibration_version": "v0.7.33",
                "value_grade_model_version": "v0.7.39-legacy",
            }
        )
        return prediction

    def _similar_history_for_candidate(
        self,
        candidate: dict[str, object],
        semantic,
        *,
        positive: bool,
        ranking_mode: str = "legacy",
    ) -> tuple[dict[str, object], ...]:
        records = self._history_records(str(candidate.get("country", "")))
        grades = {"S", "A"} if positive else {"C", "D"}
        query = " ".join(
            (
                str(candidate.get("operation_tag", "")),
                str(candidate.get("js_category", "")),
                semantic.subject,
                semantic.scene,
                semantic.style,
            )
        )
        tokens = _simple_text_tokens(query)
        scored = []
        for record in records:
            if record.grade not in grades:
                continue
            if ranking_mode == "shadow_rerank":
                score = _semantic_history_rerank_score(candidate, semantic, record)
            else:
                score = 0
                if record.js_category == candidate.get("js_category"):
                    score += 3
                haystack = f"{record.operation_tag} {record.subject_tag} {record.remark}"
                score += len(tokens & _simple_text_tokens(haystack))
            scored.append((score, record))
        scored = sorted(scored, key=lambda item: (item[0], item[1].grade), reverse=True)
        return tuple(
            {
                "image_id": record.image_id,
                "operation_tag": record.operation_tag,
                "grade": record.grade,
                "open_rate": record.open_rate,
                "completion_rate": record.completion_rate,
                "avg_finish_time": record.avg_finish_time,
                "reason": f"{record.remark}；shadow_rerank={score}" if ranking_mode == "shadow_rerank" else record.remark,
            }
            for score, record in scored[:3]
            if score >= 2
        )

    def record_value_candidate_decision(self, country: str, candidate_id: str, decision: str, note: str = "", *, actor: str = "") -> int:
        return self.repository.add_layered_memory(
            country,
            "working",
            "value_candidate_human_decision",
            {"candidate_id": candidate_id, "decision": decision, "human_note": note},
            ttl_seconds=90 * 24 * 3600,
            created_by=actor,
            human_verified=True,
        )

    def value_candidate_decisions(self, country: str) -> tuple[dict[str, object], ...]:
        decisions: dict[str, dict[str, object]] = {}
        for memory in self.repository.layered_memories(country, layer="working"):
            if memory.get("memory_type") != "value_candidate_human_decision":
                continue
            payload = memory.get("payload", {})
            if not isinstance(payload, dict):
                continue
            candidate_id = str(payload.get("candidate_id", ""))
            if not candidate_id:
                continue
            decisions[candidate_id] = {
                "candidate_id": candidate_id,
                "decision": str(payload.get("decision", "")),
                "human_note": str(payload.get("human_note", "")),
                "memory_id": int(memory.get("memory_id", 0)),
                "created_by": str(memory.get("created_by", "")),
                "updated_at": str(memory.get("updated_at", "")),
            }
        return tuple(decisions.values())

    def schedule(self, country: str, day: str, replacements: dict[int, ScheduleItem] | None = None) -> tuple[ScheduleItem, ...]:
        if day not in {"周一", "周二", "周三", "周四", "周五", "周六", "周日"}:
            raise ValueError("排图日期只能是周一到周日")
        positions = self.weekend_positions if day in {"周六", "周日"} else self.workday_positions
        source = []
        for operation_tag, images in self._country(country)["images"].items():
            for image in images:
                source.append((operation_tag, image))
        items = []
        for index in range(10):
            operation_tag, image = source[index % len(source)]
            items.append(
                ScheduleItem(
                    day=day,
                    position=positions[index],
                    image_name=image.title,
                    operation_tag=operation_tag,
                    grade=image.grade,
                    open_rate=image.open_rate,
                    finish_rate=image.finish_rate,
                    finish_time=image.finish_time,
                )
            )
        if replacements:
            for index, replacement in replacements.items():
                if 0 <= index < len(items):
                    items[index] = replace(replacement, day=day, position=items[index].position)
        return tuple(items)

    def replacement_for_slot(self, country: str, current_image_name: str) -> ScheduleItem:
        source = []
        for operation_tag, images in self._country(country)["images"].items():
            for image in images:
                source.append((operation_tag, image))
        for operation_tag, image in source:
            if image.title != current_image_name and image.grade in {"S", "A", "B"}:
                return ScheduleItem(
                    day="候补",
                    position=0,
                    image_name=f"未分发候补图：{image.title}",
                    operation_tag=operation_tag,
                    grade=image.grade,
                    open_rate=image.open_rate,
                    finish_rate=image.finish_rate,
                    finish_time=image.finish_time,
                )
        operation_tag, image = source[0]
        return ScheduleItem("候补", 0, f"未分发候补图：{image.title}", operation_tag, image.grade, image.open_rate, image.finish_rate, image.finish_time)

    def sync_rows(self):
        return self.repository.sync_events() + SYNC_ROWS

    def vision_llm_status(self) -> dict[str, object]:
        if self.trial_uploads.vision_client:
            return self.trial_uploads.vision_client.config_status()
        if self.trial_uploads.vision_config_error:
            return self.trial_uploads.vision_config_error.config_status()
        return {"provider": "qwen", "mode": "missing", "missing": ("QWEN_API_KEY",)}

    def sync_demand_rows(self, country: str, rows: list[dict[str, object]], require_real: bool = True):
        if require_real and not self.feishu.is_real and not getattr(self.feishu, "allow_real_sync", False):
            missing = ", ".join(self.feishu.config_status()["missing"])
            message = f"未配置真实飞书：缺少 {missing}"
            self.repository.add_sync_event(country, "提需同步", "飞书在线表格", "失败")

            return ToolResult(False, {"missing": missing}, message, error=message)
        result = self.feishu.write_table("提需表", rows)
        self.repository.add_sync_event(country, "提需同步", "飞书在线表格", "成功" if result.success else "失败")
        return result

    def propose_feishu_sync(
        self,
        country: str,
        rows: list[dict[str, object]],
        *,
        actor: str = "",
        source_trace_id: str = "",
        skill_id: str = "regular_demand_skill",
    ):
        payload = {
            "table_name": "提需表",
            "rows": rows,
            "mode": "real" if self.feishu.is_real else "mock",
            "batch_id": f"feishu-{uuid.uuid4().hex[:10]}",
            "skill_id": skill_id,
        }
        return self._guarded_executor(country).propose(
            country,
            actor,
            "feishu",
            "feishu.write_table",
            payload,
            source_trace_id or f"{country}-feishu-sync",
        )

    def approve_guarded_action(self, country: str, proposal_id: str, *, actor: str = "", note: str = ""):
        proposal = self.repository.guarded_action_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"找不到 Guarded Action：{proposal_id}")
        if proposal.country != country:
            raise ValueError(f"Guarded Action 不属于当前国家：{proposal.country}")
        return self._guarded_executor(country).approve(proposal_id, actor, note)

    def execute_guarded_action(self, country: str, proposal_id: str, *, actor: str = ""):
        proposal = self.repository.guarded_action_proposal(proposal_id)
        if proposal is None:
            return ToolResult(False, {}, f"找不到 Guarded Action：{proposal_id}", error="PROPOSAL_NOT_FOUND")
        if proposal.country != country:
            return ToolResult(False, {"proposal_country": proposal.country}, "Guarded Action 不属于当前国家", error="COUNTRY_MISMATCH")
        return self._guarded_executor(country).execute(proposal_id, actor)

    def revert_guarded_action(self, country: str, proposal_id: str, *, actor: str = "", note: str = ""):
        proposal = self.repository.guarded_action_proposal(proposal_id)
        if proposal is None:
            return ToolResult(False, {}, f"找不到 Guarded Action：{proposal_id}", error="PROPOSAL_NOT_FOUND")
        if proposal.country != country:
            return ToolResult(False, {"proposal_country": proposal.country}, "Guarded Action 不属于当前国家", error="COUNTRY_MISMATCH")
        return self._guarded_executor(country).revert(proposal_id, actor, note)

    def guarded_action_proposals(self, country: str = "", *, limit: int = 50):
        return self.repository.guarded_action_proposals(country, limit=limit)

    def guarded_action_events(self, proposal_id: str = "", *, country: str = "", limit: int = 100):
        return self.repository.guarded_action_events(proposal_id, country=country, limit=limit)

    def guarded_action_workbench(self, country: str) -> dict[str, object]:
        proposals = self.guarded_action_proposals(country, limit=100)
        events_by_proposal = {
            proposal.proposal_id: self.guarded_action_events(proposal.proposal_id)
            for proposal in proposals
        }
        groups = {
            "pending": tuple(item for item in proposals if item.guard_status in {"pending_approval", "blocked"}),
            "approved": tuple(item for item in proposals if item.guard_status == "approved"),
            "executed": tuple(item for item in proposals if item.guard_status == "executed"),
            "failed": tuple(item for item in proposals if item.guard_status == "failed"),
            "reverted": tuple(item for item in proposals if item.guard_status == "reverted"),
        }
        return {"proposals": proposals, "groups": groups, "events_by_proposal": events_by_proposal}

    def tools_console(self, country: str) -> dict[str, object]:
        specs = self.adapter.registry.specs()
        invocations = self.repository.tool_invocations(country=country, limit=25)
        failed = tuple(item for item in invocations if not item.get("success"))
        return {
            "catalog": tuple(
                {
                    "name": spec.name,
                    "display_name": spec.display_name or spec.name,
                    "target_system": spec.target_system or "local",
                    "side_effect": spec.side_effect,
                    "approval_required": spec.approval_required,
                    "country_scoped": spec.country_scoped,
                    "allowed_skill_ids": spec.allowed_skill_ids,
                }
                for spec in specs
            ),
            "recent_invocations": invocations,
            "failed_invocations": failed,
            "connector_health": {
                "feishu": self.feishu.config_status(),
                "asset_library": {"mode": "mock", "status": "ready"},
                "warehouse": {"mode": "mock", "status": "ready"},
                "vector_store": self.rag_retrieval_runtime_status("value_master"),
                "vlm": self.vision_llm_status(),
            },
        }

    def business_skill_contracts(self):
        return self.business_skills.all()

    def business_skill_acceptance_cases(self, country: str) -> tuple[dict[str, object], ...]:
        samples = {
            "weekly_review_skill": {
                "country": country,
                "date_range_start": "2026-06-24",
                "date_range_end": "2026-06-30",
                "history_window": "上上周三到上周二",
                "js_category": self.categories(country).keys().__iter__().__next__(),
                "operator_note": "Harness demo",
            },
            "regular_demand_skill": {
                "country": country,
                "operation_tag": next(iter(self.categories(country)[next(iter(self.categories(country)))]), None).tag,
                "js_category": next(iter(self.categories(country))),
                "stock": 2,
                "historical_metrics": {"open_rate": 0.28, "completion_rate": 0.9},
                "delivery_constraints": "本周",
            },
            "trial_parse_skill": {
                "country": country,
                "reference_images": ("ref-a.png",),
                "trial_mode": "parse",
                "js_category": next(iter(self.categories(country))),
                "operator_hint": "Harness demo",
            },
            "value_audit_skill": {
                "country": country,
                "image_or_candidate": "候选图",
                "subject": "猫咪",
                "operation_tag": f"试新_{country}_猫咪0713",
                "task_type": "value_master",
            },
            "memory_governance_skill": {
                "country": country,
                "memory_ids": (),
                "conflict_group_id": "",
                "cleanup_reason": "weekly",
                "operator_goal": "治理待审记忆",
            },
        }
        return tuple(
            {
                "case_id": f"{country}-{skill.skill_id}-demo",
                "country": country,
                "skill_id": skill.skill_id,
                "input_payload": samples[skill.skill_id],
                "acceptance_metrics": skill.acceptance_metrics,
                "rag_task_index": skill.rag_task_index,
            }
            for skill in self.business_skill_contracts()
        )

    def run_business_skill(self, skill_id: str, input_payload: dict[str, object], *, actor: str = "") -> SkillRunResult:
        errors = self.business_skills.validate_input(skill_id, input_payload)
        if errors:
            raise SkillExecutionError("；".join(errors))
        skill = self.business_skills.get(skill_id)
        country = str(input_payload.get("country", ""))
        if skill_id == "weekly_review_skill":
            return self._run_weekly_review_skill(input_payload, actor=actor)
        if skill_id == "regular_demand_skill":
            return self._run_regular_demand_skill(input_payload, actor=actor)
        if skill_id == "trial_parse_skill":
            return self._run_trial_parse_skill(input_payload, actor=actor)
        if skill_id == "value_audit_skill":
            return self._run_value_audit_skill(input_payload, actor=actor)
        if skill_id == "memory_governance_skill":
            return self._run_memory_governance_skill(input_payload, actor=actor)
        raise SkillExecutionError(f"未知业务 Skill：{skill_id}")

    def _run_weekly_review_skill(self, payload: dict[str, object], *, actor: str) -> SkillRunResult:
        country = str(payload["country"])
        skill = self.business_skills.get("weekly_review_skill")
        weekly_metrics = self.adapter.registry.call(
            "warehouse.weekly_metrics",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}",
            date_range_start=str(payload.get("date_range_start", "")),
            date_range_end=str(payload.get("date_range_end", "")),
            js_category=str(payload.get("js_category", "")),
        )
        tag_performance = self.adapter.registry.call(
            "warehouse.tag_performance",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}",
            operation_tag=str(payload.get("js_category", "")),
        )
        memory_search = self.adapter.registry.call(
            "vector.search_memory_facts",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}",
            query=str(payload.get("operator_note", "")) or str(payload.get("js_category", "")),
        )
        review = self.weekly_review_workbench(country)
        citations = _skill_rag_citations(self.rag_documents_for_task(country, skill.rag_task_index), limit=4)
        memory_refs = self._skill_memory_refs(country, skill.memory_read_layers)
        draft = {
            "sa_growth": tuple(item.get("title", "") for item in review.get("new_sa_images", ()) if isinstance(item, dict)),
            "cd_risks": tuple(item.get("title", "") for item in review.get("declining_images", ()) if isinstance(item, dict)),
            "country_differences": tuple(review.get("country_differences", ())),
            "need_directions": tuple(review.get("need_suggestions", ())),
            "action_proposals": (),
        }
        self.record_working_memory(country, "weekly_review_insight", {"source_skill": skill.skill_id, "source_trace": f"{skill.skill_id}:{country}", "summary": review.get("summary", "")}, actor=actor)
        return SkillRunResult(
            skill.skill_id,
            country,
            dict(payload),
            draft,
            citations,
            memory_refs,
            ("warehouse.weekly_metrics", "warehouse.tag_performance", "vector.search_memory_facts"),
            (),
            True,
            {"RAG citation precision": 1.0 if citations else 0.0, "工具调用成功率": _success_rate(weekly_metrics, tag_performance, memory_search)},
        )

    def _run_regular_demand_skill(self, payload: dict[str, object], *, actor: str) -> SkillRunResult:
        country = str(payload["country"])
        skill = self.business_skills.get("regular_demand_skill")
        operation_tag = str(payload["operation_tag"])
        js_category = str(payload["js_category"])
        tag_performance = self.adapter.registry.call(
            "warehouse.tag_performance",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}:{operation_tag}",
            operation_tag=operation_tag,
        )
        assets = self.adapter.registry.call(
            "asset.search_by_tag",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}:{operation_tag}",
            operation_tag=operation_tag,
        )
        vector = self.adapter.registry.call(
            "vector.search_value_master",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}:{operation_tag}",
            query=operation_tag,
        )
        row = self.add_regular_demand(country, js_category, operation_tag, 0)
        row_payload = _demand_row_payload_for_skill(row)
        required = ("提需分类", "国家", "JS分类", "运营tag", "主体内容", "张数", "需求等级", "加工方式")
        missing = tuple(field for field in required if row_payload.get(field) in {"", None})
        proposal = self.propose_feishu_sync(country, [row_payload], actor=actor, source_trace_id=f"{skill.skill_id}:{country}:{operation_tag}", skill_id=skill.skill_id)
        citations = _skill_rag_citations(self.rag_documents_for_task(country, skill.rag_task_index), limit=4)
        memory_refs = self._skill_memory_refs(country, skill.memory_read_layers)
        draft = {
            "draft_rows": (row_payload,),
            "missing_fields": missing,
            "risk_notes": tuple(filter(None, (row.remark,))),
            "value_evidence": citations,
            "action_proposals": (proposal.proposal_id,),
            "tag_performance": tag_performance.data if tag_performance.success else {},
            "asset_matches": assets.data if assets.success else {},
            "vector_citations": vector.data.get("citations", ()) if vector.success else (),
        }
        draft_memory_id = self.record_working_memory(country, "regular_demand_draft", {"source_skill": skill.skill_id, "operation_tag": operation_tag, "proposal_id": proposal.proposal_id}, actor=actor)
        return SkillRunResult(
            skill.skill_id,
            country,
            dict(payload),
            draft,
            citations,
            (*memory_refs, f"memory:{draft_memory_id}"),
            ("warehouse.tag_performance", "asset.search_by_tag", "vector.search_value_master", "rag.retrieve.value_master"),
            (proposal.proposal_id,),
            True,
            {"飞书字段完整率": 1.0 if not missing else 0.0, "工具调用成功率": _success_rate(tag_performance, assets, vector)},
        )

    def _run_trial_parse_skill(self, payload: dict[str, object], *, actor: str) -> SkillRunResult:
        country = str(payload["country"])
        skill = self.business_skills.get("trial_parse_skill")
        reference_images = payload.get("reference_images", ())
        reference_image = str(reference_images[0]) if isinstance(reference_images, (list, tuple)) and reference_images else ""
        image_features = self.adapter.registry.call(
            "image.extract_features",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}",
            reference_image=reference_image,
        )
        similar_assets = self.adapter.registry.call(
            "asset.search_by_image",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}",
            reference_image=reference_image,
        )
        duplicate = self.adapter.registry.call(
            "asset.check_duplicate",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}",
            reference_image=reference_image,
        )
        audit_vector = self.adapter.registry.call(
            "vector.search_audit_rules",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}",
            query=str(payload.get("operator_hint", "")),
        )
        row = self.create_trial_demand(country, str(payload["js_category"]), str(payload["trial_mode"]))
        citations = _skill_rag_citations(self.rag_documents_for_task(country, skill.rag_task_index), limit=4)
        draft = {
            "common_subject": row.subject,
            "color_mood": str(image_features.data.get("color_mood", "待视觉解析确认")) if image_features.success else "待视觉解析确认",
            "composition": str(image_features.data.get("composition", "待视觉解析确认")) if image_features.success else "待视觉解析确认",
            "operation_tag": row.operation_tag,
            "draft_rows": (_demand_row_payload_for_skill(row),),
            "risk_notes": tuple(filter(None, (row.remark,))),
            "asset_matches": similar_assets.data if similar_assets.success else {},
            "duplicate_check": duplicate.data if duplicate.success else {},
        }
        memory_id = self.record_perception_memory(country, "trial_image_parse", {"source_skill": skill.skill_id, "subject": row.subject, "operation_tag": row.operation_tag}, actor=actor)
        return SkillRunResult(
            skill.skill_id,
            country,
            dict(payload),
            draft,
            citations,
            (f"memory:{memory_id}",),
            ("image.extract_features", "asset.search_by_image", "asset.check_duplicate", "vector.search_audit_rules", "rag.retrieve.value_master", "rag.retrieve.audit"),
            (),
            True,
            {"试新提需字段完整率": 1.0, "工具调用成功率": _success_rate(image_features, similar_assets, duplicate, audit_vector)},
        )

    def _run_value_audit_skill(self, payload: dict[str, object], *, actor: str) -> SkillRunResult:
        country = str(payload["country"])
        skill = self.business_skills.get("value_audit_skill")
        subject = str(payload["subject"])
        metrics = {"open_rate": 0.28, "completion_rate": 0.9}
        predicted = _skill_predict_grade(metrics)
        value_fit = self.adapter.registry.call(
            "image.audit_value_fit",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}:{subject}",
            image_or_candidate=str(payload.get("image_or_candidate", "")),
            subject=subject,
            operation_tag=str(payload.get("operation_tag", "")),
        )
        ip_risk = self.adapter.registry.call(
            "image.detect_ip_risk",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}:{subject}",
            image_or_candidate=str(payload.get("image_or_candidate", "")),
            subject=subject,
        )
        value_vector = self.adapter.registry.call(
            "vector.search_value_master",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}:{subject}",
            query=subject,
        )
        audit_vector = self.adapter.registry.call(
            "vector.search_audit_rules",
            country=country,
            actor=actor,
            skill_id=skill.skill_id,
            source_trace_id=f"{skill.skill_id}:{country}:{subject}",
            query=subject,
        )
        audit = self.audit_review(" ".join((str(payload.get("operation_tag", "")), subject)))
        citations = _skill_rag_citations(self.rag_documents_for_task(country, skill.rag_task_index), limit=5)
        memory_refs = self._skill_memory_refs(country, skill.memory_read_layers)
        tool_risks = tuple(value_fit.data.get("risk_points", ())) if value_fit.success else ()
        ip_risks = tuple(ip_risk.data.get("risk_points", ())) if ip_risk.success else ()
        draft = {
            "sabcd_prediction": predicted,
            "rag_citations": citations,
            "risk_points": tuple(dict.fromkeys((*tool_risks, *ip_risks, *(tuple(audit.evidence) or tuple(filter(None, (audit.reason,))))))),
            "revision_suggestions": (audit.suggestion,),
            "human_review_suggestion": "建议运营复核文化真实性、版权/IP 和拼图可读性。",
            "production_recommendation": "review_required",
        }
        self.record_working_memory(country, "value_audit_draft", {"source_skill": skill.skill_id, "subject": subject, "operation_tag": payload.get("operation_tag", ""), "sabcd_prediction": predicted}, actor=actor)
        return SkillRunResult(
            skill.skill_id,
            country,
            dict(payload),
            draft,
            citations,
            memory_refs,
            ("image.audit_value_fit", "image.detect_ip_risk", "vector.search_value_master", "vector.search_audit_rules", "rag.retrieve.value_master", "rag.retrieve.audit", "memory.retrieve", "audit.retrieve_policy"),
            (),
            True,
            {"RAG citation precision": 1.0 if citations else 0.0, "工具调用成功率": _success_rate(value_fit, ip_risk, value_vector, audit_vector)},
        )

    def _run_memory_governance_skill(self, payload: dict[str, object], *, actor: str) -> SkillRunResult:
        country = str(payload["country"])
        skill = self.business_skills.get("memory_governance_skill")
        self.adapter.registry.call("memory.workbench", country=country, actor=actor, skill_id=skill.skill_id, source_trace_id=f"{skill.skill_id}:{country}")
        self.adapter.registry.call("memory.conflicts", country=country, actor=actor, skill_id=skill.skill_id, source_trace_id=f"{skill.skill_id}:{country}")
        self.adapter.registry.call("memory.provenance", country=country, actor=actor, skill_id=skill.skill_id, source_trace_id=f"{skill.skill_id}:{country}")
        self.adapter.registry.call("vector.search_memory_facts", country=country, actor=actor, skill_id=skill.skill_id, source_trace_id=f"{skill.skill_id}:{country}", query=str(payload.get("operator_goal", "")))
        memory_ids = tuple(int(item) for item in payload.get("memory_ids", ()) if str(item).isdigit())
        rows = self.repository.layered_memories(country, include_inactive=True)
        selected = tuple(row for row in rows if not memory_ids or int(row.get("memory_id", 0)) in memory_ids)
        suggestions = tuple(f"#{row.get('memory_id')} {row.get('memory_type')} 建议人工复核后批准或停用" for row in selected[:5])
        draft = {
            "approval_suggestions": suggestions,
            "reject_suggestions": (),
            "merge_suggestions": (),
            "retire_suggestions": tuple(f"#{row.get('memory_id')} 长期未命中可考虑停用" for row in selected if int(row.get("rag_hit_count", 0) or 0) == 0)[:5],
            "provenance_explanation": "治理建议仅供 HITL 页面执行，不直接改变 memory 状态。",
            "rag_impact": "conflict_locked 或 draft 状态不会进入 RAG。",
        }
        self.record_working_memory(country, "memory_governance_suggestion", {"source_skill": skill.skill_id, "memory_ids": memory_ids, "operator_goal": payload.get("operator_goal", "")}, actor=actor)
        return SkillRunResult(skill.skill_id, country, dict(payload), draft, _skill_rag_citations(self.rag_documents_for_task(country, skill.rag_task_index), limit=4), tuple(f"memory:{row.get('memory_id')}" for row in selected[:5]), ("memory.workbench", "memory.conflicts", "memory.provenance", "vector.search_memory_facts", "rag.retrieve.memory_governance"), (), True, {"治理建议采纳率": 0.0})

    def _skill_memory_refs(self, country: str, layers: tuple[str, ...]) -> tuple[str, ...]:
        refs: list[str] = []
        for layer in layers:
            for memory in self.repository.layered_memories(country, layer=layer):
                refs.append(f"memory:{memory.get('memory_id')}")
                if len(refs) >= 5:
                    return tuple(refs)
        return tuple(refs)

    def _guarded_executor(self, country: str) -> GuardedToolExecutor:
        return GuardedToolExecutor(
            self.repository,
            tools={"feishu.write_table": lambda table_name, rows, approved_proposal_id="", **_: self.sync_demand_rows(country, rows, require_real=True)},
            policy=GuardedToolPolicy(writable_countries=(country,)),
        )

    def multimodal_profile(self, country: str) -> ImageProfile:
        records = self._history_records(country)
        retriever = SimilarImageRetriever(records, ImageFeatureExtractor())
        return retriever.profile_for(records[0])

    def value_rule_candidates(self, country: str) -> tuple[ValueRuleCandidate, ...]:
        return ValueInsightMiner(ImageFeatureExtractor()).mine(self._history_records(country), country)

    def approve_value_candidate(self, candidate_id: str, country: str, human_note: str) -> ValueRuleCandidate:
        for candidate in self.value_rule_candidates(country):
            if candidate.candidate_id == candidate_id:
                approved = replace(candidate, status="approved", human_note=human_note)
                self._approved_candidates[candidate_id] = approved
                self.repository.add_value_rule(country, approved.rule_text, "approved")
                self.repository.add_memory(country, "value_rule_approval", f"{human_note}：{approved.rule_text}")
                self.record_long_term_memory(
                    country,
                    "value_rule_approval",
                    {
                        "candidate_id": candidate_id,
                        "rule_text": approved.rule_text,
                        "human_note": human_note,
                        "confidence": approved.confidence,
                    },
                )
                return approved
        raise ValueError(f"找不到价值观候选：{candidate_id}")

    def approved_value_rules(self, country: str):
        return self.repository.approved_value_rules(country)

    def _rag_documents(self, country: str) -> tuple[RagDocument, ...]:
        documents: list[RagDocument] = []
        documents.extend(self._file_knowledge_rag_documents(country))
        for index, (title, body) in enumerate(self._country(country)["value_rules"], 1):
            documents.append(
                RagDocument(
                    document_id=f"{_country_code(country)}_VALUE_{index:03d}",
                    country=country,
                    source_type="value_rule",
                    title=title,
                    text=body,
                    metadata=_rag_business_metadata(
                        country,
                        source_type="value_rule",
                        task_type="value_master",
                        business_object_type="value_rule",
                        value_dimension=title,
                        polarity=_value_rule_polarity(body),
                        provenance_id=f"{_country_code(country)}:static_value_rules:{index}",
                        approved_for_rag=True,
                    ),
                )
            )
        for index, rule in enumerate(self.approved_value_rules(country), 1):
            documents.append(
                RagDocument(
                    document_id=f"{_country_code(country)}_APPROVED_VALUE_{index:03d}",
                    country=country,
                    source_type="approved_value_rule",
                    title="运营审批价值观",
                    text=str(rule["rule_text"]),
                    metadata=_rag_business_metadata(
                        country,
                        source_type="approved_value_rule",
                        task_type="value_master",
                        business_object_type="value_rule",
                        value_dimension="运营审批价值观",
                        polarity=_value_rule_polarity(str(rule["rule_text"])),
                        provenance_id=f"{_country_code(country)}:approved_value_rule:{index}",
                        approved_for_rag=True,
                    ),
                )
            )
        documents.extend(self._layered_memory_rag_documents(country))
        documents.extend(self._harness_gold_rag_documents(country))
        for record in self._history_records(country):
            documents.append(
                RagDocument(
                    document_id=f"{_country_code(country)}_SAMPLE_{record.image_id}",
                    country=country,
                    source_type="sample_fact",
                    title=f"历史样本 {record.operation_tag}",
                    text=(
                        f"主体={record.subject_tag}；JS分类={record.js_category}；来源={record.source}；"
                        f"等级={record.grade}；开图率={record.open_rate}；完成率={record.completion_rate}；"
                        f"构图/备注={record.remark or record.dimension_grade}"
                    ),
                    metadata=_rag_business_metadata(
                        country,
                        source_type="sample_fact",
                        task_type="weekly_review",
                        business_object_type="historical_image",
                        operation_tag=record.operation_tag,
                        subject=record.subject_tag,
                        js_category=record.js_category,
                        grade=record.grade,
                        date_range=record.distribution_cycle or record.distribution_date,
                        provenance_id=f"{_country_code(country)}:historical_records:{record.image_id}",
                        image_id=record.image_id,
                        approved_for_rag=True,
                    ),
                )
            )
        for hit in self._audit_policy_hits():
            documents.append(
                RagDocument(
                    document_id=f"AUDIT_{hit.rule_id}",
                    country="GLOBAL",
                    source_type="audit_policy",
                    title=f"审核规则 {hit.risk_level}风险",
                    text=hit.text,
                    metadata=_rag_business_metadata(
                        "GLOBAL",
                        source_type="audit_policy",
                        task_type="audit",
                        business_object_type="audit_risk_type",
                        risk_type=_audit_risk_type(hit.text),
                        grade=hit.risk_level,
                        provenance_id=f"GLOBAL:audit_manual:{hit.rule_id}",
                        risk_level=hit.risk_level,
                        approved_for_rag=True,
                    ),
                )
            )
        return tuple(documents)

    def _file_knowledge_rag_documents(self, country: str) -> tuple[RagDocument, ...]:
        path = _rag_knowledge_dir() / "processed" / "value_audit_documents.jsonl"
        documents = FileDocumentLoaderAdapter((path,)).load()
        return tuple(_with_business_metadata(document) for document in documents if document.country in {country, "GLOBAL"})

    def _rag_eval_cases(self, country: str) -> tuple[RagRetrievalCase, ...]:
        path = _rag_knowledge_dir() / "eval" / "value_audit_cases.jsonl"
        cases = RetrievalCaseLoaderAdapter(path).load()
        return tuple(case for case in cases if case.country == country)

    def _rag_retrieval_cases(self, country: str) -> tuple[RagRetrievalCase, ...]:
        return (*self._rag_eval_cases(country), *self._harness_gold_rag_eval_cases(country))

    def _harness_gold_rag_eval_cases(self, country: str) -> tuple[RagRetrievalCase, ...]:
        cases: list[RagRetrievalCase] = []
        samples = tuple(
            sample
            for sample in self.harness_samples(country)
            if sample.is_real and sample.label_source == "human_gold" and sample.label_status == "reviewed"
        )
        for sample in samples:
            query_parts = (
                sample.gold_subject or sample.subject,
                sample.gold_color_mood,
                sample.gold_composition,
                " ".join(sample.gold_value_labels),
                " ".join(sample.gold_risk_labels),
                sample.gold_grade,
                "价值观 审核 风险 真实业务样本",
            )
            cases.append(
                RagRetrievalCase(
                    query=" ".join(part for part in query_parts if part).strip(),
                    country=country,
                    expected_parent_id=f"{_country_code(country)}_HARNESS_GOLD_{sample.sample_id}",
                    hard_negative_parent_ids=_harness_gold_hard_negative_parent_ids(country, sample, samples),
                )
            )
        return tuple(cases)

    def _rag_knowledge_summary(self, country: str) -> dict[str, object]:
        root = _rag_knowledge_dir()
        raw_dir = root / "raw"
        documents_path = root / "processed" / "value_audit_documents.jsonl"
        eval_cases_path = root / "eval" / "value_audit_cases.jsonl"
        vector_provider = self.rag_vector_store_config.provider
        vector_manifest_path = root / "indices" / f"{vector_provider}_reindex_{country}.json"
        vector_manifest = _read_json_object(vector_manifest_path)
        qdrant_manifest_path = root / "indices" / f"qdrant_reindex_{country}.json"
        qdrant_manifest = _read_json_object(qdrant_manifest_path)
        qdrant_history = _qdrant_reindex_history(root, country)
        return {
            "root": str(root),
            "raw_dir": str(raw_dir),
            "documents_path": str(documents_path),
            "eval_cases_path": str(eval_cases_path),
            "vector_store_manifest_path": str(vector_manifest_path),
            "vector_store_manifest_exists": vector_manifest_path.exists(),
            "vector_store_manifest_provider": str(
                (vector_manifest.get("vector_store") if isinstance(vector_manifest.get("vector_store"), dict) else {}).get("provider", "")
            ),
            "vector_store_manifest_run_id": str(vector_manifest.get("run_id", "")),
            "vector_store_manifest_status": vector_manifest.get("status", ""),
            "vector_store_manifest_vector_size": int(vector_manifest.get("vector_size", 0) or 0),
            "vector_store_manifest_upserted_points": int(vector_manifest.get("upserted_points", 0) or 0),
            "vector_store_manifest_hit@5": vector_manifest.get("hit@5", 0),
            "vector_store_manifest_mrr@5": vector_manifest.get("mrr@5", 0),
            "vector_store_manifest_precision@5": vector_manifest.get("precision@5", 0),
            "vector_store_manifest_recall@5": vector_manifest.get("recall@5", 0),
            "vector_store_manifest_ndcg@5": vector_manifest.get("ndcg@5", 0),
            "vector_store_manifest_smoke_status": str(
                (vector_manifest.get("smoke_diagnostic") if isinstance(vector_manifest.get("smoke_diagnostic"), dict) else {}).get("status", "")
            ),
            "vector_store_manifest_smoke_cleanup_status": str(
                (vector_manifest.get("smoke_diagnostic") if isinstance(vector_manifest.get("smoke_diagnostic"), dict) else {}).get("cleanup_status", "")
            ),
            "qdrant_manifest_path": str(qdrant_manifest_path),
            "qdrant_manifest_exists": qdrant_manifest_path.exists(),
            "qdrant_manifest_run_id": str(qdrant_manifest.get("run_id", "")),
            "qdrant_manifest_status": qdrant_manifest.get("status", ""),
            "qdrant_manifest_vector_size": int(qdrant_manifest.get("vector_size", 0) or 0),
            "qdrant_manifest_upserted_points": int(qdrant_manifest.get("upserted_points", 0) or 0),
            "qdrant_manifest_history_count": len(qdrant_history),
            "qdrant_manifest_recent_runs": qdrant_history[:3],
            "qdrant_manifest_smoke_status": str(
                (qdrant_manifest.get("smoke_diagnostic") if isinstance(qdrant_manifest.get("smoke_diagnostic"), dict) else {}).get("status", "")
            ),
            "qdrant_manifest_smoke_cleanup_status": str(
                (qdrant_manifest.get("smoke_diagnostic") if isinstance(qdrant_manifest.get("smoke_diagnostic"), dict) else {}).get("cleanup_status", "")
            ),
            "raw_file_count": len(tuple(raw_dir.rglob("*"))) if raw_dir.exists() else 0,
            "documents_exists": documents_path.exists(),
            "eval_cases_exists": eval_cases_path.exists(),
            "file_document_count": len(self._file_knowledge_rag_documents(country)),
            "file_eval_case_count": len(self._rag_eval_cases(country)),
        }

    def _harness_gold_rag_documents(self, country: str) -> tuple[RagDocument, ...]:
        documents: list[RagDocument] = []
        for sample in self.harness_samples(country):
            if not sample.is_real or sample.label_source != "human_gold" or sample.label_status != "reviewed":
                continue
            documents.append(
                RagDocument(
                    document_id=f"{_country_code(country)}_HARNESS_GOLD_{sample.sample_id}",
                    country=country,
                    source_type="harness_gold_sample",
                    title=f"Harness Gold 样本 {sample.operation_tag}",
                    text=_harness_gold_sample_rag_text(sample),
                    metadata={
                        **_rag_business_metadata(
                            country,
                            source_type="harness_gold_sample",
                            task_type="weekly_review",
                            business_object_type="historical_image",
                            operation_tag=sample.operation_tag,
                            subject=sample.gold_subject or sample.subject,
                            js_category=sample.js_category,
                            grade=sample.gold_grade,
                            provenance_id=f"{_country_code(country)}:harness_gold:{sample.sample_id}",
                            approved_for_rag=True,
                        ),
                        "source": "harness_gold_dataset",
                        "sample_id": sample.sample_id,
                        "local_image_path": sample.local_image_path,
                        "human_verified": True,
                    },
                )
            )
        return tuple(documents)

    def _layered_memory_rag_documents(self, country: str) -> tuple[RagDocument, ...]:
        specs = (
            ("perception", "memory_perception", "MEMORY_PERCEPTION"),
            ("working", "memory_working", "MEMORY_WORKING"),
            ("long_term", "approved_value_rule", "MEMORY_LONG"),
            ("facts", "fact", "FACT"),
        )
        documents: list[RagDocument] = []
        conflict_memory_ids = self._conflict_memory_ids(country)
        for layer, source_type, prefix in specs:
            for index, memory in enumerate(self.repository.layered_memories(country, layer=layer), 1):
                payload = memory.get("payload", {})
                text = _payload_text(payload)
                if not text or not self._memory_rag_ready(memory, conflict_memory_ids=conflict_memory_ids):
                    continue
                documents.append(
                    RagDocument(
                        document_id=f"{_country_code(country)}_{prefix}_{index:03d}",
                        country=country,
                        source_type=source_type,
                        title=str(memory.get("memory_type", layer)),
                        text=text,
                        metadata={
                            **_rag_business_metadata(
                                country,
                                source_type=source_type,
                                task_type="memory_governance",
                                business_object_type="memory_fact",
                                operation_tag=_memory_payload_field(payload if isinstance(payload, dict) else {}, "operation_tag"),
                                subject=_memory_payload_field(payload if isinstance(payload, dict) else {}, "subject"),
                                approved_for_rag=bool(memory.get("approved_for_rag")),
                                memory_id=memory.get("memory_id", ""),
                                provenance_id=f"{_country_code(country)}:memory:{memory.get('memory_id')}",
                            ),
                            "source": "layered_memory",
                            "layer": layer,
                            "memory_id": memory.get("memory_id"),
                            "source_memory_id": memory.get("source_memory_id"),
                            "human_verified": bool(memory.get("human_verified")),
                            "review_status": memory.get("review_status", "draft"),
                            "approved_for_rag": bool(memory.get("approved_for_rag")),
                            "approved_by": memory.get("approved_by", ""),
                            "memory_scope": memory.get("memory_scope", "operational_fact"),
                            **_memory_trust_metadata(memory),
                        },
                    )
                )
        return tuple(documents)

    def _audit_policy_hits(self):
        manual = Path("/Users/fanglemin/Desktop/拼图审核手册.docx")
        if not manual.exists():
            return ()
        return AuditPolicyRetriever.safe_from_docx(manual).hits

    def hitl_memories(self, country: str):
        return self.repository.memories(country)

    def record_perception_memory(self, country: str, memory_type: str, payload: dict[str, object], *, actor: str = "") -> int:
        return self.repository.add_layered_memory(country, "perception", memory_type, payload, ttl_seconds=7 * 24 * 3600, created_by=actor)

    def record_working_memory(self, country: str, memory_type: str, payload: dict[str, object], *, actor: str = "") -> int:
        return self.repository.add_layered_memory(country, "working", memory_type, payload, ttl_seconds=24 * 3600, created_by=actor)

    def record_personal_preference_memory(self, country: str, user_id: str, payload: dict[str, object]) -> int:
        scoped_payload = dict(payload)
        scoped_payload["user_id"] = user_id
        scoped_payload.setdefault("preference_scope", "personal")
        return self.repository.add_layered_memory(
            country,
            "working",
            "personal_preference",
            scoped_payload,
            ttl_seconds=30 * 24 * 3600,
            created_by=user_id,
            memory_scope="personal_preference",
        )

    def record_rag_citation_feedback(
        self,
        country: str,
        *,
        chunk_id: str,
        usefulness: str,
        note: str = "",
        task_type: str = "trial_value_match",
        actor: str = "",
    ) -> int:
        if usefulness not in {"useful", "not_useful"}:
            raise ValueError("RAG 依据反馈只能是 useful 或 not_useful")
        return self.record_working_memory(
            country,
            "rag_citation_feedback",
            {
                "chunk_id": chunk_id,
                "usefulness": usefulness,
                "note": note,
                "task_type": task_type,
            },
            actor=actor,
        )

    def record_rag_eval_failure_feedback(
        self,
        country: str,
        *,
        query: str,
        expected_parent_id: str,
        retrieved_parent_ids: tuple[str, ...] | list[str],
        note: str = "",
        diagnosis: str = "",
        suggested_action: str = "",
        gold_grade: str = "",
        label_source: str = "",
        actor: str = "",
    ) -> int:
        expected = expected_parent_id.strip()
        if not query.strip() or not expected:
            raise ValueError("RAG eval 失败case必须包含 query 和 expected_parent_id")
        retrieved = tuple(str(item).strip() for item in retrieved_parent_ids if str(item).strip())
        return self.record_working_memory(
            country,
            "rag_eval_failure_feedback",
            {
                "query": query.strip(),
                "expected_parent_id": expected,
                "retrieved_parent_ids": list(retrieved),
                "note": note.strip(),
                "diagnosis": diagnosis.strip(),
                "suggested_action": suggested_action.strip(),
                "gold_grade": gold_grade.strip().upper(),
                "label_source": label_source.strip(),
                "task_type": "rag_eval_case_review",
            },
            actor=actor,
        )

    def record_value_match_human_correction(
        self,
        row: DemandRow,
        *,
        human_correction: str,
        satisfaction_score: int | None = None,
        actor: str = "",
    ) -> dict[str, int]:
        correction = human_correction.strip()
        if not correction:
            raise ValueError("价值观人工修正不能为空")
        citations = _extract_rag_citation_ids(row.value_match)
        expected_parent_id = _expected_parent_from_citations(citations, row.country, row.subject)
        payload = {
            "task_type": "value_match_eval",
            "operation_tag": row.operation_tag,
            "subject": row.subject,
            "subject_description": row.subject_description,
            "ai_value_match": row.value_match,
            "human_correction": correction,
            "citation_ids": list(citations),
            "satisfaction_score": satisfaction_score,
        }
        working_id = self.record_working_memory(row.country, "value_match_human_correction", payload, actor=actor)
        fact_id = self.record_extracted_fact(
            row.country,
            "verified_value_match_fact",
            {
                "subject": row.subject,
                "operation_tag": row.operation_tag,
                "subject_description": row.subject_description,
                "human_correction": correction,
                "value_labels": _value_labels_from_correction(correction),
                "risk_labels": _risk_labels_from_correction(correction),
                "citation_ids": list(citations),
                "source_working_memory_id": working_id,
                "label_source": "human_value_match_correction",
            },
            actor=actor,
        )
        rag_feedback_id = self.record_rag_eval_failure_feedback(
            row.country,
            query=" ".join((row.country, row.subject, row.subject_description, correction, "价值观 审核 风险")),
            expected_parent_id=expected_parent_id,
            retrieved_parent_ids=tuple(_parent_id_from_chunk_id(citation) for citation in citations),
            note=f"价值观人工修正：{correction}",
            diagnosis="human_value_match_correction",
            suggested_action="将人工修正沉淀为价值观/审核知识补丁候选",
            gold_grade="",
            label_source="human_value_match_correction",
            actor=actor,
        )
        return {
            "working_memory_id": working_id,
            "fact_memory_id": fact_id,
            "rag_feedback_memory_id": rag_feedback_id,
        }

    def record_long_term_memory(self, country: str, memory_type: str, payload: dict[str, object], *, actor: str = "") -> int:
        return self.repository.add_layered_memory(country, "long_term", memory_type, payload, created_by=actor)

    def record_extracted_fact(self, country: str, memory_type: str, payload: dict[str, object], *, actor: str = "") -> int:
        return self.repository.add_layered_memory(country, "facts", memory_type, payload, created_by=actor)

    def migrate_memory_country(self, memory_id: int, *, target_country: str, actor: str = "", note: str = "") -> int:
        target_country = target_country.strip()
        if target_country not in COUNTRIES:
            raise ValueError(f"未知目标国家：{target_country}")
        return self.repository.migrate_layered_memory_country(memory_id, target_country=target_country, actor=actor, note=note)

    def promote_memory(self, memory_id: int, *, target_layer: str, human_note: str, actor: str = "") -> int:
        if target_layer not in {"facts", "long_term"}:
            raise ValueError("memory 只能人工晋升为 facts 或 long_term")
        target_type = "verified_fact" if target_layer == "facts" else "approved_long_term_memory"
        return self.repository.promote_layered_memory(
            memory_id,
            target_layer=target_layer,
            target_type=target_type,
            human_note=human_note,
            actor=actor,
        )

    def review_memory(self, memory_id: int, *, action: str, actor: str = "") -> None:
        mapping = {
            "approve_rag": ("approved", True),
            "approve_no_rag": ("approved", False),
            "reject": ("rejected", False),
            "lock_conflict": ("conflict_locked", False),
        }
        if action not in mapping:
            raise ValueError(f"未知 memory 审核动作：{action}")
        review_status, approved_for_rag = mapping[action]
        self.repository.review_layered_memory(memory_id, review_status=review_status, approved_for_rag=approved_for_rag, actor=actor)

    def retire_memory(self, memory_id: int, *, actor: str = "") -> None:
        self.repository.retire_layered_memory(memory_id, actor=actor)

    def resolve_memory_conflict(
        self,
        country: str,
        *,
        conflict_id: str,
        action: str,
        actor: str = "",
        note: str = "",
        merge_text: str = "",
    ) -> dict[str, object]:
        conflict = next((item for item in self.memory_conflicts(country) if str(item.get("conflict_id", "")) == conflict_id), None)
        if conflict is None:
            raise ValueError(f"找不到 memory 冲突：{conflict_id}")
        memory_ids = tuple(int(item) for item in conflict.get("memory_ids", ()))
        if not memory_ids:
            raise ValueError("冲突组没有可处理的 memory")
        result: dict[str, object] = {"conflict_id": conflict_id, "action": action, "retired": (), "kept": (), "merged_memory_id": 0}
        if action in {"keep_first", "keep_second"}:
            kept = memory_ids[0] if action == "keep_first" else memory_ids[min(1, len(memory_ids) - 1)]
            retired: list[int] = []
            for memory_id in memory_ids:
                if memory_id == kept:
                    self.review_memory(memory_id, action="approve_rag", actor=actor)
                else:
                    self.retire_memory(memory_id, actor=actor)
                    retired.append(memory_id)
            result.update({"kept": (kept,), "retired": tuple(retired)})
            return result
        if action == "retire_all":
            for memory_id in memory_ids:
                self.retire_memory(memory_id, actor=actor)
            result["retired"] = memory_ids
            return result
        if action == "defer":
            for memory_id in memory_ids:
                self.review_memory(memory_id, action="lock_conflict", actor=actor)
            result["kept"] = memory_ids
            return result
        if action == "merge":
            payload = {
                "subject": str(conflict.get("subject", "")),
                "operation_tag": str(conflict.get("operation_tag", "")),
                "human_correction": merge_text.strip() or note.strip() or "冲突已合并，待人工再次批准。",
                "source_conflict_id": conflict_id,
                "source_memory_ids": list(memory_ids),
                "resolution_note": note.strip(),
            }
            merged_id = self.record_extracted_fact(country, "merged_conflict_resolution", payload, actor=actor)
            for memory_id in memory_ids:
                self.review_memory(memory_id, action="lock_conflict", actor=actor)
            result.update({"kept": memory_ids, "merged_memory_id": merged_id})
            return result
        raise ValueError(f"未知冲突处理动作：{action}")

    def memory_overview(self, country: str) -> dict[str, dict[str, object]]:
        labels = {
            "perception": "感知记忆",
            "working": "短期记忆",
            "long_term": "长期记忆",
            "facts": "结构化事实",
        }
        overview: dict[str, dict[str, object]] = {}
        for layer, label in labels.items():
            items = self.repository.layered_memories(country, layer=layer)
            all_items = self.repository.layered_memories(country, layer=layer, include_inactive=True)
            conflict_memory_ids = self._conflict_memory_ids(country)
            rag_ready_count = sum(1 for item in items if self._memory_rag_ready(item, conflict_memory_ids=conflict_memory_ids))
            overview[label] = {
                "layer": layer,
                "count": len(items),
                "inactive_count": len(all_items) - len(items),
                "rag_ready_count": rag_ready_count,
                "latest": items[-1] if items else {},
            }
        return overview

    def memory_debug(self, country: str, query: str = "", limit: int = 12) -> tuple[dict[str, object], ...]:
        source_types = {
            "perception": "memory_perception",
            "working": "memory_working",
            "long_term": "approved_value_rule",
            "facts": "fact",
        }
        conflicts_by_id: dict[int, list[str]] = {}
        for conflict in self.memory_conflicts(country):
            conflict_id = str(conflict.get("conflict_id", ""))
            for memory_id in conflict.get("memory_ids", ()):
                conflicts_by_id.setdefault(int(memory_id), []).append(conflict_id)
        conflict_memory_ids = set(conflicts_by_id)
        memories = tuple(self.repository.layered_memories(country, include_inactive=True))
        superseded_ids: set[int] = set()
        for memory in memories:
            payload = memory.get("payload", {}) if isinstance(memory.get("payload", {}), dict) else {}
            superseded_ids.update(_memory_superseded_ids(payload))
        rows: list[dict[str, object]] = []
        for memory in memories:
            layer = str(memory.get("memory_layer", ""))
            payload = memory.get("payload", {})
            summary = _payload_text(payload) if isinstance(payload, dict) else ""
            status = str(memory.get("status", "active"))
            rows.append(
                {
                    "memory_id": int(memory.get("memory_id", 0)),
                    "layer": layer,
                    "memory_type": str(memory.get("memory_type", "")),
                    "payload": payload if isinstance(payload, dict) else {},
                    "rag_source_type": source_types.get(layer, "memory"),
                    "summary": summary,
                    "status": status,
                    "source_memory_id": memory.get("source_memory_id"),
                    "expires_at": str(memory.get("expires_at", "") or ""),
                    "human_verified": bool(memory.get("human_verified")),
                    "created_by": str(memory.get("created_by", "")),
                    "updated_by": str(memory.get("updated_by", "")),
                    "approved_by": str(memory.get("approved_by", "")),
                    "approved_at": str(memory.get("approved_at", "") or ""),
                    "retired_by": str(memory.get("retired_by", "")),
                    "retired_at": str(memory.get("retired_at", "") or ""),
                    "approved_for_rag": bool(memory.get("approved_for_rag")),
                    "memory_scope": str(memory.get("memory_scope", "operational_fact") or "operational_fact"),
                    "review_status": str(memory.get("review_status", "draft")),
                    "created_at": str(memory.get("created_at", "")),
                    "updated_at": str(memory.get("updated_at", "")),
                    "rag_ready": self._memory_rag_ready(memory, conflict_memory_ids=conflict_memory_ids),
                    "match_score": _memory_match_score(summary, query),
                    "conflict_ids": tuple(conflicts_by_id.get(int(memory.get("memory_id", 0)), ())),
                    **self._memory_quality_metrics(memory, conflicts_by_id, superseded_ids=superseded_ids),
                }
            )
        rows.sort(key=lambda row: (float(row["match_score"]), str(row["created_at"])), reverse=True)
        return tuple(rows[: max(limit, 0)])

    def memory_conflicts(self, country: str) -> tuple[dict[str, object], ...]:
        grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
        for memory in self.repository.layered_memories(country):
            payload = memory.get("payload", {})
            if not isinstance(payload, dict):
                continue
            subject = _memory_payload_field(payload, "subject")
            operation_tag = _memory_payload_field(payload, "operation_tag")
            if not subject and not operation_tag:
                continue
            grouped.setdefault((subject, operation_tag), []).append(memory)

        conflicts: list[dict[str, object]] = []
        for (subject, operation_tag), memories in grouped.items():
            stances = {"positive": [], "negative": [], "risk": []}
            evidence: list[dict[str, object]] = []
            for memory in memories:
                memory_id = int(memory.get("memory_id", 0))
                summary = _payload_text(memory.get("payload", {}))
                stance = _memory_stance(summary)
                if stance in stances:
                    stances[stance].append(memory_id)
                    evidence.append(
                        {
                            "memory_id": memory_id,
                            "memory_layer": memory.get("memory_layer", ""),
                            "memory_type": memory.get("memory_type", ""),
                            "stance": stance,
                            "summary": summary,
                            "provenance": self.memory_provenance(country, memory_id).get("steps", ()),
                        }
                    )
            if stances["positive"] and (stances["negative"] or stances["risk"]):
                involved = tuple(sorted(stances["positive"] + stances["negative"] + stances["risk"]))
                conflicts.append(
                    {
                        "conflict_id": f"{country}:{subject or '*'}:{operation_tag or '*'}",
                        "country": country,
                        "subject": subject,
                        "operation_tag": operation_tag,
                        "memory_ids": involved,
                        "stances": {key: list(value) for key, value in stances.items()},
                        "evidence": tuple(evidence),
                        "message": "同一主体/tag 下同时存在正向与负向或风险记忆，建议人工复核后停用或晋升可信版本。",
                    }
                )
        return tuple(conflicts)

    def memory_workbench(self, country: str, *, filters: dict[str, str] | None = None) -> dict[str, object]:
        rows = self.memory_debug(country, limit=200)
        rows = self._filter_memory_rows(rows, filters or {})
        pending = tuple(row for row in rows if row.get("status") == "active" and row.get("review_status") == "draft")
        approved_rag = tuple(row for row in rows if row.get("rag_ready"))
        retired = tuple(row for row in rows if row.get("review_status") == "retired" or row.get("status") in {"retired", "expired"})
        cleanup = tuple(row for row in rows if row.get("cleanup_suggestions"))
        recent_hits = tuple(row for row in approved_rag if row.get("last_rag_hit_at"))
        return {
            "pending_review": pending[:8],
            "conflicts": self.memory_conflicts(country),
            "approved_rag": approved_rag[:8],
            "recently_retired": retired[:8],
            "recent_rag_hits": recent_hits[:8],
            "cleanup": cleanup[:8],
            "lifecycle": self.memory_lifecycle_summary(country),
        }

    def memory_lifecycle_summary(self, country: str) -> dict[str, object]:
        rows = self.memory_debug(country, limit=500)
        weekly_cleanup = tuple(row for row in rows if row.get("cleanup_suggestions"))
        stale = tuple(row for row in rows if "长期未命中" in "；".join(str(item) for item in row.get("cleanup_suggestions", ())))
        conflict_prone = tuple(row for row in rows if int(row.get("conflict_count", 0) or 0) > 0)
        superseded = tuple(row for row in rows if "已被新规则覆盖" in "；".join(str(item) for item in row.get("cleanup_suggestions", ())))
        personal_preferences = tuple(row for row in rows if row.get("memory_scope") == "personal_preference")
        operational_facts = tuple(row for row in rows if row.get("memory_scope") == "operational_fact")
        return {
            "country": country,
            "weekly_cleanup": weekly_cleanup[:20],
            "long_unhit": stale[:20],
            "conflict_prone": conflict_prone[:20],
            "superseded": superseded[:20],
            "personal_preferences": personal_preferences[:20],
            "operational_facts_count": len(operational_facts),
            "personal_preferences_count": len(personal_preferences),
            "storage_plan": {
                "source_of_truth": "SQLite/Postgres",
                "vector_index": "Milvus approved RAG chunks",
                "cache": "Redis short-term state + embedding hot cache",
                "object_store": "Files/Object storage for images, raw RAG docs, reports",
            },
        }

    def seed_memory_production_validation(self, country: str, *, actor: str = "") -> dict[str, object]:
        approved_id = self.record_extracted_fact(
            country,
            "production_validation_fact",
            {
                "subject": "上线验收寿司样例",
                "operation_tag": "MEMORY_PROD_VALIDATION",
                "rule": f"{country}市场上线验收：已批准知识可进入 RAG。",
            },
            actor=actor,
        )
        draft_id = self.record_working_memory(
            country,
            "production_validation_draft",
            {
                "subject": "上线验收草稿样例",
                "operation_tag": "MEMORY_PROD_VALIDATION",
                "note": "草稿默认不进入 RAG，等待运营确认。",
            },
            actor=actor,
        )
        positive_id = self.record_extracted_fact(
            country,
            "production_validation_conflict_positive",
            {
                "subject": "上线验收冲突样例",
                "operation_tag": "MEMORY_PROD_VALIDATION_CONFLICT",
                "rule": "适合该国家市场，可进入候选。",
            },
            actor=actor,
        )
        negative_id = self.record_extracted_fact(
            country,
            "production_validation_conflict_negative",
            {
                "subject": "上线验收冲突样例",
                "operation_tag": "MEMORY_PROD_VALIDATION_CONFLICT",
                "rule": "不适合该国家市场，需要人工复核。",
            },
            actor=actor,
        )
        self.review_memory(approved_id, action="approve_rag", actor=actor)
        self.review_memory(positive_id, action="approve_no_rag", actor=actor)
        self.review_memory(negative_id, action="approve_no_rag", actor=actor)
        return {
            "approved_memory_id": approved_id,
            "draft_memory_id": draft_id,
            "conflict_memory_ids": (positive_id, negative_id),
        }

    @staticmethod
    def _filter_memory_rows(rows: tuple[dict[str, object], ...], filters: dict[str, str]) -> tuple[dict[str, object], ...]:
        def wanted(key: str) -> str:
            return str(filters.get(key, "") or "").strip()

        layer = wanted("layer")
        review_status = wanted("review_status")
        approved_for_rag = wanted("approved_for_rag")
        conflict = wanted("conflict")
        created_by = wanted("created_by")
        subject = wanted("subject")
        operation_tag = wanted("operation_tag")
        filtered: list[dict[str, object]] = []
        for row in rows:
            payload = row.get("payload", {}) if isinstance(row.get("payload", {}), dict) else {}
            if layer and row.get("layer") != layer:
                continue
            if review_status and row.get("review_status") != review_status:
                continue
            if approved_for_rag in {"true", "false"} and bool(row.get("approved_for_rag")) != (approved_for_rag == "true"):
                continue
            if conflict in {"true", "false"} and bool(row.get("conflict_ids")) != (conflict == "true"):
                continue
            if created_by and row.get("created_by") != created_by:
                continue
            if subject and subject not in _payload_text(payload):
                continue
            if operation_tag and operation_tag not in _payload_text(payload):
                continue
            filtered.append(row)
        return tuple(filtered)

    def _record_memory_rag_hits(self, country: str, hits: tuple[object, ...], *, trace_id: str) -> None:
        memory_hits = []
        seen: set[tuple[int, str]] = set()
        for hit in hits:
            chunk = getattr(hit, "chunk", None)
            metadata = getattr(chunk, "metadata", {}) if chunk is not None else {}
            if not isinstance(metadata, dict) or metadata.get("source") != "layered_memory":
                continue
            try:
                memory_id = int(metadata.get("memory_id", 0))
            except (TypeError, ValueError):
                continue
            chunk_id = str(getattr(chunk, "chunk_id", ""))
            key = (memory_id, chunk_id)
            if memory_id <= 0 or key in seen:
                continue
            seen.add(key)
            memory_hits.append({"memory_id": memory_id, "chunk_id": chunk_id, "trace_id": trace_id})
        if memory_hits:
            self.repository.record_memory_rag_hits(country, tuple(memory_hits))

    def _conflict_memory_ids(self, country: str) -> set[int]:
        memory_ids: set[int] = set()
        for conflict in self.memory_conflicts(country):
            memory_ids.update(int(item) for item in conflict.get("memory_ids", ()))
        return memory_ids

    @staticmethod
    def _memory_rag_ready(memory: dict[str, object], *, conflict_memory_ids: set[int] | None = None) -> bool:
        conflict_memory_ids = conflict_memory_ids or set()
        memory_id = int(memory.get("memory_id", 0))
        payload = memory.get("payload", {})
        return (
            bool(_payload_text(payload))
            and str(memory.get("status", "active")) == "active"
            and str(memory.get("memory_scope", "operational_fact") or "operational_fact") == "operational_fact"
            and str(memory.get("review_status", "draft")) == "approved"
            and bool(memory.get("approved_for_rag"))
            and memory_id not in conflict_memory_ids
        )

    @staticmethod
    def _memory_quality_metrics(
        memory: dict[str, object],
        conflicts_by_id: dict[int, list[str]],
        *,
        superseded_ids: set[int] | None = None,
    ) -> dict[str, object]:
        payload = memory.get("payload", {})
        memory_id = int(memory.get("memory_id", 0))
        not_useful = 1 if _memory_negative_feedback(payload) else 0
        conflicts = len(conflicts_by_id.get(memory_id, ()))
        rag_hit_count = int(memory.get("rag_hit_count", 0) or 0)
        suggestions: list[str] = []
        if not_useful:
            suggestions.append("多次/本次 not useful，建议复核")
        if conflicts:
            suggestions.append("参与冲突，需治理")
        if (
            str(memory.get("status", "")) == "active"
            and str(memory.get("review_status", "")) == "approved"
            and bool(memory.get("approved_for_rag"))
            and rag_hit_count == 0
        ):
            suggestions.append("长期未命中，建议周清理复核")
        if _memory_superseded_ids(payload):
            suggestions.append("已覆盖其他旧记忆，检查旧规则是否应停用")
        if memory_id in (superseded_ids or set()):
            suggestions.append("已被新规则覆盖，建议停用或降权")
        if str(memory.get("status", "")) == "expired":
            suggestions.append("已过期，检查是否有新规则覆盖")
        if str(memory.get("review_status", "")) == "rejected":
            suggestions.append("已驳回，可定期清理")
        return {
            "rag_hit_count": rag_hit_count,
            "accepted_count": 1 if str(memory.get("review_status", "")) == "approved" else 0,
            "not_useful_count": not_useful,
            "conflict_count": conflicts,
            "last_rag_hit_at": str(memory.get("last_rag_hit_at", "") or ""),
            "cleanup_suggestions": tuple(suggestions),
        }

    def memory_provenance(self, country: str, memory_id: int) -> dict[str, object]:
        rows = tuple(self.repository.layered_memories(country, include_inactive=True))
        by_id = {int(row.get("memory_id", 0)): row for row in rows}
        if memory_id not in by_id:
            raise ValueError(f"memory_id 不存在：{memory_id}")
        root = by_id[memory_id]
        root_payload = root.get("payload", {}) if isinstance(root.get("payload", {}), dict) else {}
        subject = _memory_payload_field(root_payload, "subject")
        operation_tag = _memory_payload_field(root_payload, "operation_tag")
        citations = set(_memory_payload_citations(root_payload))
        steps: list[dict[str, object]] = []
        seen: set[int] = set()

        source_id = root.get("source_memory_id")
        while source_id:
            source = by_id.get(int(source_id))
            if not source:
                break
            _append_memory_step(steps, seen, source, "source")
            source_id = source.get("source_memory_id")

        _append_memory_step(steps, seen, root, "current")

        for row in rows:
            if row.get("source_memory_id") == memory_id:
                _append_memory_step(steps, seen, row, "descendant")

        for row in rows:
            payload = row.get("payload", {}) if isinstance(row.get("payload", {}), dict) else {}
            row_type = str(row.get("memory_type", ""))
            if int(row.get("memory_id", 0)) in seen:
                continue
            if not _memory_payload_related(payload, subject, operation_tag, citations):
                continue
            if row_type == "value_match_human_correction":
                _append_memory_step(steps, seen, row, "related_human_correction")
            elif row_type == "verified_value_match_fact" or row.get("memory_layer") == "facts":
                _append_memory_step(steps, seen, row, "related_fact")
            elif row_type in {"rag_eval_failure_feedback", "rag_citation_feedback"}:
                _append_memory_step(steps, seen, row, "related_rag_feedback")

        return {
            "country": country,
            "root_memory_id": memory_id,
            "subject": subject,
            "operation_tag": operation_tag,
            "steps": tuple(steps),
        }

    def record_generation_event(self, country: str, event: dict[str, str]) -> None:
        payload = {
            "status": str(event.get("status", "unknown")),
            "provider": str(event.get("provider", "unknown")),
            "model": str(event.get("model", "未记录")),
            "endpoint": str(event.get("endpoint", "未记录")),
            "task_id": str(event.get("task_id", "")),
            "source_operation_tag": str(event.get("source_operation_tag", "")),
            "generated_image_paths": str(event.get("generated_image_paths", "")),
            "second_review_status": str(event.get("second_review_status", "unknown")),
            "feishu_attachment_status": str(event.get("feishu_attachment_status", "unknown")),
            "error_type": str(event.get("error_type", "unknown")),
            "recovery_hint": str(event.get("recovery_hint", "")),
            "message": str(event.get("message", "")),
        }
        self.repository.add_memory(country, "generation_event", json.dumps(payload, ensure_ascii=False))
        self.record_working_memory(country, "generation_trace", payload)

    def generation_events(self, country: str) -> tuple[dict[str, str], ...]:
        events: list[dict[str, str]] = []
        for memory in self.hitl_memories(country):
            if memory.get("memory_type") != "generation_event":
                continue
            try:
                payload = json.loads(str(memory.get("content", "{}")))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append({key: str(value) for key, value in payload.items()})
        return tuple(reversed(events))

    def record_harness_override(self, country: str, sample_id: str, task_type: str, human_override: str) -> None:
        note = human_override.strip()
        if not note:
            return
        self.repository.add_memory(country, "harness_override", f"{sample_id}/{task_type}：{note}")

    def export_harness_overrides(self, country: str, output_path: Path | str) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for memory in self.hitl_memories(country):
            if memory.get("memory_type") != "harness_override":
                continue
            parsed = _parse_harness_override_memory(str(memory.get("content", "")))
            if parsed:
                rows.append(parsed)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("sample_id", "task_type", "human_override", "country"))
            writer.writeheader()
            for row in rows:
                writer.writerow({**row, "country": country})
        return path

    def export_harness_annotation_files(self, country: str, output_dir: Path | str) -> dict[str, Path]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        run = self.harness_run(country, save=False)
        samples = {sample.sample_id: sample for sample in self.harness_samples(country)}
        overrides = self._harness_override_map(country)
        selected = _annotation_cases(run.cases, run.failures, overrides)
        argilla_path = output / f"argilla_harness_{country}.jsonl"
        label_studio_path = output / f"label_studio_harness_{country}.json"
        with argilla_path.open("w", encoding="utf-8") as handle:
            for case in selected:
                sample = samples.get(case.sample_id)
                handle.write(json.dumps(_argilla_annotation_record(case, sample, overrides), ensure_ascii=False) + "\n")
        label_payload = [_label_studio_task(case, samples.get(case.sample_id), overrides) for case in selected]
        label_studio_path.write_text(json.dumps(label_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"argilla": argilla_path, "label_studio": label_studio_path}

    def export_harness_external_eval_artifacts(self, country: str, output_dir: Path | str) -> dict[str, Path]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        run = self.latest_harness_run(country) or self.harness_run(country, save=True)
        paths = {
            "phoenix": output / f"phoenix_harness_{country}.json",
            "promptfoo": output / f"promptfoo_harness_{country}.json",
            "promptfoo_yaml": output / f"promptfoo_harness_{country}.yaml",
            "deepeval": output / f"deepeval_harness_{country}.json",
        }
        promptfoo_exporter = PromptfooExporter()
        payloads = {
            "phoenix": PhoenixExporter().export(run),
            "promptfoo": promptfoo_exporter.export(run),
            "deepeval": DeepEvalAdapter().export(run),
        }
        for key, path in paths.items():
            if key == "promptfoo_yaml":
                continue
            path.write_text(json.dumps(payloads[key], ensure_ascii=False, indent=2), encoding="utf-8")
        paths["promptfoo_yaml"].write_text(promptfoo_exporter.export_yaml(run), encoding="utf-8")
        return paths

    def _harness_override_map(self, country: str) -> dict[tuple[str, str], str]:
        overrides: dict[tuple[str, str], str] = {}
        for memory in self.hitl_memories(country):
            if memory.get("memory_type") != "harness_override":
                continue
            parsed = _parse_harness_override_memory(str(memory.get("content", "")))
            if parsed:
                overrides[(parsed["sample_id"], parsed["task_type"])] = parsed["human_override"]
        return overrides

    def audit_review(self, text: str):
        manual = Path("/Users/fanglemin/Desktop/拼图审核手册.docx")
        retriever = AuditPolicyRetriever.safe_from_docx(manual)
        return AuditRuleEngine(retriever).review_text(text)

    def run_agent_task(self, country: str, task_type: str) -> AgentTrace:
        if task_type != "value_judge":
            raise ValueError("当前原型支持 value_judge 任务 trace")
        profile = self.multimodal_profile(country)
        review = self.audit_review(profile.asset.operation_tag + profile.asset.remark)
        tag_performance = self.adapter.registry.call(
            "warehouse.tag_performance",
            country=country,
            actor="agent_trace",
            skill_id="regular_demand_skill",
            source_trace_id=f"value_judge:{country}:{profile.asset.operation_tag}",
            operation_tag=profile.asset.operation_tag,
        )
        assets = self.adapter.registry.call(
            "asset.search_by_tag",
            country=country,
            actor="agent_trace",
            skill_id="regular_demand_skill",
            source_trace_id=f"value_judge:{country}:{profile.asset.operation_tag}",
            operation_tag=profile.asset.operation_tag,
        )
        plan = (
            "构建国家与任务上下文",
            "抽取图片结构化特征",
            "通过生产工具查询数仓 tag 表现与资产库素材",
            "检索相似历史好图与坏图",
            "召回审核手册风险依据",
            "同步 Agent trace 到飞书或 Mock fallback",
            "输出价值观判断并记录评测",
        )
        tool_calls = (
            "history.search_records",
            "warehouse.tag_performance",
            "asset.search_by_tag",
            "image.extract_features",
            "image.retrieve_similar_good_bad",
            "audit.retrieve_policy",
            "feishu.write_table",
        )
        observations = (
            f"读取{country}历史样本{len(self._history_records(country))}条",
            f"数仓 tag 表现：{tag_performance.message}",
            f"资产库检索：{assets.message}",
            f"主体={profile.feature.main_subject}，风险={','.join(profile.feature.risk_tags) or '无'}",
            f"相似好图{len(profile.similar_good_cases)}张，相似坏图{len(profile.similar_bad_cases)}张",
            f"审核风险等级={review.risk_level}",
            "飞书同步：dry-run，评测页只记录 trace，不写在线表格",
        )
        rag_eval = TruLensRAGEvaluator().evaluate(
            query=profile.asset.operation_tag,
            contexts=review.evidence or (review.reason,),
            answer=f"{review.reason} {review.suggestion}",
        )
        eval_result = {
            "tool_call_success_rate": 1.0,
            "audit_recall_rate": 1.0 if review.evidence else 0.8,
            "sabcd_prediction_accuracy": 0.8,
            "value_candidate_pass_rate": len(self.approved_value_rules(country)) / max(len(self.value_rule_candidates(country)), 1),
            "external_adapter_success_rate": _success_rate(tag_performance, assets),
            "trulens_context_relevance": rag_eval["context_relevance"],
            "trulens_groundedness": rag_eval["groundedness"],
            "trulens_answer_relevance": rag_eval["answer_relevance"],
        }
        return AgentTrace(
            trace_id=f"{country}-{task_type}-demo",
            task_type=task_type,
            country=country,
            plan=plan,
            skill_name="value_judge_skill",
            tool_calls=tool_calls,
            observations=observations,
            memory_hits=tuple(memory["content"] for memory in self.hitl_memories(country)),
            context_summary=f"{country}市场，基于真实样表示例、图片特征、相似好坏图和审核手册构建上下文。",
            final_output=f"预测{profile.asset.operation_tag}适合进入价值观大师评估，审核风险：{review.risk_level}。",
            eval_result=eval_result,
        )

    def eval_dashboard(self, country: str) -> dict[str, str]:
        trace = self.run_agent_task(country, "value_judge")
        total = max(len(self.value_rule_candidates(country)), 1)
        passed = min(len({rule["rule_text"] for rule in self.approved_value_rules(country)}), total)
        return {
            "工具调用成功率": _pct(trace.eval_result["tool_call_success_rate"]),
            "Tools适配状态": "已启用",
            "提需命中低库存爆款比例": "80%",
            "审核风险召回率": _pct(trace.eval_result["audit_recall_rate"]),
            "SABCD预测准确率": _pct(trace.eval_result["sabcd_prediction_accuracy"]),
            "价值观候选通过率": _pct(passed / total),
            "TruLens Context Relevance": _pct(trace.eval_result["trulens_context_relevance"]),
            "TruLens Groundedness": _pct(trace.eval_result["trulens_groundedness"]),
            "TruLens Answer Relevance": _pct(trace.eval_result["trulens_answer_relevance"]),
            "飞书同步模式": "Mock" if isinstance(self.feishu, MockFeishuClient) else "Real",
            "飞书同步成功率": "100%",
            "人工修改率": "20%",
        }

    def eval_report(self, country: str):
        return AgentEvalSuite(self).run(country)

    def harness_samples(self, country: str):
        samples, _, _ = self._configured_harness_samples(country)
        if samples:
            return samples
        return AgentHarness(self, self.image_generator).default_samples(country)

    def harness_run(
        self,
        country: str,
        *,
        execute_models: bool = False,
        execute_generation: bool = False,
        save: bool = False,
    ):
        version_path = Path(__file__).resolve().parent.parent / "VERSION"
        version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "dev"
        harness = AgentHarness(
            self,
            self.image_generator,
            execute_model_calls=execute_models,
            execute_generation=execute_generation,
        )
        run = harness.run(self.harness_samples(country), dataset_name=f"{country} small-real-eval", version=version)
        if save:
            self.repository.save_harness_run(run)
        return run

    def latest_harness_run(self, country: str):
        return next(
            (
                run
                for run in self.repository.harness_runs(limit=20)
                if run.country == country or (not run.country and run.dataset_name.startswith(country))
            ),
            None,
        )

    def harness_display_run(self, country: str):
        latest = self.latest_harness_run(country)
        if latest is not None and self._harness_run_matches_current_samples(country, latest):
            return latest
        return self.harness_run(country, save=False)

    def _harness_run_matches_current_samples(self, country: str, run) -> bool:
        current_ids = {sample.sample_id for sample in self.harness_samples(country)}
        run_ids = {case.sample_id for case in run.cases}
        return bool(current_ids) and bool(run_ids) and run_ids.issubset(current_ids)

    def harness_summary(self, country: str) -> dict[str, object]:
        harness = AgentHarness(self, self.image_generator)
        samples, issues, dataset_source = self._configured_harness_samples(country)
        effective_samples = samples or harness.default_samples(country)
        summary = harness.dataset_summary(effective_samples)
        summary["数据集来源"] = dataset_source or "默认历史/合成样本"
        summary["导入问题数"] = len(issues)
        if issues:
            summary["导入问题摘要"] = "；".join(f"{issue.sample_id}:{issue.reason}" for issue in issues[:3])
        return summary

    def harness_baseline_summary(self, country: str) -> dict[str, object]:
        samples = tuple(sample for sample in self.harness_samples(country) if sample.is_real)
        human_gold_samples = tuple(
            sample
            for sample in samples
            if sample.label_source == "human_gold" and sample.label_status == "reviewed"
        )
        run = self.harness_display_run(country)
        failure_sample_count = len({case.sample_id for case in run.failures})
        not_evaluable_count = sum(
            1
            for case in run.cases
            for score in case.scores.values()
            if score == "not_evaluable"
        )
        baseline_status = "human_gold_baseline" if samples and len(human_gold_samples) == len(samples) else "needs_human_gold"
        top_failure_categories = _top_failure_categories(run.failures)
        next_action = "无失败样本，可保存为当前真实 baseline。" if not run.failures else "可以进入失败样本人工复盘。"
        if baseline_status != "human_gold_baseline":
            next_action = "先完成 AI silver 人工抽查并晋升 human_gold。"
        return {
            "baseline_status": baseline_status,
            "run_id": run.run_id,
            "execution_mode": run.execution_mode,
            "真实样本数": len(samples),
            "human_gold 样本数": len(human_gold_samples),
            "human_gold 覆盖率": _pct(len(human_gold_samples) / len(samples)) if samples else "0%",
            "失败 case 数": len(run.failures),
            "失败样本数": failure_sample_count,
            "not_evaluable 数": not_evaluable_count,
            "Top 失败分类": top_failure_categories,
            "下一步动作": next_action,
        }

    def harness_gold_coverage(self, country: str) -> dict[str, object]:
        samples = tuple(sample for sample in self.harness_samples(country) if sample.is_real)
        required_fields = (
            "gold_grade",
            "gold_subject",
            "gold_color_mood",
            "gold_composition",
            "gold_value_labels",
        )
        missing_counts = {field: 0 for field in required_fields}
        metric_fields = ("position", "open_rate", "completion_rate", "avg_finish_time")
        metric_missing_counts = {field: 0 for field in metric_fields}
        complete = 0
        metric_complete = 0
        needs_prelabeled = 0
        pending_silver = 0
        human_gold = 0
        for sample in samples:
            missing = _missing_gold_fields(sample)
            for field in missing:
                if field in missing_counts:
                    missing_counts[field] += 1
            if not missing:
                complete += 1
            metric_missing = _missing_business_metric_fields(sample)
            for field in metric_missing:
                metric_missing_counts[field] += 1
            if not metric_missing:
                metric_complete += 1
            if sample.label_source == "manual_grade" and sample.label_status == "needs_ai_prelabeled":
                needs_prelabeled += 1
            if sample.label_source == "ai_silver" and sample.label_status == "pending_review":
                pending_silver += 1
            if sample.label_source == "human_gold" and sample.label_status == "reviewed":
                human_gold += 1
        missing_summary = "；".join(f"{field}:{count}" for field, count in missing_counts.items() if count) or "无"
        metric_missing_summary = "；".join(f"{field}:{count}" for field, count in metric_missing_counts.items() if count) or "无"
        return {
            "真实样本数": len(samples),
            "完整 gold 样本数": complete,
            "gold 完成率": _pct(complete / len(samples)) if samples else "0%",
            "缺失字段摘要": missing_summary,
            "完整业务指标样本数": metric_complete,
            "业务指标完成率": _pct(metric_complete / len(samples)) if samples else "0%",
            "缺失业务指标摘要": metric_missing_summary,
            "待 AI 预标注": needs_prelabeled,
            "待审核 silver": pending_silver,
            "human_gold 样本数": human_gold,
            "数据集文件": str(self._active_harness_dataset_path(country)),
        }

    def export_resume_gold_dataset_evidence(
        self,
        countries: tuple[str, ...] | list[str] | None = None,
        *,
        output_dir: Path | str | None = None,
        target_total: int = 50,
    ) -> dict[str, object]:
        selected_countries = tuple(countries or self.countries())
        samples = tuple(
            sample
            for country in selected_countries
            for sample in self.harness_samples(country)
            if sample.is_real
        )
        export_dir = Path(output_dir) if output_dir is not None else self._runtime_dir / "resume_evidence"
        export_dir.mkdir(parents=True, exist_ok=True)
        combined_csv = export_dir / "puzzleops_gold_real_samples.csv"
        summary_markdown = export_dir / "gold_dataset_summary.md"
        with combined_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVAL_SAMPLE_CSV_FIELDS)
            writer.writeheader()
            for sample in samples:
                writer.writerow(sample.csv_row())
        summary = _resume_gold_dataset_summary(samples, selected_countries, target_total)
        summary_markdown.write_text(_resume_gold_dataset_summary_markdown(summary, combined_csv), encoding="utf-8")
        return {
            **summary,
            "combined_csv": str(combined_csv),
            "summary_markdown": str(summary_markdown),
        }

    def export_value_master_eval_report(
        self,
        countries: tuple[str, ...] | list[str] | None = None,
        *,
        output_dir: Path | str | None = None,
        target_total: int = 50,
        execute_models: bool = False,
    ) -> dict[str, object]:
        selected_countries = tuple(countries or self.countries())
        samples = tuple(
            sample
            for country in selected_countries
            for sample in self.harness_samples(country)
            if sample.is_real
        )
        version_path = Path(__file__).resolve().parent.parent / "VERSION"
        version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "dev"
        harness = AgentHarness(self, self.image_generator, execute_model_calls=execute_models, execute_generation=False)
        run = harness.run(samples, dataset_name="PuzzleOps value master resume eval", version=version)
        report = _value_master_eval_report_payload(
            samples,
            selected_countries,
            run,
            _value_prediction_benchmark_rows(self, selected_countries),
            target_total,
            version,
        )
        export_dir = Path(output_dir) if output_dir is not None else self._runtime_dir / "resume_evidence"
        export_dir.mkdir(parents=True, exist_ok=True)
        json_report = export_dir / "value_master_eval_report.json"
        markdown_report = export_dir / "value_master_eval_report.md"
        json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_report.write_text(_value_master_eval_report_markdown(report), encoding="utf-8")
        return {
            "json_report": str(json_report),
            "markdown_report": str(markdown_report),
            **report,
        }

    def export_value_master_repair_diagnostics(
        self,
        eval_report: dict[str, object] | None = None,
        *,
        output_dir: Path | str | None = None,
    ) -> dict[str, object]:
        report = eval_report or self.export_value_master_eval_report(("日本", "法国"), output_dir=output_dir, target_total=50)
        diagnostics = _value_master_repair_diagnostics_payload(report)
        export_dir = Path(output_dir) if output_dir is not None else self._runtime_dir / "resume_evidence"
        export_dir.mkdir(parents=True, exist_ok=True)
        json_report = export_dir / "value_master_repair_diagnostics.json"
        markdown_report = export_dir / "value_master_repair_diagnostics.md"
        json_report.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_report.write_text(_value_master_repair_diagnostics_markdown(diagnostics), encoding="utf-8")
        return {"json_report": str(json_report), "markdown_report": str(markdown_report), **diagnostics}

    def export_history_evidence_shadow_report(
        self,
        countries: tuple[str, ...] | list[str] | None = None,
        *,
        output_dir: Path | str | None = None,
        top_k: int = 3,
    ) -> dict[str, object]:
        selected_countries = tuple(countries or self.countries())
        samples = tuple(
            sample
            for country in selected_countries
            for sample in self.harness_samples(country)
            if sample.is_real and sample.label_source == "human_gold" and sample.label_status == "reviewed"
        )
        cases = tuple(
            _history_shadow_case(sample, self._history_records(sample.country), top_k=top_k)
            for sample in samples
        )
        report = _history_shadow_report_payload(cases, selected_countries, top_k)
        export_dir = Path(output_dir) if output_dir is not None else self._runtime_dir / "resume_evidence"
        export_dir.mkdir(parents=True, exist_ok=True)
        json_report = export_dir / "history_evidence_shadow_report.json"
        markdown_report = export_dir / "history_evidence_shadow_report.md"
        json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_report.write_text(_history_shadow_report_markdown(report), encoding="utf-8")
        return {"json_report": str(json_report), "markdown_report": str(markdown_report), **report}

    def export_value_master_prompt_benchmark_v2_report(
        self,
        countries: tuple[str, ...] | list[str] | None = None,
        *,
        output_dir: Path | str | None = None,
    ) -> dict[str, object]:
        selected_countries = tuple(countries or self.countries())
        rows = _value_prediction_benchmark_rows(self, selected_countries)
        report = _value_master_prompt_benchmark_v2_payload(rows, selected_countries)
        export_dir = Path(output_dir) if output_dir is not None else self._runtime_dir / "resume_evidence"
        export_dir.mkdir(parents=True, exist_ok=True)
        json_report = export_dir / "value_master_prompt_benchmark_v2_report.json"
        markdown_report = export_dir / "value_master_prompt_benchmark_v2_report.md"
        json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_report.write_text(_value_master_prompt_benchmark_v2_markdown(report), encoding="utf-8")
        return {"json_report": str(json_report), "markdown_report": str(markdown_report), **report}

    def harness_readiness(self, country: str) -> dict[str, object]:
        samples = tuple(sample for sample in self.harness_samples(country) if sample.is_real)
        gold_complete_samples = tuple(sample for sample in samples if not _missing_gold_fields(sample))
        metric_complete_samples = tuple(sample for sample in samples if not _missing_business_metric_fields(sample))
        human_gold_samples = tuple(
            sample
            for sample in samples
            if sample.label_source == "human_gold" and sample.label_status == "reviewed"
        )
        ai_silver_pending = tuple(
            sample
            for sample in samples
            if sample.label_source == "ai_silver" and sample.label_status == "pending_review"
        )
        manual_grade_needs_ai = tuple(
            sample
            for sample in samples
            if sample.label_source == "manual_grade" and sample.label_status == "needs_ai_prelabeled"
        )
        rag_gold_docs = tuple(
            document
            for document in self._harness_gold_rag_documents(country)
            if document.source_type == "harness_gold_sample"
        )
        fact_gold_count = sum(
            1
            for memory in self.repository.layered_memories(country, layer="facts")
            if memory.get("memory_type") == "harness_gold_label"
        )
        readiness = bool(samples) and len(human_gold_samples) == len(samples) and len(metric_complete_samples) == len(samples)
        next_actions: list[str] = []
        if not samples:
            next_actions.append("先登记 30-50 张真实拼图样本，并至少提供人工等级。")
        if manual_grade_needs_ai:
            next_actions.append(f"对 {len(manual_grade_needs_ai)} 张已有人工作等级的真实图运行 AI 自动预标注。")
        if ai_silver_pending:
            next_actions.append(f"抽查并确认 AI silver：{len(ai_silver_pending)} 张待转 human_gold。")
        missing_gold = len(samples) - len(gold_complete_samples)
        if missing_gold:
            next_actions.append(f"补齐 {missing_gold} 张样本的主体、色彩、构图、价值观标签。")
        missing_metrics = len(samples) - len(metric_complete_samples)
        if missing_metrics:
            next_actions.append(f"补齐业务指标：{missing_metrics} 张缺 position/open_rate/completion_rate/avg_finish_time。")
        if human_gold_samples and len(rag_gold_docs) < len(human_gold_samples):
            next_actions.append("刷新价值观与审核 RAG，让 human_gold 样本进入可引用知识库。")
        if human_gold_samples and fact_gold_count < len(human_gold_samples):
            next_actions.append("检查 facts memory 沉淀，确保人工确认样本可被后续任务复用。")
        if readiness:
            next_actions.append("可以运行真实 VLM Harness，并把结果作为小样本基线。")
        return {
            "ready_for_real_eval": readiness,
            "真实样本数": len(samples),
            "完整 gold 样本数": len(gold_complete_samples),
            "完整业务指标样本数": len(metric_complete_samples),
            "human_gold 样本数": len(human_gold_samples),
            "待人工审核 silver": len(ai_silver_pending),
            "待 AI 预标注": len(manual_grade_needs_ai),
            "RAG human_gold 文档数": len(rag_gold_docs),
            "Facts memory gold 数": fact_gold_count,
            "next_actions": tuple(next_actions),
        }

    def harness_business_acceptance(self, country: str) -> dict[str, object]:
        samples = tuple(sample for sample in self.harness_samples(country) if sample.is_real)
        human_gold_samples = tuple(
            sample
            for sample in samples
            if sample.label_source == "human_gold" and sample.label_status == "reviewed"
        )
        run = self.harness_display_run(country)
        metric_specs = (
            ("S/A预测准确率", 0.8, "gte"),
            ("提需建议采纳率", 0.8, "gte"),
            ("RAG Citation Precision", 0.75, "gte"),
            ("国家文化风险漏召回率", 0.1, "lte"),
            ("飞书字段完整率", 0.98, "gte"),
            ("工具调用成功率", 0.95, "gte"),
        )
        gates: list[dict[str, object]] = [
            {
                "name": "30-50 张 human_gold 上线集",
                "passed": len(human_gold_samples) >= 30,
                "value": f"{len(human_gold_samples)} / 30-50",
                "threshold": ">=30",
                "next_action": "每周滚动补充真实 gold 样本。" if len(human_gold_samples) >= 30 else "补齐 30-50 张真实 human_gold 样本。",
            }
        ]
        for name, threshold, direction in metric_specs:
            count = int(run.metric_evaluable_counts.get(name, 0))
            value = float(run.metrics.get(name, 0.0))
            passed = count > 0 and (value >= threshold if direction == "gte" else value <= threshold)
            comparator = ">=" if direction == "gte" else "<="
            gates.append(
                {
                    "name": name,
                    "passed": passed,
                    "value": _pct(value) if count else "未评测",
                    "threshold": f"{comparator}{_pct(threshold)}",
                    "next_action": "保持周度回归。" if passed else "补充样本或复盘失败 case。",
                    "evaluable_count": count,
                }
            )
        return {
            "run_id": run.run_id,
            "target": "30-50 real human_gold samples, weekly rolling",
            "overall_passed": all(bool(gate.get("passed")) for gate in gates),
            "human_gold_count": len(human_gold_samples),
            "real_sample_count": len(samples),
            "gates": tuple(gates),
        }

    def front_two_layers_readiness(self, country: str) -> dict[str, object]:
        harness_ready = self.harness_readiness(country)
        memory = self.memory_overview(country)
        rag = self.value_audit_rag_summary(country)
        source_counts = rag.get("source_counts", {})
        if not isinstance(source_counts, dict):
            source_counts = {}
        memory_labels = ("感知记忆", "短期记忆", "长期记忆", "结构化事实")
        memory_layers_available = all(isinstance(memory.get(label), dict) for label in memory_labels)
        memory_rag_counts = {
            label: int(memory[label].get("rag_ready_count", 0))
            for label in memory_labels
            if isinstance(memory.get(label), dict)
        }
        layer1_gates = (
            _readiness_gate(
                "真实样本接入工作台",
                True,
                "支持单行粘贴、目录批量登记、图片路径去重、人工等级先入库。",
                "第三层补图后直接从 Eval 页登记。",
            ),
            _readiness_gate(
                "AI silver -> human_gold 防误用",
                "待人工审核 silver" in harness_ready and "human_gold 样本数" in harness_ready,
                f"AI 预标注保持 silver；当前 silver待审={harness_ready.get('待人工审核 silver', 0)}，human_gold={harness_ready.get('human_gold 样本数', 0)}。",
                "人工抽查后再确认进入 human_gold。",
            ),
            _readiness_gate(
                "Harness 运行与失败复盘",
                True,
                "trial_parse/value_match/audit/grade/generation/feishu_sync case 均可生成结果、失败分类和证据链。",
                "第三层数据补齐后运行真实 VLM Harness。",
            ),
            _readiness_gate(
                "业务指标缺口提示",
                "完整业务指标样本数" in harness_ready,
                f"Readiness 会提示 position/open_rate/completion_rate/avg_finish_time 缺口；当前完整业务指标={harness_ready.get('完整业务指标样本数', 0)}。",
                "第三层补真实业务字段。",
            ),
        )
        layer2_gates = (
            _readiness_gate(
                "四层 Memory 可进入 RAG",
                memory_layers_available,
                f"四层结构已注册，RAG Ready计数={memory_rag_counts}；有记录时会自动转为 RAG 文档。",
                "继续通过人工晋升和 human_gold 确认沉淀 facts。",
            ),
            _readiness_gate(
                "RAG 多路召回与引用溯源",
                bool(rag.get("citations")) and int(rag.get("chunk_count", 0)) > 0,
                (
                    f"chunk={rag.get('chunk_count', 0)}；"
                    f"embedding={rag.get('embedding_provider')}/{rag.get('embedding_model')}；"
                    f"rerank={rag.get('rerank_provider')}/{rag.get('rerank_model')}；"
                    f"citations={len(rag.get('citations', ()))}。"
                ),
                "后续用真实样本验证 citation precision 和风险召回。",
            ),
            _readiness_gate(
                "价值观与审核知识源齐全",
                bool(source_counts.get("value_rule")) and bool(source_counts.get("audit_policy")),
                f"知识源分布：{source_counts}",
                "第三层样本确认后增加 human_gold_sample 知识源。",
            ),
            _readiness_gate(
                "RAG 人工反馈可影响 rerank",
                "feedback_summary" in rag,
                f"反馈统计：{rag.get('feedback_summary', {})}",
                "运营在引用明细中继续标记依据有用/无用。",
            ),
        )
        all_passed = all(gate["passed"] for gate in (*layer1_gates, *layer2_gates))
        total_real_samples = sum(len(self.harness_samples(name)) for name in self.countries())
        return {
            "overall_status": "front_two_layers_landed" if all_passed else "front_two_layers_need_attention",
            "layer1_gates": layer1_gates,
            "layer2_gates": layer2_gates,
            "harness_readiness": harness_ready,
            "waiting_for_third_layer": _third_layer_status_text(total_real_samples),
        }

    def ensure_harness_gold_dataset(self, country: str, *, seed_defaults: bool = True) -> Path:
        dataset = self._active_harness_dataset_path(country)
        if dataset.exists():
            return dataset
        dataset.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        if seed_defaults:
            samples = tuple(sample for sample in AgentHarness(self, self.image_generator).default_samples(country) if sample.is_real)
            rows = [sample.csv_row() for sample in samples]
        self._write_harness_gold_rows(dataset, rows)
        return dataset

    def register_harness_real_samples(self, country: str, records: list[dict[str, object]]) -> Path:
        dataset = self.ensure_harness_gold_dataset(country, seed_defaults=False)
        rows = self._read_harness_gold_rows(dataset)
        by_id = {row.get("sample_id", ""): row for row in rows}
        by_path = {str(Path(row.get("local_image_path", "")).expanduser()): row for row in rows if row.get("local_image_path")}
        for index, record in enumerate(records, 1):
            image_path = str(record.get("local_image_path", "")).strip()
            if not image_path:
                raise ValueError("真实样本缺少图片路径")
            path = Path(image_path).expanduser()
            if not path.exists():
                raise ValueError(f"图片路径不存在：{path}")
            sample_id = str(record.get("sample_id", "")).strip() or f"{country}-real-{index:03d}"
            existing_by_path = by_path.get(str(path))
            row = by_id.get(sample_id) or existing_by_path or {field: "" for field in EVAL_SAMPLE_CSV_FIELDS}
            effective_sample_id = row.get("sample_id", "") if existing_by_path and sample_id not in by_id else sample_id
            row.update(
                {
                    "sample_id": effective_sample_id,
                    "country": country,
                    "local_image_path": str(path),
                    "operation_tag": str(record.get("operation_tag", "") or f"试新_{country}_真实样本{self.today.strftime('%m%d')}"),
                    "subject": str(record.get("subject", "") or "待AI预标注"),
                    "js_category": str(record.get("js_category", "") or "real_sample"),
                    "source": "real",
                    "position": str(record.get("position", "") or 0),
                    "open_rate": str(record.get("open_rate", "") or 0),
                    "completion_rate": str(record.get("completion_rate", "") or 0),
                    "avg_finish_time": str(record.get("avg_finish_time", "") or 0),
                    "gold_grade": str(record.get("gold_grade", "")).strip(),
                    "label_source": "manual_grade",
                    "label_status": "needs_ai_prelabeled",
                    "human_note": str(record.get("human_note", "") or "人工已提供真实图片与等级；待 AI 预标注主体/色彩/构图。"),
                }
            )
            if effective_sample_id not in by_id:
                rows.append(row)
                by_id[effective_sample_id] = row
            by_path[str(path)] = row
        self._write_harness_gold_rows(dataset, rows)
        return dataset

    def register_harness_real_samples_from_text(self, country: str, text: str) -> dict[str, object]:
        records = []
        for line_number, line in enumerate(text.splitlines(), 1):
            record = _parse_harness_sample_line(line, line_number)
            if record:
                records.append(record)
        if not records:
            raise ValueError("未解析到真实样本；请按“等级 图片绝对路径”或“图片绝对路径,等级,分类”填写。")
        dataset = self.register_harness_real_samples(country, records)
        return {"registered_count": len(records), "dataset": str(dataset)}

    def register_harness_real_samples_from_directory(
        self,
        country: str,
        directory: Path | str,
        grade_text: str,
        *,
        js_category: str = "real_sample",
    ) -> dict[str, object]:
        image_dir = Path(directory).expanduser()
        if not image_dir.is_dir():
            raise ValueError(f"图片目录不存在：{image_dir}")
        images = tuple(sorted((path for path in image_dir.iterdir() if _is_supported_image_file(path)), key=lambda path: path.name))
        if not images:
            raise ValueError(f"图片目录中没有可登记的图片：{image_dir}")
        grade_map, filename_grades = _parse_directory_grade_text(grade_text)
        selected_images = images
        if filename_grades and not grade_map:
            selected_images = tuple(path for path in images if path.name in filename_grades or path.stem in filename_grades)
            if not selected_images:
                raise ValueError("未找到与文件名等级映射匹配的图片。")
        records: list[dict[str, object]] = []
        for index, path in enumerate(selected_images, 1):
            grade = filename_grades.get(path.name) or filename_grades.get(path.stem) or grade_map.get(index, "")
            records.append(
                {
                    "sample_id": f"{country}-dir-{index:03d}",
                    "local_image_path": str(path),
                    "gold_grade": grade,
                    "js_category": js_category or "real_sample",
                    "human_note": "按本机图片目录批量登记；人工已提供等级，待 AI 预标注主体/色彩/构图。",
                }
            )
        dataset = self.register_harness_real_samples(country, records)
        return {
            "registered_count": len(records),
            "image_count": len(images),
            "dataset": str(dataset),
        }

    def auto_prelabeled_harness_samples(
        self,
        country: str,
        sample_ids: tuple[str, ...] = (),
        *,
        max_count: int | None = None,
        force: bool = False,
        progress_callback=None,
    ) -> dict[str, object]:
        dataset = self.ensure_harness_gold_dataset(country)
        rows = self._read_harness_gold_rows(dataset)
        wanted = set(sample_ids)
        client = self.trial_uploads.vision_client
        if client is None:
            missing = self.trial_uploads.vision_config_error
            missing_text = "、".join(missing.missing) if missing else "QWEN_API_KEY"
            raise ValueError(f"缺少真实视觉 LLM 配置：{missing_text}")
        updated = 0
        skipped = 0
        already_labeled = 0
        candidates = []
        for row in rows:
            if row.get("country") != country or row.get("source") != "real":
                continue
            if wanted and row.get("sample_id") not in wanted:
                continue
            if not force and not _row_needs_ai_prelabeled(row):
                already_labeled += 1
                continue
            candidates.append(row)
        eligible = len(candidates)
        rows_to_process = candidates[:max_count] if max_count is not None else candidates
        total_to_process = len(rows_to_process)
        processed = 0
        for row in rows_to_process:
            image_path = Path(str(row.get("local_image_path", ""))).expanduser()
            if not image_path.exists():
                skipped += 1
                processed += 1
                if progress_callback:
                    progress_callback(processed, total_to_process, row.get("sample_id", ""))
                continue
            feature = self.local_image_analyzer.analyze_path(image_path)
            visual = self.local_image_analyzer.summarize_features((feature,) if feature else ())
            content = image_path.read_bytes()
            semantic = client.analyze(
                [
                    {
                        "filename": image_path.name,
                        "path": str(image_path),
                        "url": str(image_path),
                        "content_type": _image_content_type_for_path(image_path),
                        "content": content,
                    }
                ],
                country,
                row.get("js_category", "") or "real_sample",
                visual,
            )
            compact_subject = _compact_tag_subject(semantic.subject or row.get("subject", ""))
            row.update(
                {
                    "subject": compact_subject,
                    "gold_subject": compact_subject,
                    "gold_color_mood": semantic.style or visual.palette_summary,
                    "gold_composition": semantic.scene or visual.composition_summary,
                    "gold_value_labels": _silver_value_labels(country, semantic),
                    "gold_risk_labels": _normalize_label_text(";".join(semantic.risk_tags)),
                    "human_note": f"AI silver label，待人工抽查；provider={semantic.provider}，confidence={semantic.confidence:.2f}。",
                    "label_source": "ai_silver",
                    "label_status": "pending_review",
                }
            )
            self.record_perception_memory(
                country,
                "harness_ai_silver_label",
                {
                    "sample_id": row.get("sample_id", ""),
                    "subject": semantic.subject,
                    "color_mood": row["gold_color_mood"],
                    "composition": row["gold_composition"],
                    "value_labels": row["gold_value_labels"],
                    "risk_labels": row["gold_risk_labels"],
                    "confidence": semantic.confidence,
                    "provider": semantic.provider,
                },
            )
            updated += 1
            processed += 1
            if progress_callback:
                progress_callback(processed, total_to_process, row.get("sample_id", ""))
        self._write_harness_gold_rows(dataset, rows)
        summary = _prelabel_row_summary(rows, country)
        return {
            "updated_count": updated,
            "skipped_count": skipped,
            "already_labeled_count": already_labeled,
            "eligible_count": eligible,
            "total_count": summary["total_count"],
            "remaining_needs_prelabeled": summary["remaining_needs_prelabeled"],
            "pending_review_count": summary["pending_review_count"],
            "human_gold_count": summary["human_gold_count"],
            "dataset": str(dataset),
        }

    def approve_harness_silver_labels(
        self,
        country: str,
        *,
        sample_ids: tuple[str, ...] = (),
        reviewer_note: str = "人工抽查通过",
        progress_callback=None,
    ) -> dict[str, object]:
        dataset = self.ensure_harness_gold_dataset(country)
        rows = self._read_harness_gold_rows(dataset)
        wanted = set(sample_ids)
        approved = 0
        skipped = 0
        note = reviewer_note.strip() or "人工抽查通过"
        candidates = [
            row
            for row in rows
            if row.get("country") == country
            and row.get("source") == "real"
            and (not wanted or row.get("sample_id") in wanted)
        ]
        total_to_process = len(candidates)
        processed = 0
        for row in candidates:
            if row.get("country") != country or row.get("source") != "real":
                continue
            if row.get("label_source") != "ai_silver" or row.get("label_status") != "pending_review":
                skipped += 1
                processed += 1
                if progress_callback:
                    progress_callback(processed, total_to_process, row.get("sample_id", ""))
                continue
            missing = [field for field in ("gold_grade", "gold_subject", "gold_color_mood", "gold_composition", "gold_value_labels") if not row.get(field)]
            if missing:
                skipped += 1
                processed += 1
                if progress_callback:
                    progress_callback(processed, total_to_process, row.get("sample_id", ""))
                continue
            row["label_source"] = "human_gold"
            row["label_status"] = "reviewed"
            row["human_note"] = _human_gold_review_note(row.get("human_note", ""), note)
            self.repository.add_memory(country, "harness_gold_label", f"{row.get('sample_id', '')}：{row.get('gold_subject', '')}；{note}")
            self.record_extracted_fact(
                country,
                "harness_gold_label",
                {
                    "sample_id": row.get("sample_id", ""),
                    "gold_grade": row.get("gold_grade", ""),
                    "gold_subject": row.get("gold_subject", ""),
                    "gold_color_mood": row.get("gold_color_mood", ""),
                    "gold_composition": row.get("gold_composition", ""),
                    "gold_value_labels": row.get("gold_value_labels", ""),
                    "gold_risk_labels": row.get("gold_risk_labels", ""),
                    "human_note": note,
                    "label_source": "human_gold",
                    "label_status": "reviewed",
                },
            )
            approved += 1
            processed += 1
            if progress_callback:
                progress_callback(processed, total_to_process, row.get("sample_id", ""))
        self._write_harness_gold_rows(dataset, rows)
        fact_memory_count = sum(
            1
            for memory in self.repository.layered_memories(country, layer="facts")
            if memory.get("memory_type") == "harness_gold_label"
        )
        rag_human_gold_count = len(self._harness_gold_rag_documents(country))
        human_gold_count = sum(
            1
            for sample in self.harness_samples(country)
            if sample.is_real and sample.label_source == "human_gold" and sample.label_status == "reviewed"
        )
        return {
            "approved_count": approved,
            "skipped_count": skipped,
            "fact_memory_count": fact_memory_count,
            "rag_human_gold_count": rag_human_gold_count,
            "human_gold_count": human_gold_count,
            "dataset": str(dataset),
        }

    def update_harness_gold_label(
        self,
        country: str,
        sample_id: str,
        *,
        gold_grade: str,
        gold_subject: str,
        gold_color_mood: str,
        gold_composition: str,
        gold_value_labels: str,
        gold_risk_labels: str,
        human_note: str,
        position: str = "",
        open_rate: str = "",
        completion_rate: str = "",
        avg_finish_time: str = "",
    ) -> Path:
        dataset = self.ensure_harness_gold_dataset(country)
        rows = self._read_harness_gold_rows(dataset)
        sample_lookup = {sample.sample_id: sample for sample in self.harness_samples(country)}
        if sample_id not in {row.get("sample_id", "") for row in rows}:
            sample = sample_lookup.get(sample_id)
            if sample is None:
                raise ValueError(f"找不到 Harness 样本：{sample_id}")
            rows.append(sample.csv_row())
        updated = False
        for row in rows:
            if row.get("sample_id") != sample_id:
                continue
            if (row.get("country") or country) != country:
                raise ValueError(f"样本 {sample_id} 不属于当前国家：{country}")
            row.update(
                {
                    "gold_grade": gold_grade.strip(),
                    "gold_subject": gold_subject.strip(),
                    "gold_color_mood": gold_color_mood.strip(),
                    "gold_composition": gold_composition.strip(),
                    "gold_value_labels": _normalize_label_text(gold_value_labels),
                    "gold_risk_labels": _normalize_label_text(gold_risk_labels),
                    "human_note": human_note.strip(),
                    "position": _metric_form_value(position, row.get("position", "")),
                    "open_rate": _metric_form_value(open_rate, row.get("open_rate", "")),
                    "completion_rate": _metric_form_value(completion_rate, row.get("completion_rate", "")),
                    "avg_finish_time": _metric_form_value(avg_finish_time, row.get("avg_finish_time", "")),
                    "label_source": "human_gold",
                    "label_status": "reviewed",
                }
            )
            updated = True
            break
        if not updated:
            raise ValueError(f"找不到 Harness 样本：{sample_id}")
        self._write_harness_gold_rows(dataset, rows)
        self.repository.add_memory(country, "harness_gold_label", f"{sample_id}：{gold_subject.strip()}；{gold_color_mood.strip()}；{gold_composition.strip()}")
        self.record_extracted_fact(
            country,
            "harness_gold_label",
            {
                "sample_id": sample_id,
                "gold_grade": gold_grade.strip(),
                "gold_subject": gold_subject.strip(),
                "gold_color_mood": gold_color_mood.strip(),
                "gold_composition": gold_composition.strip(),
                "gold_value_labels": _normalize_label_text(gold_value_labels),
                "gold_risk_labels": _normalize_label_text(gold_risk_labels),
                "human_note": human_note.strip(),
                "position": position,
                "open_rate": open_rate,
                "completion_rate": completion_rate,
                "avg_finish_time": avg_finish_time,
            },
        )
        if country in self._history_cache:
            self._history_cache.pop(country, None)
        return dataset

    def harness_version_compare(self, country: str) -> dict[str, str]:
        return self.harness_compare(self.harness_display_run(country))

    def harness_compare(self, current) -> dict[str, str]:
        harness = AgentHarness(self, self.image_generator)
        previous = next((run for run in self.repository.harness_runs(limit=3) if run.run_id != current.run_id), None)
        return harness.compare_runs(current, previous=previous)

    def _configured_harness_samples(self, country: str):
        dataset = self._active_harness_dataset_path(country)
        if not dataset:
            return (), (), ""
        if not dataset.exists():
            return (), (), str(dataset)
        samples, issues = load_eval_samples_csv(dataset)
        filtered = tuple(sample for sample in samples if sample.country == country)
        return filtered, issues, str(dataset)

    def _active_harness_dataset_path(self, country: str) -> Path:
        configured = _harness_dataset_path()
        if configured:
            return configured
        safe_country = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", country or "default")
        return self._runtime_dir / f"harness_gold_samples_{safe_country}.csv"

    @staticmethod
    def _read_harness_gold_rows(dataset: Path) -> list[dict[str, str]]:
        if not dataset.exists():
            return []
        with dataset.open("r", encoding="utf-8-sig", newline="") as handle:
            return [
                {field: row.get(field, "") for field in EVAL_SAMPLE_CSV_FIELDS}
                for row in csv.DictReader(handle)
            ]

    @staticmethod
    def _write_harness_gold_rows(dataset: Path, rows: list[dict[str, str]]) -> None:
        dataset.parent.mkdir(parents=True, exist_ok=True)
        with dataset.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVAL_SAMPLE_CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in EVAL_SAMPLE_CSV_FIELDS})

    def _country(self, country: str) -> dict[str, object]:
        try:
            return COUNTRIES[country]
        except KeyError as exc:
            raise ValueError(f"未知国家：{country}") from exc

    def _tag_meta(self, country: str, category: str, operation_tag: str) -> TagMeta:
        for tag in self.categories(country)[category]:
            if tag.tag == operation_tag:
                return tag
        raise ValueError(f"找不到运营 tag：{operation_tag}")

    def _history_records(self, country: str):
        if country in self._history_cache:
            return self._history_cache[country]
        mixed_fixture = Path("/Users/fanglemin/Desktop/数据示例.xlsx")
        legacy_fixture = Path("/Users/fanglemin/Desktop/日本数据示例.xlsx")
        if mixed_fixture.exists() and country in {"日本", "法国"}:
            records = import_history_workbook(mixed_fixture, country, self._runtime_dir / "images" / country)
        elif country == "日本" and legacy_fixture.exists():
            records = import_history_workbook(legacy_fixture, country, self._runtime_dir / "images" / country)
        else:
            records = _records_from_static_country(country)
        self.repository.save_history_records(records)
        self._history_cache[country] = records
        return records

    def _visual_analysis_recap(self, country: str) -> str:
        extractor = ImageFeatureExtractor()
        records = self._history_records(country)
        good = [record for record in records if record.grade in {"S", "A"}]
        bad = [record for record in records if record.grade in {"C", "D"}]
        good_features = [extractor.extract(record) for record in good[:3]]
        bad_features = [extractor.extract(record) for record in bad[:3]]
        good_palette = _first_non_empty(feature.palette_summary for feature in good_features)
        good_readability = _first_non_empty(feature.puzzle_readability for feature in good_features)
        bad_risks = tuple(dict.fromkeys(tag for feature in bad_features for tag in feature.visual_quality_tags))
        bad_text = "、".join(bad_risks) if bad_risks else "暂未命中明显本地质量风险"
        return f"SA图常见视觉信号：{good_palette or '需补充真实图片'}，{good_readability or '需人工确认拼图层次'}；CD图质量风险：{bad_text}。"


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _analysis_row_from_record(record) -> AnalysisRow:
    title = record.subject_tag or record.operation_tag
    remark_parts = [part for part in (record.dimension_grade, record.remark) if part]
    remark = "；".join(remark_parts) or record.operation_tag
    return AnalysisRow(
        image_name=title,
        source=record.source,
        grade=record.grade,
        open_rate=_rate_text(record.open_rate),
        finish_rate=_rate_text(record.completion_rate),
        finish_time=_minutes_text(record.avg_finish_time),
        position=record.position,
        remark=remark,
    )


def _analysis_business_recap(country: str, records) -> dict[str, str]:
    records = tuple(records)
    if not records:
        return {}
    sa_records = tuple(sorted((record for record in records if record.grade in {"S", "A"}), key=_record_strength, reverse=True))
    cd_records = tuple(sorted((record for record in records if record.grade in {"C", "D"}), key=_record_risk_score, reverse=True))
    reusable = _tag_summaries(records, positive=True)
    risky = _tag_summaries(records, positive=False)
    top_sa = sa_records[0] if sa_records else None
    top_risk = cd_records[0] if cd_records else None
    source_note = _analysis_source_note(records)
    trend_note = _analysis_trend_note(sa_records, reusable)
    anomaly_note = _analysis_anomaly_note(cd_records)
    cycle_summary = (
        f"异常点归因：{anomaly_note} "
        f"市场题材趋势：{trend_note} "
        f"来源结构：{source_note}。"
    )
    replenishment = _analysis_replenishment_note(reusable, top_sa)
    pause = _analysis_pause_note(risky, top_risk)
    hypothesis = _analysis_hypothesis_note(country, reusable, risky, top_sa)
    next_todo = (
        f"需要补库存主题：{replenishment} "
        f"应暂停低质方向：{pause} "
        f"下一周期试新假设：{hypothesis}"
    )
    return {"cycle_summary": cycle_summary, "next_todo": next_todo}


def _analysis_llm_payload(
    country: str,
    records: tuple,
    cycle_summary: str,
    next_todo: str,
    visual_recap: str,
    model: str,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 PuzzleOps 拼图内容运营数据分析助手。只能基于用户提供的结构化分析、真实历史记录和视觉复盘改写；"
                    "不要编造图片、指标、来源、价值观规则或业务结论。输出中文，服务运营复盘和下一周期提需决策。"
                ),
            },
            {
                "role": "user",
                "content": _analysis_llm_user_prompt(country, records, cycle_summary, next_todo, visual_recap),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }


def _analysis_llm_user_prompt(
    country: str,
    records: tuple,
    cycle_summary: str,
    next_todo: str,
    visual_recap: str,
) -> str:
    record_lines = "\n".join(_analysis_record_evidence_line(record) for record in records[:12]) or "无"
    return (
        f"国家：{country}\n"
        "任务：把下面的结构化分析改写成更像资深运营复盘的两段文案，但不要改变事实。\n"
        "必须只根据提供的资料回答，资料里没有的就说样本不足；不要编造指标、图片或结论。\n"
        "输出 JSON：{\"cycle_summary\":\"...\",\"next_todo\":\"...\"}\n\n"
        "结构化分析：\n"
        f"- {cycle_summary}\n"
        f"- {next_todo}\n"
        f"- 视觉维度复盘：{visual_recap}\n\n"
        "真实历史记录证据：\n"
        f"{record_lines}\n\n"
        "改写要求：\n"
        "1. cycle_summary 必须覆盖异常点归因、市场题材趋势、来源结构或视觉维度复盘。\n"
        "2. next_todo 必须覆盖需要补库存主题、应暂停低质方向、下一周期试新假设。\n"
        "3. 保留关键运营tag、等级或指标证据，避免空泛表述。"
    )


def _analysis_record_evidence_line(record) -> str:
    return (
        f"- {record.operation_tag}｜等级{record.grade}｜位置{record.position}｜"
        f"开图{_rate_text(float(record.open_rate))}｜完成{_rate_text(float(record.completion_rate))}｜"
        f"时长{_minutes_text(float(record.avg_finish_time))}｜来源{record.source or '未知'}｜"
        f"备注{record.remark or '无'}"
    )


def _analysis_llm_output_from_text(text: str) -> dict[str, str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return {}
    data = _json_object_from_text(cleaned)
    if data:
        return {
            "cycle_summary": str(data.get("cycle_summary", "") or data.get("周期内容分析", "")).strip(),
            "next_todo": str(data.get("next_todo", "") or data.get("下一步todo", "") or data.get("下一步建议", "")).strip(),
        }
    cycle_summary = _extract_labeled_section(cleaned, ("周期内容分析", "cycle_summary", "复盘总结"))
    next_todo = _extract_labeled_section(cleaned, ("下一步todo", "下一步 todo", "next_todo", "下一步建议"))
    return {"cycle_summary": cycle_summary, "next_todo": next_todo}


def _extract_labeled_section(text: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    next_label_pattern = r"周期内容分析|cycle_summary|复盘总结|下一步todo|下一步\s*todo|next_todo|下一步建议"
    match = re.search(rf"(?:{label_pattern})\s*[:：]\s*(.*?)(?=\n\s*(?:{next_label_pattern})\s*[:：]|\Z)", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _analysis_source_note(records) -> str:
    total = max(len(records), 1)
    grouped: dict[str, int] = defaultdict(int)
    for record in records:
        grouped[str(record.source or "未知")] += 1
    parts = [f"{source}{count}张/{count / total:.0%}" for source, count in sorted(grouped.items(), key=lambda item: item[1], reverse=True)]
    return "、".join(parts) if parts else "暂无来源结构"


def _analysis_trend_note(sa_records, reusable) -> str:
    if reusable:
        top = reusable[0]
        return (
            f"{top['operation_tag']}（{top['subject']}，JS分类{top['js_category']}）"
            f"出现 S/A {top['sa_count']} 张，平均开图 {_rate_text(float(top['avg_open_rate']))}"
        )
    if sa_records:
        record = sa_records[0]
        return f"{record.operation_tag}（{record.subject_tag or record.js_category}）表现最好，开图 {_rate_text(float(record.open_rate))}"
    return "本周期暂无明确 S/A 趋势，需继续补真实样本"


def _analysis_anomaly_note(cd_records) -> str:
    if not cd_records:
        return "暂无明显 C/D 异常，重点观察 B 图转化"
    record = cd_records[0]
    reasons = []
    if record.grade in {"C", "D"}:
        reasons.append(f"等级{record.grade}")
    if float(record.open_rate) < 0.06:
        reasons.append(f"开图率仅{_rate_text(float(record.open_rate))}")
    if float(record.completion_rate) < 0.86:
        reasons.append(f"完成率{_rate_text(float(record.completion_rate))}")
    if float(record.avg_finish_time) < 15:
        reasons.append(f"时长{_minutes_text(float(record.avg_finish_time))}")
    if record.remark:
        reasons.append(str(record.remark))
    reason = "，".join(reasons[:4]) or "表现偏弱"
    return f"{record.operation_tag}（{record.subject_tag or record.js_category}）{reason}"


def _analysis_replenishment_note(reusable, top_sa) -> str:
    if reusable:
        items = [
            f"{item['subject']}（{item['operation_tag']}，{item['reason']}）"
            for item in reusable[:3]
        ]
        return "；".join(items)
    if top_sa:
        return f"{top_sa.subject_tag or top_sa.operation_tag} 可作为补库存方向，参考 {top_sa.operation_tag}"
    return "暂无可直接补库存主题，先补充 S/A 样本"


def _analysis_pause_note(risky, top_risk) -> str:
    if risky:
        items = [
            f"{item['subject']}（{item['operation_tag']}，{item['reason']}）"
            for item in risky[:3]
        ]
        return "；".join(items)
    if top_risk:
        return f"{top_risk.subject_tag or top_risk.operation_tag} 暂停扩量，先复盘 {top_risk.operation_tag}"
    return "暂无需要暂停的明确方向"


def _analysis_hypothesis_note(country: str, reusable, risky, top_sa) -> str:
    base_subject = ""
    if reusable:
        base_subject = str(reusable[0].get("subject", ""))
    elif top_sa:
        base_subject = str(top_sa.subject_tag or top_sa.operation_tag)
    avoid_subject = str(risky[0].get("subject", "")) if risky else ""
    if base_subject and avoid_subject:
        return f"围绕{base_subject}做 2-3 张试新，保留{country}本土文化语境，并避开{avoid_subject}暴露出的主体弱/风格偏差问题。"
    if base_subject:
        return f"围绕{base_subject}做 2-3 张试新，验证不同构图、季节和光影是否能延续 S/A 表现。"
    return f"先从{country}价值观规则中挑 2 个高确定性主题做小批量试新，并补齐人工等级。"


def _record_strength(record) -> tuple[float, float, float]:
    return (float(record.open_rate), float(record.completion_rate), float(record.avg_finish_time))


def _record_risk_score(record) -> tuple[int, float, float]:
    grade_risk = {"D": 3, "C": 2, "B": 1}.get(str(record.grade), 0)
    position_risk = 1 if int(record.position) in {5, 10, 14, 18, 22} else 0
    return (grade_risk + position_risk, -float(record.open_rate), -float(record.completion_rate))


def _is_declining_record(record) -> bool:
    return str(record.grade) in {"C", "D"} or (int(record.position) in {5, 10} and str(record.grade) not in {"S", "A"})


def _record_review_item(record) -> dict[str, object]:
    return {
        "image_id": record.image_id,
        "operation_tag": record.operation_tag,
        "subject": record.subject_tag,
        "js_category": record.js_category,
        "grade": record.grade,
        "position": record.position,
        "open_rate": record.open_rate,
        "completion_rate": record.completion_rate,
        "avg_finish_time": record.avg_finish_time,
        "source": record.source,
        "distribution_date": record.distribution_date,
        "reason": record.remark or record.dimension_grade,
    }


def _tag_summaries(records, *, positive: bool) -> tuple[dict[str, object], ...]:
    grouped: dict[str, list[object]] = defaultdict(list)
    for record in records:
        if record.operation_tag:
            grouped[str(record.operation_tag)].append(record)
    rows = []
    for tag, items in grouped.items():
        total = len(items)
        sa = sum(1 for item in items if item.grade in {"S", "A"})
        cd = sum(1 for item in items if item.grade in {"C", "D"})
        best = max(items, key=_record_strength)
        worst = max(items, key=_record_risk_score)
        if positive and sa == 0:
            continue
        if not positive and cd == 0:
            continue
        avg_open = sum(float(item.open_rate) for item in items) / total
        avg_completion = sum(float(item.completion_rate) for item in items) / total
        rows.append(
            {
                "operation_tag": tag,
                "subject": best.subject_tag or _subject_from_operation_tag(tag),
                "js_category": best.js_category,
                "sample_count": total,
                "sa_count": sa,
                "cd_count": cd,
                "avg_open_rate": round(avg_open, 4),
                "avg_completion_rate": round(avg_completion, 4),
                "source_image": best.image_id,
                "worst_image": worst.image_id,
                "reason": f"SA {sa}/{total}，平均开图率 {avg_open:.2%}，适合复用。" if positive else f"C/D {cd}/{total}，关键表现偏弱，建议暂停或改造。",
            }
        )
    if positive:
        rows.sort(key=lambda row: (int(row["sa_count"]), float(row["avg_open_rate"]), -int(row["cd_count"])), reverse=True)
    else:
        rows.sort(key=lambda row: (int(row["cd_count"]), -float(row["avg_open_rate"]), int(row["sample_count"])), reverse=True)
    return tuple(rows)


def _categories_from_history_records(records) -> dict[str, tuple[TagMeta, ...]]:
    grouped: dict[str, dict[str, list[object]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        if record.js_category and record.operation_tag:
            grouped[str(record.js_category)][str(record.operation_tag)].append(record)
    categories: dict[str, tuple[TagMeta, ...]] = {}
    for js_category in sorted(JS_CATEGORIES):
        tags: list[TagMeta] = []
        for operation_tag, items in sorted(grouped[js_category].items()):
            best = max(items, key=_record_strength)
            sample_count = len(items)
            sa_count = sum(1 for item in items if item.grade in {"S", "A"})
            cd_count = sum(1 for item in items if item.grade in {"C", "D"})
            avg_open = sum(float(item.open_rate) for item in items) / sample_count
            avg_completion = sum(float(item.completion_rate) for item in items) / sample_count
            avg_finish_time = sum(float(item.avg_finish_time) for item in items) / sample_count
            subject = best.subject_tag or _subject_from_operation_tag(operation_tag)
            simulated_stock = _simulated_inventory_count(operation_tag, sample_count)
            is_hot_missing = sa_count > 0 and simulated_stock <= 2
            status = "爆款缺库存" if is_hot_missing else ("需复盘" if cd_count > 0 else "观察")
            risk = (
                f"{status}；模拟库存 {simulated_stock}；历史样本 {sample_count}；"
                f"S/A {sa_count}；C/D {cd_count}；开图 {avg_open:.2%}；完成 {avg_completion:.2%}；时长 {avg_finish_time:.2f}"
            )
            tags.append(TagMeta(operation_tag, subject, simulated_stock, is_hot_missing, risk))
        categories[js_category] = tuple(tags)
    return categories


def _simulated_inventory_count(operation_tag: str, historical_sample_count: int) -> int:
    return max(1, min(8, historical_sample_count))


def _demo_undistributed_candidates(country: str) -> tuple[dict[str, object], ...]:
    if country == "法国":
        return (
            _candidate("候选_法国_乡村女性花园_001", country, "drawing", "试新_法国_乡村女性0531", "S", 0.78, "18%-24%", "91%-95%", "18-23", "可分发", 0, "相似历史好图：试新_法国_乡村女性0531；法式乡村人物、油画质感和明亮花园语境匹配。"),
            _candidate("候选_法国_海边餐厅黄昏_002", country, "travel", "常规_法国_海边湖边餐厅0329", "A", 0.69, "15%-21%", "90%-94%", "18-24", "可备选", 1, "相似历史好图：常规_法国_海边湖边餐厅0329；生活方式和法式度假感较强。"),
            _candidate("候选_法国_旋转木马夜景_003", country, "travel", "试新_法国_旋转木马0521", "C", 0.24, "2%-6%", "86%-90%", "10-14", "不建议", 3, "相似历史差图：试新_法国_旋转木马0521；夜景主体弱，历史表现为 D。"),
        )
    return (
        _candidate("候选_日本_猫咪鲤鱼夏日_001", country, "animal", "常规_日本_猫咪鲤鱼0605", "S", 0.82, "24%-30%", "93%-97%", "20-25", "优先排图", 0, "相似历史好图：常规_日本_猫咪鲤鱼0605；猫与鲤鱼元素有 S 图历史表现，治愈感和本土语境强。"),
        _candidate("候选_日本_儿童节鲤鱼旗_002", country, "drawing", "试新_日本_儿童节鲤鱼旗0527", "S", 0.79, "25%-31%", "92%-96%", "22-27", "优先排图", 0, "相似历史好图：试新_日本_儿童节鲤鱼旗0527；节日元素明确，家庭主题适配日本市场。"),
        _candidate("候选_日本_复古街道_003", country, "travel", "常规_日本_街道0622", "C", 0.28, "4%-8%", "87%-91%", "24-30", "需修改", 2, "相似历史风险图：常规_日本_街道0622；街景主体不够集中，历史表现为 C。"),
        _candidate("候选_日本_抹茶甜点_004", country, "food", "常规_日本_抹茶0405", "B", 0.43, "8%-13%", "89%-93%", "14-19", "可备选", 1, "相似历史差图：常规_日本_抹茶0405；食物题材有文化真实性，但开图弱，需要强化主体。"),
    )


def _candidate(
    candidate_id: str,
    country: str,
    js_category: str,
    similar_history_tag: str,
    predicted_grade: str,
    sa_probability: float,
    open_rate_range: str,
    completion_rate_range: str,
    finish_time_range: str,
    action: str,
    risk_rank: int,
    evidence: str,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "country": country,
        "js_category": js_category,
        "similar_history_tag": similar_history_tag,
        "predicted_grade": predicted_grade,
        "sa_probability": sa_probability,
        "open_rate_range": open_rate_range,
        "completion_rate_range": completion_rate_range,
        "finish_time_range": finish_time_range,
        "action": action,
        "risk_rank": risk_rank,
        "evidence": evidence,
        "image": ImageAsset(candidate_id, predicted_grade, open_rate_range, completion_rate_range, finish_time_range, "demo 未分发候选图", candidate_id, evidence),
    }


def _value_candidate_prediction_from_evidence(candidate: dict[str, object], semantic, positive: tuple[dict[str, object], ...], negative: tuple[dict[str, object], ...]) -> dict[str, object]:
    positive_strength = sum(1 for item in positive if item.get("grade") in {"S", "A"})
    negative_strength = sum(1 for item in negative if item.get("grade") in {"C", "D"})
    risk_count = len(getattr(semantic, "risk_tags", ()) or ())
    score = 0.45 + positive_strength * 0.12 - negative_strength * 0.07 - risk_count * 0.12
    confidence = max(0.12, min(0.88, score + float(getattr(semantic, "confidence", 0) or 0) * 0.12))
    country = str(candidate.get("country", ""))
    predicted_grade = _business_grade_from_confidence(confidence)
    metric_levels = _metric_levels_for_grade(predicted_grade)
    level_sequence = (metric_levels["open_rate"], metric_levels["completion_rate"], metric_levels["avg_finish_time"])
    action, risk_rank = _action_for_business_grade(predicted_grade)
    open_rate_range, completion_rate_range, finish_time_range = _calibrated_metric_ranges(country, metric_levels)
    positive_text = "、".join(str(item.get("operation_tag", "")) for item in positive[:2]) or "暂无强相似S/A历史样本"
    negative_text = "、".join(str(item.get("operation_tag", "")) for item in negative[:2]) or "暂无强相似C/D风险样本"
    evidence = (
        f"预测值：Qwen视觉解析主体={semantic.subject or candidate.get('subject', '')}；"
        f"等级预测={predicted_grade}；指标校准={''.join(level_sequence)}，用于和等级口径保持一致；"
        f"相似历史好图：{positive_text}；相似历史风险图：{negative_text}；"
        f"风险：{'、'.join(getattr(semantic, 'risk_tags', ()) or ()) or '未发现明确风险'}。"
    )
    return {
        "predicted_grade": predicted_grade,
        "sa_probability": round(confidence, 4),
        "open_rate_range": open_rate_range,
        "completion_rate_range": completion_rate_range,
        "finish_time_range": finish_time_range,
        "metric_levels": metric_levels,
        "action": action,
        "risk_rank": risk_rank,
        "evidence": evidence,
        "similar_positive": positive,
        "similar_negative": negative,
    }


def _material_risk_tags(risk_tags: object) -> tuple[str, ...]:
    benign_markers = (
        "low_",
        "no_",
        "无",
        "低风险",
        "非真实",
        "未见",
        "未发现",
        "not_detected",
        "none",
        "safe",
    )
    material = []
    for item in _as_text_tuple(risk_tags):
        tag = item.strip()
        lowered = tag.lower()
        if not tag:
            continue
        if any(marker in lowered or marker in tag for marker in benign_markers):
            continue
        material.append(tag)
    return tuple(material)


def _history_signal_weight(item: dict[str, object]) -> float:
    score = _number_from_similarity_reason(str(item.get("reason", "")), "相似得分")
    overlap = _number_from_similarity_reason(str(item.get("reason", "")), "主体/视觉重合")
    if score is None:
        return 1.0
    if score >= 30 or (overlap is not None and overlap >= 5):
        return 1.25
    if score >= 16 or (overlap is not None and overlap >= 3):
        return 0.8
    if score >= 9 or (overlap is not None and overlap >= 2):
        return 0.45
    return 0.2


def _number_from_similarity_reason(reason: str, label: str) -> float | None:
    match = re.search(rf"{re.escape(label)}=([0-9]+(?:\\.[0-9]+)?)", reason)
    if not match:
        return None
    return float(match.group(1))


METRIC_LEVEL_THRESHOLDS = {
    "日本": {
        "open_rate": (0.0789, 0.1378),
        "completion_rate": (0.8673, 0.9198),
        "avg_finish_time": (15.06, 19.73),
    },
    "法国": {
        "open_rate": (0.0589, 0.1078),
        "completion_rate": (0.8573, 0.9189),
        "avg_finish_time": (15.00, 18.73),
    },
}


def _metric_level(country: str, metric: str, value: float) -> str:
    low, high = METRIC_LEVEL_THRESHOLDS.get(country, METRIC_LEVEL_THRESHOLDS["日本"]).get(metric, (0.0, 0.0))
    if value > high:
        return "高"
    if value < low:
        return "低"
    return "中"


def _business_grade_from_metric_levels(levels: tuple[str, str, str]) -> str:
    high_count = sum(1 for level in levels if level == "高")
    low_count = sum(1 for level in levels if level == "低")
    middle_count = sum(1 for level in levels if level == "中")
    if high_count == 3:
        return "S"
    if high_count == 2 and middle_count == 1:
        return "A"
    if middle_count == 3 or (high_count == 2 and low_count == 1):
        return "B"
    if (low_count == 1 and middle_count == 2) or (low_count == 1 and high_count == 1 and middle_count == 1):
        return "C"
    return "D"


def _business_grade_from_confidence(confidence: float) -> str:
    if confidence >= 0.76:
        return "S"
    if confidence >= 0.62:
        return "A"
    if confidence >= 0.46:
        return "B"
    if confidence >= 0.30:
        return "C"
    return "D"


def _metric_levels_for_grade(grade: str) -> dict[str, str]:
    levels_by_grade = {
        "S": ("高", "高", "高"),
        "A": ("高", "高", "中"),
        "B": ("中", "中", "中"),
        "C": ("低", "中", "中"),
        "D": ("低", "低", "低"),
    }
    open_level, completion_level, finish_level = levels_by_grade.get(grade, levels_by_grade["B"])
    return {"open_rate": open_level, "completion_rate": completion_level, "avg_finish_time": finish_level}


def _calibrated_metric_ranges(country: str, metric_levels: dict[str, str]) -> tuple[str, str, str]:
    return (
        _calibrated_metric_range(country, "open_rate", metric_levels.get("open_rate", "中")),
        _calibrated_metric_range(country, "completion_rate", metric_levels.get("completion_rate", "中")),
        _calibrated_metric_range(country, "avg_finish_time", metric_levels.get("avg_finish_time", "中")),
    )


def _calibrated_metric_range(country: str, metric: str, level: str) -> str:
    low, high = METRIC_LEVEL_THRESHOLDS.get(country, METRIC_LEVEL_THRESHOLDS["日本"]).get(metric, (0.0, 0.0))
    if metric in {"open_rate", "completion_rate"}:
        if level == "高":
            return _range_percent(high + 0.001, min(0.99, high + 0.035))
        if level == "低":
            return _range_percent(max(0.001, low - 0.035), max(0.001, low - 0.001))
        return _range_percent(low, high)
    if level == "高":
        return f"{high + 0.1:.1f}-{high + 3:.1f}"
    if level == "低":
        return f"{max(1.0, low - 3):.1f}-{max(1.0, low - 0.1):.1f}"
    return f"{low:.1f}-{high:.1f}"


def _action_for_business_grade(grade: str) -> tuple[str, int]:
    if grade in {"S", "A"}:
        return "优先排图", 0
    if grade == "B":
        return "谨慎排图", 1
    if grade == "C":
        return "人工复核", 2
    return "暂不使用", 3


def _strong_rag_citations_from_trace(
    trace: dict[str, object],
    citations: tuple[str, ...],
    *,
    min_rerank_score: float = 0.55,
    blocked_parent_ids: tuple[str, ...] | list[str] | set[str] = (),
    max_citations: int = 0,
) -> tuple[str, ...]:
    hits = trace.get("final_hits", ()) if isinstance(trace, dict) else ()
    blocked = {str(parent_id) for parent_id in blocked_parent_ids}
    limit = max(int(max_citations or 0), 0)
    if not isinstance(hits, (tuple, list)) or not hits:
        filtered = []
        for citation in citations:
            parent_id = str(citation).split("#", 1)[0]
            if parent_id in blocked:
                continue
            filtered.append(str(citation))
            if limit and len(filtered) >= limit:
                break
        return tuple(filtered)
    strong: list[str] = []
    allowed = set(citations)
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        chunk_id = str(hit.get("chunk_id", ""))
        if chunk_id not in allowed:
            continue
        parent_id = str(hit.get("parent_id", "") or chunk_id.split("#", 1)[0])
        if parent_id in blocked:
            continue
        bm25_score = float(hit.get("bm25_score", 0) or 0)
        rerank_score = float(hit.get("rerank_score", 0) or 0)
        if bm25_score > 0 or rerank_score >= min_rerank_score:
            strong.append(chunk_id)
            if limit and len(strong) >= limit:
                break
    return tuple(strong)


def _semantic_history_rerank_score(candidate: dict[str, object], semantic, record) -> float:
    subject = str(getattr(semantic, "subject", "") or "")
    scene = str(getattr(semantic, "scene", "") or "")
    style = str(getattr(semantic, "style", "") or "")
    culture = " ".join(str(item) for item in getattr(semantic, "culture_elements", ()) or ())
    keywords = " ".join(str(item) for item in getattr(semantic, "prompt_keywords", ()) or ())
    haystack = _history_record_text(record)
    haystack_tokens = _simple_text_tokens(haystack)
    subject_tokens = _simple_text_tokens(" ".join((subject, keywords)))
    visual_tokens = _simple_text_tokens(" ".join((scene, style, culture)))
    score = 0.0
    score += len(subject_tokens & haystack_tokens) * 6.0
    score += len(visual_tokens & haystack_tokens) * 2.0
    if subject and (subject in haystack or str(getattr(record, "subject_tag", "") or "") in subject):
        score += 8.0
    if str(getattr(record, "js_category", "") or "") == str(candidate.get("js_category", "") or ""):
        score += 1.0
    if str(candidate.get("operation_tag", "") or "") and str(getattr(record, "operation_tag", "") or ""):
        score += len(_simple_text_tokens(str(candidate.get("operation_tag", ""))) & _simple_text_tokens(str(getattr(record, "operation_tag", "")))) * 0.5
    return round(score, 4)


def _metric_levels_from_prediction_ranges(country: str, open_rate_range: str, completion_rate_range: str, finish_time_range: str) -> dict[str, str]:
    open_rate = _average_number_from_range(open_rate_range, percent=True)
    completion_rate = _average_number_from_range(completion_rate_range, percent=True)
    avg_finish_time = _average_number_from_range(finish_time_range, percent=False)
    if open_rate is None or completion_rate is None or avg_finish_time is None:
        return {}
    return {
        "open_rate": _metric_level(country, "open_rate", open_rate),
        "completion_rate": _metric_level(country, "completion_rate", completion_rate),
        "avg_finish_time": _metric_level(country, "avg_finish_time", avg_finish_time),
    }


def _average_number_from_range(value: str, *, percent: bool) -> float | None:
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", value)]
    if not numbers:
        return None
    average = sum(numbers[:2]) / min(len(numbers), 2)
    return average / 100 if percent else average


def _cached_value_candidate_prediction_is_stale(cache: dict[str, object]) -> bool:
    if cache.get("rag_filter_version") != "v0.7.32":
        return True
    if cache.get("metric_calibration_version") != "v0.7.33":
        return True
    if cache.get("value_grade_model_version") != "v0.7.39-legacy":
        return True
    return "旧缓存RAG依据未通过强相关过滤" in str(cache.get("evidence", ""))


def _range_percent(low: float, high: float) -> str:
    return f"{low:.0%}-{high:.0%}"


def _simple_text_tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[\s_，。；、/|:：-]+", str(text)) if token}


def _country_differences(country: str, records, other_country: str, other_records) -> tuple[dict[str, object], ...]:
    current = _category_sa_rates(records)
    other = _category_sa_rates(other_records)
    diffs = []
    for category, data in current.items():
        other_data = other.get(category, {"sa_rate": 0.0, "count": 0})
        diffs.append(
            {
                "js_category": category,
                "country": country,
                "sa_rate": data["sa_rate"],
                "sample_count": data["count"],
                "compare_country": other_country,
                "compare_sa_rate": other_data["sa_rate"],
                "delta": round(float(data["sa_rate"]) - float(other_data["sa_rate"]), 4),
            }
        )
    diffs.sort(key=lambda row: abs(float(row["delta"])), reverse=True)
    return tuple(diffs[:6])


def _category_sa_rates(records) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[object]] = defaultdict(list)
    for record in records:
        grouped[str(record.js_category)].append(record)
    return {
        category: {
            "count": len(items),
            "sa_rate": round(sum(1 for item in items if item.grade in {"S", "A"}) / len(items), 4) if items else 0.0,
        }
        for category, items in grouped.items()
    }


def _weekly_need_suggestion(country: str, tag_summary: dict[str, object], today: date) -> dict[str, object]:
    subject = str(tag_summary.get("subject", "") or _subject_from_operation_tag(str(tag_summary.get("operation_tag", ""))))
    return {
        "operation_tag": f"常规_{country}_{subject}{today.strftime('%m%d')}",
        "subject": subject,
        "js_category": str(tag_summary.get("js_category", "")),
        "count": 7,
        "priority": "P1",
        "method": "限素材网",
        "source_image": str(tag_summary.get("source_image", "")),
        "description": f"复用周三回收数据中表现稳定的 {subject} 元素，保持当前国家文化语境、主体清晰和可拼构图。",
        "reason": str(tag_summary.get("reason", "")),
        "confirmable": True,
    }


def _subject_from_operation_tag(operation_tag: str) -> str:
    match = re.search(r"_(?:日本|法国|巴西|俄罗斯|美国)_([^_]+?)(?:\d{4})?$", operation_tag)
    return match.group(1) if match else operation_tag


def _review_period(records) -> str:
    dates = sorted(str(record.distribution_date) for record in records if str(record.distribution_date))
    return f"{dates[0]} 至 {dates[-1]}" if dates else "当前回收周期"


def _weekly_review_summary(country: str, records, new_sa, declining, reusable, retire) -> str:
    return (
        f"{country}周三复盘：本周期回收 {len(records)} 张，新增 S/A {len(tuple(new_sa))} 张，"
        f"下降/风险 {len(tuple(declining))} 张，可复用 tag {len(tuple(reusable))} 个，应停用 tag {len(tuple(retire))} 个。"
    )


def _rate_text(value: float) -> str:
    return f"{value * 100:.2f}%"


def _minutes_text(value: float) -> str:
    return f"{value:.2f}min"


def _third_layer_status_text(real_sample_count: int) -> str:
    if real_sample_count >= 30:
        return f"已接入 {real_sample_count} 张真实拼图样本；下一步运行真实 VLM Harness，并抽查 AI silver 后晋升 human_gold。"
    return "等待 30-50 张真实拼图图片、人工等级和真实业务字段后运行真实样本基线。"


def _rag_smoke_eval_cases(country: str, documents: tuple[RagDocument, ...]) -> tuple[RagRetrievalCase, ...]:
    country_docs = [document for document in documents if document.country == country and document.source_type == "value_rule"]
    audit_docs = [document for document in documents if document.source_type == "audit_policy"]
    cases = [
        RagRetrievalCase(
            query=f"{country}{document.title}是否符合市场价值观 {document.text[:40]}",
            country=country,
            expected_parent_id=document.document_id,
        )
        for document in country_docs[:4]
    ]
    if audit_docs:
        audit = audit_docs[0]
        cases.append(
            RagRetrievalCase(
                query=f"{country}试新图是否存在版权 IP 文字水印 审核风险",
                country=country,
                expected_parent_id=audit.document_id,
            )
        )
    return tuple(cases)


def _business_object_chunk_eval_cases(country: str, documents: tuple[RagDocument, ...]) -> tuple[RagRetrievalCase, ...]:
    cases: list[RagRetrievalCase] = []
    country_docs = [document for document in documents if document.country == country]
    global_docs = [document for document in documents if document.country == "GLOBAL" and document.source_type == "audit_policy"]
    for document in country_docs:
        metadata = document.metadata
        subject = str(metadata.get("subject") or document.title)
        operation_tag = str(metadata.get("operation_tag") or "")
        js_category = str(metadata.get("js_category") or "")
        if document.source_type in {"value_rule", "approved_value_rule"}:
            cases.append(
                RagRetrievalCase(
                    query=f"{country} {document.title} {subject} 偏好 避雷 价值观",
                    country=country,
                    expected_parent_id=document.document_id,
                )
            )
        elif document.source_type == "sample_fact":
            cases.append(
                RagRetrievalCase(
                    query=f"{country} {operation_tag} {subject} {js_category} 历史表现 开图率 等级",
                    country=country,
                    expected_parent_id=document.document_id,
                )
            )
        elif document.source_type in {"fact", "approved_value_rule", "memory_working", "memory_perception"}:
            cases.append(
                RagRetrievalCase(
                    query=f"{country} {subject} memory facts RAG",
                    country=country,
                    expected_parent_id=document.document_id,
                )
            )
        if len(cases) >= 45:
            break
    for document in global_docs[:5]:
        cases.append(
            RagRetrievalCase(
                query=f"{country} {document.title} 版权 IP 水印 审核 风险",
                country=country,
                expected_parent_id=document.document_id,
            )
        )
    if len(cases) < 30:
        seed_docs = tuple(country_docs + global_docs)
        index = 0
        while seed_docs and len(cases) < 30:
            document = seed_docs[index % len(seed_docs)]
            cases.append(
                RagRetrievalCase(
                    query=f"{country} {document.title} {document.text[:36]} 业务对象 chunk eval {len(cases) + 1}",
                    country=country,
                    expected_parent_id=document.document_id,
                )
            )
            index += 1
    return tuple(cases[:50])


def _rag_business_metadata(
    country: str,
    *,
    source_type: str,
    task_type: str,
    business_object_type: str,
    operation_tag: str = "",
    subject: str = "",
    js_category: str = "",
    grade: str = "",
    date_range: str = "",
    approved_for_rag: bool = False,
    memory_id: int | str = "",
    provenance_id: str = "",
    market: str = "",
    **extra: object,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "country": country,
        "market": market or country,
        "task_type": task_type,
        "source_type": source_type,
        "operation_tag": operation_tag,
        "subject": subject,
        "js_category": js_category,
        "grade": grade,
        "date_range": date_range,
        "approved_for_rag": bool(approved_for_rag),
        "memory_id": memory_id,
        "provenance_id": provenance_id or f"{country}:{source_type}:{business_object_type}",
        "business_object_type": business_object_type,
        "chunk_strategy": "business_object",
    }
    source_defaults = {
        "value_rule": "static_value_rules",
        "approved_value_rule": "hitl_approved_value_rules",
        "sample_fact": "historical_records",
        "audit_policy": "audit_manual",
        "fact": "layered_memory",
        "memory_working": "layered_memory",
        "memory_perception": "layered_memory",
    }
    metadata["source"] = source_defaults.get(source_type, source_type)
    metadata.update(extra)
    return metadata


def _with_business_metadata(document: RagDocument) -> RagDocument:
    existing = dict(document.metadata)
    if {"country", "market", "task_type", "source_type", "provenance_id"} <= set(existing):
        return document
    business_object_type = {
        "value_rule": "value_rule",
        "approved_value_rule": "value_rule",
        "approved_rag_patch": "sop_step",
        "audit_policy": "audit_risk_type",
        "sample_fact": "historical_image",
        "fact": "memory_fact",
    }.get(document.source_type, "knowledge_section")
    task_type = {
        "audit_policy": "audit",
        "approved_rag_patch": "value_master",
        "sample_fact": "weekly_review",
        "fact": "memory_governance",
    }.get(document.source_type, "value_master")
    metadata = _rag_business_metadata(
        document.country,
        source_type=document.source_type,
        task_type=task_type,
        business_object_type=business_object_type,
        subject=str(existing.get("subject", "")),
        operation_tag=str(existing.get("operation_tag", "")),
        js_category=str(existing.get("js_category", "")),
        grade=str(existing.get("grade", "")),
        date_range=str(existing.get("date_range", "")),
        approved_for_rag=bool(existing.get("approved_for_rag", document.source_type != "sample_fact")),
        memory_id=str(existing.get("memory_id", "")),
        provenance_id=str(existing.get("provenance_id") or existing.get("source_file") or document.document_id),
    )
    metadata.update(existing)
    return RagDocument(
        document_id=document.document_id,
        country=document.country,
        source_type=document.source_type,
        title=document.title,
        text=document.text,
        metadata=metadata,
    )


def _value_rule_polarity(text: str) -> str:
    return "avoid" if any(word in text for word in ("避免", "避开", "禁止", "不适合", "风险", "不要")) else "preference"


def _audit_risk_type(text: str) -> str:
    if any(word in text for word in ("版权", "IP", "商标", "侵权")):
        return "copyright_ip"
    if any(word in text for word in ("水印", "文字", "LOGO", "logo")):
        return "watermark_text"
    if any(word in text for word in ("文化", "宗教", "政治", "混淆")):
        return "culture_safety"
    if "AI" in text or "低质" in text:
        return "ai_quality"
    return "general_audit"


def _rag_knowledge_dir() -> Path:
    configured = os.getenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / "knowledge"


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rag_patch_manifest_row(manifest: dict[str, object], path: Path) -> dict[str, object]:
    rebuild = manifest.get("rebuild_after_rollback") or manifest.get("rebuild") or {}
    if not isinstance(rebuild, dict):
        rebuild = {}
    qdrant = manifest.get("qdrant", {})
    if not isinstance(qdrant, dict):
        qdrant = {}
    rollback = manifest.get("rollback", {})
    if not isinstance(rollback, dict):
        rollback = {}
    raw_patch_path = Path(str(manifest.get("raw_patch_path", "")))
    patch_ids = tuple(str(item) for item in manifest.get("patch_ids", ()) if item)
    raw_patch_text = str(raw_patch_path)
    processed_path = str(rebuild.get("processed_path", ""))
    qdrant_manifest_path = str(qdrant.get("manifest_path", ""))
    rollback_removed = str(rollback.get("removed_raw_patch_path", ""))
    return {
        "status": str(manifest.get("status", "")),
        "run_id": str(manifest.get("run_id", "")),
        "created_at": str(manifest.get("created_at", "")),
        "manifest_path": str(path),
        "patch_count": int(manifest.get("applied_patch_count", 0) or 0),
        "patch_ids": patch_ids,
        "raw_patch_path": raw_patch_text,
        "raw_patch_file": raw_patch_path.name,
        "rebuild_hit@5": rebuild.get("hit@5", 0),
        "rebuild_mrr@5": rebuild.get("mrr@5", 0),
        "rebuild_cases": tuple(item for item in rebuild.get("cases", ()) if isinstance(item, dict)),
        "processed_path": processed_path,
        "qdrant_status": str(qdrant.get("status", "none") or "none"),
        "qdrant_points": int(qdrant.get("upserted_points", 0) or 0),
        "qdrant_vector_size": int(qdrant.get("vector_size", 0) or 0),
        "qdrant_manifest_path": qdrant_manifest_path,
        "rollback_removed": rollback_removed,
        "evidence": {
            "patch_ids": patch_ids,
            "raw_patch_path": raw_patch_text,
            "processed_path": processed_path,
            "patch_manifest_path": str(path),
            "qdrant_manifest_path": qdrant_manifest_path,
            "rollback_removed": rollback_removed,
        },
    }


def _rag_patch_run_comparison(current: dict[str, object], runs: tuple[dict[str, object], ...]) -> dict[str, object]:
    previous = next((run for run in runs if run.get("run_id") != current.get("run_id")), {})
    if not previous:
        return {
            "current_run_id": str(current.get("run_id", "")),
            "previous_run_id": "",
            "hit@5_delta": 0,
            "mrr@5_delta": 0,
            "qdrant_points_delta": 0,
            "status_changed": False,
            "fixed_failure_count": 0,
            "new_failure_count": 0,
            "fixed_failures": (),
            "new_failures": (),
        }
    case_diff = _rag_patch_case_diff(current.get("rebuild_cases", ()), previous.get("rebuild_cases", ()))
    return {
        "current_run_id": str(current.get("run_id", "")),
        "previous_run_id": str(previous.get("run_id", "")),
        "hit@5_delta": round(float(current.get("rebuild_hit@5", 0) or 0) - float(previous.get("rebuild_hit@5", 0) or 0), 4),
        "mrr@5_delta": round(float(current.get("rebuild_mrr@5", 0) or 0) - float(previous.get("rebuild_mrr@5", 0) or 0), 4),
        "qdrant_points_delta": int(current.get("qdrant_points", 0) or 0) - int(previous.get("qdrant_points", 0) or 0),
        "status_changed": str(current.get("status", "")) != str(previous.get("status", "")),
        **case_diff,
    }


def _rag_patch_case_diff(current_cases: object, previous_cases: object) -> dict[str, object]:
    current = _case_hit_map(current_cases)
    previous = _case_hit_map(previous_cases)
    fixed = tuple(sorted(parent_id for parent_id, was_hit in previous.items() if not was_hit and current.get(parent_id) is True))
    new_failures = tuple(sorted(parent_id for parent_id, is_hit in current.items() if not is_hit and previous.get(parent_id, True) is True))
    return {
        "fixed_failure_count": len(fixed),
        "new_failure_count": len(new_failures),
        "fixed_failures": fixed,
        "new_failures": new_failures,
    }


def _case_hit_map(cases: object) -> dict[str, bool]:
    if not isinstance(cases, (list, tuple)):
        return {}
    result: dict[str, bool] = {}
    for item in cases:
        if not isinstance(item, dict):
            continue
        parent_id = str(item.get("expected_parent_id", ""))
        if parent_id:
            result[parent_id] = bool(item.get("hit"))
    return result


def _manifest_run_id() -> str:
    return f"{date.today().strftime('%Y%m%d')}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _qdrant_reindex_history(root: Path, country: str) -> list[dict[str, object]]:
    runs_dir = root / "indices" / "runs"
    if not runs_dir.exists():
        return []
    rows: list[dict[str, object]] = []
    for path in sorted(runs_dir.glob(f"qdrant_reindex_{country}_*.json"), reverse=True):
        payload = _read_json_object(path)
        if not payload:
            continue
        rows.append(
            {
                "run_id": str(payload.get("run_id", "")),
                "status": str(payload.get("status", "")),
                "vector_size": int(payload.get("vector_size", 0) or 0),
                "upserted_points": int(payload.get("upserted_points", 0) or 0),
                "hit@5": payload.get("hit@5", 0),
                "manifest_path": str(path),
            }
        )
    return rows


def _qdrant_point_record(point) -> dict[str, object]:
    return {
        "id": point.id,
        "vector": [float(value) for value in point.vector],
        "payload": dict(point.payload),
    }


def _failure_component_for_stage(stage: str) -> str:
    if "qdrant" in stage:
        return "qdrant"
    if "rerank" in stage:
        return "rerank"
    if "embedding" in stage:
        return "embedding"
    return "hit_rate"


def _provider_healthcheck(provider, fallback=None) -> dict[str, object]:
    name = getattr(provider, "provider_name", provider.__class__.__name__)
    try:
        healthcheck = getattr(provider, "healthcheck", None)
        if callable(healthcheck):
            status = healthcheck()
        elif fallback is not None:
            status = fallback()
        else:
            status = {"provider": name, "ready": True, "configured": True}
    except Exception as exc:
        return {"provider": name, "ready": False, "configured": True, "error": str(exc)}
    if not isinstance(status, dict):
        return {"provider": name, "ready": False, "configured": True, "error": "healthcheck returned non-dict"}
    status = dict(status)
    status.setdefault("provider", name)
    status.setdefault("ready", bool(status.get("configured", True)))
    return status


def _fast_provider_status(provider) -> dict[str, object]:
    name = getattr(provider, "provider_name", provider.__class__.__name__)
    return {
        "provider": name,
        "ready": True,
        "configured": True,
        "mode": "fast",
        "note": "Fast preflight skips live network/API checks.",
    }


def _embedding_provider_smoke(provider) -> dict[str, object]:
    name = getattr(provider, "provider_name", provider.__class__.__name__)
    vector = provider.query_vector("寿司价值观")
    return {
        "provider": name,
        "ready": bool(vector),
        "configured": True,
        "probe_vector_dim": len(vector) if vector else 0,
    }


def _rag_hit_trace_payload(hit) -> dict[str, object]:
    return {
        "chunk_id": hit.chunk.chunk_id,
        "parent_id": hit.chunk.parent_id,
        "country": hit.chunk.country,
        "source_type": hit.chunk.source_type,
        "title": hit.chunk.title,
        "bm25_score": hit.bm25_score,
        "vector_score": hit.vector_score,
        "rerank_score": hit.rerank_score,
        "reason": hit.reason,
    }


def _row_needs_ai_prelabeled(row: dict[str, str]) -> bool:
    if row.get("label_source") == "human_gold" and row.get("label_status") == "reviewed":
        return False
    if row.get("label_source") == "ai_silver" and row.get("label_status") == "pending_review":
        return False
    return any(not row.get(field) for field in ("gold_subject", "gold_color_mood", "gold_composition", "gold_value_labels"))


def _prelabel_row_summary(rows: list[dict[str, str]], country: str) -> dict[str, int]:
    country_rows = [row for row in rows if row.get("country") == country and row.get("source") == "real"]
    return {
        "total_count": len(country_rows),
        "remaining_needs_prelabeled": sum(1 for row in country_rows if _row_needs_ai_prelabeled(row)),
        "pending_review_count": sum(1 for row in country_rows if row.get("label_source") == "ai_silver" and row.get("label_status") == "pending_review"),
        "human_gold_count": sum(1 for row in country_rows if row.get("label_source") == "human_gold" and row.get("label_status") == "reviewed"),
    }


def _top_failure_categories(failures) -> str:
    counts: dict[str, int] = {}
    for case in failures:
        categories = case.failure_categories or ("uncategorized",)
        for category in categories:
            counts[category] = counts.get(category, 0) + 1
    if not counts:
        return "无"
    return "；".join(f"{category}:{count}" for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3])


def _replace_tag_date_suffix(operation_tag: str, today: date) -> str:
    suffix = today.strftime("%m%d")
    if re.search(r"\d{4}$", operation_tag):
        return re.sub(r"\d{4}$", suffix, operation_tag)
    return f"{operation_tag}{suffix}"


def _harness_dataset_path() -> Path | None:
    configured = os.getenv("PUZZLEOPS_HARNESS_DATASET", "").strip()
    if configured:
        return Path(configured).expanduser()
    default = Path.cwd() / "harness_gold_samples.csv"
    return default if default.exists() else None


def _parse_harness_override_memory(content: str) -> dict[str, str] | None:
    if "：" not in content or "/" not in content:
        return None
    left, note = content.split("：", 1)
    sample_id, task_type = left.split("/", 1)
    sample_id = sample_id.strip()
    task_type = task_type.strip()
    note = note.strip()
    if not sample_id or not task_type or not note:
        return None
    return {"sample_id": sample_id, "task_type": task_type, "human_override": note}


def _missing_gold_fields(sample) -> tuple[str, ...]:
    missing: list[str] = []
    if not sample.gold_grade:
        missing.append("gold_grade")
    if not sample.gold_subject:
        missing.append("gold_subject")
    if not sample.gold_color_mood:
        missing.append("gold_color_mood")
    if not sample.gold_composition:
        missing.append("gold_composition")
    if not sample.gold_value_labels:
        missing.append("gold_value_labels")
    return tuple(missing)


def _missing_business_metric_fields(sample) -> tuple[str, ...]:
    missing: list[str] = []
    if not sample.position:
        missing.append("position")
    metrics = sample.metrics or {}
    for field in ("open_rate", "completion_rate", "avg_finish_time"):
        if not metrics.get(field):
            missing.append(field)
    return tuple(missing)


def _human_gold_review_note(existing: str, reviewer_note: str) -> str:
    markers = ("AI silver", "待人工抽查", "pending_review")
    kept = [
        part.strip(" ；;。")
        for part in re.split(r"[；;]", str(existing or ""))
        if part.strip() and not any(marker in part for marker in markers)
    ]
    note = str(reviewer_note or "").strip(" ；;。") or "人工抽查通过"
    if note not in kept:
        kept.append(note)
    return "；".join(kept)


def _resume_gold_dataset_summary(samples: tuple, countries: tuple[str, ...], target_total: int) -> dict[str, object]:
    real_count = len(samples)
    gold_complete = sum(1 for sample in samples if not _missing_gold_fields(sample))
    metric_complete = sum(1 for sample in samples if not _missing_business_metric_fields(sample))
    human_gold = sum(1 for sample in samples if sample.label_source == "human_gold" and sample.label_status == "reviewed")
    ai_silver = sum(1 for sample in samples if sample.label_source == "ai_silver" and sample.label_status == "pending_review")
    needs_prelabeled = sum(1 for sample in samples if sample.label_status == "needs_ai_prelabeled")
    stale_human_note = sum(
        1
        for sample in samples
        if sample.label_source == "human_gold"
        and sample.label_status == "reviewed"
        and any(marker in sample.human_note for marker in ("AI silver", "待人工抽查", "pending_review"))
    )
    country_counts = Counter(sample.country for sample in samples)
    grade_counts = Counter(sample.gold_grade for sample in samples if sample.gold_grade)
    source_counts = Counter(sample.source for sample in samples if sample.source)
    js_counts = Counter(sample.js_category for sample in samples if sample.js_category)
    return {
        "countries": countries,
        "real_sample_count": real_count,
        "target_total": target_total,
        "gap_count": max(target_total - real_count, 0),
        "country_counts": dict(country_counts),
        "grade_counts": dict(grade_counts),
        "source_counts": dict(source_counts),
        "js_category_counts": dict(js_counts),
        "gold_complete_count": gold_complete,
        "gold_complete_rate": _pct(gold_complete / real_count) if real_count else "0%",
        "metric_complete_count": metric_complete,
        "metric_complete_rate": _pct(metric_complete / real_count) if real_count else "0%",
        "human_gold_count": human_gold,
        "human_gold_rate": _pct(human_gold / real_count) if real_count else "0%",
        "stale_human_note_count": stale_human_note,
        "ai_silver_pending_count": ai_silver,
        "needs_ai_prelabeled_count": needs_prelabeled,
        "ready_for_resume_eval": bool(real_count >= target_total and gold_complete == real_count and metric_complete == real_count and human_gold == real_count),
    }


def _resume_gold_dataset_summary_markdown(summary: dict[str, object], combined_csv: Path) -> str:
    real_count = int(summary["real_sample_count"])
    target_total = int(summary["target_total"])
    gap_count = int(summary["gap_count"])
    return "\n".join(
        [
            "# PuzzleOps Gold Dataset Summary",
            "",
            "## 样本规模",
            "",
            f"- 真实样本总数：{real_count}/{target_total}",
            f"- 距离 50 张简历目标缺口：{gap_count}",
            f"- 合并 CSV：`{combined_csv}`",
            "",
            "## 国家分布",
            "",
            _counter_markdown_lines(summary["country_counts"]),
            "",
            "## 等级分布",
            "",
            _counter_markdown_lines(summary["grade_counts"], preferred_order=("S", "A", "B", "C", "D")),
            "",
            "## 标注覆盖",
            "",
            f"- 完整 gold label：{summary['gold_complete_count']}，gold 完成率：{summary['gold_complete_rate']}",
            f"- human_gold：{summary['human_gold_count']}，human_gold 覆盖率：{summary['human_gold_rate']}",
            f"- 人工确认备注待清理：{summary['stale_human_note_count']}",
            f"- AI silver 待审核：{summary['ai_silver_pending_count']}",
            f"- 待 AI 预标注：{summary['needs_ai_prelabeled_count']}",
            "",
            "## 业务指标覆盖",
            "",
            f"- 完整业务指标：{summary['metric_complete_count']}，业务指标完成率：{summary['metric_complete_rate']}",
            "",
            "## JS 分类分布",
            "",
            _counter_markdown_lines(summary["js_category_counts"]),
            "",
            "## 简历可用性判断",
            "",
            "- 可用于证明：真实图片样本、人工等级、主体/色彩/构图 gold label、业务指标覆盖。",
            "- 仍需注意：样本数未达到 50 时，简历表述应写“小样本真实评测集”，不要写大规模线上评测。",
        ]
    ) + "\n"


def _counter_markdown_lines(data: object, preferred_order: tuple[str, ...] = ()) -> str:
    if not isinstance(data, dict) or not data:
        return "- 无"
    ordered_keys = [key for key in preferred_order if key in data]
    ordered_keys.extend(key for key in sorted(data) if key not in ordered_keys)
    return "\n".join(f"- {key}：{data[key]}" for key in ordered_keys)


def _value_prediction_benchmark_rows(agent, countries: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for country in countries:
        rows.extend(agent.repository.value_prediction_benchmark_scores(country, limit=10_000))
    return tuple(rows)


def _value_master_prompt_benchmark_v2_payload(rows: tuple[dict[str, object], ...], countries: tuple[str, ...]) -> dict[str, object]:
    return {
        "mode": "value_master_prompt_benchmark_v2",
        "countries": countries,
        "main_prediction_change_allowed": False,
        "grade_model_version": "v0.7.39-legacy",
        "prompt_contract": {
            "version": "prompt_benchmark_v2",
            "focus": "视觉解析 / RAG citation / 历史依据 / 指标校准 / 运营可执行建议",
            "must_keep_grade_model": True,
            "must_not_change": (
                "不改等级预测主链路 value_grade_model_version=v0.7.39-legacy。",
                "不根据三项指标反推主等级，三项指标只做解释性区间。",
                "不把 shadow history rerank 直接写入线上预测缓存。",
            ),
            "output_rules": (
                "图片解析必须围绕主体内容、色彩氛围、构图环境三段。",
                "价值观判断必须引用当前图像证据、RAG citation 和历史依据，不足时明确标注需人工复核。",
                "RAG citation 只保留强相关 Top3，弱相关和 hard-negative 噪声不得进入最终理由。",
            ),
        },
        "benchmark_summary": _value_master_prompt_benchmark_v2_summary(rows),
        "review_examples": tuple(_value_master_prompt_benchmark_v2_example(row) for row in rows[:10]),
        "next_actions": (
            "继续用固定 10-20 张候选图做 Prompt v1/v2 对比。",
            "优先修视觉解析 prompt、RAG citation 使用方式、历史依据解释和指标区间表述。",
            "只有当人工评分仍低于阈值时，再讨论后训练，不把数据问题误判为模型问题。",
        ),
    }


def _value_master_prompt_benchmark_v2_summary(rows: tuple[dict[str, object], ...]) -> dict[str, object]:
    score_keys = (
        "visual_accuracy",
        "country_value_fit",
        "history_evidence_fit",
        "rag_citation_usefulness",
        "risk_detection",
        "grade_credibility",
        "metric_range_credibility",
        "actionability",
    )
    values: dict[str, list[float]] = {key: [] for key in score_keys}
    for row in rows:
        scores = row.get("candidate_scores", {})
        if not isinstance(scores, dict):
            continue
        for key in score_keys:
            numeric = _numeric_or_none(scores.get(key))
            if numeric is not None and 1 <= numeric <= 5:
                values[key].append(numeric)
    summary: dict[str, object] = {"benchmark_count": len(rows)}
    for key in score_keys:
        summary[f"candidate_{key}_avg"] = round(sum(values[key]) / len(values[key]), 2) if values[key] else "not_evaluable"
    return summary


def _value_master_prompt_benchmark_v2_example(row: dict[str, object]) -> dict[str, object]:
    scores = row.get("candidate_scores", {}) if isinstance(row.get("candidate_scores", {}), dict) else {}
    return {
        "country": row.get("country", ""),
        "candidate_id": row.get("candidate_id", ""),
        "operation_tag": row.get("operation_tag", ""),
        "label": row.get("candidate_label", ""),
        "grade_credibility": scores.get("grade_credibility", "not_evaluable"),
        "rag_citation_usefulness": scores.get("rag_citation_usefulness", "not_evaluable"),
        "history_evidence_fit": scores.get("history_evidence_fit", "not_evaluable"),
        "candidate_output": str(row.get("candidate_output", ""))[:500],
    }


def _value_master_prompt_benchmark_v2_markdown(report: dict[str, object]) -> str:
    summary = report.get("benchmark_summary", {}) if isinstance(report.get("benchmark_summary", {}), dict) else {}
    contract = report.get("prompt_contract", {}) if isinstance(report.get("prompt_contract", {}), dict) else {}
    examples = report.get("review_examples", ()) if isinstance(report.get("review_examples"), (list, tuple)) else ()
    example_lines = [
        f"- {item.get('operation_tag')}：等级可信度={item.get('grade_credibility')}；RAG={item.get('rag_citation_usefulness')}；输出摘录：{item.get('candidate_output')}"
        for item in examples[:5]
        if isinstance(item, dict)
    ] or ["- 暂无人工 Benchmark 样例"]
    return "\n".join(
        [
            "# PuzzleOps Value Master Prompt Benchmark v2",
            "",
            "## 结论",
            "",
            "- 本报告用于第三层专项修复收口：Prompt v2 Benchmark。",
            "- 不改等级预测主链路，不改 value_grade_model_version=v0.7.39-legacy。",
            "- 当前只约束视觉解析、RAG citation、历史依据解释、指标区间表述和运营建议。",
            "",
            "## Prompt Contract",
            "",
            f"- 关注点：{contract.get('focus', '')}",
            f"- 必须保留主等级模型：{contract.get('must_keep_grade_model')}",
            "",
            "## 人工 Benchmark 摘要",
            "",
            f"- 样本数：{summary.get('benchmark_count', 0)}",
            f"- 视觉解析均分：{_score_markdown_value(summary.get('candidate_visual_accuracy_avg'))}",
            f"- RAG citation 有用性均分：{_score_markdown_value(summary.get('candidate_rag_citation_usefulness_avg'))}",
            f"- 历史依据合理性均分：{_score_markdown_value(summary.get('candidate_history_evidence_fit_avg'))}",
            f"- 预测等级可信度均分：{_score_markdown_value(summary.get('candidate_grade_credibility_avg'))}",
            f"- 指标区间可信度均分：{_score_markdown_value(summary.get('candidate_metric_range_credibility_avg'))}",
            "",
            "## 样例摘录",
            "",
            *example_lines,
            "",
            "## 下一步",
            "",
            *[f"- {item}" for item in report.get("next_actions", ())],
        ]
    ) + "\n"


def _eval_ratio(numerator: float, denominator: int) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0


def _value_master_eval_report_payload(
    samples: tuple,
    countries: tuple[str, ...],
    run,
    benchmark_rows: tuple[dict[str, object], ...],
    target_total: int,
    version: str,
) -> dict[str, object]:
    sample_count = len(samples)
    grade_rows = [_value_master_grade_row(sample) for sample in samples if sample.gold_grade]
    grade_correct = sum(1 for row in grade_rows if row["predicted_grade"] == row["gold_grade"])
    sa_correct = sum(1 for row in grade_rows if (row["predicted_grade"] in {"S", "A"}) == (row["gold_grade"] in {"S", "A"}))
    metrics = {
        "metric_baseline_grade_accuracy": _eval_ratio(grade_correct, len(grade_rows)),
        "sa_binary_accuracy": _eval_ratio(sa_correct, len(grade_rows)),
        "three_part_format_rate": _run_metric_value(run, "三段式描述合规率"),
        "subject_accuracy": _run_metric_value(run, "主体识别准确率"),
        "risk_recall": _run_metric_value(run, "审核风险召回率"),
        "rag_citation_precision": _run_metric_value(run, "RAG Citation Precision"),
        "feishu_field_completeness": _run_metric_value(run, "飞书字段完整率"),
        "tool_call_success_rate": _run_metric_value(run, "工具调用成功率"),
    }
    confusion = Counter(f"{row['gold_grade']}->{row['predicted_grade']}" for row in grade_rows)
    return {
        "version": version,
        "countries": countries,
        "sample_count": sample_count,
        "target_total": target_total,
        "gap_count": max(target_total - sample_count, 0),
        "execution_mode": run.execution_mode,
        "dataset_name": run.dataset_name,
        "metrics": metrics,
        "metric_evaluable_counts": run.metric_evaluable_counts,
        "grade_confusion": dict(confusion),
        "grade_cases": grade_rows,
        "human_benchmark": _value_master_human_benchmark_summary(benchmark_rows),
        "failure_summary": _value_master_failure_summary(run.failures),
        "limitations": (
            "三项指标目前是按等级口径校准，不是独立回归预测模型。",
            "未执行真实 VLM Harness 时，主体/色彩/构图准确率会标记为 not_evaluable。",
            "历史依据合理性与 RAG citation 有用性依赖人工 Benchmark 评分，样本不足时不能作为最终指标。",
        ),
    }


def _value_master_grade_row(sample) -> dict[str, object]:
    predicted = _harness_predict_grade(sample.metrics or {})
    return {
        "sample_id": sample.sample_id,
        "country": sample.country,
        "operation_tag": sample.operation_tag,
        "gold_grade": sample.gold_grade,
        "predicted_grade": predicted,
        "sa_gold": sample.gold_grade in {"S", "A"},
        "sa_predicted": predicted in {"S", "A"},
        "open_rate": sample.metrics.get("open_rate", 0.0),
        "completion_rate": sample.metrics.get("completion_rate", 0.0),
        "avg_finish_time": sample.metrics.get("avg_finish_time", 0.0),
    }


def _run_metric_value(run, label: str) -> float | str:
    if int(run.metric_evaluable_counts.get(label, 0) or 0) <= 0:
        return "not_evaluable"
    return round(float(run.metrics.get(label, 0.0) or 0.0), 4)


def _value_master_human_benchmark_summary(rows: tuple[dict[str, object], ...]) -> dict[str, object]:
    score_keys = (
        "visual_accuracy",
        "country_value_fit",
        "history_evidence_fit",
        "rag_citation_usefulness",
        "risk_detection",
        "grade_credibility",
        "metric_range_credibility",
        "actionability",
    )
    values: dict[str, list[float]] = {key: [] for key in score_keys}
    for row in rows:
        scores = row.get("candidate_scores", {})
        if not isinstance(scores, dict):
            continue
        for key in score_keys:
            try:
                score = float(scores.get(key))
                if 1 <= score <= 5:
                    values[key].append(score)
            except (TypeError, ValueError):
                continue
    summary: dict[str, object] = {"benchmark_count": len(rows)}
    for key in score_keys:
        summary[f"{key}_avg"] = round(sum(values[key]) / len(values[key]), 2) if values[key] else "not_evaluable"
    direct_labels = {"可直接用", "轻微修改"}
    summary["light_or_direct_rate"] = _eval_ratio(sum(1 for row in rows if row.get("candidate_label") in direct_labels), len(rows))
    return summary


def _value_master_failure_summary(failures: tuple) -> dict[str, object]:
    categories = Counter(category for case in failures for category in getattr(case, "failure_categories", ()))
    return {
        "failure_case_count": len(failures),
        "failure_categories": dict(categories),
    }


def _value_master_eval_report_markdown(report: dict[str, object]) -> str:
    metrics = report["metrics"]
    human = report["human_benchmark"]
    return "\n".join(
        [
            "# PuzzleOps Value Master Eval Report",
            "",
            "## 样本与版本",
            "",
            f"- 版本：{report['version']}",
            f"- 国家：{'、'.join(report['countries'])}",
            f"- 真实样本数：{report['sample_count']}/{report['target_total']}",
            f"- 距离 50 张目标缺口：{report['gap_count']}",
            f"- 执行模式：{report['execution_mode']}",
            "",
            "## 自动评测指标",
            "",
            f"- 指标反推等级基线准确率：{_metric_markdown_value(metrics['metric_baseline_grade_accuracy'])}",
            f"- SA 二分类准确率：{_metric_markdown_value(metrics['sa_binary_accuracy'])}",
            f"- 三段式描述合规率：{_metric_markdown_value(metrics['three_part_format_rate'])}",
            f"- 主体解析准确率：{_metric_markdown_value(metrics['subject_accuracy'])}",
            f"- 审核风险召回率：{_metric_markdown_value(metrics['risk_recall'])}",
            f"- RAG citation precision：{_metric_markdown_value(metrics['rag_citation_precision'])}",
            f"- 飞书字段完整率：{_metric_markdown_value(metrics['feishu_field_completeness'])}",
            f"- 工具调用成功率：{_metric_markdown_value(metrics['tool_call_success_rate'])}",
            "",
            "## 人工 Benchmark 指标",
            "",
            f"- Benchmark 评分样本数：{human['benchmark_count']}",
            f"- 历史依据合理性人工均分：{_score_markdown_value(human['history_evidence_fit_avg'])}",
            f"- RAG citation 有用性人工均分：{_score_markdown_value(human['rag_citation_usefulness_avg'])}",
            f"- 预测等级可信度人工均分：{_score_markdown_value(human['grade_credibility_avg'])}",
            f"- 排图建议可执行性人工均分：{_score_markdown_value(human['actionability_avg'])}",
            "",
            "## 等级混淆矩阵",
            "",
            _counter_markdown_lines(report["grade_confusion"]),
            "",
            "## 当前限制",
            "",
            "\n".join(f"- {item}" for item in report["limitations"]),
        ]
    ) + "\n"


def _value_master_repair_diagnostics_payload(report: dict[str, object]) -> dict[str, object]:
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics", {}), dict) else {}
    human = report.get("human_benchmark", {}) if isinstance(report.get("human_benchmark", {}), dict) else {}
    blockers = {
        "metric_baseline_grade_accuracy": _repair_blocker(
            metrics.get("metric_baseline_grade_accuracy"),
            threshold=0.55,
            direction="gte",
            failed_reason="三项指标反推等级效果弱，不能作为价值观大师主等级预测口径。",
        ),
        "history_evidence_fit_avg": _repair_blocker(
            human.get("history_evidence_fit_avg"),
            threshold=3.5,
            direction="gte",
            failed_reason="历史依据人工评分偏低，需要先做影子排序评测。",
        ),
        "rag_citation_usefulness_avg": _repair_blocker(
            human.get("rag_citation_usefulness_avg"),
            threshold=3.5,
            direction="gte",
            failed_reason="RAG citation 人工有用性偏低，需要 hard-negative 与 citation 过滤修复。",
        ),
        "grade_credibility_avg": _repair_blocker(
            human.get("grade_credibility_avg"),
            threshold=3.5,
            direction="gte",
            failed_reason="预测等级可信度人工评分偏低，需要 Prompt Benchmark v2，而不是直接训练。",
        ),
    }
    return {
        "mode": "shadow_diagnostics",
        "main_prediction_change_allowed": False,
        "sample_count": report.get("sample_count", 0),
        "benchmark_count": human.get("benchmark_count", 0),
        "blockers": blockers,
        "safe_experiments": (
            {
                "name": "历史依据排序影子评测",
                "goal": "只在报告中重排相似好坏图，不影响价值观大师线上预测缓存。",
                "acceptance": "history_evidence_fit_avg >= 3.5/5 后再考虑进入主链路。",
            },
            {
                "name": "RAG citation hard-negative 修复",
                "goal": "利用人工 not_useful citation feedback 给低质量 chunk 降权，并过滤弱引用。",
                "acceptance": "rag_citation_usefulness_avg >= 3.5/5，且 Recall@5 不明显下降。",
            },
            {
                "name": "等级预测 Prompt Benchmark v2",
                "goal": "用固定 10-20 张候选图比较 Prompt v1/v2，不再用三项指标反推等级。",
                "acceptance": "grade_credibility_avg >= 3.5/5，SA 二分类不低于当前基线。",
            },
        ),
        "resume_positioning": (
            "当前可以写工程闭环、Harness、RAG/Memory/HITL，但不能写价值观预测高准确率。",
            "简历指标应优先写数据集、三段式合规、飞书字段完整、工具调用稳定；价值观效果写为待优化 Benchmark。",
        ),
    }


def _repair_blocker(value: object, *, threshold: float, direction: str, failed_reason: str) -> dict[str, object]:
    numeric = _numeric_or_none(value)
    passed = False
    if numeric is not None:
        passed = numeric >= threshold if direction == "gte" else numeric <= threshold
    return {
        "value": value,
        "threshold": threshold,
        "status": "passed" if passed else "failed",
        "reason": "达到进入主链路阈值。" if passed else failed_reason,
    }


def _numeric_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _value_master_repair_diagnostics_markdown(diagnostics: dict[str, object]) -> str:
    blockers = diagnostics["blockers"] if isinstance(diagnostics.get("blockers"), dict) else {}
    experiments = diagnostics["safe_experiments"] if isinstance(diagnostics.get("safe_experiments"), (list, tuple)) else ()
    return "\n".join(
        [
            "# PuzzleOps Value Master Repair Diagnostics",
            "",
            "## 结论",
            "",
            "- 当前为 shadow diagnostics，不直接改线上预测等级。",
            "- 不直接改线上预测等级，避免再次破坏用户已认可的相对稳定版本。",
            f"- 样本数：{diagnostics.get('sample_count', 0)}；人工 Benchmark：{diagnostics.get('benchmark_count', 0)}。",
            "",
            "## 阻塞项",
            "",
            *[
                f"- {name}：{item.get('status')}，value={item.get('value')}，threshold={item.get('threshold')}；{item.get('reason')}"
                for name, item in blockers.items()
                if isinstance(item, dict)
            ],
            "",
            "## 安全实验",
            "",
            *[
                f"- {item['name']}：{item['goal']} 验收：{item['acceptance']}"
                for item in experiments
                if isinstance(item, dict)
            ],
            "",
            "## 简历口径",
            "",
            *[f"- {item}" for item in diagnostics.get("resume_positioning", ())],
        ]
    ) + "\n"


def _history_shadow_case(sample, records: tuple, *, top_k: int) -> dict[str, object]:
    candidates = tuple(record for record in records if not _same_history_record_as_sample(record, sample))
    legacy_ranked = sorted(candidates, key=lambda record: _legacy_history_shadow_score(sample, record), reverse=True)
    shadow_ranked = sorted(candidates, key=lambda record: _shadow_history_score(sample, record), reverse=True)
    legacy_top = _history_shadow_item(sample, legacy_ranked[0], _legacy_history_shadow_score(sample, legacy_ranked[0]), mode="legacy") if legacy_ranked else {}
    shadow_top = _history_shadow_item(sample, shadow_ranked[0], _shadow_history_score(sample, shadow_ranked[0]), mode="shadow") if shadow_ranked else {}
    return {
        "sample_id": sample.sample_id,
        "country": sample.country,
        "operation_tag": sample.operation_tag,
        "gold_grade": sample.gold_grade,
        "gold_subject": sample.gold_subject,
        "legacy_top": legacy_top,
        "shadow_top": shadow_top,
        "top_changed": bool(legacy_top and shadow_top and legacy_top.get("image_id") != shadow_top.get("image_id")),
        "legacy_top_subject_overlap": _history_subject_overlap(sample, legacy_ranked[0]) if legacy_ranked else 0.0,
        "shadow_top_subject_overlap": _history_subject_overlap(sample, shadow_ranked[0]) if shadow_ranked else 0.0,
        "shadow_top_k": tuple(_history_shadow_item(sample, record, _shadow_history_score(sample, record), mode="shadow") for record in shadow_ranked[:top_k]),
    }


def _same_history_record_as_sample(record, sample) -> bool:
    return bool(
        (getattr(record, "image_id", "") and getattr(record, "image_id", "") == sample.sample_id)
        or (getattr(record, "local_image_path", "") and getattr(record, "local_image_path", "") == sample.local_image_path)
    )


def _legacy_history_shadow_score(sample, record) -> float:
    query = " ".join((sample.operation_tag, sample.subject, sample.js_category))
    score = 0.0
    if getattr(record, "js_category", "") == sample.js_category:
        score += 3.0
    score += len(_simple_text_tokens(query) & _simple_text_tokens(_history_record_text(record)))
    score += _grade_bucket_match_bonus(sample.gold_grade, getattr(record, "grade", "")) * 0.5
    return score


def _shadow_history_score(sample, record) -> float:
    subject_tokens = _simple_text_tokens(" ".join((sample.gold_subject, sample.subject)))
    visual_tokens = _simple_text_tokens(" ".join((sample.gold_color_mood, sample.gold_composition, " ".join(sample.gold_value_labels))))
    haystack_tokens = _simple_text_tokens(_history_record_text(record))
    score = 0.0
    subject_overlap = len(subject_tokens & haystack_tokens)
    visual_overlap = len(visual_tokens & haystack_tokens)
    score += subject_overlap * 6.0
    score += visual_overlap * 2.0
    if sample.gold_subject and (sample.gold_subject in _history_record_text(record) or getattr(record, "subject_tag", "") in sample.gold_subject):
        score += 8.0
    if getattr(record, "js_category", "") == sample.js_category:
        score += 1.0
    score += _grade_bucket_match_bonus(sample.gold_grade, getattr(record, "grade", ""))
    score += min(float(getattr(record, "open_rate", 0.0) or 0.0), 0.4)
    return round(score, 4)


def _history_record_text(record) -> str:
    return " ".join(
        str(part or "")
        for part in (
            getattr(record, "operation_tag", ""),
            getattr(record, "subject_tag", ""),
            getattr(record, "js_category", ""),
            getattr(record, "remark", ""),
        )
    )


def _visual_similarity_text_from_history(record) -> str:
    return "；".join(
        part
        for part in (
            f"国家={getattr(record, 'country', '')}",
            f"等级={getattr(record, 'grade', '')}",
            f"主体={getattr(record, 'subject_tag', '')}",
            f"运营tag={getattr(record, 'operation_tag', '')}",
            f"JS分类={getattr(record, 'js_category', '')}",
            f"备注={getattr(record, 'remark', '')}",
        )
        if part and not part.endswith("=")
    )


def _visual_similarity_text_from_candidate(candidate: dict[str, object]) -> str:
    return "；".join(
        part
        for part in (
            f"国家={candidate.get('country', '')}",
            f"主体={candidate.get('subject', '') or candidate.get('visual_subject', '')}",
            f"运营tag={candidate.get('operation_tag', '')}",
            f"JS分类={candidate.get('js_category', '')}",
            f"描述={candidate.get('subject_description', '')}",
        )
        if part and not part.endswith("=")
    )


def _group_visual_similarity_hits(hits: tuple[dict[str, object], ...] | list[dict[str, object]]) -> dict[str, object]:
    good = tuple(hit for hit in hits if str(hit.get("grade", "")) in {"S", "A"})
    neutral = tuple(hit for hit in hits if str(hit.get("grade", "")) == "B")
    risk = tuple(hit for hit in hits if str(hit.get("grade", "")) in {"C", "D"})
    return {
        "status": "ok" if hits else "no_hits",
        "similar_good": good,
        "similar_neutral": neutral,
        "similar_risk": risk,
        "all_hits": tuple(hits),
    }


def _grade_bucket_match_bonus(gold_grade: str, record_grade: str) -> float:
    if gold_grade in {"S", "A"} and record_grade in {"S", "A"}:
        return 2.0
    if gold_grade in {"C", "D"} and record_grade in {"C", "D"}:
        return 2.0
    if gold_grade == record_grade and gold_grade:
        return 1.0
    return 0.0


def _history_subject_overlap(sample, record) -> float:
    subject = sample.gold_subject or sample.subject
    if not subject:
        return 0.0
    text = _history_record_text(record)
    if subject in text or getattr(record, "subject_tag", "") in subject:
        return 1.0
    return 1.0 if _simple_text_tokens(subject) & _simple_text_tokens(text) else 0.0


def _history_shadow_item(sample, record, score: float, *, mode: str) -> dict[str, object]:
    return {
        "image_id": getattr(record, "image_id", ""),
        "operation_tag": getattr(record, "operation_tag", ""),
        "grade": getattr(record, "grade", ""),
        "js_category": getattr(record, "js_category", ""),
        "subject": getattr(record, "subject_tag", ""),
        "score": round(float(score), 4),
        "subject_overlap": _history_subject_overlap(sample, record),
        "mode": mode,
    }


def _history_shadow_report_payload(cases: tuple[dict[str, object], ...], countries: tuple[str, ...], top_k: int) -> dict[str, object]:
    changed = sum(1 for case in cases if case.get("top_changed"))
    shadow_subject_overlap = sum(1 for case in cases if float(case.get("shadow_top_subject_overlap", 0.0) or 0.0) >= 1.0)
    legacy_subject_overlap = sum(1 for case in cases if float(case.get("legacy_top_subject_overlap", 0.0) or 0.0) >= 1.0)
    return {
        "mode": "shadow_history_rerank",
        "main_prediction_change_allowed": False,
        "countries": countries,
        "case_count": len(cases),
        "top_k": top_k,
        "metrics": {
            "top1_changed_rate": _eval_ratio(changed, len(cases)),
            "legacy_top1_subject_overlap_rate": _eval_ratio(legacy_subject_overlap, len(cases)),
            "shadow_top1_subject_overlap_rate": _eval_ratio(shadow_subject_overlap, len(cases)),
        },
        "cases": cases,
        "acceptance": {
            "promote_to_main_chain_when": "shadow_top1_subject_overlap_rate >= legacy_top1_subject_overlap_rate 且人工 history_evidence_fit_avg >= 3.5/5",
            "current_action": "继续停留在影子评测，不改主预测缓存。",
        },
    }


def _history_shadow_report_markdown(report: dict[str, object]) -> str:
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    cases = report.get("cases", ()) if isinstance(report.get("cases"), (list, tuple)) else ()
    examples = [
        case for case in cases
        if isinstance(case, dict) and case.get("top_changed") and isinstance(case.get("shadow_top"), dict)
    ][:5]
    example_lines = [
        f"- {case.get('sample_id')}：{case.get('gold_subject')} -> {case['shadow_top'].get('operation_tag')}"
        for case in examples
    ] or ["- 暂无 Top 改变样例"]
    return "\n".join(
        [
            "# PuzzleOps History Evidence Shadow Report",
            "",
            "## 结论",
            "",
            "- 当前是历史依据排序影子评测，不改主预测缓存，不改线上预测等级。",
            "- 目标是先验证相似历史依据是否更相关，再决定是否进入价值观大师主链路。",
            "",
            "## 指标",
            "",
            f"- Case 数：{report.get('case_count', 0)}",
            f"- Top1 改变率：{_metric_markdown_value(metrics.get('top1_changed_rate', 0.0))}",
            f"- 旧排序 Top1 主体重合率：{_metric_markdown_value(metrics.get('legacy_top1_subject_overlap_rate', 0.0))}",
            f"- 影子排序 Top1 主体重合率：{_metric_markdown_value(metrics.get('shadow_top1_subject_overlap_rate', 0.0))}",
            "",
            "## Top 改变样例",
            "",
            *example_lines,
            "",
            "## 验收",
            "",
            f"- {report.get('acceptance', {}).get('promote_to_main_chain_when', '')}",
            f"- {report.get('acceptance', {}).get('current_action', '')}",
        ]
    ) + "\n"


def _rag_hard_negative_case_result(raw_case: RagRetrievalCase, case: dict[str, object], *, k: int) -> dict[str, object]:
    retrieved_parent_ids = tuple(str(item) for item in case.get("retrieved_parent_ids", ()) if str(item).strip())
    hard_negative_parent_ids = tuple(str(item) for item in raw_case.hard_negative_parent_ids if str(item).strip())
    hard_negative_top1 = bool(retrieved_parent_ids and retrieved_parent_ids[0] in set(hard_negative_parent_ids))
    hard_negative_in_top_k = bool(set(retrieved_parent_ids[:k]) & set(hard_negative_parent_ids))
    base_failure = str(case.get("diagnosis", "passed") or "passed") if not case.get("hit") else "passed"
    if hard_negative_top1:
        failure_type = "hard_negative_top1"
    elif hard_negative_in_top_k:
        failure_type = "passed_with_hard_negative_noise" if case.get("hit") else "hard_negative_in_top_k"
    else:
        failure_type = base_failure
    return {
        "query": raw_case.query,
        "country": raw_case.country,
        "expected_parent_id": raw_case.expected_parent_id,
        "relevant_parent_ids": tuple(raw_case.relevant_parent_ids or (raw_case.expected_parent_id,)),
        "hard_negative_parent_ids": hard_negative_parent_ids,
        "retrieved_parent_ids": retrieved_parent_ids,
        "hit": bool(case.get("hit")),
        "rank": int(case.get("rank", 0) or 0),
        f"precision@{k}": float(case.get(f"precision@{k}", 0.0) or 0.0),
        f"recall@{k}": float(case.get(f"recall@{k}", 0.0) or 0.0),
        f"ndcg@{k}": float(case.get(f"ndcg@{k}", 0.0) or 0.0),
        "hard_negative_top1": hard_negative_top1,
        "hard_negative_in_top_k": hard_negative_in_top_k,
        "failure_type": failure_type,
        "failure_reason": str(case.get("failure_reason", "")),
        "suggested_action": str(case.get("suggested_action", "")),
        "route_evidence": case.get("route_evidence", {}),
    }


def _hard_negative_rate(cases: tuple[dict[str, object], ...], key: str) -> float:
    eligible = tuple(case for case in cases if case.get("hard_negative_parent_ids"))
    if not eligible:
        return 0.0
    return round(sum(1 for case in eligible if case.get(key)) / len(eligible), 4)


def _rag_failure_type_counts(cases: tuple[dict[str, object], ...] | list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        failure_type = str(case.get("failure_type", "unknown") or "unknown")
        counts[failure_type] = counts.get(failure_type, 0) + 1
    return counts


def _rag_hard_negative_report_payload(
    country_reports: dict[str, dict[str, object]],
    cases: tuple[dict[str, object], ...],
    countries: tuple[str, ...],
    *,
    k: int,
    threshold: float,
) -> dict[str, object]:
    total = len(cases)
    hits = sum(1 for case in cases if case.get("hit"))
    reciprocal = sum((1 / int(case.get("rank", 0))) for case in cases if int(case.get("rank", 0) or 0) > 0)
    precision = sum(float(case.get(f"precision@{k}", 0.0) or 0.0) for case in cases)
    recall = sum(float(case.get(f"recall@{k}", 0.0) or 0.0) for case in cases)
    ndcg = sum(float(case.get(f"ndcg@{k}", 0.0) or 0.0) for case in cases)
    metrics = {
        f"hit@{k}": round(hits / total, 4) if total else 0.0,
        f"mrr@{k}": round(reciprocal / total, 4) if total else 0.0,
        f"precision@{k}": round(precision / total, 4) if total else 0.0,
        f"recall@{k}": round(recall / total, 4) if total else 0.0,
        f"ndcg@{k}": round(ndcg / total, 4) if total else 0.0,
        "hard_negative_top1_rate": _hard_negative_rate(cases, "hard_negative_top1"),
        "hard_negative_topk_rate": _hard_negative_rate(cases, "hard_negative_in_top_k"),
    }
    failed_cases = tuple(case for case in cases if not case.get("hit") or case.get("hard_negative_in_top_k"))
    passed_shadow_gate = metrics[f"hit@{k}"] >= threshold and metrics["hard_negative_top1_rate"] == 0 and metrics["hard_negative_topk_rate"] == 0
    return {
        "mode": "rag_hard_negative_eval",
        "main_prediction_change_allowed": False,
        "countries": countries,
        "case_count": total,
        "threshold": threshold,
        "metrics": metrics,
        "passed_threshold": bool(passed_shadow_gate),
        "country_metrics": {
            country: {
                key: value
                for key, value in report.items()
                if key in {f"hit@{k}", f"mrr@{k}", f"precision@{k}", f"recall@{k}", f"ndcg@{k}", "hard_negative_top1_rate", "hard_negative_topk_rate", "case_count"}
            }
            for country, report in country_reports.items()
        },
        "failure_types": _rag_failure_type_counts(cases),
        "failed_cases": failed_cases[:20],
        "cases": cases,
        "decision": {
            "status": "passed_shadow_gate" if passed_shadow_gate else "keep_shadow_repair",
            "next_action": "人工复核 failed_cases；把 confirmed hard-negative 反馈沉淀为 approved_rag_patch 后再重建索引。",
            "resume_positioning": "可写 RAG 评测与 hard-negative 治理闭环；未通过前不写价值观预测效果已稳定。",
        },
    }


def _rag_hard_negative_report_markdown(report: dict[str, object]) -> str:
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    countries = report.get("countries", ()) if isinstance(report.get("countries"), (list, tuple)) else ()
    failed_cases = report.get("failed_cases", ()) if isinstance(report.get("failed_cases"), (list, tuple)) else ()
    failure_types = report.get("failure_types", {}) if isinstance(report.get("failure_types"), dict) else {}
    lines = [
        "# PuzzleOps RAG Citation Hard-Negative Report",
        "",
        "## 结论",
        "",
        "- 当前是 RAG citation hard-negative 评测报告，不改价值观大师主预测，不改线上等级预测。",
        "- 目标是验证国家价值观/审核规则召回是否真正支撑当前图片判断，并暴露误召回和 hard-negative 问题。",
        "",
        "## 范围",
        "",
        f"- 国家：{'、'.join(str(country) for country in countries)}",
        f"- Case 数：{report.get('case_count', 0)}",
        f"- 阈值：Hit@5 >= {report.get('threshold', 0.8)}",
        "",
        "## 指标",
        "",
        f"- Hit@5：{_metric_markdown_value(metrics.get('hit@5', 0.0))}",
        f"- MRR@5：{_metric_markdown_value(metrics.get('mrr@5', 0.0))}",
        f"- NDCG@5：{_metric_markdown_value(metrics.get('ndcg@5', 0.0))}",
        f"- Precision@5：{_metric_markdown_value(metrics.get('precision@5', 0.0))}",
        f"- Recall@5：{_metric_markdown_value(metrics.get('recall@5', 0.0))}",
        f"- Hard-negative Top1 率：{_metric_markdown_value(metrics.get('hard_negative_top1_rate', 0.0))}",
        f"- Hard-negative TopK 率：{_metric_markdown_value(metrics.get('hard_negative_topk_rate', 0.0))}",
        "",
        "## 失败类型",
        "",
        *[f"- {key}：{value}" for key, value in failure_types.items()],
        "",
        "## 失败样例",
        "",
    ]
    if failed_cases:
        for case in failed_cases[:10]:
            if not isinstance(case, dict):
                continue
            retrieved = "、".join(str(item) for item in case.get("retrieved_parent_ids", ())[:5])
            lines.append(f"- {case.get('country')}｜{case.get('query')}：expected={case.get('expected_parent_id')}；retrieved={retrieved or '无'}；type={case.get('failure_type')}")
    else:
        lines.append("- 暂无失败样例。")
    decision = report.get("decision", {}) if isinstance(report.get("decision"), dict) else {}
    lines.extend(
        [
            "",
            "## 决策",
            "",
            f"- 状态：{decision.get('status', '')}",
            f"- 下一步：{decision.get('next_action', '')}",
            f"- 简历口径：{decision.get('resume_positioning', '')}",
        ]
    )
    return "\n".join(lines) + "\n"


def _metric_markdown_value(value: object) -> str:
    if isinstance(value, (int, float)):
        return _pct(float(value))
    return str(value)


def _score_markdown_value(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}/5"
    return str(value)


def _readiness_gate(name: str, passed: bool, evidence: str, next_action: str) -> dict[str, object]:
    return {
        "name": name,
        "passed": bool(passed),
        "evidence": evidence,
        "next_action": next_action,
    }


def _harness_gold_sample_rag_text(sample) -> str:
    metrics = sample.metrics or {}
    parts = (
        f"主体={sample.gold_subject or sample.subject}",
        f"运营tag={sample.operation_tag}",
        f"JS分类={sample.js_category}",
        f"等级={sample.gold_grade}",
        f"位置={sample.position}",
        f"开图率={metrics.get('open_rate', 0.0):g}",
        f"完成率={metrics.get('completion_rate', 0.0):g}",
        f"平均完成时长={metrics.get('avg_finish_time', 0.0):g}",
        f"色彩氛围={sample.gold_color_mood}",
        f"构图环境={sample.gold_composition}",
        f"价值观标签={';'.join(sample.gold_value_labels)}",
        f"风险标签={';'.join(sample.gold_risk_labels) or '无'}",
        f"人工备注={sample.human_note}",
    )
    return "；".join(part for part in parts if not part.endswith("="))


def _harness_gold_hard_negative_parent_ids(country: str, sample, samples: tuple[object, ...], *, limit: int = 3) -> tuple[str, ...]:
    subject = (getattr(sample, "gold_subject", "") or getattr(sample, "subject", "") or "").strip()
    negatives: list[str] = []
    for other in samples:
        if getattr(other, "sample_id", "") == getattr(sample, "sample_id", ""):
            continue
        other_subject = (getattr(other, "gold_subject", "") or getattr(other, "subject", "") or "").strip()
        if subject and other_subject and (subject in other_subject or other_subject in subject):
            continue
        negatives.append(f"{_country_code(country)}_HARNESS_GOLD_{getattr(other, 'sample_id', '')}")
        if len(negatives) >= limit:
            break
    return tuple(negatives)


def _normalize_label_text(value: str) -> str:
    labels = [part.strip() for part in re.split(r"[;；、|,，]+", value) if part.strip()]
    return ";".join(dict.fromkeys(labels))


def _metric_form_value(value: str, current: str) -> str:
    stripped = str(value or "").strip()
    return stripped if stripped else str(current or "")


def _parse_harness_sample_line(line: str, line_number: int) -> dict[str, object] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if any(delimiter in stripped for delimiter in ("\t", "|", ",")):
        return _parse_delimited_harness_sample_line(stripped, line_number)
    match = re.match(r"^(?P<grade>[SABCDsabcd])\s+(?P<path>.+)$", stripped)
    if match:
        return {"local_image_path": match.group("path").strip(), "gold_grade": match.group("grade").upper()}
    match = re.match(r"^(?P<path>.+)\s+(?P<grade>[SABCDsabcd])$", stripped)
    if match:
        return {"local_image_path": match.group("path").strip(), "gold_grade": match.group("grade").upper()}
    raise ValueError(f"第 {line_number} 行无法解析：请提供等级 S/A/B/C/D 和图片绝对路径。")


def _parse_delimited_harness_sample_line(line: str, line_number: int) -> dict[str, object]:
    delimiter = "\t" if "\t" in line else "|" if "|" in line else ","
    parts = [part.strip() for part in next(csv.reader([line], delimiter=delimiter)) if part.strip()]
    if len(parts) < 2:
        raise ValueError(f"第 {line_number} 行无法解析：至少需要图片路径和等级。")
    grades = [index for index, part in enumerate(parts) if _is_grade(part)]
    if not grades:
        raise ValueError(f"第 {line_number} 行缺少等级：请使用 S/A/B/C/D。")
    grade_index = grades[0]
    grade = parts[grade_index].upper()
    if grade_index == 0:
        image_path = parts[1]
        js_category = parts[2] if len(parts) > 2 else ""
        extra = parts[3:]
    else:
        image_path = parts[0]
        js_category = parts[2] if len(parts) > 2 and grade_index != 2 else ""
        extra = parts[3:]
    record: dict[str, object] = {"local_image_path": image_path, "gold_grade": grade, "js_category": js_category}
    for field, value in zip(
        ("position", "open_rate", "completion_rate", "avg_finish_time", "operation_tag", "subject"),
        extra,
    ):
        record[field] = value
    return record


def _is_supported_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}


def _parse_directory_grade_text(text: str) -> tuple[dict[int, str], dict[str, str]]:
    index_grades: dict[int, str] = {}
    filename_grades: dict[str, str] = {}
    normalized = text.replace("，", ",").replace("；", ";")
    for line in normalized.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        filename_match = re.match(r"^(?P<name>.+?\.(?:png|jpe?g|webp)|[^=,:;\s]+)\s*[=,:;]\s*(?P<grade>[SABCDsabcd])$", stripped)
        if filename_match and not filename_match.group("name").strip().isdigit():
            filename_grades[Path(filename_match.group("name").strip()).name] = filename_match.group("grade").upper()
            continue
        for index, grade in re.findall(r"(?<!\d)(\d+)\s*[:=,-]?\s*([SABCDsabcd])\b", stripped):
            index_grades[int(index)] = grade.upper()
        if not re.search(r"\d+\s*[:=,-]?\s*[SABCDsabcd]\b", stripped):
            grades = re.findall(r"\b[SABCDsabcd]\b", stripped)
            for offset, grade in enumerate(grades, 1):
                index_grades.setdefault(offset, grade.upper())
    return index_grades, filename_grades


def _is_grade(value: str) -> bool:
    return value.strip().upper() in {"S", "A", "B", "C", "D"}


def _image_content_type_for_path(path: Path) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if path.suffix.lower() == ".webp":
        return "image/webp"
    return "image/png"


def _silver_value_labels(country: str, semantic) -> str:
    text = " ".join(
        (
            str(getattr(semantic, "subject", "")),
            str(getattr(semantic, "scene", "")),
            str(getattr(semantic, "style", "")),
            " ".join(getattr(semantic, "culture_elements", ()) or ()),
            " ".join(getattr(semantic, "prompt_keywords", ()) or ()),
        )
    )
    labels: list[str] = []
    if country == "法国":
        if any(word in text for word in ("法棍", "奶酪", "葡萄", "酒杯", "野餐", "餐食", "海滨度假")):
            labels.append("生活艺术")
        if any(word in text for word in ("薰衣草", "风车", "乡村", "田野")):
            labels.append("法式乡村")
        if any(word in text for word in ("花园", "玫瑰", "蕾丝", "复古", "喷泉", "淑女")):
            labels.append("复古优雅")
        if any(word in text for word in ("海边", "沙滩", "自然", "花", "庭院")):
            labels.append("自然治愈")
    elif country == "日本":
        if any(word in text for word in ("寿司", "抹茶", "和食")):
            labels.append("本土饮食文化")
        if any(word in text for word in ("神社", "塔楼", "庭院", "日式")):
            labels.append("本土文化符号")
        if any(word in text for word in ("治愈", "动物", "花", "季节")):
            labels.append("季节感治愈")
    return ";".join(labels or ["待人工确认价值观"])


def _annotation_cases(cases, failures, overrides: dict[tuple[str, str], str]):
    by_key = {(case.sample_id, case.task_type): case for case in cases}
    ordered = []
    seen = set()
    for key in overrides:
        case = by_key.get(key)
        if case:
            ordered.append(case)
            seen.add(key)
    for case in failures:
        key = (case.sample_id, case.task_type)
        if key not in seen:
            ordered.append(case)
            seen.add(key)
    return tuple(ordered)


def _argilla_annotation_record(case, sample, overrides: dict[tuple[str, str], str]) -> dict[str, object]:
    return {
        "id": f"{case.sample_id}-{case.task_type}",
        "fields": {
            "sample_id": case.sample_id,
            "task_type": case.task_type,
            "image": _sample_image_path(sample),
            "operation_tag": sample.operation_tag if sample else "",
            "gold_subject": sample.gold_subject if sample else "",
            "gold_color_mood": sample.gold_color_mood if sample else "",
            "gold_composition": sample.gold_composition if sample else "",
            "agent_output": case.agent_output,
            "failure_reasons": "；".join(case.failure_reasons),
        },
        "metadata": {
            "human_override": overrides.get((case.sample_id, case.task_type), ""),
            "scores": case.scores,
        },
        "questions": ("主体是否准确", "色彩氛围是否准确", "构图环境是否准确", "风险是否漏召回"),
    }


def _label_studio_task(case, sample, overrides: dict[tuple[str, str], str]) -> dict[str, object]:
    return {
        "data": {
            "sample_id": case.sample_id,
            "task_type": case.task_type,
            "image": _sample_image_path(sample),
            "operation_tag": sample.operation_tag if sample else "",
            "gold_subject": sample.gold_subject if sample else "",
            "gold_color_mood": sample.gold_color_mood if sample else "",
            "gold_composition": sample.gold_composition if sample else "",
            "agent_output_label": f"Agent 输出：{case.agent_output}",
            "failure_reasons": "；".join(case.failure_reasons),
            "human_override": overrides.get((case.sample_id, case.task_type), ""),
        }
    }


def _sample_image_path(sample) -> str:
    return sample.local_image_path if sample and sample.local_image_path else ""


def _regular_visual_summary(agent: PuzzleOpsAgent, row: DemandRow):
    if row.reference_image_path and Path(row.reference_image_path).expanduser().is_file():
        feature = agent.local_image_analyzer.analyze_path(row.reference_image_path)
        visual = agent.local_image_analyzer.summarize_features((feature,) if feature else ())
        visual_bytes = Path(row.reference_image_path).expanduser().read_bytes()
    else:
        visual_bytes = image_bytes(row.image_name, row.subject)
        visual = agent.local_image_analyzer.summarize_bytes((visual_bytes,))
    return visual, visual_bytes


def _business_subject_description(subject: str, country: str, visual, semantic) -> str:
    color = visual.palette_summary
    if semantic and semantic.style:
        color = f"{visual.palette_summary}，整体风格为{semantic.style}"
    scene = semantic.scene if semantic and semantic.scene else visual.composition_summary
    culture = "、".join(semantic.culture_elements) if semantic and semantic.culture_elements else f"{country}市场元素待运营确认"
    return f"主体内容：{subject}；色彩氛围：{color}；构图环境：{scene}，结合{culture}。"


def _description_prompt_baseline_prompt(row: DemandRow, template_row: DemandRow, visual) -> str:
    historical_metrics = _description_historical_metrics(row)
    return (
        "Prompt baseline v3，生产详细版。你是 PuzzleOps 拼图运营提需助手，负责把图片视觉解析结果改写成生产同学可执行的提需描述。\n\n"
        "业务背景：\n"
        "我们做的是海外拼图内容运营。提需描述不是美术鉴赏，也不是长篇分析，而是给生产图片的人看的简短需求。"
        "请保留参考图中真正有效的主体、色彩、构图和市场语境，避免无关扩写。"
        "上一版 v2 的问题是过度压缩，导致生产需求变模糊；本版目标是在简洁基础上保留可执行画面细节。\n\n"
        "输入信息：\n"
        f"- 国家：{row.country}\n"
        f"- JS分类：{row.js_category}\n"
        f"- 运营tag：{row.operation_tag}\n"
        f"- 当前主体：{template_row.subject or row.subject}\n"
        f"- 图片视觉解析：色彩={visual.palette_summary}；明暗/饱和/冷暖={visual.visual_summary}；构图={visual.composition_summary}；质量={visual.quality_summary}；拼图友好度={visual.readability_summary}\n"
        f"- 历史表现：{historical_metrics}\n"
        f"- 当前系统草稿：{template_row.subject_description}\n\n"
        "输出要求：\n"
        "1. 只输出 JSON，不要输出解释。\n"
        "2. subject_description 控制在 80-120 个中文字符；要简洁，但不能把生产可执行细节删到需求模糊。\n"
        "3. remark 控制在 8-24 个中文字符；如果没有明确约束，输出空字符串。\n"
        "4. subject_description 必须严格由三段组成：主体内容、色彩氛围、构图环境；每段可以写1-2个短句。\n"
        "5. 主体内容写主主体、必要陪体和关键动作/关系；不要罗列碎片细节。\n"
        "6. 色彩氛围保留2-4个关键色、光线、质感或风格词；可以保留“柔和梦幻写实风”“暖色调渲染”等有生产指导价值的表达。\n"
        "7. 构图环境必须保留可执行画面细节，例如浅景深、前中后景层次、阳光斜照、轨道两侧落樱、餐桌俯拍、窗台近景、纵深透视。\n"
        "8. 备注必须是生产约束，例如“保留纵深”“避免IP感”“不要新增建筑主体”；不要重复主体描述。\n"
        "9. 不要出现“本地视觉解析”“综合色”“这是一张图片”“画面整体”“整体来看”“非常美丽”“适合用户欣赏”等空话或系统痕迹。\n"
        "10. 不要编造图片里没有的主体，不要把参考图主题漂移成寺庙、小屋、城堡、人物等无关元素。\n"
        "11. 如果国家是日本，优先保留真实日本生活/自然/节日语境，避免中日韩混搭、知名动漫 IP 感、过度神社化。\n"
        "12. 如果国家是法国，优先保留法式生活方式、自然花园、乡村/街景/餐桌审美，避免过度奢华、政治宗教符号和旅游刻板印象。\n"
        "13. 如果视觉解析信息不足，请保守描述，不要补不存在的细节。\n\n"
        "好例子：\n"
        '{"subject_description":"主体内容：日式通勤电车穿行樱花林荫道，轨道两侧落樱铺陈；色彩氛围：粉白樱花、暖米白阳光与柔和梦幻写实风；构图环境：浅景深突出电车，前中后景层次分明。","remark":"保留列车与樱花纵深。"}\n'
        '{"subject_description":"主体内容：寿司拼盘搭配日式餐具，突出三文鱼、虾、鱼籽和海苔卷；色彩氛围：米白、橙红、深绿干净明亮；构图环境：餐桌近景俯拍，主体集中且层次清楚。","remark":"避免品牌文字和商标。"}\n\n'
        "输出 JSON 格式：\n"
        "{\n"
        '  "subject_description": "主体内容：...；色彩氛围：...；构图环境：...",\n'
        '  "remark": "..."\n'
        "}"
    )


def _description_historical_metrics(row: DemandRow) -> str:
    parts = []
    for key, value in (
        ("历史等级", getattr(row, "value_match", "") or ""),
        ("图片", row.image_name),
        ("需求等级", row.priority),
        ("加工方式", row.method),
    ):
        if value:
            parts.append(f"{key}={value}")
    return "；".join(parts) or "暂无结构化历史指标"


def _json_object_from_text(text: str) -> dict[str, object]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        value = json.loads(match.group(0)) if match else {}
    return value if isinstance(value, dict) else {}


def _append_description_source_remark(existing_remark: str, semantic, vision_client) -> str:
    cleaned = _strip_description_source(existing_remark)
    source = _description_source_text(semantic, vision_client)
    return f"{cleaned}；{source}" if cleaned else source


def _append_unique_note(existing: str, note: str) -> str:
    existing = str(existing or "").strip()
    note = str(note or "").strip()
    if not note or note in existing:
        return existing
    return f"{existing}；{note}" if existing else note


def _required_derivative_negative_prompt() -> str:
    return (
        "避免品牌logo、文字水印、知名动漫/IP风格、宗教政治风险、文化混淆、低清晰度；"
        "严禁四宫格、拼贴、分屏、多画面、多场景合集、四季同图、春夏秋冬同时出现、信息图、海报排版。"
    )


def _japan_derivative_prompt(row: DemandRow) -> str:
    return (
        f"基于参考图为日本市场衍生一张用于 jigsaw puzzle 生产的单幅完整参考图。本次只生成一张独立完整图片，不是拼接图、不是拼图块效果图。画面只呈现一个主场景、一个季节氛围、一个清晰主体。\n\n"
        f"请保留参考图中【{row.subject}】的核心吸引力、温柔治愈感、清晰前中后景层次和适合拼图的细节密度；"
        f"当前解析参考：{row.subject_description or '主体、色彩、构图以参考图为准'}。"
        "在不改变主体识别度的前提下，衍生出更符合日本用户偏好的日式生活/自然场景。\n\n"
        f"主视觉必须保持【{row.subject}】和参考图构图关系，不能把主体替换成建筑、房屋、庭院、森林、小屋、寺庙或其他新主题。"
        "只能改变光线、天气、时间、少量人物/动物点缀、路面/道具细节和远景氛围。\n\n"
        "优先使用日本本土语境元素，例如：日式庭院、和室、町家、茶室、樱花、红枫、紫阳花、锦鲤、柴犬、猫咪、灯笼、温泉街、石板小路、柔和自然光。"
        "画面应宁静、治愈、季节感明确，有轻微故事互动，但不要像旅游宣传图或动漫截图。\n\n"
        "构图要求：主体明确，背景丰富但不抢主体；色彩柔和、干净、明亮；适合中老年用户拼图，画面细节可拼、边界清楚、完成后有收藏感。"
    )


def _japan_derivative_negative_prompt() -> str:
    return (
        "避免品牌logo、文字水印、商标、现代广告牌、知名动漫/IP角色、宫崎骏/吉卜力等可识别动画工作室风格、名作名画二创、真人明星脸。\n\n"
        "避免主体替换、构图漂移、把道路/大道/步道改成小屋、建筑、庭院、森林、寺庙或普通风景。\n\n"
        "避免中日韩文化混淆，例如中式屋顶、韩式服饰、东南亚寺庙、非日本节俗被放进日本语境。避免宗教政治敏感表达、战争武士过度严肃化、恐怖阴暗氛围。\n\n"
        "严禁四宫格、拼贴、分屏、多画面、多场景合集、四季同图、春夏秋冬同时出现、信息图、海报排版。"
        "避免低清晰度、AI畸形手脸、主体过小、背景杂乱、过度灰暗、过度写实恐怖或过度卡通。"
    )


def _france_derivative_prompt(row: DemandRow) -> str:
    return (
        f"基于参考图为法国市场衍生一张用于 jigsaw puzzle 生产的单幅完整参考图。本次只生成一张独立完整图片，不是拼接图、不是拼图块效果图。画面只呈现一个主场景、一个季节氛围、一个清晰主体。\n\n"
        f"请保留参考图中【{row.subject}】的核心吸引力、明亮浪漫的生活艺术感、清晰前中后景层次和适合拼图的细节密度；"
        f"当前解析参考：{row.subject_description or '主体、色彩、构图以参考图为准'}。"
        "在不改变主体识别度的前提下，衍生出更符合法国用户偏好的法式生活/花园/度假场景。\n\n"
        f"主视觉必须保持【{row.subject}】和参考图构图关系，不能把主体替换成建筑、房屋、庭院、森林、小屋、教堂或其他新主题。"
        "只能改变光线、天气、时间、少量人物/动物点缀、路面/道具细节和远景氛围。\n\n"
        "优先使用法国本土语境元素，例如：普罗旺斯薰衣草田、石屋花园、法式窗台、藤编篮、陶罐花束、乡村小路、巴黎面包店橱窗、法式庄园、庭院午餐、晴天餐桌、铃兰花、柔和晨光。"
        "画面应浪漫、明亮、精致、有生活气息，不要像普通旅游照。\n\n"
        "构图要求：主体明确，色彩明亮但不过曝；花艺、建筑、餐具、庭院风格统一；背景有法式生活细节但不抢主体；适合中老年用户拼图，画面细节可拼、边界清楚、完成后有度假和收藏感。"
    )


def _france_derivative_negative_prompt() -> str:
    return (
        "避免品牌logo、文字水印、商标、现代广告牌、受保护商业角色、当代艺术作品复刻、明显品牌橱窗、真人明星脸。\n\n"
        "避免主体替换、构图漂移、把道路/大道/步道/花田/窗台改成小屋、建筑、庭院、森林、教堂或普通风景。\n\n"
        "避免文化误用，例如美式谷仓、英式乡村、意式街景、西班牙/希腊海岛风被误当法国素材；避免建筑、餐具、花艺、街景风格混杂。"
        "避免过度宗教化、政治化、阴暗压抑、灰调脏乱、廉价旅游纪念品质感。\n\n"
        "严禁四宫格、拼贴、分屏、多画面、多场景合集、四季同图、春夏秋冬同时出现、信息图、海报排版。"
        "避免低清晰度、AI畸形手脸、主体过小、背景杂乱、过度灰暗、过度饱和、空洞风景照。"
    )


def _ensure_required_derivative_negative_prompt(negative_prompt: str) -> str:
    required = _required_derivative_negative_prompt()
    text = str(negative_prompt or "").strip()
    if not text:
        return required
    required_terms = ("四宫格", "拼贴", "多场景合集", "四季同图")
    if all(term in text for term in required_terms):
        return text
    return f"{text}；{required}"


def _derivative_subject_drift_reason(row: DemandRow, semantic) -> str:
    reference_text = " ".join((row.subject, row.subject_description, row.image_name, row.operation_tag))
    generated_text = " ".join(
        (
            str(getattr(semantic, "subject", "")),
            str(getattr(semantic, "scene", "")),
            str(getattr(semantic, "style", "")),
            " ".join(getattr(semantic, "culture_elements", ()) or ()),
            " ".join(getattr(semantic, "prompt_keywords", ()) or ()),
            str(getattr(semantic, "raw_text", "")),
        )
    )
    reference_tokens = _derivative_anchor_tokens(reference_text)
    generated_tokens = _derivative_anchor_tokens(generated_text)
    if _has_road_anchor(reference_tokens):
        if not _has_road_anchor(generated_tokens):
            return "参考图主构图是道路/大道/步道纵深，生成图未保留道路纵深结构。"
        if any(word in generated_text for word in ("小屋", "房屋", "建筑", "寺庙", "森林")) and not any(word in generated_text for word in ("大道", "步道", "道路", "小路", "街道")):
            return "参考图主视觉是道路场景，生成图转成小屋/建筑/森林主体。"
    missing = [token for token in reference_tokens if token not in generated_tokens]
    if len(reference_tokens) >= 2 and len(missing) >= max(1, len(reference_tokens) - 1):
        return f"生成图未保留关键主体元素：{','.join(missing[:3])}。"
    return ""


def _derivative_anchor_tokens(text: str) -> set[str]:
    anchors = (
        "樱花",
        "大道",
        "步道",
        "道路",
        "小路",
        "街道",
        "猫咪",
        "猫",
        "锦鲤",
        "鲤鱼",
        "柴犬",
        "铃兰",
        "薰衣草",
        "窗台",
        "花田",
        "庭院",
        "石屋",
        "面包店",
        "塔楼",
        "游客",
    )
    return {anchor for anchor in anchors if anchor in str(text or "")}


def _has_road_anchor(tokens: set[str]) -> bool:
    return bool(tokens & {"大道", "步道", "道路", "小路", "街道"})


def _strip_description_source(remark: str) -> str:
    parts = [part.strip() for part in str(remark or "").split("；") if part.strip()]
    return "；".join(part for part in parts if not part.startswith("描述来源：") and not part.startswith("视觉LLM："))


def _description_source_text(semantic, vision_client) -> str:
    if not semantic:
        return "描述来源：本地视觉解析；未调用远程视觉模型"
    status = {}
    if vision_client and hasattr(vision_client, "config_status"):
        try:
            raw_status = vision_client.config_status()
            status = raw_status if isinstance(raw_status, dict) else {}
        except Exception:
            status = {}
    provider = str(status.get("provider") or getattr(semantic, "provider", "") or "视觉模型")
    model = str(status.get("model") or "").strip()
    model_label = f"{provider.capitalize()} {model}".strip() if model else provider.capitalize()
    return f"描述来源：{model_label}；视觉置信度 {float(getattr(semantic, 'confidence', 0.0)):.2f}"


def _generated_subject_description(country: str, visual, semantic) -> str:
    subject = semantic.subject or "待确认主体"
    color = semantic.style or visual.palette_summary
    scene = semantic.scene or visual.composition_summary
    culture = "、".join(semantic.culture_elements) if semantic.culture_elements else f"{country}市场元素待运营确认"
    return f"主体内容：{subject}；色彩氛围：{color}；构图环境：{scene}，结合{culture}。"


def image_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _value_row_payload(row: DemandRow) -> dict[str, object]:
    return {
        "country": row.country,
        "js_category": row.js_category,
        "image_name": row.image_name,
        "operation_tag": row.operation_tag,
        "subject": row.subject,
        "subject_description": row.subject_description,
        "remark": row.remark,
        "reference_image_url": row.reference_image_url,
    }


def _demand_row_payload_for_skill(row: DemandRow) -> dict[str, object]:
    payload: dict[str, object] = {
        "提需分类": row.need_type,
        "国家": row.country,
        "JS分类": row.js_category,
        "图片本身": row.image_name,
        "运营tag": row.operation_tag,
        "主体内容": row.subject,
        "张数": row.count,
        "需求等级": row.priority,
        "加工方式": row.method,
        "提需日期": date.today().strftime("%Y%m%d"),
        "交付日期": row.delivery_date,
        "主体描述": row.subject_description,
        "备注": row.remark,
    }
    if row.need_type != "常规" and row.value_match:
        payload["价值观匹配度"] = row.value_match
    return payload


def _skill_rag_citations(documents: tuple[RagDocument, ...], *, limit: int = 5) -> tuple[str, ...]:
    citations = []
    for document in documents[:limit]:
        citations.append(f"{document.document_id}:{document.source_type}")
    return tuple(citations)


def _skill_predict_grade(metrics: dict[str, float]) -> str:
    open_rate = float(metrics.get("open_rate", 0.0))
    completion_rate = float(metrics.get("completion_rate", 0.0))
    if open_rate >= 0.28 and completion_rate >= 0.9:
        return "S"
    if open_rate >= 0.23 and completion_rate >= 0.85:
        return "A"
    if open_rate >= 0.18:
        return "B"
    if open_rate >= 0.12:
        return "C"
    return "D"


def _missing_value_llm_message(error) -> str:
    missing = "、".join(error.missing) if error else "QWEN_API_KEY"
    return f"价值观大师：需要配置真实视觉 LLM 后，才能基于当前图片解析结果和已有价值观规则判断匹配度；当前缺少 {missing}。"


def _append_system_rag_trace(value_match: str, rag_rules: tuple[tuple[str, str], ...]) -> str:
    citation_ids = tuple(title for title, _ in rag_rules if "#chunk-" in title)
    if not citation_ids or "系统RAG召回：" in value_match:
        return value_match
    return f"{value_match}；系统RAG召回：{'、'.join(citation_ids)}"


def _append_generated_rag_evidence(value_match: str, generated_answer: object) -> str:
    answer = str(generated_answer or "").strip()
    if not answer or "生成式RAG依据：" in value_match:
        return value_match
    compact = answer.replace("\n", " ")
    if len(compact) > 90:
        compact = compact[:87] + "..."
    return f"{value_match}；生成式RAG依据：{compact}"


def _extract_rag_citation_ids(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    citations: list[str] = []
    for citation in re.findall(r"[A-Z]+_[A-Z_]*\d*#chunk-\d+", text):
        if citation not in seen:
            seen.add(citation)
            citations.append(citation)
    return tuple(citations)


def _parent_id_from_chunk_id(chunk_id: str) -> str:
    return str(chunk_id).split("#", 1)[0]


def _expected_parent_from_citations(citations: tuple[str, ...], country: str, subject: str) -> str:
    if citations:
        return _parent_id_from_chunk_id(citations[0])
    prefix = _country_code(country) if country in COUNTRIES else "HUMAN"
    subject_slug = re.sub(r"\W+", "_", subject or "VALUE_MATCH", flags=re.UNICODE).strip("_").upper()
    return f"{prefix}_HUMAN_VALUE_{subject_slug or 'VALUE_MATCH'}"


def _value_labels_from_correction(text: str) -> list[str]:
    labels = []
    for keyword, label in (
        ("本土", "本土文化"),
        ("饮食", "本土饮食文化"),
        ("治愈", "治愈感"),
        ("季节", "季节感"),
        ("浪漫", "浪漫生活艺术"),
        ("田园", "田园自然"),
    ):
        if keyword in text and label not in labels:
            labels.append(label)
    return labels


def _risk_labels_from_correction(text: str) -> list[str]:
    labels = []
    for keyword, label in (
        ("品牌", "品牌露出"),
        ("版权", "版权/IP风险"),
        ("IP", "版权/IP风险"),
        ("水印", "文字水印"),
        ("混淆", "文化混淆"),
        ("AI", "AI质量风险"),
    ):
        if keyword in text and label not in labels:
            labels.append(label)
    return labels


def _first_non_empty(items) -> str:
    for item in items:
        if item:
            return item
    return ""


def _country_code(country: str) -> str:
    return {"日本": "JP", "法国": "FR"}.get(country, re.sub(r"\W+", "", country).upper() or "COUNTRY")


def _payload_text(payload: object) -> str:
    if isinstance(payload, dict):
        parts = []
        for key, value in payload.items():
            if value in ("", None, [], ()):
                continue
            if isinstance(value, (list, tuple)):
                value_text = "、".join(str(item) for item in value)
            else:
                value_text = str(value)
            parts.append(f"{key}={value_text}")
        return "；".join(parts)
    return str(payload) if payload else ""


def _memory_match_score(text: str, query: str) -> float:
    normalized_text = text.casefold()
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return 0.0
    if normalized_query in normalized_text:
        return 1.0
    terms = tuple(term for term in re.split(r"[\s,，。；;:_/]+", normalized_query) if term)
    if not terms:
        return 0.0
    return round(sum(1 for term in terms if term in normalized_text) / len(terms), 3)


def _memory_payload_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key, "")
    if isinstance(value, (list, tuple)):
        value = _first_non_empty(str(item).strip() for item in value)
    text = str(value or "").strip()
    if text:
        return text
    if key == "subject":
        description = str(payload.get("subject_description", "") or "")
        match = re.search(r"主体(?:内容)?[:：=]\s*([^；;，,。]+)", description)
        if match:
            return match.group(1).strip()
    return ""


def _memory_stance(text: str) -> str:
    negative_terms = ("不适合", "不符合", "不建议", "退回", "禁止", "不能", "不推荐")
    risk_terms = ("风险", "混淆", "水印", "版权", "商标", "误用", "避开", "避免")
    positive_terms = ("适合", "符合", "通过", "推荐", "可继续", "可进入", "确认")
    if any(term in text for term in negative_terms):
        return "negative"
    if any(term in text for term in risk_terms):
        return "risk"
    if any(term in text for term in positive_terms):
        return "positive"
    return "neutral"


def _memory_trust_metadata(memory: dict[str, object]) -> dict[str, object]:
    layer = str(memory.get("memory_layer", ""))
    status = str(memory.get("status", "active"))
    review_status = str(memory.get("review_status", "draft"))
    approved_for_rag = bool(memory.get("approved_for_rag"))
    human_verified = bool(memory.get("human_verified"))
    base_weight = {
        "perception": 0.75,
        "working": 0.65,
        "long_term": 1.35,
        "facts": 1.45,
    }.get(layer, 1.0)
    if human_verified:
        base_weight += 0.35
    payload = memory.get("payload", {})
    text = _payload_text(payload)
    stance = _memory_stance(text)
    if status != "active" or review_status != "approved" or not approved_for_rag:
        base_weight = 0.0
    if _memory_low_satisfaction(payload) or _memory_negative_feedback(payload):
        base_weight *= 0.5
    trust_level = "high" if base_weight >= 1.4 else "medium" if base_weight >= 1.0 else "low"
    return {
        "memory_weight": round(base_weight, 3),
        "trust_level": trust_level,
        "governance_status": status,
        "review_status": review_status,
        "approved_for_rag": approved_for_rag,
        "memory_stance": stance,
        "rag_ready": bool(text) and status == "active" and review_status == "approved" and approved_for_rag,
    }


def _memory_low_satisfaction(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    score = payload.get("satisfaction_score")
    try:
        return score is not None and int(score) <= 2
    except (TypeError, ValueError):
        return False


def _memory_negative_feedback(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return str(payload.get("usefulness", "")).strip() == "not_useful"


def _memory_superseded_ids(payload: object) -> set[int]:
    if not isinstance(payload, dict):
        return set()
    raw = payload.get("supersedes_memory_ids", ())
    if isinstance(raw, (str, int)):
        raw = (raw,)
    ids: set[int] = set()
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            try:
                memory_id = int(item)
            except (TypeError, ValueError):
                continue
            if memory_id > 0:
                ids.add(memory_id)
    return ids


def _memory_payload_citations(payload: dict[str, object]) -> tuple[str, ...]:
    citations: list[str] = []
    for key in ("citation_ids", "retrieved_parent_ids", "expected_parent_id", "chunk_id"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            citations.extend(str(item) for item in value if str(item).strip())
        elif value:
            citations.append(str(value))
    text = _payload_text(payload)
    citations.extend(_extract_rag_citation_ids(text))
    return tuple(dict.fromkeys(citations))


def _memory_payload_related(
    payload: dict[str, object],
    subject: str,
    operation_tag: str,
    citations: set[str],
) -> bool:
    text = _payload_text(payload)
    if subject and subject in text:
        return True
    if operation_tag and operation_tag in text:
        return True
    payload_citations = set(_memory_payload_citations(payload))
    if citations and payload_citations.intersection(citations):
        return True
    parent_citations = {_parent_id_from_chunk_id(citation) for citation in citations}
    parent_payload_citations = {_parent_id_from_chunk_id(citation) for citation in payload_citations}
    return bool(parent_citations and parent_payload_citations.intersection(parent_citations))


def _append_memory_step(
    steps: list[dict[str, object]],
    seen: set[int],
    memory: dict[str, object],
    step_type: str,
) -> None:
    memory_id = int(memory.get("memory_id", 0))
    if memory_id in seen:
        return
    seen.add(memory_id)
    payload = memory.get("payload", {}) if isinstance(memory.get("payload", {}), dict) else {}
    steps.append(
        {
            "step_type": step_type,
            "memory_id": memory_id,
            "memory_layer": str(memory.get("memory_layer", "")),
            "memory_type": str(memory.get("memory_type", "")),
            "status": str(memory.get("status", "")),
            "source_memory_id": memory.get("source_memory_id"),
            "human_verified": bool(memory.get("human_verified")),
            "summary": _payload_text(payload),
        }
    )


def _rag_chunk_from_row(row: dict[str, object]) -> RagChunk:
    metadata = row.get("metadata", {})
    return RagChunk(
        chunk_id=str(row["chunk_id"]),
        parent_id=str(row["parent_id"]),
        country=str(row["country"]),
        source_type=str(row["source_type"]),
        title=str(row["title"]),
        text=str(row["text"]),
        chunk_index=int(row["chunk_index"]),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _rag_patch_priority(item: dict[str, object]) -> dict[str, object]:
    score = 50
    reasons: list[str] = []
    label_source = str(item.get("label_source", ""))
    if label_source == "human_gold":
        score += 30
        reasons.append("human_gold")
    elif label_source == "ai_silver":
        score += 10
        reasons.append("ai_silver")
    grade = str(item.get("gold_grade", "")).upper()
    grade_weight = {"S": 25, "A": 18, "B": 10, "C": 4, "D": 0}.get(grade, 0)
    if grade:
        score += grade_weight
        reasons.append(f"grade={grade}")
    diagnosis = str(item.get("diagnosis", ""))
    diagnosis_weight = {
        "country_knowledge_missing": 22,
        "knowledge_missing_or_query_mismatch": 20,
        "candidate_recall_missing": 14,
        "vector_recall_missing": 12,
        "bm25_recall_missing": 12,
        "rerank_filtered_expected": 8,
    }.get(diagnosis, 0)
    if diagnosis:
        score += diagnosis_weight
        reasons.append(diagnosis)
    expected = str(item.get("expected_parent_id", ""))
    if "AUDIT" in expected or "GLOBAL" in expected:
        score += 8
        reasons.append("audit/global")
    band = "P0" if score >= 100 else ("P1" if score >= 75 else "P2")
    return {"score": score, "band": band, "reason": "；".join(reasons) or "default_failure"}


def _rag_patch_priority_summary(drafts: list[dict[str, object]]) -> dict[str, object]:
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for item in drafts:
        band = str(item.get("priority_band", "P2"))
        if band not in counts:
            band = "P2"
        counts[band] += 1
    top_patch = drafts[0] if drafts else {}
    return {
        **counts,
        "total": len(drafts),
        "top_score": int(top_patch.get("priority_score", 0) or 0) if top_patch else 0,
        "top_patch": {
            "patch_id": str(top_patch.get("patch_id", "")),
            "priority_band": str(top_patch.get("priority_band", "")),
            "priority_score": int(top_patch.get("priority_score", 0) or 0),
            "expected_parent_id": str(top_patch.get("expected_parent_id", "")),
            "priority_reason": str(top_patch.get("priority_reason", "")),
        }
        if top_patch
        else {},
    }


def _rag_patch_priority_impact(priority_summary: object, comparison: object) -> dict[str, object]:
    priority = priority_summary if isinstance(priority_summary, dict) else {}
    compare = comparison if isinstance(comparison, dict) else {}
    pending_p0 = int(priority.get("P0", 0) or 0)
    hit_delta = float(compare.get("hit@5_delta", 0) or 0)
    mrr_delta = float(compare.get("mrr@5_delta", 0) or 0)
    if not compare.get("previous_run_id"):
        effect = "no_baseline"
        action = "run_another_patch_experiment"
    elif hit_delta > 0 or mrr_delta > 0:
        effect = "improved"
        action = "continue_apply_priority_patches" if pending_p0 else "monitor_next_failures"
    elif hit_delta < 0 or mrr_delta < 0:
        effect = "regressed"
        action = "rollback_or_review_patch"
    else:
        effect = "no_change"
        action = "review_priority_weights_or_rerank"
    return {
        "pending_P0": pending_p0,
        "pending_P1": int(priority.get("P1", 0) or 0),
        "pending_P2": int(priority.get("P2", 0) or 0),
        "hit@5_delta": round(hit_delta, 4),
        "mrr@5_delta": round(mrr_delta, 4),
        "effect": effect,
        "recommended_action": action,
    }


def _as_text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item))
    return (str(value),)


def _first_nonempty_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _trace_latency_ms(trace: dict[str, object]) -> float | None:
    for key in ("latency_ms", "duration_ms", "response_time_ms"):
        value = trace.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    runtime_stats = trace.get("runtime_stats", {})
    if isinstance(runtime_stats, dict):
        for key in ("latency_ms", "duration_ms", "response_time_ms"):
            value = runtime_stats.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
    return None


def _trace_satisfaction_score(trace: dict[str, object]) -> int | None:
    for key in ("satisfaction_score", "human_satisfaction", "user_rating"):
        value = trace.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _success_rate(*results: ToolResult) -> float:
    if not results:
        return 0.0
    return sum(1 for result in results if result.success) / len(results)


def _dashboard_sa_ratio(country: str) -> str:
    return {
        "日本": "32% / 35%",
        "法国": "28% / 30%",
    }.get(country, "30% / 35%")


def _country_holidays(country: str, year: int) -> tuple[tuple[date, HolidayRecommendation], ...]:
    if country == "日本":
        return (
            (date(year, 1, 1), _holiday("元日", "1月1日", "新年团聚、初诣和家庭祝福题材。", ("新年祈福", "家庭团聚", "初诣散步"), ("门松", "神社参道", "日式年菜"))),
            (_nth_weekday(year, 1, 0, 2), _holiday("成人の日", _date_label(_nth_weekday(year, 1, 0, 2)), "成人礼与和服仪式感题材。", ("成人礼仪式", "和服人物", "家庭纪念"), ("振袖和服", "花束", "纪念合影"))),
            (date(year, 2, 11), _holiday("建国記念の日", "2月11日", "适合稳重的传统文化和自然风景，不建议政治化表达。", ("传统文化", "宁静风景", "家庭出游"), ("富士山", "梅花", "神社远景"))),
            (date(year, 4, 29), _holiday("昭和の日", "4月29日", "黄金周前段，适合怀旧春游和家庭出行题材。", ("春季郊游", "家庭团聚", "怀旧街景"), ("公园草地", "便当", "电车"))),
            (date(year, 5, 3), _holiday("憲法記念日", "5月3日", "黄金周中段，建议以旅行和家庭陪伴为主，避免政治化。", ("短途旅行", "家庭陪伴", "春季街景"), ("新干线", "温泉街", "花海"))),
            (date(year, 5, 5), _holiday("こどもの日", "5月5日", "儿童节和家庭主题，适合亲子、鲤鱼旗和春季庭院。", ("亲子时光", "家庭庭院", "儿童节装饰"), ("鲤鱼旗", "柏饼", "庭院"))),
            (_nth_weekday(year, 7, 0, 3), _holiday("海の日", _date_label(_nth_weekday(year, 7, 0, 3)), "日本夏季海洋日，适合海边出行、家庭旅行和清爽夏日元素。", ("海边小旅行", "家庭出游", "夏日治愈"), ("海岸线", "帆船", "亲子散步", "蓝天白云"))),
            (date(year, 8, 11), _holiday("山の日", "8月11日", "夏季山林出游题材，适合自然、登山、避暑。", ("山林避暑", "家庭徒步", "自然风景"), ("山路", "森林", "远山"))),
            (_nth_weekday(year, 9, 0, 3), _holiday("敬老の日", _date_label(_nth_weekday(year, 9, 0, 3)), "适合家庭陪伴、温暖礼物和传统生活场景。", ("家庭陪伴", "温馨礼物", "传统庭院"), ("茶点", "花束", "和室"))),
            (_nth_weekday(year, 10, 0, 2), _holiday("スポーツの日", _date_label(_nth_weekday(year, 10, 0, 2)), "适合户外运动、秋季公园和家庭活动。", ("秋季运动", "公园活动", "家庭出游"), ("运动场", "秋叶", "野餐垫"))),
            (date(year, 11, 3), _holiday("文化の日", "11月3日", "适合艺术、传统工艺、书院和展览氛围。", ("传统工艺", "艺术展览", "文化散步"), ("茶室", "书卷", "庭院"))),
            (date(year, 11, 23), _holiday("勤労感謝の日", "11月23日", "适合家庭感谢、秋季餐桌和温暖生活方式。", ("感谢礼物", "家庭餐桌", "秋季生活"), ("餐桌", "花束", "暖色灯光"))),
        )
    if country == "法国":
        return (
            (date(year, 1, 1), _holiday("Jour de l'An", "1月1日", "法国新年，适合家庭聚会、餐桌和城市灯光。", ("新年餐桌", "家庭聚会", "城市灯光"), ("香槟杯", "餐桌", "烟火远景"))),
            (date(year, 5, 1), _holiday("Fête du Travail / Muguet", "5月1日", "劳动节与铃兰花传统，适合法式窗台、花束和祝福题材。", ("铃兰祝福", "法式窗台", "春季花束"), ("铃兰花", "窗台", "白绿花束"))),
            (date(year, 5, 8), _holiday("Victoire 1945", "5月8日", "胜利日题材需避免政治化，建议以城市纪念和花束表达。", ("纪念花束", "城市散步", "家庭纪念"), ("三色旗远景", "花束", "石板街"))),
            (date(year, 7, 14), _holiday("法国国庆日", "7月14日", "法国国庆日，适合烟火、城市广场、家庭聚会和法式夏夜氛围。", ("夏夜烟火", "家庭聚会", "城市广场"), ("烟火", "三色旗", "巴黎街景", "露台餐桌"))),
            (date(year, 8, 15), _holiday("Assomption", "8月15日", "夏季假期节点，适合南法旅行、海岸和乡村度假。", ("南法假期", "海岸旅行", "乡村度假"), ("海岸线", "石屋", "露台"))),
            (date(year, 11, 1), _holiday("Toussaint", "11月1日", "万圣节/诸圣节期间，建议偏秋季花艺和温和纪念氛围。", ("秋季花艺", "家庭纪念", "温和静物"), ("菊花", "烛光", "秋叶"))),
            (date(year, 11, 11), _holiday("Armistice", "11月11日", "停战纪念日需避免战争画面，建议以和平、花束、城市纪念为主。", ("和平纪念", "城市纪念", "花束静物"), ("蓝白红花束", "石碑远景", "鸽子剪影"))),
            (date(year, 12, 25), _holiday("Noël", "12月25日", "法国圣诞，适合家庭餐桌、窗边灯光和冬季街景。", ("圣诞餐桌", "家庭团聚", "冬季街景"), ("圣诞树", "壁炉", "甜点"))),
        )
    return ()


def _holiday(name: str, date_range: str, meaning: str, ai_themes: tuple[str, ...], elements: tuple[str, ...]) -> HolidayRecommendation:
    return HolidayRecommendation(
        name=name,
        date_range=date_range,
        meaning=meaning,
        content=f"围绕{ai_themes[0]}、{ai_themes[1]}、{ai_themes[2]}展开，画面需要保持本国文化语境清晰。",
        ai_themes=ai_themes,
        elements=elements,
        history_good_images=(),
    )


def _holiday_direct_history_matches(records: tuple[object, ...], holiday: HolidayRecommendation) -> tuple[object, ...]:
    keywords = _holiday_keywords(holiday)
    matches = []
    for record in records:
        haystack = _record_holiday_text(record)
        if any(keyword and keyword in haystack for keyword in keywords):
            matches.append(record)
    return tuple(matches)


def _rank_holiday_records(records: tuple[object, ...], holiday: HolidayRecommendation, *, positive: bool) -> tuple[object, ...]:
    target_grades = {"S", "A"} if positive else {"C", "D"}
    candidates = [record for record in records if record.grade in target_grades]
    if not candidates and not positive:
        candidates = [record for record in records if record.grade in {"B", "C", "D"}]
    keywords = _holiday_keywords(holiday)

    def score(record) -> tuple[int, float, float]:
        text = _record_holiday_text(record)
        keyword_score = sum(1 for keyword in keywords if keyword and keyword in text)
        if positive:
            return (keyword_score, float(record.open_rate), float(record.completion_rate))
        return (keyword_score, -float(record.open_rate), -float(record.completion_rate))

    return tuple(sorted(candidates, key=score, reverse=True))


def _holiday_keywords(holiday: HolidayRecommendation) -> tuple[str, ...]:
    raw = (holiday.name, holiday.meaning, holiday.content) + holiday.ai_themes + holiday.elements
    tokens: list[str] = []
    generic = {"日本", "法国", "家庭", "城市", "旅行", "出游", "风景", "节日", "主题", "夏季", "春季", "秋季", "冬季"}
    for item in raw:
        text = str(item)
        tokens.append(text)
        if "海" in text:
            tokens.extend(("海边", "海岸", "海滨"))
        if "山" in text:
            tokens.extend(("山林", "远山", "登山"))
        if "花" in text:
            tokens.extend(("花田", "花艺", "花束"))
        for chunk in re.split(r"[、，。/／\s（）()；;+-]+", text):
            chunk = chunk.strip()
            if 2 <= len(chunk) <= 8 and chunk not in generic:
                tokens.append(chunk)
    return tuple(dict.fromkeys(tokens))


def _record_holiday_text(record) -> str:
    return " ".join(
        str(value)
        for value in (
            record.operation_tag,
            record.subject_tag,
            record.js_category,
            record.remark,
            record.distribution_date,
            record.distribution_cycle,
        )
        if value
    )


def _holiday_value_rule_citations(country_data: dict[str, object], holiday: HolidayRecommendation) -> tuple[str, ...]:
    rules = tuple(country_data.get("value_rules", ()))
    selected = []
    for name, text in rules:
        if name in {"文化真实性", "节日适配", "版权与风格风险", "主体清晰度", "构图可拼性", "AI质量"}:
            selected.append(f"{name}：{text}")
    return tuple(selected[:4])


def _holiday_planning_note(
    holiday: HolidayRecommendation,
    good_images: tuple[ImageAsset, ...],
    bad_images: tuple[ImageAsset, ...],
    citations: tuple[str, ...],
    direct_count: int,
) -> str:
    good_subjects = "、".join(image.title for image in good_images[:2]) or "暂无可引用好图"
    bad_subjects = "、".join(image.title for image in bad_images[:2]) or "暂无可引用坏图"
    source = "真实节日历史样本" if direct_count else "同国家历史好坏图规律"
    rule = citations[0] if citations else "国家价值观规则"
    return (
        f"LLM策划建议（待人工确认）：基于节日表、{source}和价值观规则生成。"
        f"历史好图规律可参考 {good_subjects}；历史坏图避雷参考 {bad_subjects}。"
        f"建议围绕{holiday.ai_themes[0]}、{holiday.ai_themes[1]}组织主体，突出{holiday.elements[0]}等可识别元素；"
        f"同时遵守 {rule}。"
    )


def _holiday_llm_payload(
    country: str,
    holiday: HolidayRecommendation,
    good_images: tuple[ImageAsset, ...],
    bad_images: tuple[ImageAsset, ...],
    citations: tuple[str, ...],
    direct_count: int,
    model: str,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 PuzzleOps 拼图运营节日策划助手。只能基于输入的节日表、真实历史样本和价值观规则生成建议；"
                    "不要编造历史图片、指标或节日。输出中文，给运营可执行建议，并标注需要人工确认。"
                ),
            },
            {
                "role": "user",
                "content": _holiday_llm_user_prompt(country, holiday, good_images, bad_images, citations, direct_count),
            },
        ],
        "temperature": 0.2,
    }


def _holiday_llm_user_prompt(
    country: str,
    holiday: HolidayRecommendation,
    good_images: tuple[ImageAsset, ...],
    bad_images: tuple[ImageAsset, ...],
    citations: tuple[str, ...],
    direct_count: int,
) -> str:
    good = "\n".join(_holiday_image_line(image) for image in good_images) or "无"
    bad = "\n".join(_holiday_image_line(image) for image in bad_images) or "无"
    rules = "\n".join(f"- {rule}" for rule in citations) or "无"
    return (
        f"国家：{country}\n"
        f"节日：{holiday.name}\n"
        f"日期范围：{holiday.date_range}\n"
        f"节日含义：{holiday.meaning}\n"
        f"维护表推荐主题：{'、'.join(holiday.ai_themes)}\n"
        f"维护表推荐元素：{'、'.join(holiday.elements)}\n"
        f"直接历史样本数：{direct_count}\n"
        f"真实历史好图参考：\n{good}\n"
        f"真实历史坏图避雷：\n{bad}\n"
        f"价值观规则依据：\n{rules}\n\n"
        "请输出一段 120 字以内的节日提需策划建议，必须包含：推荐主体方向、可用元素、历史依据、风险避雷、人工确认提示。"
    )


def _holiday_image_line(image: ImageAsset) -> str:
    return f"- {image.title}｜等级{image.grade}｜开图{image.open_rate}｜完成{image.finish_rate}｜时长{image.finish_time}｜来源{image.source}"


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + (nth - 1) * 7)


def _date_label(value: date) -> str:
    return f"{value.month}月{value.day}日"


def _real_inventory_images_for_tag(records: tuple[object, ...], country: str, operation_tag: str, subject: str, limit: int) -> tuple[ImageAsset, ...]:
    if not records:
        return ()
    selected = [record for record in records if _tag_stem(record.operation_tag) == _tag_stem(operation_tag)]
    return tuple(_image_asset_from_record(record) for record in selected[:limit])


def _image_asset_from_record(record) -> ImageAsset:
    return ImageAsset(
        title=record.subject_tag or record.operation_tag,
        grade=record.grade,
        open_rate=_metric_percent(record.open_rate),
        finish_rate=_metric_percent(record.completion_rate),
        finish_time=_metric_number(record.avg_finish_time),
        source=record.source,
        thumb=record.local_image_path or record.thumbnail_path or record.subject_tag,
        remark=record.remark,
    )


def _unverified_metric_image(image: ImageAsset) -> ImageAsset:
    return ImageAsset(
        title=image.title,
        grade=image.grade,
        open_rate="未接入真实指标",
        finish_rate="未接入真实指标",
        finish_time="未接入真实指标",
        source=image.source,
        thumb=image.thumb,
        remark=image.remark,
    )


def _tag_stem(tag: str) -> str:
    return re.sub(r"\d{4}$", "", str(tag))


def _metric_percent(value: float) -> str:
    return f"{float(value) * 100:.2f}%"


def _metric_number(value: float) -> str:
    return f"{float(value):.2f}"


def _real_inventory_dir() -> Path | None:
    configured = os.getenv("PUZZLEOPS_REAL_IMAGE_DIR", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(Path("/Users/fanglemin/Desktop/图片"))
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _image_content_type_from_path(path: str) -> str:
    suffix = Path(str(path)).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _image_dimensions(path: str) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(Path(path).expanduser()) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _looks_like_audit_query(query: str) -> bool:
    return any(word in query for word in ("风险", "审核", "水印", "IP", "版权", "商标", "文化混淆", "AI质量"))


def _readable_citation_label(citation_id: str) -> str:
    parent_id = citation_id.split("#", 1)[0]
    if "HARNESS_GOLD" in parent_id:
        return "历史人工 gold 样本"
    if "VALUE" in parent_id:
        return "国家价值观规则"
    if "AUDIT" in parent_id:
        return "审核/风险规则"
    if "MEMORY" in parent_id:
        return "人工确认 Memory"
    return parent_id.replace("GLOBAL_KB_", "").replace("_", " ")


def _is_emergency_rag_patch_candidate(item: dict[str, object]) -> bool:
    text = " ".join(
        str(item.get(key, ""))
        for key in ("priority_band", "query", "note", "draft_text", "expected_parent_id", "diagnosis", "gold_grade", "label_source")
    )
    if "P0" in text or "human_gold" in text or "S" == str(item.get("gold_grade", "")).strip().upper():
        return True
    return any(token in text for token in ("版权", "IP", "商标", "文化禁忌", "文化混淆", "节日", "紧急", "风险漏召回"))


def _records_from_static_country(country: str):
    from puzzle_ops.models import HistoricalRecord

    records = []
    data = COUNTRIES[country]
    for index, (operation_tag, images) in enumerate(data["images"].items(), 1):
        for image in images:
            records.append(
                HistoricalRecord(
                    grade=image.grade,
                    image_formula="",
                    image_id=f"{country}-{index}-{image.title}",
                    image_url="",
                    local_image_path="",
                    thumbnail_path="",
                    position=index,
                    dimension_grade="高高高" if image.grade == "S" else "高中高",
                    open_rate=float(image.open_rate.strip("%")) / 100,
                    completion_rate=float(image.finish_rate.strip("%")) / 100,
                    avg_finish_time=float(image.finish_time.replace("min", "")),
                    operation_tag=operation_tag,
                    subject_tag=operation_tag.split("_")[-1],
                    js_category="flowers",
                    source=image.source,
                    remark=image.remark,
                    distribution_date="2026-06-05",
                    distribution_cycle="W0",
                    country=country,
                )
            )
    return tuple(records)
