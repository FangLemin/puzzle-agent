from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        query = parse_qs(urlparse(self.path).query)
        update_state_from_query(APP.state, query)
        self.respond(render_page(APP.agent, APP.state))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        form = parse_qs(payload)
        path = urlparse(self.path).path
        handle_action(path, form)
        self.send_response(303)
        self.send_header("Location", redirect_location(APP.state))
        self.end_headers()

    def respond(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


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
        state.trial_row = None
    if state.trial_mode != old_trial_mode:
        state.trial_row = None


def handle_action(path: str, form: dict[str, list[str]]) -> None:
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
                    delivery_date=value(form, f"delivery_date_{index}", row.delivery_date),
                    remark=value(form, f"remark_{index}", row.remark),
                )
            )
        state.need_rows = saved
        state.view = "regular"
    elif path == "/save_trial":
        row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        state.trial_row = agent.edit_demand_row(
            row,
            priority=value(form, "priority", row.priority),
            count=int(value(form, "count", str(row.count))),
            method=value(form, "method", row.method),
            delivery_date=value(form, "delivery_date", row.delivery_date),
            remark=value(form, "remark", row.remark),
        )
        state.view = "trial"
    elif path == "/apply_value_master":
        row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        state.trial_row = agent.apply_value_master(row)
        state.view = "trial"
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


def redirect_location(state: AppState) -> str:
    return "/?" + urlencode({"country": state.country, "view": state.view})


def value(form: dict[str, list[str]], key: str, default: str) -> str:
    return form.get(key, [default])[0]


def run(host: str = "127.0.0.1", port: int = 5188) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PuzzleOps Agent Python app running at http://{host}:{port}")
    server.serve_forever()
