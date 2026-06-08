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
        self.agent = PuzzleOpsAgent()
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
                    remark=value(form, f"remark_{index}", row.remark),
                )
            )
        state.need_rows = saved
        state.view = "regular"
    elif path == "/sync_needs_feishu":
        rows = [_demand_row_payload(row) for row in state.need_rows]
        count = len(rows)
        result = agent.sync_demand_rows(state.country, rows, require_real=True)
        if result.success:
            state.need_rows.clear()
            state.sync_message = f"同步成功，当前已完成提需{count}条"
            return agent.feishu.web_url()
        else:
            state.sync_message = f"同步失败：{result.error}"
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
                    remark=value(form, f"remark{suffix}", value(form, "remark", row.remark)),
                )
            )
        state.trial_rows = saved if state.trial_rows else []
        state.trial_row = saved[-1]
        state.view = "trial"
    elif path == "/sync_trial_feishu":
        rows_to_sync = state.trial_rows or [state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)]
        result = agent.sync_demand_rows(state.country, [_demand_row_payload(row) for row in rows_to_sync], require_real=True)
        if result.success:
            state.trial_row = agent.create_trial_demand(state.country, state.category, state.trial_mode)
            state.trial_rows = []
            state.trial_uploads = []
            state.sync_message = f"同步成功，当前已完成试新提需{len(rows_to_sync)}条"
            return agent.feishu.web_url()
        else:
            state.sync_message = f"同步失败：{result.error}"
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
    return None


def redirect_location(state: AppState) -> str:
    return "/?" + urlencode({"country": state.country, "view": state.view})


def value(form: dict[str, list[str]], key: str, default: str) -> str:
    return form.get(key, [default])[0]


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
    return {
        "提需分类": row.need_type,
        "国家": row.country,
        "JS分类": row.js_category,
        "图片本身": row.image_name,
        "运营tag": row.operation_tag,
        "主体内容": row.subject,
        "张数": row.count,
        "需求等级": row.priority,
        "加工方式": row.method,
        "交付日期": row.delivery_date,
        "主体描述": row.subject_description,
        "备注": row.remark,
    }


def run(host: str = "127.0.0.1", port: int = 5188) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PuzzleOps Agent Python app running at http://{host}:{port}")
    server.serve_forever()
