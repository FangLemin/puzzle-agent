from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default
import os
from pathlib import Path
import threading
import uuid
from urllib.parse import parse_qs, urlencode, urlparse

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.data import COUNTRIES
from puzzle_ops.rag import QdrantVectorStore
from puzzle_ops.renderer import AppState, DEFAULT_USER_ID, LOGIN_COUNTRIES, can_write_country, normalize_state, render_page, user_label


WRITE_PATHS = {
    "/add_regular",
    "/add_regular_all",
    "/save_dashboard",
    "/generate_descriptions",
    "/generate_description_benchmark",
    "/confirm_weekly_review_needs",
    "/save_needs",
    "/save_description_benchmark",
    "/sync_needs_feishu",
    "/approve_guarded_action",
    "/revert_guarded_action",
    "/run_business_skill",
    "/save_trial",
    "/sync_trial_feishu",
    "/apply_value_master",
    "/save_value_match_correction",
    "/simulate_trial_upload",
    "/upload_trial_images",
    "/parse_trial_uploads",
    "/clear_trial_uploads",
    "/generate_trial_derivatives",
    "/save_derivative_prompt",
    "/reset_derivative_prompt",
    "/clear_derivative_candidates",
    "/approve_generated_derivatives",
    "/save_analysis",
    "/import_value_candidates_excel",
    "/predict_value_candidates",
    "/predict_single_value_candidate",
    "/save_value_candidate_decision",
    "/generate_value_prediction_benchmark",
    "/save_value_prediction_benchmark",
    "/approve_value_candidate",
    "/review_memory",
    "/promote_memory",
    "/retire_memory",
    "/migrate_memory_country",
    "/resolve_memory_conflict",
    "/seed_memory_validation",
    "/record_rag_feedback",
    "/submit_rag_feedback_batch",
    "/record_rag_eval_failure_feedback",
    "/rebuild_rag_knowledge",
    "/reindex_rag_qdrant",
    "/reindex_rag_vector_store",
    "/export_rag_acceptance_report",
    "/export_rag_ops_report",
    "/export_rag_eval_failure_feedback",
    "/export_rag_knowledge_patch_drafts",
    "/export_approved_rag_patch_markdown",
    "/apply_approved_rag_patch_markdown",
    "/apply_approved_rag_patch_and_rebuild",
    "/rollback_latest_rag_patch_and_rebuild",
    "/apply_rag_patch_rebuild_and_reindex_qdrant",
    "/apply_approved_rag_patch_rebuild_and_reindex_vector_store",
    "/mark_rag_feedback_monthly",
    "/mark_rag_feedback_emergency",
    "/apply_emergency_rag_patch_and_rebuild",
    "/run_full_rag_acceptance",
    "/qdrant_smoke_diagnostic",
    "/milvus_smoke_diagnostic",
    "/rollback_qdrant_manifest",
    "/run_harness",
    "/run_real_vlm_harness",
    "/save_harness_gold_label",
    "/export_harness_gold_skeleton",
    "/register_harness_real_samples",
    "/auto_prelabeled_harness_gold",
    "/prelabel_harness_silver",
    "/approve_harness_silver_labels",
    "/export_harness_overrides",
    "/export_harness_annotations",
    "/export_harness_external_eval",
    "/create_production_backup",
}


class PuzzleOpsServer:
    def __init__(self) -> None:
        self.agent = PuzzleOpsAgent(enable_regular_vision=True)
        self.state = AppState()
        self.session_states: dict[str, AppState] = {self._session_key(self.state.user_id, self.state.country): self.state}
        self.state_lock = threading.RLock()
        self.derivative_job_foreground_grace_seconds = 0.05
        self.value_prediction_job_foreground_grace_seconds = 0.05
        self.harness_prelabel_job_foreground_grace_seconds = 0.05
        self.harness_approval_job_foreground_grace_seconds = 0.05

    def state_for_params(self, params: dict[str, list[str]]) -> AppState:
        user_id = value(params, "user_id", self.state.user_id or DEFAULT_USER_ID)
        country = value(params, "country", self.state.country or "日本")
        known_countries = {country_name for country_name, _ in LOGIN_COUNTRIES}
        if country not in known_countries:
            country = "日本"
        key = self._session_key(user_id, country)
        if key not in self.session_states:
            self.session_states[key] = AppState(user_id=user_id, country=country)
        return self.session_states[key]

    @staticmethod
    def _session_key(user_id: str, country: str) -> str:
        return f"{user_id or DEFAULT_USER_ID}:{country or '日本'}"


