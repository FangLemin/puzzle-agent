from dataclasses import dataclass, field
from html import escape
from urllib.parse import urlencode

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.models import DemandRow


@dataclass
class AppState:
    country: str = "日本"
    view: str = "dashboard"
    category: str = "人物"
    tag: str = "常规_日本_传统浴袍美女0604"
    trial_mode: str = "parse"
    schedule_day: str = "周一"
    value_grade: str = "S"
    need_rows: list[DemandRow] = field(default_factory=list)
    trial_row: DemandRow | None = None


def render_page(agent: PuzzleOpsAgent, state: AppState) -> str:
    normalize_state(agent, state)
    body = {
        "dashboard": render_dashboard,
        "regular": render_regular,
        "trial": render_trial,
        "analysis": render_analysis,
        "value": render_value,
        "schedule": render_schedule,
        "sync": render_sync,
    }[state.view](agent, state)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>拼图运营智能后台 Python版</title>
  <style>{CSS}</style>
</head>
<body>
  <aside>
    <div class="brand"><div class="logo">🧩</div><strong>PuzzleOps Agent</strong><span>纯 Python 后台原型</span></div>
    {render_country_switch(agent, state)}
    {render_nav(state)}
    <p class="note">所有页面由 Python 标准库服务端渲染；业务逻辑在 <code>puzzle_ops/agents.py</code>。</p>
  </aside>
  <main>
    <header>
      <div><p>{escape(state.country)}市场</p><h1>{page_title(state.view)}</h1></div>
      <div class="header-actions"><a class="button" href="{href(state, view='sync')}">同步记录</a></div>
    </header>
    {body}
  </main>
</body>
</html>"""


def normalize_state(agent: PuzzleOpsAgent, state: AppState) -> None:
    categories = agent.categories(state.country)
    if state.category not in categories:
        state.category = next(iter(categories))
    tags = agent.sorted_tags(state.country, state.category)
    if state.tag not in {tag.tag for tag in tags}:
        state.tag = tags[0].tag
    if state.trial_row is None or state.trial_row.country != state.country:
        state.trial_row = agent.create_trial_demand(state.country, state.category, state.trial_mode)


def render_country_switch(agent: PuzzleOpsAgent, state: AppState) -> str:
    buttons = []
    for country in agent.countries():
        data = agent.dashboard(country)
        active = " active" if country == state.country else ""
        buttons.append(f'<a class="pill{active}" href="{href(state, country=country, view="dashboard")}">{escape(data["country_label"])}</a>')
    return '<section class="switcher"><h2>当前国家</h2><div class="pills">' + "".join(buttons) + "</div></section>"


def render_nav(state: AppState) -> str:
    items = (
        ("dashboard", "🏠", "首页工作台"),
        ("regular", "📦", "常规提需"),
        ("trial", "✨", "试新提需"),
        ("analysis", "📈", "数据分析大师"),
        ("value", "🔮", "价值观大师"),
        ("schedule", "🗓️", "排图工作台"),
        ("sync", "🔁", "同步记录"),
    )
    links = [f'<a class="nav {"active" if key == state.view else ""}" href="{href(state, view=key)}">{icon} {label}</a>' for key, icon, label in items]
    return "<nav>" + "".join(links) + "</nav>"


def render_dashboard(agent: PuzzleOpsAgent, state: AppState) -> str:
    dashboard = agent.dashboard(state.country)
    holiday = agent.holiday_recommendation(state.country)
    tasks = "".join(f'<article><strong>{escape(task["title"])}</strong><p>{escape(task["body"])}</p></article>' for task in dashboard["tasks"])
    images = "".join(render_image_card(image) for image in holiday.history_good_images)
    return f"""
<section class="metrics">
  <article><span>当前国家</span><strong>{escape(dashboard["country_label"])}</strong><small>{escape(dashboard["owner"])}</small></article>
  <article><span>本季度累计 SA 占比 / OKR</span><strong>{escape(dashboard["sa"])}</strong></article>
  <article><span>本季度累计 AI 占比 / OKR</span><strong>{escape(dashboard["ai"])}</strong></article>
