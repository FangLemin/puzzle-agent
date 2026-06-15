from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.data import COUNTRIES
from puzzle_ops.renderer import AppState, render_page


class PuzzleOpsServer:
    def __init__(self) -> None:
        self.agent = PuzzleOpsAgent(enable_regular_vision=True)
        self.state = AppState()


APP = PuzzleOpsServer()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/uploads/"):
            self.respond_upload(parsed.path.removeprefix("/uploads/"))
            return
        query = parse_qs(parsed.query)
        update_state_from_query(APP.state, query)
        self.respond(render_page(APP.agent, APP.state))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        form, files = parse_post_body(self.headers.get("Content-Type", ""), body)
        path = urlparse(self.path).path
        location = handle_action(path, form, files)
        self.send_response(303)
        self.send_header("Location", location or redirect_location(APP.state))
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


def update_state_from_query(state: AppState, query: dict[str, list[str]]) -> None:
    old_country = state.country
    old_trial_mode = state.trial_mode
    if "country" in query and query["country"][0] in COUNTRIES:
        state.country = query["country"][0]
    for field in ("view", "category", "tag", "trial_mode", "schedule_day", "value_grade"):
        if field in query and query[field][0]:
            setattr(state, field, query[field][0])
    state.show_holiday = query.get("show_holiday", [""])[0] == "1"
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
    if path == "/add_regular":
        image_index = int(value(form, "image_index", "0"))
        state.need_rows.append(agent.add_regular_demand(state.country, state.category, state.tag, image_index))
        state.view = "regular"
    elif path == "/save_dashboard":
        state.workflow_notes = [value(form, f"workflow_{index}", note) for index, note in enumerate(state.workflow_notes)]
        state.task_notes = [value(form, f"task_{index}", note) for index, note in enumerate(state.task_notes)]
        state.view = "dashboard"
    elif path == "/generate_descriptions":
        state.need_rows = [agent.generate_subject_description(row) for row in state.need_rows]
        state.view = "regular"
    elif path == "/save_needs":
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
        state.need_rows = saved
        state.view = "regular"
    elif path == "/sync_needs_feishu":
        rows = [_demand_row_payload(row) for row in state.need_rows]
        count = len(rows)
        if count == 0:
            state.sync_message = "请先加入至少一条常规提需，再同步飞书表格。"
            state.sync_url = ""
            state.view = "regular"
            return None
        result = agent.sync_demand_rows(state.country, rows, require_real=True)
        if result.success:
            state.need_rows.clear()
            state.sync_message = f"同步成功，当前已完成提需{count}条"
            state.sync_url = agent.feishu.web_url()
            state.view = "regular"
        else:
            state.sync_message = f"同步失败：{result.error}"
            state.sync_url = ""
        state.view = "regular"
    elif path == "/save_trial":
        rows = state.trial_rows or [state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)]
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
        state.trial_rows = saved if state.trial_rows else []
        state.trial_row = saved[-1]
        state.view = "trial"
    elif path == "/sync_trial_feishu":
        rows_to_sync = state.trial_rows
        if not rows_to_sync:
            state.sync_message = "请先上传解析图片或模拟上传，生成至少一条试新提需记录。"
            state.sync_url = ""
            state.view = "trial"
            return None
        result = agent.sync_demand_rows(state.country, [_demand_row_payload(row) for row in rows_to_sync], require_real=True)
        if result.success:
            state.trial_row = agent.create_trial_demand(state.country, state.category, state.trial_mode)
            state.trial_rows = []
            state.trial_uploads = []
            state.sync_message = f"同步成功，当前已完成试新提需{len(rows_to_sync)}条"
            state.sync_url = agent.feishu.web_url()
            state.view = "trial"
        else:
            state.sync_message = f"同步失败：{result.error}"
            state.sync_url = ""
        state.view = "trial"
    elif path == "/apply_value_master":
        if state.trial_rows:
            state.trial_rows = [agent.apply_value_master(row) for row in state.trial_rows]
            state.trial_row = state.trial_rows[-1]
        else:
            row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
            state.trial_row = agent.apply_value_master(row)
        state.view = "trial"
    elif path == "/simulate_trial_upload":
        state.trial_row = agent.simulate_trial_upload(state.country, state.category, state.trial_mode)
        state.trial_rows.append(state.trial_row)
        state.trial_uploads = []
        state.view = "trial"
    elif path == "/upload_trial_images":
        row, previews = agent.parse_trial_uploads(state.country, state.category, state.trial_mode, files.get("trial_images", []))
        state.trial_row = row
        if previews:
            state.trial_rows.append(row)
        state.trial_uploads = list(previews)
        state.view = "trial"
    elif path == "/generate_trial_derivatives":
        row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        try:
            updated, rows, previews = agent.generate_trial_derivatives(row)
        except Exception as exc:
            provider_status = agent.generation_provider_status()
            error_type = classify_generation_error(str(exc))
            message = f"生成衍生参考图失败：{exc}；错误类型={error_type}"
            state.generation_event = generation_event(
                status="failed",
                provider_status=provider_status,
                message=str(exc),
                error_type=error_type,
                source_operation_tag=row.operation_tag,
                second_review_status="not_started",
                feishu_attachment_status="blocked",
            )
            agent.record_generation_event(state.country, state.generation_event)
            state.trial_row = row.edited(remark=(row.remark + "；" if row.remark else "") + message)
            state.trial_rows = []
            state.trial_uploads = []
            state.sync_message = message
            state.sync_url = ""
            state.view = "trial"
            return None
        state.trial_row = updated
        state.trial_rows = list(rows)
        state.trial_uploads = list(previews)
        state.generation_event = generation_event(
            status="succeeded",
            provider_status=agent.generation_provider_status(),
            message=f"已生成{len(rows)}张衍生参考图，等待二次 VLM 审核结果。",
            error_type="none",
            source_operation_tag=row.operation_tag,
            task_id=",".join(str(item.get("image_id", "")) for item in previews),
            generated_image_paths=",".join(item.reference_image_path for item in rows if item.reference_image_path),
            second_review_status=_second_review_status(rows),
            feishu_attachment_status=_feishu_attachment_status(rows),
        )
        agent.record_generation_event(state.country, state.generation_event)
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
    elif path == "/replace_schedule":
        slot_index = int(value(form, "slot_index", "0"))
        image_name = value(form, "image_name", "")
        state.schedule_replacements[slot_index] = agent.replacement_for_slot(state.country, image_name)
        state.view = "schedule"
    elif path == "/approve_value_candidate":
        agent.approve_value_candidate(
            value(form, "candidate_id", ""),
            state.country,
            value(form, "human_note", "运营确认加入固定价值观"),
        )
        state.view = "runtime"
    elif path == "/save_harness_override":
        agent.record_harness_override(
            state.country,
            value(form, "sample_id", ""),
            value(form, "task_type", ""),
            value(form, "human_override", ""),
        )
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
    return None