APP = PuzzleOpsServer()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/uploads/"):
            self.respond_upload(parsed.path.removeprefix("/uploads/"))
            return
        if parsed.path == "/local_image":
            self.respond_local_image(parse_qs(parsed.query).get("path", [""])[0])
            return
        query = parse_qs(parsed.query)
        with APP.state_lock:
            APP.state = APP.state_for_params(query)
            update_state_from_query(APP.state, query)
            html = render_page(APP.agent, APP.state)
        self.respond(html)

    def do_POST(self) -> None:
        state = APP.state
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            form, files = parse_post_body(self.headers.get("Content-Type", ""), body)
            path = urlparse(self.path).path
            with APP.state_lock:
                APP.state = APP.state_for_params(form)
                state = APP.state
                location = handle_action(path, form, files)
        except Exception as exc:
            state.sync_message = f"页面操作失败：{user_facing_error(str(exc))}"
            state.sync_url = ""
            location = redirect_location(state)
        self.send_response(303)
        self.send_header("Location", location or redirect_location(state))
        self.end_headers()

    def respond(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def respond_upload(self, filename: str) -> None:
        path = (APP.agent._runtime_dir / "trial_uploads" / Path(filename).name)
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def respond_local_image(self, image_path: str) -> None:
        path = Path(image_path).expanduser()
        if not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", image_content_type(path))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def update_state_from_query(state: AppState, query: dict[str, list[str]]) -> None:
    old_country = state.country
    old_trial_mode = state.trial_mode
    if "user_id" in query and query["user_id"][0]:
        state.user_id = query["user_id"][0]
    known_countries = set(COUNTRIES) | {country for country, _ in LOGIN_COUNTRIES}
    if "country" in query and query["country"][0] in known_countries:
        state.country = query["country"][0]
    for field in ("view", "category", "tag", "trial_mode", "schedule_day", "value_grade"):
        if field in query and query[field][0]:
            setattr(state, field, query[field][0])
    for field in (
        "memory_layer",
        "memory_review_status",
        "memory_approved_for_rag",
        "memory_conflict",
        "memory_created_by",
        "memory_subject",
        "memory_operation_tag",
    ):
        if field in query:
            setattr(state, field, query[field][0])
    state.show_holiday = query.get("show_holiday", [""])[0] == "1"
    state.show_prompt_benchmark = query.get("show_prompt_benchmark", [""])[0] == "1"
    state.show_value_benchmark = query.get("show_value_benchmark", [""])[0] == "1"
    if state.country != old_country:
        state.need_rows.clear()
        state.trial_rows.clear()
        state.trial_row = None
    if state.trial_mode != old_trial_mode:
        state.trial_rows.clear()
        state.trial_row = None


def handle_action(path: str, form: dict[str, list[str]], files: dict[str, list[dict[str, object]]] | None = None) -> str | None:
    files = files or {}
    state = APP.state
    agent = APP.agent
    update_state_from_query(state, form)
    normalize_state(agent, state)
    if path in WRITE_PATHS and not can_write_country(state.user_id, state.country):
        state.sync_message = f"当前用户 {user_label(state.user_id)} 对 {state.country} 只有只读权限，不能执行该操作。"
        state.sync_url = ""
        if state.view == "login":
            state.view = "dashboard"
        return None
    if path == "/add_regular":
        image_index = int(value(form, "image_index", "0"))
        state.need_rows.append(agent.add_regular_demand(state.country, state.category, state.tag, image_index))
        state.view = "regular"
    elif path == "/add_regular_all":
        for image_index, _ in enumerate(agent.images_for_tag(state.country, state.tag)):
            state.need_rows.append(agent.add_regular_demand(state.country, state.category, state.tag, image_index))
        state.view = "regular"
    elif path == "/save_dashboard":
        state.workflow_notes = [value(form, f"workflow_{index}", note) for index, note in enumerate(state.workflow_notes)]
        state.task_notes = [value(form, f"task_{index}", note) for index, note in enumerate(state.task_notes)]
        state.view = "dashboard"
    elif path == "/generate_descriptions":
        state.need_rows = _saved_need_rows_from_form(agent, state, form)
        selected = _selected_row_indexes(form, len(state.need_rows))
        updated_rows = []
        for index, row in enumerate(state.need_rows):
            if index not in selected:
                updated_rows.append(row)
                continue
            updated_rows.append(_generate_v3_subject_description(agent, row))
        state.need_rows = updated_rows
        state.description_benchmarks = []
        state.show_prompt_benchmark = False
        state.view = "regular"
    elif path == "/generate_description_benchmark":
        state.need_rows = _saved_need_rows_from_form(agent, state, form)
        selected = _selected_row_indexes(form, len(state.need_rows))
        updated_rows = []
        benchmarks = []
        for index, row in enumerate(state.need_rows):
            if index not in selected:
                updated_rows.append(row)
                continue
            template_row = agent.generate_subject_description(row)
            prompt_result = agent.generate_subject_description_prompt_baseline(row, template_row=template_row)
            v3_row = _row_with_v3_prompt_result(template_row, prompt_result)
            updated_rows.append(v3_row)
            benchmarks.append(_description_benchmark_item(template_row, prompt_result))
        state.need_rows = updated_rows
        state.description_benchmarks = benchmarks
        state.show_prompt_benchmark = True
        state.view = "regular"
    elif path == "/confirm_weekly_review_needs":
        rows = agent.weekly_review_need_rows(state.country)
        state.need_rows = list(rows)
        state.sync_message = f"周三复盘提需建议已生成：{len(rows)} 条，确认后可继续一键同步飞书。"
        state.sync_url = ""
        state.view = "regular"
    elif path == "/save_needs":
        state.need_rows = _saved_need_rows_from_form(agent, state, form)
        state.view = "regular"
    elif path == "/save_description_benchmark":
        saved_count = _save_description_benchmark_scores(agent, state, form)
        summary = agent.repository.description_benchmark_summary(state.country)
        state.sync_message = (
            f"Prompt Benchmark 评分已保存，批量保存 {saved_count} 条：当前国家累计 {summary['count']} 条；"
            f"模板平均 {summary['template_average']}；prompt平均 {summary['prompt_average']}；"
            f"prompt轻改/直用率 {int(float(summary['prompt_light_or_direct_rate']) * 100)}%。"
        )
        state.sync_url = ""
        state.view = "regular"
    elif path == "/sync_needs_feishu":
        state.need_rows = _saved_need_rows_from_form(agent, state, form)
        selected = _selected_row_indexes(form, len(state.need_rows))
        selected_rows = [row for index, row in enumerate(state.need_rows) if index in selected]
        rows = [_demand_row_payload(row) for row in selected_rows]
        count = len(rows)
        if count == 0:
            state.sync_message = "请先加入至少一条常规提需，再同步飞书表格。"
            state.sync_url = ""
            state.view = "regular"
            return None
        result = agent.sync_demand_rows(state.country, rows, require_real=True)
        if result.success:
            state.need_rows = [row for index, row in enumerate(state.need_rows) if index not in selected]
            state.sync_message = f"同步成功，已写入飞书表格 {count} 条。"
            state.sync_url = agent.feishu.web_url()
        else:
            state.sync_message = result.message or f"同步失败：{result.error}"
            state.sync_url = ""
        state.view = "regular"
    elif path == "/approve_guarded_action":
        proposal_id = value(form, "proposal_id", "")
        note = value(form, "approval_note", "")
        try:
            proposal = agent.approve_guarded_action(state.country, proposal_id, actor=state.user_id, note=note)
            if proposal.guard_status != "approved":
                state.sync_message = f"Guarded Action 未批准：{'；'.join(proposal.guard_reasons) or proposal.guard_status}"
                state.sync_url = ""
                return None
            if value(form, "execute_after_approval", "") == "1":
                result = agent.execute_guarded_action(state.country, proposal_id, actor=state.user_id)
                if result.success:
                    _clear_rows_after_guarded_execution(state, agent, proposal)
                    state.sync_message = f"同步成功，Guarded Action 已执行：{proposal_id}"
                    state.sync_url = agent.feishu.web_url()
                else:
                    state.sync_message = f"Guarded Action 执行失败：{result.error}"
                    state.sync_url = ""
            else:
                state.sync_message = f"Guarded Action 已批准：{proposal_id}"
                state.sync_url = ""
        except ValueError as exc:
            state.sync_message = f"Guarded Action 批准失败：{exc}"
            state.sync_url = ""
    elif path == "/revert_guarded_action":
        proposal_id = value(form, "proposal_id", "")
        result = agent.revert_guarded_action(state.country, proposal_id, actor=state.user_id, note=value(form, "revert_note", "运营撤销"))
        state.sync_message = result.message if result.success else f"Guarded Action 撤销失败：{result.error}"
        state.sync_url = ""
        if state.view not in {"regular", "trial", "runtime"}:
            state.view = "runtime"
    elif path == "/run_business_skill":
        skill_id = value(form, "skill_id", "")
        demo = next((case for case in agent.business_skill_acceptance_cases(state.country) if case.get("skill_id") == skill_id), None)
        if not demo:
            state.sync_message = f"Skill 运行失败：找不到 demo case {skill_id}"
        else:
            try:
                result = agent.run_business_skill(skill_id, dict(demo["input_payload"]), actor=state.user_id)
                proposal_copy = f"，生成 proposal {len(result.guarded_action_proposals)} 个" if result.guarded_action_proposals else ""
                state.sync_message = f"Skill 已运行：{result.skill_id}，草稿字段 {len(result.draft_output)} 个{proposal_copy}。"
            except Exception as exc:
                state.sync_message = f"Skill 运行失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/save_trial":
        active_rows = state.trial_derive_rows if state.trial_mode == "derive" else state.trial_parse_rows
        active_row = state.trial_derive_row if state.trial_mode == "derive" else state.trial_parse_row
        rows = active_rows or state.trial_rows or [active_row or state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)]
        saved = []
        for index, row in enumerate(rows):
            suffix = f"_{index}"
            saved.append(
                agent.edit_demand_row(
                    row,
                    priority=value(form, f"priority{suffix}", value(form, "priority", row.priority)),
                    count=int(value(form, f"count{suffix}", value(form, "count", str(row.count)))),
                    method=value(form, f"method{suffix}", value(form, "method", row.method)),
                    operation_tag=value(form, f"operation_tag{suffix}", value(form, "operation_tag", row.operation_tag)),
                    delivery_date=value(form, f"delivery_date{suffix}", value(form, "delivery_date", row.delivery_date)),
                    subject_description=value(form, f"subject_description{suffix}", value(form, "subject_description", row.subject_description)),
                    remark=value(form, f"remark{suffix}", value(form, "remark", row.remark)),
                )
            )
        if state.trial_mode == "derive":
            state.trial_derive_rows = saved if active_rows or state.trial_rows else []
            state.trial_derive_row = saved[-1]
        else:
            state.trial_parse_rows = saved if active_rows or state.trial_rows else []
            state.trial_parse_row = saved[-1]
        state.trial_rows = saved if state.trial_rows else []
        state.trial_row = saved[-1]
        state.view = "trial"
    elif path == "/sync_trial_feishu":
        rows_to_sync = state.trial_derive_rows if state.trial_mode == "derive" else state.trial_parse_rows
        if not rows_to_sync:
            rows_to_sync = state.trial_rows
        if not rows_to_sync:
            state.sync_message = "请先上传解析图片，生成至少一条试新提需记录。"
            state.sync_url = ""
            state.view = "trial"
            return None
        pending_generated = [row for row in rows_to_sync if row.generation_review_status and not row.reference_image_syncable]
        if pending_generated:
            state.sync_message = "生成图尚未完成二次审核与运营确认，暂不能同步飞书附件。"
            state.sync_url = ""
            state.view = "trial"
            return None
        rows = [_demand_row_payload(row) for row in rows_to_sync]
        result = agent.sync_demand_rows(state.country, rows, require_real=True)
        if result.success:
            state.sync_message = f"已一键同步试新提需到飞书表格：{len(rows_to_sync)} 条。"
            state.sync_url = agent.feishu.web_url()
            if state.trial_mode == "derive":
                state.trial_derive_rows = []
                state.trial_derive_row = None
            else:
                state.trial_parse_rows = []
                state.trial_parse_row = None
            state.trial_rows = []
            state.trial_row = agent.create_trial_demand(state.country, state.category, state.trial_mode)
        else:
            state.sync_message = result.message
            state.sync_url = ""
        state.view = "trial"
    elif path == "/apply_value_master":
        active_rows = state.trial_derive_rows if state.trial_mode == "derive" else state.trial_parse_rows
        active_row = state.trial_derive_row if state.trial_mode == "derive" else state.trial_parse_row
        if active_rows:
            updated_rows = [agent.apply_value_master(row) for row in active_rows]
            if state.trial_mode == "derive":
                state.trial_derive_rows = updated_rows
                state.trial_derive_row = updated_rows[-1]
            else:
                state.trial_parse_rows = updated_rows
                state.trial_parse_row = updated_rows[-1]
            state.trial_rows = updated_rows
            state.trial_row = updated_rows[-1]
            state.sync_message = "价值观大师已完成，结果已写入当前试新提需表。"
        elif active_row:
            updated = agent.apply_value_master(active_row)
            if state.trial_mode == "derive":
                state.trial_derive_row = updated
            else:
                state.trial_parse_row = updated
            state.trial_row = updated
            state.sync_message = "价值观大师已完成，结果已写入当前试新提需表。"
        elif state.trial_rows:
            state.trial_rows = [agent.apply_value_master(row) for row in state.trial_rows]
            state.trial_row = state.trial_rows[-1]
            state.sync_message = "价值观大师已完成，结果已写入当前试新提需表。"
        else:
            row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
            state.trial_row = agent.apply_value_master(row)
            state.sync_message = "价值观大师已完成，结果已写入当前试新提需表。"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/save_value_match_correction":
        row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        try:
            result = agent.record_value_match_human_correction(
                row,
                human_correction=value(form, "human_correction", ""),
                satisfaction_score=_optional_positive_int(value(form, "satisfaction_score", "")),
                actor=state.user_id,
            )
            state.sync_message = (
                "价值观人工修正已反哺RAG/Memory："
                f"working={result['working_memory_id']}；facts={result['fact_memory_id']}；rag_feedback={result['rag_feedback_memory_id']}"
            )
        except ValueError as exc:
            state.sync_message = f"价值观人工修正保存失败：{exc}"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/simulate_trial_upload":
        state.trial_row = agent.simulate_trial_upload(state.country, state.category, state.trial_mode)
        state.trial_rows.append(state.trial_row)
        state.trial_uploads = []
        state.view = "trial"
    elif path == "/upload_trial_images":
        previews = agent.save_trial_uploads(files.get("trial_images", [])[:3])
        if state.trial_mode == "derive":
            state.trial_derive_uploads = list(previews)
            state.trial_derivative_candidates = []
            state.trial_derivative_candidate_uploads = []
            clear_derivative_generation_job_state(state)
            if not state.trial_derivative_prompt_touched:
                state.trial_derivative_prompt = ""
                state.trial_derivative_negative_prompt = ""
            state.trial_rows = []
        else:
            state.trial_parse_uploads = list(previews)
            state.trial_parse_rows = []
            state.trial_rows = []
        state.trial_uploads = list(previews)
        if previews:
            if state.trial_mode == "derive":
                state.sync_message = f"已上传{len(previews)}张历史好图，请点击“解析图片”生成衍生方向。"
            else:
                state.sync_message = f"已上传{len(previews)}张参考图，请点击“解析图片”写入下方试新提需表。"
        else:
            state.sync_message = "未选择图片，无法上传。"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/parse_trial_uploads":
        uploads = state.trial_derive_uploads if state.trial_mode == "derive" else state.trial_parse_uploads
        row, previews, used_vision, background_pending = parse_trial_uploads_with_foreground_wait(
            state.country,
            state.category,
            state.trial_mode,
            tuple(uploads),
        )
        state.trial_row = row
        state.trial_uploads = list(previews)
        if state.trial_mode == "derive":
            state.trial_derive_row = row
            state.trial_derive_uploads = list(previews)
            state.trial_derivative_candidates = []
            state.trial_derivative_candidate_uploads = []
            clear_derivative_generation_job_state(state)
            if not state.trial_derivative_prompt_touched:
                prompt, negative_prompt = agent.derivative_generation_prompts(row)
                state.trial_derivative_prompt = prompt
                state.trial_derivative_negative_prompt = negative_prompt
            state.trial_rows = []
            state.sync_message = (
                _trial_parse_sync_message("derive", previews, used_vision, background_pending)
                if previews
                else row.remark or "请先上传历史好图。"
            )
        else:
            state.trial_parse_row = row
            state.trial_parse_rows = [row] if previews else []
            state.trial_parse_uploads = list(previews)
            state.trial_rows = [row] if previews else []
            state.sync_message = (
                _trial_parse_sync_message("parse", previews, used_vision, background_pending)
                if previews
                else row.remark or "请先上传参考图。"
            )
        state.sync_url = ""
        state.view = "trial"
    elif path == "/clear_trial_uploads":
        if state.trial_mode == "derive":
            state.trial_derive_row = None
            state.trial_derive_uploads = []
            state.trial_derivative_candidates = []
            state.trial_derivative_candidate_uploads = []
            clear_derivative_generation_job_state(state)
            state.trial_derivative_prompt = ""
            state.trial_derivative_negative_prompt = ""
            state.trial_derivative_prompt_touched = False
        else:
            state.trial_parse_row = None
            state.trial_parse_rows = []
            state.trial_parse_uploads = []
        state.trial_row = agent.create_trial_demand(state.country, state.category, state.trial_mode)
        state.trial_rows = []
        state.trial_uploads = []
        state.sync_message = "已取消当前模式上传的图片。"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/generate_trial_derivatives":
        row = state.trial_derive_row or state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        default_prompt, default_negative_prompt = agent.derivative_generation_prompts(row)
        submitted_prompt = value(form, "derivative_prompt", state.trial_derivative_prompt or default_prompt)
        submitted_negative_prompt = value(form, "derivative_negative_prompt", state.trial_derivative_negative_prompt or default_negative_prompt)
        state.trial_derivative_prompt = submitted_prompt
        state.trial_derivative_negative_prompt = submitted_negative_prompt
        state.trial_derivative_prompt_touched = (
            submitted_prompt.strip() != default_prompt or submitted_negative_prompt.strip() != default_negative_prompt
        )
        start_derivative_generation_job(row, state.trial_derivative_prompt, state.trial_derivative_negative_prompt)
        state.sync_url = ""
        state.view = "trial"
    elif path == "/save_derivative_prompt":
        row = state.trial_derive_row or state.trial_row or agent.create_trial_demand(state.country, state.category, "derive")
        default_prompt, default_negative_prompt = agent.derivative_generation_prompts(row)
        submitted_prompt = value(form, "derivative_prompt", state.trial_derivative_prompt or default_prompt)
        submitted_negative_prompt = value(form, "derivative_negative_prompt", state.trial_derivative_negative_prompt or default_negative_prompt)
        state.trial_derivative_prompt = submitted_prompt
        state.trial_derivative_negative_prompt = submitted_negative_prompt
        state.trial_derivative_prompt_touched = (
            submitted_prompt.strip() != default_prompt or submitted_negative_prompt.strip() != default_negative_prompt
        )
        state.sync_message = "已保存衍生 prompt，上传并解析图片后可直接生成。"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/reset_derivative_prompt":
        row = state.trial_derive_row or state.trial_row or agent.create_trial_demand(state.country, state.category, "derive")
        prompt, negative_prompt = agent.derivative_generation_prompts(row)
        state.trial_derivative_prompt = prompt
        state.trial_derivative_negative_prompt = negative_prompt
        state.trial_derivative_prompt_touched = False
        state.sync_message = "已恢复系统推荐衍生 prompt。"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/clear_derivative_candidates":
        state.trial_derivative_candidates = []
        state.trial_derivative_candidate_uploads = []
        clear_derivative_generation_job_state(state)
        state.generation_event = {}
        state.sync_message = "已清空AI效果图候选，可继续修改 prompt 后重试。"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/approve_generated_derivatives":
        candidate_rows = state.trial_derivative_candidates or state.trial_rows
        selected = _selected_derivative_candidate_indexes(form, len(candidate_rows))
        if state.trial_derivative_candidates and not selected:
            state.sync_message = "请至少选择一张满意的衍生图。"
            state.sync_url = ""
            state.view = "trial"
            return None
        approved = []
        for index, row in enumerate(candidate_rows):
            if selected and index not in selected:
                continue
            approved.append(row.edited(human_approved=True, reference_image_syncable=True))
        state.trial_derive_rows = approved
        state.trial_derivative_candidates = []
        state.trial_derivative_candidate_uploads = []
        state.trial_rows = approved
        if approved:
            state.trial_row = approved[-1]
            state.trial_derive_row = approved[-1]
        state.generation_event = dict(state.generation_event)
        state.generation_event["feishu_attachment_status"] = _feishu_attachment_status(approved)
        state.generation_event["message"] = "AI效果图已由运营确认并加入试新提需表。"
        agent.record_generation_event(state.country, state.generation_event)
        state.sync_message = f"已确认 {len(approved)} 张AI效果图，并加入下方试新提需表。"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/check_generation_provider":
        status = agent.generation_provider_status()
        state.sync_message = format_generation_provider_diagnostic(status)
        state.sync_url = ""
        state.view = "trial"
    elif path == "/save_analysis":
        report = agent.analysis_report(state.country)
        state.analysis_edits = {
            "remarks": {
                index: value(form, f"analysis_remark_{index}", row.remark)
                for index, row in enumerate(report.rows)
            },
            "cycle_summary": value(form, "cycle_summary", report.cycle_summary),
            "next_todo": value(form, "next_todo", report.next_todo),
        }
        state.view = "analysis"
    elif path == "/import_value_candidates_excel":
        candidates = agent.import_value_candidate_excel(state.country)
        ready_count = sum(1 for item in candidates if item.get("image_status") == "ready")
        state.sync_message = f"候选图 Excel 已导入：{len(candidates)} 条；可预测图片 {ready_count} 条。"
        state.sync_url = ""
        state.view = "value"
    elif path == "/predict_value_candidates":
        start_value_prediction_job(state.country)
        state.sync_url = ""
        state.view = "value"
    elif path == "/predict_single_value_candidate":
        candidate_id = value(form, "candidate_id", "")
        result = agent.predict_single_undistributed_value_candidate(state.country, candidate_id, force=True)
        status = str(result.get("status", "unknown"))
        if status in {"predicted", "cached"}:
            action = "单张预测完成" if status == "predicted" else "单张预测已复用缓存"
            state.sync_message = f"{action}：{candidate_id}"
        elif status == "missing_vision_model":
            state.sync_message = f"单张预测失败：{candidate_id} 未配置真实 Qwen3-VL。"
        elif status == "missing_image":
            state.sync_message = f"单张预测失败：{candidate_id} 图片缺失。"
        else:
            state.sync_message = f"单张预测失败：{candidate_id} 状态={status}。"
        state.sync_url = ""
        state.view = "value"
    elif path == "/generate_value_prediction_benchmark":
        selected_ids = tuple(form.get("candidate_id", ()))
        initial_candidates = {
            str(candidate.get("candidate_id", "")): candidate
            for candidate in agent.undistributed_value_candidates(state.country)
        }
        prediction_statuses = []
        for candidate_id in selected_ids[:10]:
            candidate = initial_candidates.get(candidate_id)
            if not candidate:
                continue
            if not _value_candidate_has_prediction(candidate):
                result = agent.predict_single_undistributed_value_candidate(state.country, candidate_id, force=False)
                prediction_statuses.append(f"{candidate_id}:{result.get('status', 'unknown')}")
            elif _value_candidate_needs_prediction_refresh(candidate):
                result = agent.predict_single_undistributed_value_candidate(state.country, candidate_id, force=True)
                prediction_statuses.append(f"{candidate_id}:{result.get('status', 'unknown')}")
        candidates = {
            str(candidate.get("candidate_id", "")): candidate
            for candidate in agent.undistributed_value_candidates(state.country)
        }
        selected = [candidates[candidate_id] for candidate_id in selected_ids if candidate_id in candidates]
        state.value_prediction_benchmarks = [
            _value_prediction_benchmark_item(agent, state.country, candidate)
            for candidate in selected[:10]
            if _value_candidate_has_prediction(candidate)
        ]
        state.show_value_benchmark = True
        if state.value_prediction_benchmarks:
            suffix = f"；补预测状态：{'; '.join(prediction_statuses)}" if prediction_statuses else ""
            state.sync_message = f"价值观预测评测已生成：{len(state.value_prediction_benchmarks)} 条{suffix}。"
        else:
            status_copy = f" 状态：{'; '.join(prediction_statuses)}。" if prediction_statuses else ""
            state.sync_message = f"价值观预测评测未生成：所选候选图尚未完成预测。请确认 Qwen3-VL 配置可用后重试。{status_copy}"
        state.sync_url = ""
        state.view = "value"
    elif path == "/save_value_prediction_benchmark":
        saved_count = _save_value_prediction_benchmark_scores(agent, state, form)
        summary = agent.repository.value_prediction_benchmark_summary(state.country)
        state.sync_message = (
            f"价值观预测 Benchmark 评分已保存，批量保存 {saved_count} 条：当前国家累计 {summary['count']} 条；"
            f"线上均分 {summary['baseline_average']}；候选均分 {summary['candidate_average']}；"
            f"候选轻改/直用率 {int(float(summary['candidate_light_or_direct_rate']) * 100)}%。"
        )
        state.sync_url = ""
        state.show_value_benchmark = True
        state.view = "value"
    elif path == "/save_value_candidate_decision":
        memory_id = agent.record_value_candidate_decision(
            state.country,
            value(form, "candidate_id", ""),
            value(form, "decision", "人工复核"),
            note=value(form, "decision_note", value(form, "human_note", "")),
            actor=state.user_id,
        )
        decision = value(form, "decision", "人工复核")
        candidate_id = value(form, "candidate_id", "")
        if decision == "优先排图":
            state.sync_message = f"已加入下周排图池：{candidate_id}；memory_id={memory_id}"
        elif decision == "人工复核":
            state.sync_message = f"要求修改已保存：{candidate_id}；memory_id={memory_id}"
        else:
            state.sync_message = f"候选图人工决策已保存：{candidate_id} · {decision}；memory_id={memory_id}"
        state.sync_url = ""
        state.view = "value"
    elif path == "/approve_value_candidate":
        agent.approve_value_candidate(
            value(form, "candidate_id", ""),
            state.country,
            value(form, "human_note", "运营确认加入固定价值观"),
        )
        state.view = "runtime"
    elif path == "/promote_memory":
        try:
            target_id = agent.promote_memory(
                int(value(form, "memory_id", "0")),
                target_layer=value(form, "target_layer", "facts"),
                human_note=value(form, "human_note", "运营人工确认"),
                actor=state.user_id,
            )
            state.sync_message = f"Memory 晋升成功：新 memory_id={target_id}"
        except (TypeError, ValueError) as exc:
            state.sync_message = f"Memory 晋升失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/review_memory":
        try:
            agent.review_memory(
                int(value(form, "memory_id", "0")),
                action=value(form, "review_action", "approve_no_rag"),
                actor=state.user_id,
            )
            state.sync_message = "Memory 审核状态已更新。"
        except (TypeError, ValueError) as exc:
            state.sync_message = f"Memory 审核失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/retire_memory":
        try:
            agent.retire_memory(int(value(form, "memory_id", "0")), actor=state.user_id)
            state.sync_message = "Memory 已停用，不再进入 RAG。"
        except (TypeError, ValueError) as exc:
            state.sync_message = f"Memory 停用失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/migrate_memory_country":
        try:
            migrated_id = agent.migrate_memory_country(
                int(value(form, "memory_id", "0")),
                target_country=value(form, "target_country", ""),
                actor=state.user_id,
                note=value(form, "migration_note", ""),
            )
            state.sync_message = f"Memory 国家迁移成功：新 memory_id={migrated_id}"
        except (TypeError, ValueError) as exc:
            state.sync_message = f"Memory 国家迁移失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/resolve_memory_conflict":
        try:
            result = agent.resolve_memory_conflict(
                state.country,
                conflict_id=value(form, "conflict_id", ""),
                action=value(form, "resolution_action", "defer"),
                actor=state.user_id,
                note=value(form, "resolution_note", ""),
                merge_text=value(form, "merge_text", ""),
            )
            merged = int(result.get("merged_memory_id", 0) or 0)
            suffix = f"；合并新 memory_id={merged}" if merged else ""
            state.sync_message = f"Memory 冲突已处理：{result.get('action', '')}{suffix}"
        except (TypeError, ValueError) as exc:
            state.sync_message = f"Memory 冲突处理失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/seed_memory_validation":
        try:
            result = agent.seed_memory_production_validation(state.country, actor=state.user_id)
            state.sync_message = (
                "Memory 生产验收样例已生成："
                f"approved={result['approved_memory_id']}；draft={result['draft_memory_id']}"
            )
        except ValueError as exc:
            state.sync_message = f"Memory 生产验收样例生成失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/record_rag_feedback":
        try:
            memory_id = agent.record_rag_citation_feedback(
                state.country,
                chunk_id=value(form, "chunk_id", ""),
                usefulness=value(form, "usefulness", "useful"),
                note=value(form, "note", ""),
                task_type=value(form, "task_type", "trial_value_match"),
                actor=state.user_id,
            )
            state.sync_message = f"RAG 依据反馈已记录：memory_id={memory_id}"
        except ValueError as exc:
            state.sync_message = f"RAG 依据反馈记录失败：{exc}"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/submit_rag_feedback_batch":
        row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        citation_count = _optional_positive_int(value(form, "citation_count", "0")) or 0
        citation_memory_ids: list[int] = []
        for index in range(citation_count):
            usefulness = value(form, f"usefulness_{index}", "").strip()
            chunk_id = value(form, f"chunk_id_{index}", "").strip()
            if usefulness not in {"useful", "not_useful"} or not chunk_id:
                continue
            citation_memory_ids.append(
                agent.record_rag_citation_feedback(
                    state.country,
                    chunk_id=chunk_id,
                    usefulness=usefulness,
                    note=value(form, f"note_{index}", ""),
                    task_type="trial_value_match",
                    actor=state.user_id,
                )
            )
        score = _optional_positive_int(value(form, "satisfaction_score", ""))
        score_memory_id = 0
        if score:
            score_memory_id = agent.record_working_memory(
                state.country,
                "value_match_human_score",
                {
                    "task_type": "value_match_eval",
                    "operation_tag": row.operation_tag,
                    "subject": row.subject,
                    "ai_value_match": row.value_match,
                    "satisfaction_score": score,
                },
                actor=state.user_id,
            )
        correction = value(form, "human_correction", "").strip()
        correction_result: dict[str, int] = {}
        if correction:
            try:
                correction_result = agent.record_value_match_human_correction(
                    row,
                    human_correction=correction,
                    satisfaction_score=score,
                    actor=state.user_id,
                )
            except ValueError as exc:
                state.sync_message = f"RAG 批量反馈部分提交；人工修正保存失败：{exc}"
                state.sync_url = ""
                state.view = "trial"
                return None
        correction_copy = f"；correction={correction_result.get('working_memory_id', 0)}" if correction_result else ""
        score_copy = f"；score={score}" if score else ""
        state.sync_message = f"RAG 批量反馈已提交：citation={len(citation_memory_ids)}{score_copy}；score_memory={score_memory_id}{correction_copy}"
        state.sync_url = ""
        state.view = "trial"
    elif path == "/record_rag_eval_failure_feedback":
        try:
            memory_id = agent.record_rag_eval_failure_feedback(
                state.country,
                query=value(form, "query", ""),
                expected_parent_id=value(form, "expected_parent_id", ""),
                retrieved_parent_ids=_split_parent_ids(value(form, "retrieved_parent_ids", "")),
                note=value(form, "note", ""),
                actor=state.user_id,
            )
            state.sync_message = f"RAG eval 失败case已记录：memory_id={memory_id}"
        except ValueError as exc:
            state.sync_message = f"RAG eval 失败case记录失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/rebuild_rag_knowledge":
        try:
            result = agent.rebuild_rag_knowledge_from_raw(state.country)
            state.sync_message = (
                "RAG 知识库已重建："
                f"documents={result.get('document_count', 0)}，"
                f"hit@5={result.get('hit@5', 0)}，"
                f"mrr@5={result.get('mrr@5', 0)}，"
                f"processed={result.get('processed_path', '')}"
            )
        except Exception as exc:
            state.sync_message = f"RAG 知识库重建失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/reindex_rag_qdrant":
        try:
            result = agent.reindex_rag_qdrant_from_raw(state.country)
            state.sync_message = (
                "Qdrant RAG 已重建入库："
                f"status={result.get('status', '')}，"
                f"points={result.get('upserted_points', 0)}，"
                f"chunks={result.get('chunk_count', 0)}，"
                f"vector_size={result.get('vector_size', 0)}，"
                f"hit@5={result.get('hit@5', 0)}，"
                f"collection={result.get('qdrant_collection', '')}，"
                f"manifest={result.get('manifest_path', '')}"
            )
        except Exception as exc:
            state.sync_message = f"Qdrant RAG 重建入库失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/reindex_rag_vector_store":
        provider_label = _vector_store_label(agent.rag_vector_store_config.provider)
        try:
            result = agent.reindex_rag_vector_store_from_raw(state.country)
            state.sync_message = (
                f"{provider_label} RAG 已重建入库："
                f"status={result.get('status', '')}，"
                f"points={result.get('upserted_points', 0)}，"
                f"chunks={result.get('chunk_count', 0)}，"
                f"vector_size={result.get('vector_size', 0)}，"
                f"hit@5={result.get('hit@5', 0)}，"
                f"mrr@5={result.get('mrr@5', 0)}，"
                f"precision@5={result.get('precision@5', 0)}，"
                f"recall@5={result.get('recall@5', 0)}，"
                f"ndcg@5={result.get('ndcg@5', 0)}，"
                f"collection={result.get('vector_store_collection', result.get('qdrant_collection', ''))}，"
                f"manifest={result.get('manifest_path', '')}"
            )
        except Exception as exc:
            state.sync_message = f"{provider_label} RAG 重建入库失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/export_rag_acceptance_report":
        output_dir = agent._runtime_dir / "rag_acceptance_reports"
        result = agent.export_value_audit_rag_acceptance_report(state.country, output_dir)
        state.sync_message = (
            "RAG 工业验收报告已导出："
            f"path={result['path']}；hit@5={result.get('hit@5', 0)}；"
            f"mrr@5={result.get('mrr@5', 0)}；passed={result.get('passed_threshold', False)}"
        )
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/export_rag_ops_report":
        output_dir = agent._runtime_dir / "rag_acceptance_reports"
        result = agent.export_rag_ops_report(state.country, output_dir)
        state.sync_message = (
            "RAG Ops 报告已导出："
            f"json={result.get('json_path', '')}；markdown={result.get('markdown_path', '')}"
        )
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/export_rag_eval_failure_feedback":
        export_path = agent.export_rag_eval_failure_feedback(
            state.country,
            agent._runtime_dir / f"rag_eval_failure_feedback_{state.country}.jsonl",
        )
        state.sync_message = f"已导出 RAG 失败反馈：{export_path}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/export_rag_knowledge_patch_drafts":
        export_path = agent.export_rag_knowledge_patch_drafts(
            state.country,
            agent._runtime_dir / f"rag_knowledge_patch_drafts_{state.country}.jsonl",
        )
        state.sync_message = f"已导出 RAG 知识补丁草案：{export_path}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/export_approved_rag_patch_markdown":
        export_path = agent.export_approved_rag_patch_markdown(
            state.country,
            agent._runtime_dir / f"approved_rag_patch_{state.country}.md",
        )
        state.sync_message = f"已导出已审核 RAG Markdown 补丁：{export_path}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/apply_approved_rag_patch_markdown":
        result = agent.apply_approved_rag_patch_markdown_to_raw(state.country)
        state.sync_message = (
            "已应用已审核 RAG Markdown 补丁："
            f"raw={result.get('raw_patch_path', '')}；"
            f"manifest={result.get('manifest_path', '')}；"
            f"patches={result.get('applied_patch_count', 0)}"
        )
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/apply_approved_rag_patch_and_rebuild":
        result = agent.apply_approved_rag_patch_and_rebuild(state.country)
        state.sync_message = (
            "已应用补丁并重建 RAG："
            f"raw={result.get('raw_patch_path', '')}；"
            f"processed={result.get('processed_path', '')}；"
            f"hit@5={result.get('hit@5', 0)}；"
            f"mrr@5={result.get('mrr@5', 0)}；"
            f"manifest={result.get('manifest_path', '')}"
        )
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/rollback_latest_rag_patch_and_rebuild":
        try:
            result = agent.rollback_latest_approved_rag_patch_and_rebuild(state.country)
            state.sync_message = (
                "已回滚最新 RAG 补丁并重建："
                f"removed={result.get('removed_raw_patch_path', '')}；"
                f"processed={result.get('processed_path', '')}；"
                f"hit@5={result.get('hit@5', 0)}；"
                f"mrr@5={result.get('mrr@5', 0)}；"
                f"manifest={result.get('manifest_path', '')}"
            )
        except ValueError as exc:
            state.sync_message = f"回滚最新 RAG 补丁失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/apply_rag_patch_rebuild_and_reindex_qdrant":
        result = agent.apply_approved_rag_patch_rebuild_and_reindex_qdrant(state.country)
        qdrant = result.get("qdrant", {}) if isinstance(result.get("qdrant"), dict) else {}
        state.sync_message = (
            "已应用补丁、重建 RAG 并入库 Qdrant："
            f"status={qdrant.get('status', '')}；"
            f"points={qdrant.get('upserted_points', 0)}；"
            f"vector_size={qdrant.get('vector_size', 0)}；"
            f"hit@5={qdrant.get('hit@5', 0)}；"
            f"patch_manifest={result.get('manifest_path', '')}；"
            f"qdrant_manifest={qdrant.get('manifest_path', '')}"
        )
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/apply_rag_patch_rebuild_and_reindex_vector_store":
        provider_label = _vector_store_label(agent.rag_vector_store_config.provider)
        result = agent.apply_approved_rag_patch_rebuild_and_reindex_vector_store(state.country)
        vector_store = result.get("vector_store", {}) if isinstance(result.get("vector_store"), dict) else {}
        state.sync_message = (
            f"已应用补丁、重建 RAG 并入库 {provider_label}："
            f"status={vector_store.get('status', '')}；"
            f"points={vector_store.get('upserted_points', 0)}；"
            f"vector_size={vector_store.get('vector_size', 0)}；"
            f"hit@5={vector_store.get('hit@5', 0)}；"
            f"mrr@5={vector_store.get('mrr@5', 0)}；"
            f"patch_manifest={result.get('manifest_path', '')}；"
            f"vector_store_manifest={vector_store.get('manifest_path', '')}"
        )
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/approve_rag_knowledge_patch_draft":
        try:
            memory_id = agent.approve_rag_knowledge_patch_draft(
                state.country,
                value(form, "patch_id", ""),
                human_note=value(form, "human_note", "运营审核通过，进入长期RAG记忆"),
            )
            state.sync_message = f"RAG 知识补丁已审核通过：memory_id={memory_id}"
        except ValueError as exc:
            state.sync_message = f"RAG 知识补丁审核失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/mark_rag_feedback_monthly":
        try:
            memory_id = agent.mark_rag_feedback_for_monthly_review(
                state.country,
                int(value(form, "memory_id", "0") or 0),
                actor=state.user_id,
                note=value(form, "review_note", ""),
            )
            state.sync_message = f"RAG 反馈已标记为月度处理：marker_memory_id={memory_id}"
        except ValueError as exc:
            state.sync_message = f"RAG 月度标记失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/mark_rag_feedback_emergency":
        try:
            memory_id = agent.mark_rag_feedback_for_emergency_patch(
                state.country,
                int(value(form, "memory_id", "0") or 0),
                actor=state.user_id,
                note=value(form, "review_note", ""),
            )
            state.sync_message = f"RAG 反馈已标记为紧急补丁：marker_memory_id={memory_id}"
        except ValueError as exc:
            state.sync_message = f"RAG 紧急标记失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/apply_emergency_rag_patch_and_rebuild":
        try:
            result = agent.apply_emergency_rag_patch_and_rebuild(
                state.country,
                int(value(form, "memory_id", "0") or 0),
                actor=state.user_id,
                note=value(form, "review_note", "负责人确认紧急补丁"),
            )
            state.sync_message = (
                "紧急 RAG 补丁已应用："
                f"status={result.get('status', '')}；"
                f"patch_id={result.get('patch_id', '')}；"
                f"hit@5={result.get('hit@5', 0)}；"
                f"mrr@5={result.get('mrr@5', 0)}"
            )
        except ValueError as exc:
            state.sync_message = f"紧急 RAG 补丁应用失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/run_full_rag_acceptance":
        output_dir = agent._runtime_dir / "rag_acceptance_reports"
        result = agent.run_full_rag_industrial_acceptance(state.country, output_dir, preflight_mode="live")
        reindex = result.get("reindex", {}) if isinstance(result.get("reindex"), dict) else {}
        report = result.get("report", {}) if isinstance(result.get("report"), dict) else {}
        observed = report.get("observed_retrieval", {}) if isinstance(report.get("observed_retrieval"), dict) else {}
        stats = report.get("runtime_stats", {}) if isinstance(report.get("runtime_stats"), dict) else {}
        preflight = result.get("preflight", {}) if isinstance(result.get("preflight"), dict) else {}
        state.sync_message = (
            "RAG 工业全链路验收完成："
            f"status={result.get('status')}；stage={result.get('failure_stage', '')}；"
            f"error={result.get('error', '')}；points={reindex.get('upserted_points', 0)}；"
            f"vector_size={reindex.get('vector_size', 0)}；hit@5={report.get('hit@5', 0)}；"
            f"mrr@5={report.get('mrr@5', 0)}；qdrant_hit={observed.get('qdrant_vector_hits', False)}；"
            f"preflight={_rag_preflight_summary(preflight)}；"
            f"embedding_remote={stats.get('embedding_remote_calls', 0)}；rerank_remote={stats.get('rerank_remote_calls', 0)}；"
            f"report={result.get('report_path', '')}"
        )
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/qdrant_smoke_diagnostic":
        try:
            result = agent.run_qdrant_smoke_diagnostic(state.country)
            state.sync_message = (
                "Qdrant smoke 诊断完成："
                f"status={result.get('status', '')}，"
                f"search_hit={result.get('search_hit', False)}，"
                f"cleanup={result.get('cleanup_status', '')}，"
                f"vector_size={result.get('vector_size', 0)}"
            )
        except Exception as exc:
            state.sync_message = f"Qdrant smoke 诊断失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/milvus_smoke_diagnostic":
        try:
            result = agent.run_milvus_smoke_diagnostic(state.country)
            state.sync_message = (
                "Milvus smoke 诊断完成："
                f"status={result.get('status', '')}，"
                f"search_hit={result.get('search_hit', False)}，"
                f"cleanup={result.get('cleanup_status', '')}，"
                f"vector_size={result.get('vector_size', 0)}"
            )
        except Exception as exc:
            state.sync_message = f"Milvus smoke 诊断失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/rollback_qdrant_manifest":
        try:
            restore_points = value(form, "restore_points", "") == "1"
            vector_store = None
            if restore_points:
                if agent.rag_vector_store_config.provider != "qdrant" or not agent.rag_vector_store_config.ready:
                    state.sync_message = f"Qdrant points 未恢复：{agent.rag_vector_store_config.status_text}"
                    state.sync_url = ""
                    state.view = "runtime"
                    return None
                vector_store = QdrantVectorStore(agent.rag_vector_store_config)
            result = agent.rollback_qdrant_manifest(state.country, value(form, "run_id", ""), vector_store=vector_store)
            restore_status = result.get("restore_status") if isinstance(result.get("restore_status"), dict) else {}
            state.sync_message = (
                "Qdrant manifest 已回滚："
                f"run_id={result.get('run_id', '')}，"
                f"vector_size={result.get('vector_size', 0)}，"
                f"points={result.get('upserted_points', 0)}，"
                f"restore={restore_status.get('status', '')}，"
                f"restored_points={restore_status.get('restored_points', 0)}"
            )
        except Exception as exc:
            state.sync_message = f"Qdrant manifest 回滚失败：{exc}"
        state.sync_url = ""
        state.view = "runtime"
    elif path == "/run_harness":
        execute_models = value(form, "run_real_models", "") == "1"
        execute_generation = value(form, "include_generation", "") == "1"
        real_samples = tuple(sample for sample in agent.harness_samples(state.country) if sample.is_real)
        if execute_models and not real_samples:
            state.sync_message = "真实 VLM Harness 未运行：当前没有可读取的真实图片样本。"
        else:
            try:
                run = agent.harness_run(
                    state.country,
                    execute_models=execute_models,
                    execute_generation=execute_generation,
                    save=True,
                )
                generation_copy = "，包含图像生成评测" if execute_generation else "，未调用图像生成模型"
                state.sync_message = f"真实 VLM Harness 已完成：run_id={run.run_id}{generation_copy}。"
            except Exception as exc:
                state.sync_message = f"Harness 运行失败：{exc}"
        state.sync_url = ""
        state.view = "eval"
    elif path == "/save_harness_override":
        agent.record_harness_override(
            state.country,
            value(form, "sample_id", ""),
            value(form, "task_type", ""),
            value(form, "human_override", ""),
        )
        state.view = "eval"
    elif path == "/save_harness_gold_label":
        try:
            dataset = agent.update_harness_gold_label(
                state.country,
                value(form, "sample_id", ""),
                gold_grade=value(form, "gold_grade", ""),
                gold_subject=value(form, "gold_subject", ""),
                gold_color_mood=value(form, "gold_color_mood", ""),
                gold_composition=value(form, "gold_composition", ""),
                gold_value_labels=value(form, "gold_value_labels", ""),
                gold_risk_labels=value(form, "gold_risk_labels", ""),
                human_note=value(form, "human_note", ""),
                position=value(form, "position", ""),
                open_rate=value(form, "open_rate", ""),
                completion_rate=value(form, "completion_rate", ""),
                avg_finish_time=value(form, "avg_finish_time", ""),
            )
            state.sync_message = f"Gold Label 已保存：{dataset}"
        except ValueError as exc:
            state.sync_message = f"Gold Label 保存失败：{exc}"
        state.sync_url = ""
        state.view = "eval"
    elif path == "/export_harness_gold_skeleton":
        dataset = agent.ensure_harness_gold_dataset(state.country)
        state.sync_message = f"已生成 Gold Dataset 骨架：{dataset}"
        state.sync_url = ""
        state.view = "eval"
    elif path == "/register_harness_real_samples":
        try:
            image_dir = value(form, "image_dir", "").strip()
            if image_dir:
                result = agent.register_harness_real_samples_from_directory(
                    state.country,
                    image_dir,
                    value(form, "directory_grade_text", ""),
                    js_category=value(form, "directory_js_category", "real_sample"),
                )
                state.sync_message = (
                    f"真实样本目录已登记：{result['registered_count']}/{result['image_count']} 张；"
                    f"数据集：{result['dataset']}"
                )
            else:
                result = agent.register_harness_real_samples_from_text(state.country, value(form, "samples_text", ""))
                state.sync_message = f"真实样本已登记：{result['registered_count']} 条；数据集：{result['dataset']}"
            if value(form, "auto_prelabeled", "") == "1":
                try:
                    prelabel = agent.auto_prelabeled_harness_samples(state.country, max_count=5)
                    state.sync_message += _prelabel_message(prelabel)
                except ValueError as exc:
                    state.sync_message += f"；AI 预标注失败：{exc}"
        except ValueError as exc:
            state.sync_message = f"真实样本登记失败：{exc}"
        state.sync_url = ""
        state.view = "eval"
    elif path == "/auto_prelabeled_harness_gold":
        max_count = _optional_positive_int(value(form, "max_count", ""))
        selected_sample_ids = tuple(item.strip() for item in form.get("sample_id", ()) if item.strip())
        prelabel_kwargs = {}
        if selected_sample_ids:
            prelabel_kwargs["sample_ids"] = selected_sample_ids
        if max_count is not None:
            prelabel_kwargs["max_count"] = max_count
        start_harness_prelabel_job(state.country, prelabel_kwargs)
        state.sync_url = ""
        state.view = "eval"
    elif path == "/approve_harness_silver_labels":
        selected_sample_ids = tuple(item.strip() for item in form.get("sample_id", ()) if item.strip())
        approve_kwargs = {"reviewer_note": value(form, "reviewer_note", "人工抽查通过")}
        if selected_sample_ids:
            approve_kwargs["sample_ids"] = selected_sample_ids
        start_harness_approval_job(state.country, approve_kwargs)
        state.sync_url = ""
        state.view = "eval"
    elif path == "/export_harness_overrides":
        export_path = agent.export_harness_overrides(state.country, agent._runtime_dir / f"harness_overrides_{state.country}.csv")
        state.sync_message = f"已导出 Harness 人工修正：{export_path}"
        state.sync_url = ""
        state.view = "eval"
    elif path == "/export_harness_annotations":
        output_dir = agent._runtime_dir / "harness_annotation_exports"
        paths = agent.export_harness_annotation_files(state.country, output_dir)
        state.sync_message = f"已导出标注平台文件：Argilla={paths['argilla']}；Label Studio={paths['label_studio']}"
        state.sync_url = ""
        state.view = "eval"
    elif path == "/export_harness_external_eval":
        output_dir = agent._runtime_dir / "harness_external_eval_exports"
        paths = agent.export_harness_external_eval_artifacts(state.country, output_dir)
        state.sync_message = f"已导出外部评测文件：Phoenix={paths['phoenix']}；Promptfoo={paths['promptfoo']}；Promptfoo YAML={paths['promptfoo_yaml']}；DeepEval={paths['deepeval']}"
        state.sync_url = ""
        state.view = "eval"
    elif path == "/create_production_backup":
        result = agent.create_production_backup(label=value(form, "backup_label", "manual"))
        state.sync_message = f"生产运行数据已备份：{result['backup_dir']}"
        state.sync_url = ""
        state.view = "runtime"
    return None