</section>
<section class="grid two">
  <div class="panel"><h2>本周工作流</h2>{render_workflow()}</div>
  <div class="panel"><h2>今日待办 <span>🧸💦</span></h2><div class="tasks">{tasks}</div></div>
</section>
<section class="panel">
  <h2>节日提需建议：{escape(holiday.name)}</h2>
  <dl class="detail">
    <div><dt>日期范围</dt><dd>{escape(holiday.date_range)}</dd></div>
    <div><dt>节日含义</dt><dd>{escape(holiday.meaning)}</dd></div>
    <div><dt>主要内容</dt><dd>{escape(holiday.content)}</dd></div>
    <div><dt>AI推荐主题</dt><dd>{escape("；".join(holiday.ai_themes))}</dd></div>
    <div><dt>推荐元素</dt><dd>{escape("；".join(holiday.elements))}</dd></div>
  </dl>
  <div class="cards">{images}</div>
</section>
"""


def render_workflow() -> str:
    items = (
        ("周一", "排两个国家的图，优先处理低库存爆款 tag。"),
        ("周二", "补充排图、检查 5/10 分发位、确认上新节日素材。"),
        ("周三", "数据分析大师回收上周期数据，输出 SA/CD/AI 趋势和明细备注。"),
        ("周四", "过图会，结合复盘结论修改排图与提需优先级。"),
        ("周一到周五", "常规提需、试新提需持续进行，审核规则自动写入备注。"),
    )
    return "<ol class='timeline'>" + "".join(f"<li><strong>{day}</strong><span>{text}</span></li>" for day, text in items) + "</ol>"


def render_regular(agent: PuzzleOpsAgent, state: AppState) -> str:
    categories = "".join(f'<a class="choice {"active" if name == state.category else ""}" href="{href(state, view="regular", category=name)}">{escape(name)}</a>' for name in agent.categories(state.country))
    tags = "".join(render_tag_choice(state, tag) for tag in agent.sorted_tags(state.country, state.category))
    images = "".join(render_reference_image(state, image, index) for index, image in enumerate(agent.images_for_tag(state.country, state.tag)))
    rows = render_need_rows(state.need_rows)
    return f"""
<section class="grid three">
  <div class="panel"><h2>分类</h2>{categories}</div>
  <div class="panel"><h2>运营 tag + 库存</h2><p class="alert">低库存爆款会置顶提醒运营提需。</p>{tags}</div>
  <div class="panel"><h2>已分发图片参考</h2><div class="cards">{images}</div></div>
</section>
<section class="panel">
  <div class="section-line"><h2>批量提需清单</h2>
    <form method="post" action="/generate_descriptions"><button>AI生成描述</button></form>
  </div>
  <form method="post" action="/save_needs">{rows}<button class="primary">保存表格修改</button></form>
</section>
"""


def render_tag_choice(state: AppState, tag) -> str:
    hot = " hot" if tag.hot and tag.stock <= 5 else ""
    active = " active" if tag.tag == state.tag else ""
    return f'<a class="choice{active}{hot}" href="{href(state, view="regular", tag=tag.tag)}"><strong>{escape(tag.tag)}</strong><span>库存 {tag.stock}</span></a>'


def render_reference_image(state: AppState, image, index: int) -> str:
    return f"""
<article class="image-card">
  <div class="thumb">{escape(image.title)}</div>
  <strong><span class="grade {image.grade}">{escape(image.grade)}</span> {escape(image.title)}</strong>
  <small>开图 {escape(image.open_rate)} · 完成 {escape(image.finish_rate)} · {escape(image.finish_time)}</small>
  <form method="post" action="/add_regular"><input type="hidden" name="image_index" value="{index}"><button>＋加入提需</button></form>
</article>
"""


def render_need_rows(rows: list[DemandRow]) -> str:
    if not rows:
        return "<p class='empty'>暂未加入提需。点击图片卡片的加号后，在这里批量提需。</p>"
    body = "".join(render_need_row(row, index, include_value=False) for index, row in enumerate(rows))
    return f"<div class='table-wrap'><table>{need_header(include_check=False, include_value=False)}<tbody>{body}</tbody></table></div>"


def render_trial(agent: PuzzleOpsAgent, state: AppState) -> str:
    mode_links = "".join(f'<a class="pill {"active" if state.trial_mode == mode else ""}" href="{href(state, view="trial", trial_mode=mode)}">{label}</a>' for mode, label in (("parse", "参考图解析提需"), ("derive", "好图衍生提需")))
    row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
    row_html = render_need_row(row, 0, include_value=True, prefix="")
    return f"""
