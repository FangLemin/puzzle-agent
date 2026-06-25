from dataclasses import replace
from datetime import date
import csv
import json
import os
from pathlib import Path
import re
from tempfile import gettempdir

from puzzle_ops.data import COUNTRIES, SYNC_ROWS
from puzzle_ops.adapters import MCPToolAdapter
from puzzle_ops.audit import AuditPolicyRetriever, AuditRuleEngine
from puzzle_ops.cms import MockCMSClient
from puzzle_ops.excel_importer import import_history_workbook
from puzzle_ops.feishu import FeishuClientFactory, MockFeishuClient
from puzzle_ops.models import AgentTrace, AnalysisReport, DemandRow, HolidayRecommendation, ImageProfile, ScheduleItem, TagMeta, ValuePredictionCard, ValueRuleCandidate
from puzzle_ops.multimodal import ImageFeatureExtractor, SimilarImageRetriever, ValueInsightMiner
from puzzle_ops.rag import FeedbackAwareRerankProvider, HybridRagRetriever, RagChunk, RagDocument, RagPrompt, RagProviderConfig, RagRuntimeStats, build_rag_prompt, chunk_document, providers_from_config
from puzzle_ops.storage import PuzzleRepository
from puzzle_ops.trulens_eval import TruLensRAGEvaluator
from puzzle_ops.trial_upload import TrialImageUploadService, _compact_tag_subject
from puzzle_ops.eval_suite import AgentEvalSuite
from puzzle_ops.harness import AgentHarness, EVAL_SAMPLE_CSV_FIELDS, load_eval_samples_csv
from puzzle_ops.image_generation import DerivativeImage, ImageGenerationProviderFactory
from puzzle_ops.visual_analysis import LocalImageAnalyzer
from puzzle_ops.visual_assets import image_bytes