def redirect_location(state: AppState) -> str:
    params = {
        "user_id": state.user_id,
        "country": state.country,
        "view": state.view,
        "category": state.category,
        "tag": state.tag,
        "trial_mode": state.trial_mode,
        "schedule_day": state.schedule_day,
        "value_grade": state.value_grade,
        "memory_layer": state.memory_layer,
        "memory_review_status": state.memory_review_status,
        "memory_approved_for_rag": state.memory_approved_for_rag,
        "memory_conflict": state.memory_conflict,
        "memory_created_by": state.memory_created_by,
        "memory_subject": state.memory_subject,
        "memory_operation_tag": state.memory_operation_tag,
    }
    if state.show_holiday:
        params["show_holiday"] = "1"
    if state.show_prompt_benchmark:
        params["show_prompt_benchmark"] = "1"
    if state.show_value_benchmark:
        params["show_value_benchmark"] = "1"
    return "/?" + urlencode(params)


def value(form: dict[str, list[str]], key: str, default: str) -> str:
    return form.get(key, [default])[0]


def _optional_positive_int(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _prelabel_message(result: dict[str, object]) -> str:
    return (
        f"；AI 预标注 {result.get('updated_count', 0)} 条，跳过 {result.get('skipped_count', 0)} 条，"
        f"剩余待预标注 {result.get('remaining_needs_prelabeled', 0)} 条"
    )


def image_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def format_generation_provider_diagnostic(status: dict[str, object]) -> str:
    provider = str(status.get("provider", "not_configured"))
    configured = str(status.get("configured", False))
    ready = str(status.get("ready", status.get("configured", False)))
    message = str(status.get("message", "生成 provider 未配置"))
    model = str(status.get("model", "未配置"))
    endpoint = str(status.get("base_url") or status.get("submit_url") or "未配置")
    api_key_source = str(status.get("api_key_source", "未配置"))
    sdk_available = str(status.get("sdk_available", "未记录"))
    workflow_path = str(status.get("workflow_path", "未配置"))
    workflow_configured = str(status.get("workflow_configured", "未记录"))
    return (
        f"Qwen 图像生成诊断：provider={generation_provider_label(provider)}；configured={configured}；ready={ready}；"
        f"model={model}；endpoint={endpoint}；api_key_source={api_key_source}；"
        f"sdk_available={sdk_available}；workflow_path={workflow_path}；"
        f"workflow_configured={workflow_configured}；{user_facing_generation_message(message)}"
    )


def generation_event(
    status: str,
    provider_status: dict[str, object],
    message: str,
    error_type: str,
    source_operation_tag: str = "",
    task_id: str = "",
    generated_image_paths: str = "",
    second_review_status: str = "unknown",
    feishu_attachment_status: str = "unknown",
) -> dict[str, str]:
    return {
        "status": status,
        "provider": generation_provider_label(str(provider_status.get("provider", "not_configured"))),
        "model": str(provider_status.get("model", "未记录")),
        "endpoint": str(provider_status.get("base_url") or provider_status.get("submit_url") or "未记录"),
        "task_id": task_id,
        "source_operation_tag": source_operation_tag,
        "generated_image_paths": generated_image_paths,
        "second_review_status": second_review_status,
        "feishu_attachment_status": feishu_attachment_status,
        "error_type": error_type,
        "recovery_hint": generation_error_recovery_hint(error_type),
        "message": message,
    }


def _second_review_status(rows) -> str:
    if not rows:
        return "not_started"
    return "passed" if all(row.generation_review_status == "passed" for row in rows) else "blocked"


def _feishu_attachment_status(rows) -> str:
    if not rows:
        return "blocked"
    if all(row.reference_image_syncable for row in rows):
        return "ready"
    if all(row.generation_review_status == "passed" for row in rows):
        return "pending_human_approval"
    return "blocked"


def classify_generation_error(message: str) -> str:
    text = message.lower()
    if any(token in text for token in ("nameresolutionerror", "failed to resolve", "nodename nor servname", "qwen.aliyuncs.com")):
        return "endpoint_dns"
    if any(token in text for token in ("arrearage", "overdue-payment", "good standing", "欠费", "逾期")):
        return "billing_arrearage"
    if any(token in text for token in ("quota", "insufficient", "balance", "余额", "额度", "欠费")):
        return "quota_exceeded"
    if any(token in text for token in ("下线", "deprecated", "retired", "model not found", "模型不存在", "模型已")):
        return "model_deprecated"
    if any(token in text for token in ("timeout", "timed out", "超时")):
        return "timeout"
    if any(token in text for token in ("unauthorized", "forbidden", "invalid api key", "api key", "401", "403", "鉴权", "权限")):
        return "auth_error"
    if any(token in text for token in ("未配置", "not_configured", "missing")):
        return "config_missing"
    if any(token in text for token in ("task_id", "results", "b64_json", "image_base64", "返回结构", "schema")):
        return "response_schema"
    return "unknown"


def generation_error_recovery_hint(error_type: str) -> str:
    return {
        "billing_arrearage": "请到阿里云控制台处理账号欠费、余额或资源包状态，确认模型服务可用后再重试真实生成。",
        "quota_exceeded": "请检查 Qwen 图像生成额度、资源包余量或调用频控，必要时降低生成张数后重试。",
        "model_deprecated": "请把 IMAGE_GENERATION_MODEL 迁移到当前可用的通义万相模型，并完成一次 smoke test。",
        "timeout": "请稍后重试；如果频繁超时，建议记录 task_id 并降低单次生成数量。",
        "auth_error": "请检查 QWEN_API_KEY 或 IMAGE_GENERATION_API_KEY 是否有效，并确认账号有模型调用权限。",
        "config_missing": "请补齐 IMAGE_GENERATION_PROVIDER、生成模型和 API key 配置后再重试。",
        "response_schema": "请保留原始响应并更新 Qwen 图像生成响应解析适配层。",
        "endpoint_dns": "请检查 Qwen 图像生成 endpoint/DNS 配置；DASHSCOPE_HTTP_BASE_URL 应使用 https://dashscope.aliyuncs.com/api/v1，不能使用 Qwen.aliyuncs.com。",
        "none": "生成任务已完成，等待二次 VLM 审核和人工确认。",
    }.get(error_type, "请查看生成任务原始错误，必要时保留 task_id 与 provider 日志后排查。")


def generation_provider_label(provider: str) -> str:
    value = (provider or "").strip().lower()
    if value in {"dashscope", "wanx", "cloud"}:
        return "Qwen 图像生成"
    if value == "mock":
        return "本地模拟生成"
    if value == "comfyui":
        return "ComfyUI 本地图像生成"
    if value in {"not_configured", "missing", ""}:
        return "未配置"
    return provider


def user_facing_generation_message(message: str) -> str:
    text = (
        str(message)
        .replace("DashScope", "Qwen")
        .replace("provider", "服务")
        .replace("Provider", "服务")
        .replace("dashscope", "Qwen")
    )
    if "Image dimensions must be in [240, 8000]" in text:
        return "Qwen 图像生成要求参考图宽高在 240-8000 像素之间，请换一张更清晰的历史好图。"
    return text


def user_facing_error(message: str) -> str:
    return user_facing_generation_message(message)


def parse_post_body(content_type: str, body: bytes) -> tuple[dict[str, list[str]], dict[str, list[dict[str, object]]]]:
    if "multipart/form-data" not in content_type:
        return parse_qs(body.decode("utf-8")), {}
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    form: dict[str, list[str]] = {}
    files: dict[str, list[dict[str, object]]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.setdefault(name, []).append(
                {"filename": filename, "content_type": part.get_content_type(), "content": payload}
            )
        else:
            form.setdefault(name, []).append(payload.decode(part.get_content_charset() or "utf-8"))
    return form, files


def start_derivative_generation_job(row, prompt: str, negative_prompt: str) -> None:
    job_id = f"derive-{uuid.uuid4().hex[:12]}"
    state = APP.state
    with APP.state_lock:
        state.trial_derivative_job_id = job_id
        state.trial_derivative_job_status = "pending"
        state.trial_derivative_job_progress = 10
        state.trial_derivative_job_message = "后台生成任务已启动，正在准备并行生成 2 张候选图。"
        state.trial_derivative_candidates = []
        state.trial_derivative_candidate_uploads = []
        state.sync_message = "后台生成任务已启动，页面会自动刷新；你可以先继续检查 prompt 或等待候选图出现。"

    thread = threading.Thread(
        target=_run_derivative_generation_job,
        args=(job_id, row, prompt, negative_prompt),
        daemon=True,
    )
    thread.start()
    thread.join(APP.derivative_job_foreground_grace_seconds)


def start_value_prediction_job(country: str) -> None:
    job_id = f"value-{uuid.uuid4().hex[:12]}"
    state = APP.state
    with APP.state_lock:
        state.value_prediction_job_id = job_id
        state.value_prediction_job_status = "pending"
        state.value_prediction_job_progress = 10
        state.value_prediction_job_message = f"{country}候选图预测任务已启动，正在准备 Qwen3-VL 与 RAG 依据。"
        state.sync_message = "价值观大师后台预测已启动，页面会自动刷新；完成后候选卡会显示预测值。"
        state.sync_url = ""

    thread = threading.Thread(target=_run_value_prediction_job, args=(job_id, country), daemon=True)
    thread.start()
    thread.join(APP.value_prediction_job_foreground_grace_seconds)


def start_harness_prelabel_job(country: str, prelabel_kwargs: dict[str, object]) -> None:
    job_id = f"prelabel-{uuid.uuid4().hex[:12]}"
    state = APP.state
    sample_ids = tuple(prelabel_kwargs.get("sample_ids", ()) or ())
    target_copy = f"已勾选 {len(sample_ids)} 张" if sample_ids else "未勾选样本，将按当前筛选解析待预标注样本"
    with APP.state_lock:
        state.harness_prelabel_job_id = job_id
        state.harness_prelabel_job_status = "pending"
        state.harness_prelabel_job_progress = 8
        state.harness_prelabel_job_message = f"Qwen 预标注任务已启动：{target_copy}，正在准备视觉解析。"
        state.sync_message = "Qwen 预标注任务已启动，页面会自动刷新；你可以看到每张图的解析进度。"
        state.sync_url = ""

    thread = threading.Thread(target=_run_harness_prelabel_job, args=(job_id, country, prelabel_kwargs), daemon=True)
    thread.start()
    thread.join(APP.harness_prelabel_job_foreground_grace_seconds)


def _run_harness_prelabel_job(job_id: str, country: str, prelabel_kwargs: dict[str, object]) -> None:
    with APP.state_lock:
        if APP.state.harness_prelabel_job_id != job_id:
            return
        APP.state.harness_prelabel_job_status = "running"
        APP.state.harness_prelabel_job_progress = 15
        APP.state.harness_prelabel_job_message = f"Qwen 正在准备解析{country}真实样本。"

    def progress(done: int, total: int, sample_id: str) -> None:
        total = max(total, 1)
        pct = 15 + int((max(0, done) / total) * 75)
        with APP.state_lock:
            if APP.state.harness_prelabel_job_id != job_id:
                return
            APP.state.harness_prelabel_job_status = "running"
            APP.state.harness_prelabel_job_progress = max(15, min(95, pct))
            APP.state.harness_prelabel_job_message = f"Qwen 正在解析 {done}/{total}：{sample_id}"

    try:
        result = APP.agent.auto_prelabeled_harness_samples(country, **prelabel_kwargs, progress_callback=progress)
    except Exception as exc:
        _finish_harness_prelabel_failure(job_id, exc)
        return
    _finish_harness_prelabel_success(job_id, result)


def _finish_harness_prelabel_success(job_id: str, result: dict[str, object]) -> None:
    message = (
        f"AI 预标注完成：{result['updated_count']} 条，跳过 {result['skipped_count']} 条，"
        f"剩余待预标注 {result.get('remaining_needs_prelabeled', 0)} 条，待审核 silver {result.get('pending_review_count', 0)} 条；"
        f"数据集：{result['dataset']}"
    )
    with APP.state_lock:
        if APP.state.harness_prelabel_job_id != job_id:
            return
        APP.state.harness_prelabel_job_status = "succeeded"
        APP.state.harness_prelabel_job_progress = 100
        APP.state.harness_prelabel_job_message = message
        APP.state.sync_message = message
        APP.state.sync_url = ""
        APP.state.view = "eval"


def _finish_harness_prelabel_failure(job_id: str, exc: Exception) -> None:
    message = f"AI 预标注失败：{user_facing_error(str(exc))}"
    with APP.state_lock:
        if APP.state.harness_prelabel_job_id != job_id:
            return
        APP.state.harness_prelabel_job_status = "failed"
        APP.state.harness_prelabel_job_progress = 100
        APP.state.harness_prelabel_job_message = message
        APP.state.sync_message = message
        APP.state.sync_url = ""
        APP.state.view = "eval"


def start_harness_approval_job(country: str, approve_kwargs: dict[str, object]) -> None:
    job_id = f"approve-gold-{uuid.uuid4().hex[:12]}"
    state = APP.state
    sample_ids = tuple(approve_kwargs.get("sample_ids", ()) or ())
    target_copy = f"已勾选 {len(sample_ids)} 张" if sample_ids else "未勾选样本，将确认当前国家全部待审核 silver"
    with APP.state_lock:
        state.harness_approval_job_id = job_id
        state.harness_approval_job_status = "pending"
        state.harness_approval_job_progress = 8
        state.harness_approval_job_message = f"human_gold 批量确认任务已启动：{target_copy}。"
        state.sync_message = "human_gold 批量确认任务已启动，页面会自动刷新；确认完成后会显示写入结果。"
        state.sync_url = ""

    thread = threading.Thread(target=_run_harness_approval_job, args=(job_id, country, approve_kwargs), daemon=True)
    thread.start()
    thread.join(APP.harness_approval_job_foreground_grace_seconds)


def _run_harness_approval_job(job_id: str, country: str, approve_kwargs: dict[str, object]) -> None:
    with APP.state_lock:
        if APP.state.harness_approval_job_id != job_id:
            return
        APP.state.harness_approval_job_status = "running"
        APP.state.harness_approval_job_progress = 15
        APP.state.harness_approval_job_message = f"正在把{country}已抽查通过的 silver 写入 human_gold。"

    def progress(done: int, total: int, sample_id: str) -> None:
        total = max(total, 1)
        pct = 15 + int((max(0, done) / total) * 75)
        with APP.state_lock:
            if APP.state.harness_approval_job_id != job_id:
                return
            APP.state.harness_approval_job_status = "running"
            APP.state.harness_approval_job_progress = max(15, min(95, pct))
            APP.state.harness_approval_job_message = f"正在确认 {done}/{total}：{sample_id}"

    try:
        result = APP.agent.approve_harness_silver_labels(country, **approve_kwargs, progress_callback=progress)
    except Exception as exc:
        _finish_harness_approval_failure(job_id, exc)
        return
    _finish_harness_approval_success(job_id, result)


def _finish_harness_approval_success(job_id: str, result: dict[str, object]) -> None:
    message = (
        f"AI Silver 已确认晋升：{result['approved_count']} 条，跳过 {result['skipped_count']} 条；"
        f"Facts {result.get('fact_memory_count', 0)} 条，RAG human_gold {result.get('rag_human_gold_count', 0)} 条；"
        f"数据集：{result['dataset']}"
    )
    with APP.state_lock:
        if APP.state.harness_approval_job_id != job_id:
            return
        APP.state.harness_approval_job_status = "succeeded"
        APP.state.harness_approval_job_progress = 100
        APP.state.harness_approval_job_message = message
        APP.state.sync_message = message
        APP.state.sync_url = ""
        APP.state.view = "eval"


def _finish_harness_approval_failure(job_id: str, exc: Exception) -> None:
    message = f"human_gold 批量确认失败：{user_facing_error(str(exc))}"
    with APP.state_lock:
        if APP.state.harness_approval_job_id != job_id:
            return
        APP.state.harness_approval_job_status = "failed"
        APP.state.harness_approval_job_progress = 100
        APP.state.harness_approval_job_message = message
        APP.state.sync_message = message
        APP.state.sync_url = ""
        APP.state.view = "eval"


def _run_value_prediction_job(job_id: str, country: str) -> None:
    with APP.state_lock:
        if APP.state.value_prediction_job_id != job_id:
            return
        APP.state.value_prediction_job_status = "running"
        APP.state.value_prediction_job_progress = 35
        APP.state.value_prediction_job_message = f"正在预测{country}未分发候选图：视觉解析、相似历史样本、RAG citation。"
    try:
        result = APP.agent.predict_undistributed_value_candidates(country)
    except Exception as exc:
        _finish_value_prediction_failure(job_id, country, exc)
        return
    _finish_value_prediction_success(job_id, country, result)


def _finish_value_prediction_success(job_id: str, country: str, result: dict[str, object]) -> None:
    blocked = int(result.get("blocked_count", 0) or 0)
    blocked_copy = f"；未预测 {blocked} 条，请检查图片或 Qwen3-VL 配置" if blocked else ""
    message = (
        f"价值观大师预测完成：新预测 {result.get('predicted_count', 0)} 条，"
        f"复用缓存 {result.get('cached_count', 0)} 条，候选共 {result.get('candidate_count', 0)} 条"
        f"{blocked_copy}。"
    )
    with APP.state_lock:
        if APP.state.value_prediction_job_id != job_id:
            return
        APP.state.value_prediction_job_status = "succeeded"
        APP.state.value_prediction_job_progress = 100
        APP.state.value_prediction_job_message = message
        APP.state.sync_message = message
        APP.state.sync_url = ""
        APP.state.view = "value"


def _finish_value_prediction_failure(job_id: str, country: str, exc: Exception) -> None:
    message = f"价值观大师预测失败：{user_facing_error(str(exc))}"
    with APP.state_lock:
        if APP.state.value_prediction_job_id != job_id:
            return
        APP.state.value_prediction_job_status = "failed"
        APP.state.value_prediction_job_progress = 100
        APP.state.value_prediction_job_message = message
        APP.state.sync_message = message
        APP.state.sync_url = ""
        APP.state.view = "value"


def clear_derivative_generation_job_state(state: AppState) -> None:
    state.trial_derivative_job_id = ""
    state.trial_derivative_job_status = ""
    state.trial_derivative_job_progress = 0
    state.trial_derivative_job_message = ""


def _run_derivative_generation_job(job_id: str, row, prompt: str, negative_prompt: str) -> None:
    agent = APP.agent
    with APP.state_lock:
        if APP.state.trial_derivative_job_id != job_id:
            return
        APP.state.trial_derivative_job_status = "running"
        APP.state.trial_derivative_job_progress = 45
        APP.state.trial_derivative_job_message = "正在并行生成 2 张候选图，并准备二次 VLM 审核。"
    try:
        updated, rows, previews = agent.generate_trial_derivatives(row, prompt=prompt, negative_prompt=negative_prompt)
    except Exception as exc:
        _finish_derivative_generation_failure(job_id, row, exc)
        return
    _finish_derivative_generation_success(job_id, updated, rows, previews)


def _finish_derivative_generation_failure(job_id: str, row, exc: Exception) -> None:
    state = APP.state
    agent = APP.agent
    provider_status = agent.generation_provider_status()
    error_type = classify_generation_error(str(exc))
    recovery_hint = generation_error_recovery_hint(error_type)
    clean_error = user_facing_error(str(exc))
    message = f"生成衍生参考图失败：{clean_error}；错误类型={error_type}；处理建议：{recovery_hint}"
    event = generation_event(
        status="failed",
        provider_status=provider_status,
        message=clean_error,
        error_type=error_type,
        source_operation_tag=row.operation_tag,
        second_review_status="not_started",
        feishu_attachment_status="blocked",
    )
    agent.record_generation_event(state.country, event)
    with APP.state_lock:
        if state.trial_derivative_job_id != job_id:
            return
        state.generation_event = event
        state.trial_row = row.edited(remark=(row.remark + "；" if row.remark else "") + message)
        state.trial_derive_row = state.trial_row
        state.trial_derivative_candidates = []
        state.trial_derivative_candidate_uploads = []
        state.trial_derivative_job_status = "failed"
        state.trial_derivative_job_progress = 100
        state.trial_derivative_job_message = message
        state.sync_message = message
        state.sync_url = ""
        state.view = "trial"


def _finish_derivative_generation_success(job_id: str, updated, rows, previews) -> None:
    state = APP.state
    agent = APP.agent
    with APP.state_lock:
        if state.trial_derivative_job_id != job_id:
            return
        state.trial_row = updated
        state.trial_derive_row = updated
        if not rows:
            state.trial_derivative_candidates = []
            state.trial_derivative_candidate_uploads = list(previews)
            state.trial_derivative_job_status = "failed"
            state.trial_derivative_job_progress = 100
            state.trial_derivative_job_message = updated.remark or "请先上传并解析真实历史好图，再生成衍生参考图。"
            state.sync_message = state.trial_derivative_job_message
            state.sync_url = ""
            state.view = "trial"
            return
        state.trial_derivative_candidates = list(rows)
        state.trial_derivative_candidate_uploads = list(previews)
        state.trial_rows = []
        event = generation_event(
            status="succeeded",
            provider_status=agent.generation_provider_status(),
            message=f"已生成{len(rows)}张衍生参考图，等待二次 VLM 审核结果。",
            error_type="none",
            source_operation_tag=updated.operation_tag,
            task_id=",".join(str(item.get("image_id", "")) for item in previews),
            generated_image_paths=",".join(item.reference_image_path for item in rows if item.reference_image_path),
            second_review_status=_second_review_status(rows),
            feishu_attachment_status=_feishu_attachment_status(rows),
        )
        state.generation_event = event
        state.trial_derivative_job_status = "succeeded"
        state.trial_derivative_job_progress = 100
        state.trial_derivative_job_message = f"已生成{len(rows)}张AI效果图候选，请确认后加入提需表。"
        state.sync_message = f"已生成{len(rows)}张AI效果图候选，请在“衍生方向 + AI效果图候选”区域确认后加入提需表。"
        state.sync_url = ""
        state.view = "trial"
    agent.record_generation_event(state.country, event)


def _demand_row_payload(row) -> dict[str, object]:
    image_value: object = [{"text": row.image_name}]
    if row.reference_image_url:
        image_value = [{"text": row.image_name, "link": row.reference_image_url}]
    payload = {
        "提需分类": row.need_type,
        "国家": row.country,
        "JS分类": row.js_category,
        "图片本身": image_value,
        "运营tag": row.operation_tag,
        "主体内容": row.subject,
        "张数": row.count,
        "需求等级": row.priority,
        "加工方式": row.method,
        "提需日期": APP.agent.today.strftime("%Y%m%d"),
        "交付日期": row.delivery_date,
        "主体描述": row.subject_description,
        "备注": row.remark,
    }
    if row.need_type != "常规" and row.value_match:
        payload["价值观匹配度"] = row.value_match
    if row.reference_image_path:
        payload["_reference_image_path"] = row.reference_image_path
        payload["_reference_image_content_type"] = row.reference_image_content_type or "image/png"
        payload["_reference_image_syncable"] = row.reference_image_syncable
    return payload


def _saved_need_rows_from_form(agent: PuzzleOpsAgent, state: AppState, form: dict[str, list[str]]):
    saved = []
    for index, row in enumerate(state.need_rows):
        saved.append(
            agent.edit_demand_row(
                row,
                priority=value(form, f"priority_{index}", row.priority),
                count=int(value(form, f"count_{index}", str(row.count))),
                method=value(form, f"method_{index}", row.method),
                operation_tag=value(form, f"operation_tag_{index}", row.operation_tag),
                delivery_date=value(form, f"delivery_date_{index}", row.delivery_date),
                subject_description=value(form, f"subject_description_{index}", row.subject_description),
                remark=value(form, f"remark_{index}", row.remark),
            )
        )
    return saved


def _generate_v3_subject_description(agent: PuzzleOpsAgent, row):
    template_row = agent.generate_subject_description(row)
    prompt_result = agent.generate_subject_description_prompt_baseline(row, template_row=template_row)
    return _row_with_v3_prompt_result(template_row, prompt_result)


def _row_with_v3_prompt_result(template_row, prompt_result: dict[str, object]):
    subject_description = str(prompt_result.get("subject_description", "") or "").strip()
    if str(prompt_result.get("status", "")) != "ok" or not subject_description:
        return template_row
    remark = str(prompt_result.get("remark", "") or "").strip()
    changes = {"subject_description": subject_description}
    if remark:
        changes["remark"] = remark
    return template_row.edited(**changes)


def _description_benchmark_item(template_row, prompt_result: dict[str, object]) -> dict[str, str]:
    return {
        "image_name": template_row.image_name,
        "operation_tag": template_row.operation_tag,
        "template_subject_description": template_row.subject_description,
        "template_remark": template_row.remark,
        "prompt_subject_description": str(prompt_result.get("subject_description", "")),
        "prompt_remark": str(prompt_result.get("remark", "")),
        "prompt_status": str(prompt_result.get("status", "")),
        "prompt_model": str(prompt_result.get("model", "")),
        "prompt": str(prompt_result.get("prompt", "")),
        "raw_output": str(prompt_result.get("raw_output", "")),
    }


def _selected_row_indexes(form: dict[str, list[str]], row_count: int) -> set[int]:
    raw = form.get("selected_rows", ())
    selected = {int(item) for item in raw if str(item).isdigit() and 0 <= int(item) < row_count}
    return selected or set(range(row_count))


def _benchmark_scores_from_form(form: dict[str, list[str]], prefix: str) -> dict[str, int]:
    keys = (
        "subject_accuracy",
        "production_actionability",
        "conciseness",
        "market_fit",
        "remark_usefulness",
    )
    scores: dict[str, int] = {}
    for index, key in enumerate(keys):
        try:
            score = int(value(form, f"{prefix}_benchmark_score_{index}", "0"))
        except ValueError:
            score = 0
        scores[key] = max(0, min(5, score))
    return scores


def _benchmark_scores_from_index(form: dict[str, list[str]], prefix: str, benchmark_index: int) -> dict[str, int]:
    keys = (
        "subject_accuracy",
        "production_actionability",
        "conciseness",
        "market_fit",
        "remark_usefulness",
    )
    scores: dict[str, int] = {}
    for score_index, key in enumerate(keys):
        try:
            score = int(value(form, f"{prefix}_benchmark_score_{benchmark_index}_{score_index}", "0"))
        except ValueError:
            score = 0
        scores[key] = max(0, min(5, score))
    return scores


def _save_description_benchmark_scores(agent: PuzzleOpsAgent, state: AppState, form: dict[str, list[str]]) -> int:
    count_text = value(form, "benchmark_count", "")
    if count_text.isdigit():
        indexes = range(max(0, int(count_text)))
        saved = 0
        for index in indexes:
            operation_tag = value(form, f"operation_tag_{index}", "")
            image_name = value(form, f"image_name_{index}", "")
            if not operation_tag and not image_name:
                continue
            agent.repository.add_description_benchmark_score(
                {
                    "country": state.country,
                    "actor": state.user_id,
                    "image_name": image_name,
                    "operation_tag": operation_tag,
                    "template_scores": _benchmark_scores_from_index(form, "template", index),
                    "prompt_scores": _benchmark_scores_from_index(form, "prompt", index),
                    "template_label": value(form, f"template_benchmark_label_{index}", ""),
                    "prompt_label": value(form, f"prompt_benchmark_label_{index}", ""),
                    "template_output": value(form, f"template_output_{index}", ""),
                    "prompt_output": value(form, f"prompt_output_{index}", ""),
                    "metadata": {
                        "prompt_model": value(form, f"prompt_model_{index}", ""),
                        "prompt_status": value(form, f"prompt_status_{index}", ""),
                        "prompt_version": "v3",
                    },
                }
            )
            saved += 1
        return saved

    agent.repository.add_description_benchmark_score(
        {
            "country": state.country,
            "actor": state.user_id,
            "image_name": value(form, "image_name", ""),
            "operation_tag": value(form, "operation_tag", ""),
            "template_scores": _benchmark_scores_from_form(form, "template"),
            "prompt_scores": _benchmark_scores_from_form(form, "prompt"),
            "template_label": value(form, "template_benchmark_label", ""),
            "prompt_label": value(form, "prompt_benchmark_label", ""),
            "template_output": value(form, "template_output", ""),
            "prompt_output": value(form, "prompt_output", ""),
            "metadata": {
                "prompt_model": value(form, "prompt_model", ""),
                "prompt_status": value(form, "prompt_status", ""),
                "prompt_version": "v3",
            },
        }
    )
    return 1


def _value_prediction_benchmark_item(agent: PuzzleOpsAgent, country: str, candidate: dict[str, object]) -> dict[str, str]:
    output = _value_prediction_output_text(agent, country, candidate)
    return {
        "candidate_id": str(candidate.get("candidate_id", "")),
        "operation_tag": str(candidate.get("operation_tag", "")),
        "baseline_output": output,
        "candidate_output": output,
    }


def _value_candidate_has_prediction(candidate: dict[str, object]) -> bool:
    if str(candidate.get("prediction_status", "")) == "predicted":
        return True
    grade = str(candidate.get("predicted_grade", "")).strip()
    if grade and grade != "待预测":
        return True
    return bool(str(candidate.get("visual_subject", "")).strip() and str(candidate.get("evidence", "")).strip())


def _value_candidate_needs_prediction_refresh(candidate: dict[str, object]) -> bool:
    if not _value_candidate_has_prediction(candidate):
        return False
    if candidate.get("rag_filter_version") != "v0.7.32":
        return True
    if candidate.get("metric_calibration_version") != "v0.7.33":
        return True
    if candidate.get("value_grade_model_version") != "v0.7.39-legacy":
        return True
    evidence = str(candidate.get("evidence", ""))
    return "旧缓存RAG依据未通过强相关过滤" in evidence


def _value_prediction_output_text(agent: PuzzleOpsAgent, country: str, candidate: dict[str, object]) -> str:
    citations = candidate.get("rag_citations", ())
    if not isinstance(citations, (tuple, list)):
        citations = ()
    citation_details = candidate.get("rag_citation_details", ())
    if not citation_details:
        citation_details = agent.rag_citation_details(country, tuple(str(item) for item in citations))
    similar_positive = candidate.get("similar_positive", ())
    similar_negative = candidate.get("similar_negative", ())
    return (
        f"视觉主体={candidate.get('visual_subject', candidate.get('subject', ''))}；"
        f"场景={candidate.get('visual_scene', '')}；风格={candidate.get('visual_style', '')}；"
        f"预测等级={candidate.get('predicted_grade', '待预测')}；SA概率={float(candidate.get('sa_probability', 0) or 0):.0%}；"
        f"开图率={candidate.get('open_rate_range', '待预测')}；完成率={candidate.get('completion_rate_range', '待预测')}；"
        f"完成时长={candidate.get('finish_time_range', '待预测')}；建议={candidate.get('action', '待预测')}；"
        f"风险={','.join(str(item) for item in candidate.get('risk_points', ()) or ()) or '未发现明确风险'}；"
        f"RAG依据={_value_citation_labels(citation_details, citations)}；"
        f"相似好图={_value_similar_tags(similar_positive)}；相似风险图={_value_similar_tags(similar_negative)}；"
        f"证据={candidate.get('evidence', '')}"
    )


def _value_citation_labels(details: object, citations: tuple[object, ...] | list[object]) -> str:
    labels = []
    if isinstance(details, (tuple, list)):
        for item in details[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or item.get("parent_id", "") or item.get("chunk_id", "")).strip()
            source_type = str(item.get("source_type", "")).strip()
            text = _compact_server_text(str(item.get("text", "")), 42)
            label = f"{_value_citation_source_label(source_type)}：{title}"
            if text:
                label += f"（{text}）"
            labels.append(label)
    if labels:
        return "；".join(labels)
    return ",".join(str(item) for item in citations[:3]) or "待预测后生成"


def _value_citation_source_label(source_type: str) -> str:
    labels = {
        "human_gold": "历史样本",
        "harness_gold_sample": "历史样本",
        "memory_fact": "Memory",
        "memory_long_term": "Memory",
        "value_master": "价值观规则",
        "value_rule": "价值观规则",
        "audit_rule": "审核规则",
        "audit_policy": "审核规则",
        "sop": "SOP",
        "unknown": "RAG依据",
    }
    return labels.get(source_type, source_type or "RAG依据")


def _compact_server_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 1, 0)] + "…"


