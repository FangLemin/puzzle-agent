from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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
        self.send_header("Location", f"/?country={APP.state.country}&view={APP.state.view}")
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
    if state.country != old_country:
        state.need_rows.clear()
        state.trial_row = None
    if state.trial_mode != old_trial_mode:
        state.trial_row = None


def handle_action(path: str, form: dict[str, list[str]]) -> None:
    state = APP.state
    agent = APP.agent
    if path == "/add_regular":
        image_index = int(value(form, "image_index", "0"))
        state.need_rows.append(agent.add_regular_demand(state.country, state.category, state.tag, image_index))
        state.view = "regular"
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


def value(form: dict[str, list[str]], key: str, default: str) -> str:
    return form.get(key, [default])[0]


def run(host: str = "127.0.0.1", port: int = 5188) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PuzzleOps Agent Python app running at http://{host}:{port}")
    server.serve_forever()