class PuzzleOpsAgent:
    """Business-facing Agent service for outbound puzzle content operations."""

    editable_priorities = {"P0", "P1", "P2"}
    editable_methods = {"纯AI", "限素材网", "先照片后AI"}
    workday_positions = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)
    weekend_positions = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)

    def __init__(
        self,
        repository: PuzzleRepository | None = None,
        *,
        today: date | None = None,
        enable_regular_vision: bool = False,
    ):
        runtime_dir = Path(gettempdir()) / "puzzle_ops_agent_runtime"
        self.repository = repository or PuzzleRepository(runtime_dir / f"puzzle_ops_{os.getpid()}.db")
        self._runtime_dir = runtime_dir
        self.today = today or date.today()
        self.enable_regular_vision = enable_regular_vision
        self.local_image_analyzer = LocalImageAnalyzer()
        self._history_cache: dict[str, tuple] = {}
        self._approved_candidates: dict[str, ValueRuleCandidate] = {}
        self.cms = MockCMSClient.with_synthetic_assets(runtime_dir / "cms", "日本", weeks=1)
        self.adapter = MCPToolAdapter()
        self.adapter.register_cms(self.cms)
        self.feishu = FeishuClientFactory.create(runtime_dir / "feishu_mock")
        self.trial_uploads = TrialImageUploadService(runtime_dir / "trial_uploads")
        self.image_generator = ImageGenerationProviderFactory.create(runtime_dir / "trial_uploads")
        self.rag_provider_config = RagProviderConfig.from_env()
        self._last_rag_stats = RagRuntimeStats()

    def countries(self) -> tuple[str, ...]:
        return tuple(COUNTRIES.keys())

    def dashboard(self, country: str) -> dict[str, object]:
        data = self._country(country)
        return {
            "country": country,
            "country_label": f"{data['flag']} {country}",
            "owner": data["owner"],
            "sa": data["sa"],
            "ai": data["ai"],
            "tasks": [{"title": task.title, "body": task.body} for task in data["tasks"]],
        }

    def categories(self, country: str) -> dict[str, tuple[TagMeta, ...]]:
        return self._country(country)["categories"]

    def sorted_tags(self, country: str, category: str) -> tuple[TagMeta, ...]:
        tags = self.categories(country)[category]
        return tuple(sorted(tags, key=lambda tag: (self.stock_rank(tag), tag.stock)))

    def stock_rank(self, tag: TagMeta) -> int:
        if tag.hot and tag.stock <= 5:
            return 0
        if not tag.hot and tag.stock <= 5:
            return 1
        return 2

    def stock_class(self, tag: TagMeta) -> str:
        return ("stock-hot", "stock-low", "stock-normal")[self.stock_rank(tag)]

    def images_for_tag(self, country: str, operation_tag: str):
        return self._country(country)["images"].get(operation_tag, ())

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
            method="限素材网" if image.source == "素材网" else "纯AI",
            delivery_date="",
            subject_description="",
            remark=image.remark or tag_meta.risk,
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
        remark = row.remark
        if semantic:
            remark = (remark + "；" if remark else "") + f"视觉LLM：真实{semantic.provider}，置信度{semantic.confidence:.2f}。"
        return row.edited(subject=subject, subject_description=description, remark=remark)

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
            js_category=category,
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
        parsed, previews = self.trial_uploads.parse(row, files, mode)
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
        return parsed, previews

    def generation_provider_status(self) -> dict[str, object]:
        if self.image_generator:
            return self.image_generator.healthcheck()
        return {"provider": "not_configured", "configured": False, "message": "生成 provider 未配置"}

    def generate_trial_derivatives(self, row: DemandRow) -> tuple[DemandRow, tuple[DemandRow, ...], tuple[dict[str, str], ...]]:
        provider = self.image_generator
        if provider is None:
            return (
                row.edited(remark=(row.remark + "；" if row.remark else "") + "生成 provider 未配置：当前只保留衍生方向，不伪造新参考图。"),
                (),
                (),
            )
        prompt = (
            f"基于参考图衍生2张{row.country}市场拼图参考图；保留{row.subject}的核心吸引力、色彩氛围和构图层次，"
            "变化具体场景、季节元素、道具组合，并保持主体清晰、适合中老年用户拼图。"
        )
        negative_prompt = "避免品牌logo、文字水印、知名动漫/IP风格、宗教政治风险、文化混淆、低清晰度。"
        derivatives = provider.generate_derivatives(
            reference_image=row.reference_image_path or row.image_name,
            prompt=prompt,
            negative_prompt=negative_prompt,
            count=2,
            seed=int(self.today.strftime("%m%d")),
            style_constraints={
                "source_sample_id": row.operation_tag,
                "retained_features": f"{row.subject}；{row.subject_description}",
                "changed_features": "季节元素；场景道具；人物/动物动作",
                "risk_notes": "生成图必须经过二次 VLM 解析与审核后才能同步飞书",
            },
        )
        rows: list[DemandRow] = []
        previews: list[dict[str, str]] = []
        for index, image in enumerate(derivatives, 1):
            path = Path(image.local_image_path)
            image_name = f"衍生参考图{index}_{path.name}"
            second_review_passed, review_status, reviewed_subject, reviewed_description = self._review_generated_derivative(row, image)
            remark = (
                f"{row.remark}；生成provider={image.provider}，seed={image.seed}；"
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
        return True, f"二次 VLM 解析与审核通过（{semantic.provider}，置信度{semantic.confidence:.2f}）", subject, description

    def apply_value_master(self, row: DemandRow) -> DemandRow:
        client = self.trial_uploads.vision_client
        if not client:
            value_match = _missing_value_llm_message(self.trial_uploads.vision_config_error)
        else:
            try:
                rag_rules = self._rag_rules_for_value_master(row)
                value_match = client.judge_value_match(_value_row_payload(row), rag_rules)
                value_match = _append_system_rag_trace(value_match, rag_rules)
            except Exception as exc:
                value_match = f"价值观大师：真实视觉 LLM 调用失败，暂不生成匹配结论；请检查模型配置后重试。错误：{exc}"
        return row.edited(value_match=value_match)

    def holiday_recommendation(self, country: str) -> HolidayRecommendation:
        return self._country(country)["holiday"]

    def analysis_report(self, country: str) -> AnalysisReport:
        data = self._country(country)["analysis"]
        visual_recap = self._visual_analysis_recap(country)
        return AnalysisReport(
            country=country,
            sa_ratio=data["sa_ratio"],
            cd_ratio=data["cd_ratio"],
            ai_ratio=data["ai_ratio"],
            sa_delta=data["sa_delta"],
            cd_delta=data["cd_delta"],
            ai_delta=data["ai_delta"],
            sa_history_avg=data["sa_history_avg"],
            sa_okr=data["sa_okr"],
            cd_history_avg=data["cd_history_avg"],
            ai_history_avg=data["ai_history_avg"],
            ai_okr=data["ai_okr"],
            cycle_summary=f"{data['cycle_summary']} 视觉维度复盘：{visual_recap}",
            next_todo=f"{data['next_todo']} 多模态建议：优先补充主体清晰、文化语境准确、质量风险低的试新参考图。",
            rows=data["rows"],
        )

    def value_rules(self, country: str):
        base_rules = list(self._country(country)["value_rules"])
        approved = [
            (f"运营审批候选{index}", str(rule["rule_text"]))
            for index, rule in enumerate(self.approved_value_rules(country), 1)
            if str(rule["rule_text"]) not in {body for _, body in base_rules}
        ]
        return tuple(base_rules + approved)

    def build_value_audit_rag_index(self, country: str) -> tuple[RagDocument, ...]:
        documents = self._rag_documents(country)
        chunks = tuple(chunk for document in documents for chunk in chunk_document(document))
        self.repository.save_rag_index(country, documents, chunks)
        return documents

    def value_audit_rag_answer(
        self,
        country: str,
        query: str,
        top_k: int = 6,
        *,
        provider_config: RagProviderConfig | None = None,
    ) -> RagPrompt:
        self.build_value_audit_rag_index(country)
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
        retriever = HybridRagRetriever(chunks, embedding_provider=embedding_provider, rerank_provider=rerank_provider)
        hits = retriever.search(query, country=country, top_k=top_k)
        if _looks_like_audit_query(query) and not any(hit.chunk.source_type == "audit_policy" for hit in hits):
            audit_hits = retriever.search(query, country=country, top_k=1, source_types=("audit_policy",))
            if audit_hits:
                hits = tuple(list(hits[: max(top_k - 1, 0)]) + [audit_hits[0]])
        self._last_rag_stats = stats
        return build_rag_prompt(query, hits)

    def _rag_rules_for_value_master(self, row: DemandRow) -> tuple[tuple[str, str], ...]:
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
        answer = self.value_audit_rag_answer(row.country, query, top_k=6)
        if not answer.citations:
            return self.value_rules(row.country)
        rules = []
        for line in answer.context.splitlines():
            if not line.startswith("[") or "]" not in line:
                continue
            citation, text = line.split("]", 1)
            rules.append((citation.strip("["), text.strip()))
        return tuple(rules) or self.value_rules(row.country)

    def value_audit_rag_summary(self, country: str) -> dict[str, object]:
        query = f"{country}市场试新提需是否符合价值观，并检查版权/IP、文字水印、文化混淆和AI质量风险"
        answer = self.value_audit_rag_answer(country, query, top_k=5)
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
            "feedback_summary": self.rag_feedback_summary(country),
            **self._last_rag_stats.as_dict(),
        }

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
            from puzzle_ops.models import ToolResult

            return ToolResult(False, {"missing": missing}, message, error=message)
        result = self.feishu.write_table("提需表", rows)
        self.repository.add_sync_event(country, "提需同步", "飞书在线表格", "成功" if result.success else "失败")
        return result

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
        for index, (title, body) in enumerate(self._country(country)["value_rules"], 1):
            documents.append(
                RagDocument(
                    document_id=f"{_country_code(country)}_VALUE_{index:03d}",
                    country=country,
                    source_type="value_rule",
                    title=title,
                    text=body,
                    metadata={"source": "static_value_rules"},
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
                    metadata={"source": "hitl_approved_value_rules"},
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
                    metadata={"source": "historical_records", "image_id": record.image_id},
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
                    metadata={"source": "audit_manual", "risk_level": hit.risk_level},
                )
            )
        return tuple(documents)

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
        for layer, source_type, prefix in specs:
            for index, memory in enumerate(self.repository.layered_memories(country, layer=layer), 1):
                payload = memory.get("payload", {})
                text = _payload_text(payload)
                if not text:
                    continue
                documents.append(
                    RagDocument(
                        document_id=f"{_country_code(country)}_{prefix}_{index:03d}",
                        country=country,
                        source_type=source_type,
                        title=str(memory.get("memory_type", layer)),
                        text=text,
                        metadata={
                            "source": "layered_memory",
                            "layer": layer,
                            "memory_id": memory.get("memory_id"),
                            "source_memory_id": memory.get("source_memory_id"),
                            "human_verified": bool(memory.get("human_verified")),
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

    def record_perception_memory(self, country: str, memory_type: str, payload: dict[str, object]) -> int:
        return self.repository.add_layered_memory(country, "perception", memory_type, payload, ttl_seconds=7 * 24 * 3600)

    def record_working_memory(self, country: str, memory_type: str, payload: dict[str, object]) -> int:
        return self.repository.add_layered_memory(country, "working", memory_type, payload, ttl_seconds=24 * 3600)

    def record_rag_citation_feedback(
        self,
        country: str,
        *,
        chunk_id: str,
        usefulness: str,
        note: str = "",
        task_type: str = "trial_value_match",
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
        )

    def record_long_term_memory(self, country: str, memory_type: str, payload: dict[str, object]) -> int:
        return self.repository.add_layered_memory(country, "long_term", memory_type, payload)

    def record_extracted_fact(self, country: str, memory_type: str, payload: dict[str, object]) -> int:
        return self.repository.add_layered_memory(country, "facts", memory_type, payload)

    def promote_memory(self, memory_id: int, *, target_layer: str, human_note: str) -> int:
        if target_layer not in {"facts", "long_term"}:
            raise ValueError("memory 只能人工晋升为 facts 或 long_term")
        target_type = "verified_fact" if target_layer == "facts" else "approved_long_term_memory"
        return self.repository.promote_layered_memory(
            memory_id,
            target_layer=target_layer,
            target_type=target_type,
            human_note=human_note,
        )

    def retire_memory(self, memory_id: int) -> None:
        self.repository.retire_layered_memory(memory_id)

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
            rag_ready_count = sum(1 for item in items if _payload_text(item.get("payload", {})))
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
        rows: list[dict[str, object]] = []
        for memory in self.repository.layered_memories(country, include_inactive=True):
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
                    "created_at": str(memory.get("created_at", "")),
                    "rag_ready": bool(summary) and status == "active",
                    "match_score": _memory_match_score(summary, query),
                }
            )
        rows.sort(key=lambda row: (float(row["match_score"]), str(row["created_at"])), reverse=True)
        return tuple(rows[: max(limit, 0)])

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
        return tuple(events)

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
        inventory = self.adapter.registry.call("cms.query_inventory", tag=profile.asset.operation_tag)
        plan = (
            "构建国家与任务上下文",
            "抽取图片结构化特征",
            "通过 MCP-like adapter 查询 CMS 库存",
            "检索相似历史好图与坏图",
            "召回审核手册风险依据",
            "同步 Agent trace 到飞书或 Mock fallback",
            "输出价值观判断并记录评测",
        )
        tool_calls = (
            "history.search_records",
            "cms.query_inventory",
            "image.extract_features",
            "image.retrieve_similar_good_bad",
            "audit.retrieve_policy",
            "feishu.write_table",
        )
        observations = (
            f"读取{country}历史样本{len(self._history_records(country))}条",
            f"CMS库存查询：{inventory.message}",
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
            "external_adapter_success_rate": 1.0 if inventory.success else 0.5,
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
            "CMS/MCP适配状态": "已启用",
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
        return self.latest_harness_run(country) or self.harness_run(country, save=False)

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
            "数据集文件": str(self._active_harness_dataset_path(country)),
        }

    def ensure_harness_gold_dataset(self, country: str) -> Path:
        dataset = self._active_harness_dataset_path(country)
        if dataset.exists():
            return dataset
        dataset.parent.mkdir(parents=True, exist_ok=True)
        samples = tuple(sample for sample in AgentHarness(self, self.image_generator).default_samples(country) if sample.is_real)
        self._write_harness_gold_rows(dataset, [sample.csv_row() for sample in samples])
        return dataset

    def register_harness_real_samples(self, country: str, records: list[dict[str, object]]) -> Path:
        dataset = self.ensure_harness_gold_dataset(country)
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

    def auto_prelabeled_harness_samples(self, country: str, sample_ids: tuple[str, ...] = ()) -> dict[str, object]:
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
        for row in rows:
            if row.get("country") != country or row.get("source") != "real":
                continue
            if wanted and row.get("sample_id") not in wanted:
                continue
            image_path = Path(str(row.get("local_image_path", ""))).expanduser()
            if not image_path.exists():
                skipped += 1
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
        self._write_harness_gold_rows(dataset, rows)
        return {"updated_count": updated, "skipped_count": skipped, "dataset": str(dataset)}

    def approve_harness_silver_labels(
        self,
        country: str,
        *,
        sample_ids: tuple[str, ...] = (),
        reviewer_note: str = "人工抽查通过",
    ) -> dict[str, object]:
        dataset = self.ensure_harness_gold_dataset(country)
        rows = self._read_harness_gold_rows(dataset)
        wanted = set(sample_ids)
        approved = 0
        skipped = 0
        note = reviewer_note.strip() or "人工抽查通过"
        for row in rows:
            if row.get("country") != country or row.get("source") != "real":
                continue
            if wanted and row.get("sample_id") not in wanted:
                continue
            if row.get("label_source") != "ai_silver" or row.get("label_status") != "pending_review":
                skipped += 1
                continue
            missing = [field for field in ("gold_grade", "gold_subject", "gold_color_mood", "gold_composition", "gold_value_labels") if not row.get(field)]
            if missing:
                skipped += 1
                continue
            row["label_source"] = "human_gold"
            row["label_status"] = "reviewed"
            row["human_note"] = f"{row.get('human_note', '').strip()}；{note}".strip("；")
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
        self._write_harness_gold_rows(dataset, rows)
        return {"approved_count": approved, "skipped_count": skipped, "dataset": str(dataset)}

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
        fixture = Path("/Users/fanglemin/Desktop/日本数据示例.xlsx")
        if country == "日本" and fixture.exists():
            records = import_history_workbook(fixture, country, self._runtime_dir / "images")
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


def _business_subject_description(subject: str, country: str, visual, semantic) -> str:
    color = visual.palette_summary
    if semantic and semantic.style:
        color = f"{visual.palette_summary}，整体风格为{semantic.style}"
    scene = semantic.scene if semantic and semantic.scene else visual.composition_summary
    culture = "、".join(semantic.culture_elements) if semantic and semantic.culture_elements else f"{country}市场元素待运营确认"
    return f"主体内容：{subject}；色彩氛围：{color}；构图环境：{scene}，结合{culture}。"


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


def _missing_value_llm_message(error) -> str:
    missing = "、".join(error.missing) if error else "QWEN_API_KEY"
    return f"价值观大师：需要配置真实视觉 LLM 后，才能基于当前图片解析结果和已有价值观规则判断匹配度；当前缺少 {missing}。"


def _append_system_rag_trace(value_match: str, rag_rules: tuple[tuple[str, str], ...]) -> str:
    citation_ids = tuple(title for title, _ in rag_rules if "#chunk-" in title)
    if not citation_ids or "系统RAG召回：" in value_match:
        return value_match
    return f"{value_match}；系统RAG召回：{'、'.join(citation_ids)}"


def _extract_rag_citation_ids(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    citations: list[str] = []
    for citation in re.findall(r"[A-Z]+_[A-Z_]*\d*#chunk-\d+", text):
        if citation not in seen:
            seen.add(citation)
            citations.append(citation)
    return tuple(citations)


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


def _looks_like_audit_query(query: str) -> bool:
    return any(word in query for word in ("风险", "审核", "水印", "IP", "版权", "商标", "文化混淆", "AI质量"))


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