def _value_similar_tags(items: object) -> str:
    if not isinstance(items, (tuple, list)):
        return "待预测后生成"
    tags = [str(item.get("operation_tag", "")) for item in items[:2] if isinstance(item, dict)]
    return "、".join(tag for tag in tags if tag) or "暂无"


def _value_benchmark_scores_from_index(form: dict[str, list[str]], prefix: str, benchmark_index: int) -> dict[str, int]:
    keys = (
        "visual_accuracy",
        "country_value_fit",
        "history_evidence_fit",
        "rag_citation_usefulness",
        "risk_detection",
        "grade_credibility",
        "metric_range_credibility",
        "actionability",
    )
    scores: dict[str, int] = {}
    for score_index, key in enumerate(keys):
        try:
            score = int(value(form, f"{prefix}_value_score_{benchmark_index}_{score_index}", "0"))
        except ValueError:
            score = 0
        scores[key] = max(0, min(5, score))
    return scores


def _save_value_prediction_benchmark_scores(agent: PuzzleOpsAgent, state: AppState, form: dict[str, list[str]]) -> int:
    count_text = value(form, "benchmark_count", "0")
    count = int(count_text) if count_text.isdigit() else 0
    saved = 0
    for index in range(count):
        candidate_id = value(form, f"candidate_id_{index}", "")
        operation_tag = value(form, f"operation_tag_{index}", "")
        if not candidate_id and not operation_tag:
            continue
        model_scores = _value_benchmark_scores_from_index(form, "baseline", index)
        model_label = value(form, f"baseline_label_{index}", "")
        model_output = value(form, f"baseline_output_{index}", "")
        candidate_scores = _value_benchmark_scores_from_index(form, "candidate", index)
        if not any(candidate_scores.values()):
            candidate_scores = dict(model_scores)
        agent.repository.add_value_prediction_benchmark_score(
            {
                "country": state.country,
                "actor": state.user_id,
                "candidate_id": candidate_id,
                "operation_tag": operation_tag,
                "baseline_scores": model_scores,
                "candidate_scores": candidate_scores,
                "baseline_label": model_label,
                "candidate_label": value(form, f"candidate_label_{index}", model_label),
                "baseline_output": model_output,
                "candidate_output": value(form, f"candidate_output_{index}", model_output),
                "metadata": {"candidate_version": "value_model_current", "benchmark_mode": "single_model"},
            }
        )
        saved += 1
    return saved