def redirect_location(state: AppState) -> str:
    return "/?" + urlencode({"country": state.country, "view": state.view})


def value(form: dict[str, list[str]], key: str, default: str) -> str:
    return form.get(key, [default])[0]


def format_generation_provider_diagnostic(status: dict[str, object]) -> str:
    provider = str(status.get("provider", "not_configured"))
    configured = str(status.get("configured", False))
    message = str(status.get("message", "生成 provider 未配置"))
    model = str(status.get("model", "未配置"))
    endpoint = str(status.get("base_url") or status.get("submit_url") or "未配置")
    return f"生成 Provider 诊断：provider={provider}；configured={configured}；model={model}；endpoint={endpoint}；{message}"


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
        "provider": str(provider_status.get("provider", "not_configured")),
        "model": str(provider_status.get("model", "未记录")),
        "endpoint": str(provider_status.get("base_url") or provider_status.get("submit_url") or "未记录"),
        "task_id": task_id,
        "source_operation_tag": source_operation_tag,
        "generated_image_paths": generated_image_paths,
        "second_review_status": second_review_status,
        "feishu_attachment_status": feishu_attachment_status,
        "error_type": error_type,
        "message": message,
    }


def _second_review_status(rows) -> str:
    if not rows:
        return "not_started"
    return "passed" if all(row.reference_image_syncable for row in rows) else "blocked"


def _feishu_attachment_status(rows) -> str:
    if not rows:
        return "blocked"
    return "ready" if all(row.reference_image_syncable for row in rows) else "blocked"


def classify_generation_error(message: str) -> str:
    text = message.lower()
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


def _demand_row_payload(row) -> dict[str, object]:
    image_value: object = row.image_name
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
        "交付日期": row.delivery_date,
        "主体描述": row.subject_description,
        "备注": row.remark,
    }
    if row.value_match:
        payload["价值观匹配度"] = row.value_match
    if row.reference_image_path:
        payload["_reference_image_path"] = row.reference_image_path
        payload["_reference_image_content_type"] = row.reference_image_content_type or "image/png"
        payload["_reference_image_syncable"] = row.reference_image_syncable
    return payload


def run(host: str = "127.0.0.1", port: int = 5188) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PuzzleOps Agent Python app running at http://{host}:{port}")
    server.serve_forever()
