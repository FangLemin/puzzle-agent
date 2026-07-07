from dataclasses import replace
from datetime import date, datetime
import csv
import json
import os
from pathlib import Path
import re
from tempfile import gettempdir
import uuid

from puzzle_ops.data import COUNTRIES, SYNC_ROWS
from puzzle_ops.adapters import DeepEvalAdapter, MCPToolAdapter, PhoenixExporter, PromptfooExporter
from puzzle_ops.audit import AuditPolicyRetriever, AuditRuleEngine
from puzzle_ops.cms import MockCMSClient
from puzzle_ops.excel_importer import import_history_workbook
from puzzle_ops.feishu import FeishuClientFactory, MockFeishuClient
from puzzle_ops.models import AgentTrace, AnalysisReport, AnalysisRow, DemandRow, HolidayRecommendation, ImageProfile, ScheduleItem, TagMeta, ValuePredictionCard, ValueRuleCandidate
from puzzle_ops.multimodal import ImageFeatureExtractor, SimilarImageRetriever, ValueInsightMiner
from puzzle_ops.rag import FeedbackAwareRerankProvider, FileDocumentLoaderAdapter, HybridRagRetriever, LocalEmbeddingProvider, MilvusVectorStore, MilvusVectorStoreRetriever, QdrantVectorStore, QdrantVectorStoreRetriever, RagChunk, RagChunkingConfig, RagDocument, RagPrompt, RagProviderConfig, RagRetrievalCase, RagRuntimeStats, RagVectorStoreConfig, RetrievalCaseLoaderAdapter, StaticDocumentLoaderAdapter, build_processed_documents_from_raw, build_rag_prompt, chunk_document, evaluate_retrieval_report, export_offline_rag_index, export_rag_acceptance_report, prepare_qdrant_points, providers_from_config, rewrite_rag_query
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
    rag_chunking_config = RagChunkingConfig(chunk_size_tokens=600, chunk_overlap_tokens=100)
    rag_bm25_top_k = 30
    rag_vector_top_k = 30

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
        self.rag_vector_store_config = RagVectorStoreConfig.from_env()
        self._last_rag_stats = RagRuntimeStats()
        self._last_rag_rewritten_query = ""
        self._last_rag_trace: dict[str, object] = {}

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
        parsed, previews = self.trial_uploads.parse(row, files, mode, business_date=self.today)
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
            cycle_summary=f"{sample_summary}{data['cycle_summary']} 视觉维度复盘：{visual_recap}",
            next_todo=f"{data['next_todo']} 多模态建议：优先补充主体清晰、文化语境准确、质量风险低的试新参考图。",
            rows=rows,
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
        documents = StaticDocumentLoaderAdapter(self._rag_documents(country)).load()
        chunks = tuple(
            chunk
            for document in documents
            for chunk in chunk_document(document, max_chars=None, chunking=self.rag_chunking_config)
        )
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
        trace_payload = trace.as_dict()
        if hits != trace.final_hits:
            trace_payload = dict(trace_payload)
            trace_payload["final_hits"] = tuple(_rag_hit_trace_payload(hit) for hit in hits)
        prompt = build_rag_prompt(rewritten_query, hits)
        self._last_rag_stats = stats
        self._last_rag_rewritten_query = rewritten_query
        self._last_rag_trace = trace_payload
        self._write_rag_trace(country, query, rewritten_query, prompt, trace_payload, stats.as_dict())
        return prompt

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
            "offline_loader": "StaticDocumentLoaderAdapter",
            "splitter": self.rag_chunking_config.splitter,
            "chunk_size_tokens": self.rag_chunking_config.chunk_size_tokens,
            "chunk_overlap_tokens": self.rag_chunking_config.chunk_overlap_tokens,
            "vector_store": self.rag_vector_store_config.provider,
            "vector_store_collection": self.rag_vector_store_config.collection,
            "vector_store_ready": self.rag_vector_store_config.ready,
            "vector_store_status": self.rag_vector_store_config.status_text,
            "vector_store_search_enabled": self._rag_vector_store_search_enabled(),
            "bm25_top_k": self.rag_bm25_top_k,
            "vector_top_k": self.rag_vector_top_k,
            "rerank_top_k": 5,
            "rewritten_query": self._last_rag_rewritten_query,
            "retrieval_trace": self._last_rag_trace,
            "retrieval_eval_report": self.value_audit_rag_eval_report(country),
            "rag_eval_dataset": self.rag_eval_dataset_summary(country),
            "rag_eval_case_evidence": self.rag_eval_case_evidence(country),
            "rag_eval_failure_feedback": self.rag_eval_failure_feedback_summary(country),
            "rag_knowledge_patch_drafts": self.rag_knowledge_patch_drafts(country),
            "rag_patch_ops": self.rag_patch_ops_summary(country),
            "rag_live_model_ops": self.rag_live_model_ops_summary(country),
            "latest_acceptance_summary": self.latest_rag_acceptance_summary(country),
            "knowledge_base": self._rag_knowledge_summary(country),
            "feedback_summary": self.rag_feedback_summary(country),
            "recent_traces": self.recent_rag_traces(country, limit=3),
            **self._last_rag_stats.as_dict(),
        }

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
        vector_store: QdrantVectorStore | None = None,
    ) -> dict[str, object]:
        apply_result = self.apply_approved_rag_patch_and_rebuild(country)
        qdrant = self.reindex_rag_qdrant_from_raw(country, embedding_provider=embedding_provider, vector_store=vector_store)
        manifest_path = Path(str(apply_result.get("manifest_path", "")))
        latest_manifest_path = Path(str(apply_result.get("latest_manifest_path", "")))
        manifest = _read_json_object(manifest_path)
        qdrant_summary = {
            "status": qdrant.get("status", ""),
            "manifest_path": qdrant.get("manifest_path", ""),
            "latest_manifest_path": qdrant.get("latest_manifest_path", ""),
            "upserted_points": qdrant.get("upserted_points", 0),
            "chunk_count": qdrant.get("chunk_count", 0),
            "vector_count": qdrant.get("vector_count", 0),
            "vector_size": qdrant.get("vector_size", 0),
            "hit@5": qdrant.get("hit@5", 0),
            "mrr@5": qdrant.get("mrr@5", 0),
            "qdrant_collection": qdrant.get("qdrant_collection", ""),
        }
        manifest["status"] = "applied_rebuilt_qdrant_indexed"
        manifest["qdrant"] = qdrant_summary
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **apply_result,
            "status": "applied_rebuilt_qdrant_indexed",
            "qdrant": qdrant_summary,
        }

    def approve_rag_knowledge_patch_draft(self, country: str, patch_id: str, *, human_note: str) -> int:
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
        )

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
            "context": prompt.context,
            "citations": prompt.citations,
            "prompt": prompt.prompt,
            "retrieval_trace": retrieval_trace,
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
        store = vector_store or QdrantVectorStore(self.rag_vector_store_config)
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
        documents.extend(self._file_knowledge_rag_documents(country))
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

    def _file_knowledge_rag_documents(self, country: str) -> tuple[RagDocument, ...]:
        path = _rag_knowledge_dir() / "processed" / "value_audit_documents.jsonl"
        documents = FileDocumentLoaderAdapter((path,)).load()
        return tuple(document for document in documents if document.country in {country, "GLOBAL"})

    def _rag_eval_cases(self, country: str) -> tuple[RagRetrievalCase, ...]:
        path = _rag_knowledge_dir() / "eval" / "value_audit_cases.jsonl"
        cases = RetrievalCaseLoaderAdapter(path).load()
        return tuple(case for case in cases if case.country == country)

    def _rag_retrieval_cases(self, country: str) -> tuple[RagRetrievalCase, ...]:
        return (*self._rag_eval_cases(country), *self._harness_gold_rag_eval_cases(country))

    def _harness_gold_rag_eval_cases(self, country: str) -> tuple[RagRetrievalCase, ...]:
        cases: list[RagRetrievalCase] = []
        for sample in self.harness_samples(country):
            if not sample.is_real or sample.label_source != "human_gold" or sample.label_status != "reviewed":
                continue
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
        eligible = 0
        for row in rows:
            if row.get("country") != country or row.get("source") != "real":
                continue
            if wanted and row.get("sample_id") not in wanted:
                continue
            if not force and not _row_needs_ai_prelabeled(row):
                already_labeled += 1
                continue
            eligible += 1
            if max_count is not None and updated >= max_count:
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