def _selected_derivative_candidate_indexes(form: dict[str, list[str]], row_count: int) -> set[int]:
    raw = form.get("selected_derivative_candidates", ())
    return {int(item) for item in raw if str(item).isdigit() and 0 <= int(item) < row_count}


def _clear_rows_after_guarded_execution(state: AppState, agent: PuzzleOpsAgent, proposal) -> None:
    source = str(getattr(proposal, "source_trace_id", ""))
    if state.view == "trial" or "trial" in source:
        state.trial_row = agent.create_trial_demand(state.country, state.category, state.trial_mode)
        state.trial_rows = []
        state.trial_uploads = []
        return
    if state.view == "regular" or "regular" in source:
        state.need_rows.clear()


def parse_trial_uploads_with_foreground_wait(
    country: str,
    category: str,
    mode: str,
    uploads: tuple[dict[str, object], ...],
) -> tuple[object, tuple[dict[str, str], ...], bool, bool]:
    if not uploads:
        parsed, previews = APP.agent.parse_saved_trial_uploads(country, category, mode, uploads, run_vision=False)
        return parsed, previews, False, False
    if not APP.agent.trial_uploads.vision_client:
        parsed, previews = APP.agent.parse_saved_trial_uploads(country, category, mode, uploads, run_vision=False)
        return parsed, previews, False, False

    done = threading.Event()
    result: dict[str, object] = {}

    def worker() -> None:
        parsed, previews = APP.agent.parse_saved_trial_uploads(country, category, mode, uploads, run_vision=True)
        result["parsed"] = parsed
        result["previews"] = previews
        done.set()

    threading.Thread(target=worker, name=f"trial-vision-foreground-{country}-{mode}", daemon=True).start()
    if done.wait(_foreground_vision_wait_seconds(mode)):
        parsed = result["parsed"]
        previews = result["previews"]
        if "视觉LLM：调用失败" in getattr(parsed, "remark", ""):
            return parsed, previews, False, False  # type: ignore[return-value]
        return parsed, previews, True, False  # type: ignore[return-value]

    parsed, previews = APP.agent.parse_saved_trial_uploads(country, category, mode, uploads, run_vision=False)
    upload_paths = tuple(str(item.get("path", "")) for item in previews)

    def completion_watcher() -> None:
        done.wait()
        parsed_result = result.get("parsed")
        previews_result = result.get("previews")
        if parsed_result is None or previews_result is None:
            return
        apply_trial_vision_background_result(country, mode, upload_paths, parsed_result, previews_result)  # type: ignore[arg-type]

    threading.Thread(target=completion_watcher, name=f"trial-vision-background-{country}-{mode}", daemon=True).start()
    return parsed, previews, False, True