<section class="panel"><h2>试新模式</h2><div class="pills">{mode_links}</div><p>好图衍生模式：上传单张好图，自动生成 2 张类似参考图，再基于三张图共性 AI 解析提需。</p></section>
<section class="panel">
  <div class="section-line"><h2>试新提需表预览</h2><form method="post" action="/apply_value_master"><button>价值观大师</button></form></div>
  <form method="post" action="/save_trial"><div class="table-wrap"><table>{need_header(include_check=True, include_value=True)}<tbody>{row_html}</tbody></table></div><button class="primary">保存试新修改</button></form>
</section>
"""


def need_header(include_check: bool, include_value: bool) -> str:
    heads = ["提需分类", "国家", "JS分类", "图片本身", "运营tag", "主体内容", "张数", "需求等级", "加工方式", "交付日期", "主体描述", "备注"]
    if include_check:
        heads.insert(0, "选择")
    if include_value:
        heads.append("价值观匹配度")
    return "<thead><tr>" + "".join(f"<th>{head}</th>" for head in heads) + "</tr></thead>"


def render_need_row(row: DemandRow, index: int, include_value: bool, prefix: str | None = None) -> str:
    prefix = f"_{index}" if prefix is None else prefix
    cells = [
        escape(row.need_type),
        escape(row.country),
        escape(row.js_category),
        escape(row.image_name),
        escape(row.operation_tag),
        escape(row.subject),
        f'<input name="count{prefix}" value="{row.count}" size="3">',
        select(f"priority{prefix}", ("P0", "P1", "P2"), row.priority),
        select(f"method{prefix}", ("纯AI", "限素材网", "先照片后AI"), row.method),
        f'<input name="delivery_date{prefix}" value="{escape(row.delivery_date)}" placeholder="">',
        escape(row.subject_description),
        f'<textarea name="remark{prefix}">{escape(row.remark)}</textarea>',
    ]
    if include_value:
        cells.append(escape(row.value_match))
        return "<tr><td><input type='checkbox' checked></td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def render_analysis(agent: PuzzleOpsAgent, state: AppState) -> str:
    report = agent.analysis_report(state.country)
    rows = "".join(
        f"<tr><td>{escape(row.image_name)}</td><td>{escape(row.source)}</td><td>{grade(row.grade)}</td><td>{escape(row.open_rate)}</td><td>{escape(row.finish_rate)}</td><td>{escape(row.finish_time)}</td><td>{position(row.position)}</td><td><textarea>{escape(row.remark)}</textarea></td></tr>"
        for row in report.rows
    )
    return f"""
<section class="metrics">
  <article><span>SA 占比 {escape(report.sa_delta)}</span><strong>{escape(report.sa_ratio)}</strong><small>历史均值 {escape(report.sa_history_avg)} · OKR {escape(report.sa_okr)}</small></article>
  <article><span>CD 占比 {escape(report.cd_delta)}</span><strong>{escape(report.cd_ratio)}</strong></article>
  <article><span>AI率 {escape(report.ai_delta)}</span><strong>{escape(report.ai_ratio)}</strong></article>