def start_trial_vision_background_parse(country: str, category: str, mode: str, uploads: tuple[dict[str, str], ...]) -> None:
    if not uploads or not APP.agent.trial_uploads.vision_client:
        return
    upload_paths = tuple(str(item.get("path", "")) for item in uploads)

    def worker() -> None:
        try:
            parsed, previews = APP.agent.parse_saved_trial_uploads(country, category, mode, uploads, run_vision=True)
            apply_trial_vision_background_result(country, mode, upload_paths, parsed, previews)
        except Exception as exc:
            if _trial_upload_paths_still_current(APP.state, mode, upload_paths):
                APP.state.sync_message = f"Qwen视觉后台解析失败：{user_facing_error(str(exc))}；已保留本地解析结果。"
                APP.state.sync_url = ""

    threading.Thread(target=worker, name=f"trial-vision-{country}-{mode}", daemon=True).start()


def apply_trial_vision_background_result(country: str, mode: str, upload_paths: tuple[str, ...], parsed, previews) -> None:
    if not _trial_upload_paths_still_current(APP.state, mode, upload_paths):
        return
    APP.state.trial_row = parsed
    APP.state.trial_uploads = list(previews)
    if mode == "derive":
        APP.state.trial_derive_row = parsed
        APP.state.trial_derive_uploads = list(previews)
        if "视觉LLM：调用失败" in parsed.remark:
            APP.state.sync_message = f"Qwen视觉后台解析失败；已保留本地解析结果。{_vision_failure_suffix(parsed.remark)}"
        else:
            APP.state.sync_message = "Qwen视觉后台解析完成，衍生方向已补充语义主体、场景和风险判断。"
    else:
        APP.state.trial_parse_row = parsed
        APP.state.trial_parse_rows = [parsed]
        APP.state.trial_parse_uploads = list(previews)
        APP.state.trial_rows = [parsed]
        if "视觉LLM：调用失败" in parsed.remark:
            APP.state.sync_message = f"Qwen视觉后台解析失败；已保留本地解析结果。{_vision_failure_suffix(parsed.remark)}"
        else:
            APP.state.sync_message = "Qwen视觉后台解析完成，试新提需表已补充语义主体、场景和风险判断。"
    APP.state.sync_url = ""
    APP.state.view = "trial"


def _foreground_vision_wait_seconds(mode: str) -> float:
    key = "QWEN_FOREGROUND_WAIT_DERIVE_SECONDS" if mode == "derive" else "QWEN_FOREGROUND_WAIT_PARSE_SECONDS"
    try:
        return min(max(float(os.getenv(key, os.getenv("QWEN_FOREGROUND_WAIT_SECONDS", "30"))), 0.0), 30.0)
    except ValueError:
        return 30.0


def _trial_parse_sync_message(mode: str, previews: tuple[dict[str, str], ...], used_vision: bool, background_pending: bool) -> str:
    if used_vision:
        if mode == "derive":
            return f"Qwen视觉已完成，已解析{len(previews)}张历史好图并生成衍生方向。"
        return f"Qwen视觉已完成，已解析{len(previews)}张参考图并写入下方试新提需表。"
    if background_pending:
        if mode == "derive":
            return "已先用本地解析生成衍生方向，可继续操作；Qwen视觉仍在后台补充，最多等待60秒。"
        return "已先用本地解析写入下方试新提需表；Qwen视觉仍在后台补充，最多等待60秒。"
    if mode == "derive":
        return "已用本地解析生成衍生方向，可继续点击“生成衍生参考图”。"
    return "已用本地解析写入下方试新提需表。"