</section>
<section class="panel"><h2>周期内容分析</h2><textarea class="wide">{escape(report.cycle_summary)}</textarea><h2>下一步 todo 和建议</h2><textarea class="wide">{escape(report.next_todo)}</textarea></section>
<section class="panel"><h2>图片明细与 AI 分析备注</h2><div class="table-wrap"><table><thead><tr><th>图片</th><th>来源</th><th>等级</th><th>开图率</th><th>完成率</th><th>时长</th><th>分发位置</th><th>备注</th></tr></thead><tbody>{rows}</tbody></table></div></section>
"""


def render_value(agent: PuzzleOpsAgent, state: AppState) -> str:
    tabs = "".join(f'<a class="pill {"active" if grade == state.value_grade else ""}" href="{href(state, view="value", value_grade=grade)}">{grade}</a>' for grade in ("S", "A", "B", "C", "D"))
    cards = "".join(f"<article class='image-card'><div class='thumb'>{escape(card.image.title)}</div><strong>{escape(card.operation_tag)}</strong><p>{grade(card.image.grade)} 预测等级</p><small>开图 {escape(card.image.open_rate)} · 完成 {escape(card.image.finish_rate)} · {escape(card.image.finish_time)}</small><p>{escape(card.prediction_remark)}</p></article>" for card in agent.value_predictions(state.country, state.value_grade))
    rules = "".join(f"<li><strong>{escape(title)}</strong>：{escape(body)}</li>" for title, body in agent.value_rules(state.country))
    return f"<section class='panel'><h2>SABCD 预测</h2><div class='pills'>{tabs}</div><div class='cards'>{cards or '<p class=\"empty\">当前等级暂无样例。</p>'}</div></section><section class='panel'><details><summary>查看完整价值观规则库</summary><ul>{rules}</ul></details></section>"


def render_schedule(agent: PuzzleOpsAgent, state: AppState) -> str:
    days = "".join(f'<a class="pill {"active" if day == state.schedule_day else ""}" href="{href(state, view="schedule", schedule_day=day)}">{day}</a>' for day in ("周一", "周二", "周三", "周四", "周五", "周六", "周日"))
    rule = "周末允许 1-9、12-18 位" if state.schedule_day in {"周六", "周日"} else "工作日允许 1-9、12-15 位"
    slots = "".join(f"<article class='slot'><strong>排图位 {position(item.position)}</strong><span>{escape(item.image_name)}</span><small>{escape(item.operation_tag)}</small><p>{grade(item.grade)} 开图 {escape(item.open_rate)} · 完成 {escape(item.finish_rate)} · {escape(item.finish_time)}</p><button>－替换</button></article>" for item in agent.schedule(state.country, state.schedule_day))
    return f"<section class='panel'><h2>排图工作台</h2><div class='pills'>{days}</div><p>{rule}，一天 10 张，共 70 张推荐排图。</p><div class='schedule'>{slots}</div></section>"


def render_sync(agent: PuzzleOpsAgent, state: AppState) -> str:
    rows = "".join(f"<tr><td>{escape(time)}</td><td>{escape(country)}</td><td>{escape(action)}</td><td>{escape(target)}</td><td>{escape(status)}</td></tr>" for time, country, action, target, status in agent.sync_rows())
    return f"<section class='panel'><h2>同步记录</h2><table><thead><tr><th>时间</th><th>国家</th><th>动作</th><th>目标</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table></section>"


def render_image_card(image) -> str:
    return f"<article class='image-card'><div class='thumb'>{escape(image.title)}</div><strong>{grade(image.grade)} {escape(image.title)}</strong><small>开图 {escape(image.open_rate)} · 完成 {escape(image.finish_rate)} · {escape(image.finish_time)}</small></article>"


def select(name: str, options: tuple[str, ...], value: str) -> str:
    return f'<select name="{name}">' + "".join(f'<option value="{escape(option)}" {"selected" if option == value else ""}>{escape(option)}</option>' for option in options) + "</select>"


def grade(value: str) -> str:
    return f'<span class="grade {escape(value)}">{escape(value)}</span>'


def position(value: int) -> str:
    return f'<span class="pos">{value}</span>' if value in {5, 10} else str(value)


def page_title(view: str) -> str:
    return {
        "dashboard": "首页工作台",
        "regular": "常规提需",
        "trial": "试新提需",
        "analysis": "数据分析大师",
        "value": "价值观大师",
        "schedule": "排图工作台",
        "sync": "同步记录",
    }[view]


def href(state: AppState, **changes: str) -> str:
    params = {
        "country": state.country,
        "view": state.view,
        "category": state.category,
        "tag": state.tag,
        "trial_mode": state.trial_mode,
        "schedule_day": state.schedule_day,
        "value_grade": state.value_grade,
    }
    params.update({key: value for key, value in changes.items() if value is not None})
    return "/?" + urlencode(params)


CSS = """
:root { --ink:#21313a; --muted:#65747e; --brand:#2f8f74; --soft:#f2f7f5; --line:#dbe5e3; --red:#d84a3a; --orange:#d78b24; }
* { box-sizing: border-box; }
body { margin:0; display:grid; grid-template-columns:280px 1fr; min-height:100vh; color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#f7f2e8; }
aside { padding:22px; background:#fffaf0; border-right:1px solid var(--line); }
main { padding:22px; }
header { display:flex; align-items:center; justify-content:space-between; margin-bottom:18px; }
header p { margin:0 0 4px; color:var(--muted); font-weight:800; }
h1, h2 { margin:0 0 12px; }
a { color:inherit; text-decoration:none; }
.brand { display:grid; gap:6px; margin-bottom:18px; }
.logo { width:52px; height:52px; display:grid; place-items:center; border-radius:14px; background:#e7f4ee; font-size:30px; }
.note { color:var(--muted); font-size:13px; line-height:1.6; }
.pills { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
.pill, .button, button { border:1px solid var(--line); background:#fff; border-radius:8px; padding:9px 12px; cursor:pointer; font-weight:800; }
.pill.active, .nav.active, button.primary { background:#dff1ea; border-color:var(--brand); color:#17644e; }
nav { display:grid; gap:8px; margin:18px 0; }
.nav { padding:12px; border-radius:8px; }
.nav:hover, .choice:hover { background:#fff; }
.metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin-bottom:14px; }
.metrics article, .panel { background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px; box-shadow:0 10px 24px rgba(53,67,75,.06); }
.metrics span, small { color:var(--muted); }
.metrics strong { display:block; margin:8px 0; font-size:28px; }
.grid { display:grid; gap:14px; margin-bottom:14px; }
.grid.two { grid-template-columns:1.1fr .9fr; }
.grid.three { grid-template-columns:.75fr 1fr 1.4fr; }
.timeline { display:grid; gap:10px; padding-left:20px; }
.timeline li span { display:block; color:var(--muted); margin-top:4px; }
.tasks { display:grid; gap:10px; }
.tasks article { padding:12px; border-radius:8px; background:var(--soft); }
.detail { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.detail div { padding:10px; background:var(--soft); border-radius:8px; }
.detail dt { font-weight:900; }
.detail dd { margin:4px 0 0; color:var(--muted); }
.cards, .schedule { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }
.image-card, .slot { display:grid; gap:8px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.thumb { min-height:95px; display:grid; place-items:center; padding:12px; border-radius:8px; background:linear-gradient(135deg,#e6f4ee,#fff0cb); text-align:center; font-weight:900; }
.choice { display:flex; justify-content:space-between; gap:10px; padding:10px; margin-bottom:8px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.choice.hot { border-color:#e8c35b; background:#fff7d8; }
.alert { color:#996b00; background:#fff7d8; border-radius:8px; padding:10px; }
.section-line { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.table-wrap { overflow-x:auto; }
table { width:100%; min-width:1280px; border-collapse:collapse; }
th, td { padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:13px; }
th { background:#eef5f2; }
input, select, textarea { width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; font:inherit; background:#fff; }
textarea { min-height:58px; resize:vertical; }
.wide { min-height:120px; }
.grade { display:inline-block; min-width:24px; padding:2px 7px; border-radius:999px; text-align:center; font-weight:900; }
.grade.S { background:#1f9d68; color:#fff; }
.grade.A { background:#b8ebbb; color:#1d5d31; }
.grade.B { background:#ffd188; color:#7a4a00; }
.grade.C { background:#ffd8d5; color:#9b281f; }
.grade.D { background:#ef6b5b; color:#fff; }
.pos { display:inline-block; padding:3px 8px; border-radius:999px; background:#ffe1de; color:var(--red); font-weight:900; }
.empty { color:var(--muted); background:#f8faf9; padding:12px; border-radius:8px; }
@media (max-width: 900px) { body { grid-template-columns:1fr; } aside { border-right:0; border-bottom:1px solid var(--line); } .metrics, .grid.two, .grid.three, .detail { grid-template-columns:1fr; } }
"""