def _trial_upload_paths_still_current(state: AppState, mode: str, upload_paths: tuple[str, ...]) -> bool:
    current = state.trial_derive_uploads if mode == "derive" else state.trial_parse_uploads
    return tuple(str(item.get("path", "")) for item in current) == upload_paths


def _vision_failure_suffix(remark: str) -> str:
    marker = "视觉LLM：调用失败"
    if marker not in remark:
        return ""
    return "；" + remark.split(marker, 1)[1].strip("；。")


def _rag_preflight_summary(preflight: dict[str, object]) -> str:
    parts = []
    for key in ("embedding", "qdrant", "rerank"):
        status = preflight.get(key, {}) if isinstance(preflight.get(key), dict) else {}
        parts.append(f"{key}:{status.get('ready', False)}")
    return ",".join(parts)


def _vector_store_label(provider: str) -> str:
    labels = {"milvus": "Milvus", "qdrant": "Qdrant", "sqlite": "SQLite"}
    return labels.get((provider or "").strip().lower(), provider or "VectorStore")


def _split_parent_ids(value: str) -> tuple[str, ...]:
    normalized = value.replace("、", ",").replace("；", ",").replace(";", ",")
    return tuple(part.strip() for part in normalized.split(",") if part.strip())


def run(host: str = "127.0.0.1", port: int = 5188) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PuzzleOps Agent Python app running at http://{host}:{port}")
    server.serve_forever()
