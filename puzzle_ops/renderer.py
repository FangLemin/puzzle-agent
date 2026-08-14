from dataclasses import dataclass, field
from html import escape
import json
from pathlib import Path
from urllib.parse import urlencode

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.models import DemandRow
from puzzle_ops.production import is_production_write_country, production_write_countries
from puzzle_ops.visual_assets import image_data_uri


OPERATOR_USERS = (
    {"user_id": "jp_owner", "name": "日本运营", "writable_countries": ("日本",)},
    {"user_id": "fr_owner", "name": "法国运营", "writable_countries": ("法国",)},
    {"user_id": "jp_fr_assist", "name": "日本/法国协助运营", "writable_countries": ("日本", "法国")},
    {"user_id": "br_ru_owner", "name": "巴西/俄罗斯运营", "writable_countries": ("巴西", "俄罗斯")},
    {"user_id": "us_owner", "name": "美国运营", "writable_countries": ("美国",)},
)

DEFAULT_USER_ID = "jp_fr_assist"

LOGIN_COUNTRIES = (
    ("日本", "🇯🇵"),
    ("法国", "🇫🇷"),
    ("巴西", "🇧🇷"),
    ("俄罗斯", "🇷🇺"),
    ("美国", "🇺🇸"),
)


@dataclass
class AppState:
    user_id: str = DEFAULT_USER_ID
    country: str = "日本"
    view: str = "dashboard"
    category: str = "animal"
    tag: str = "常规_日本_猫咪鲤鱼0605"
    trial_mode: str = "parse"
    schedule_day: str = "周一"
    value_grade: str = "all"
    memory_layer: str = ""
    memory_review_status: str = ""
    memory_approved_for_rag: str = ""
    memory_conflict: str = ""
    memory_created_by: str = ""
    memory_subject: str = ""
    memory_operation_tag: str = ""
    show_holiday: bool = False
    show_prompt_benchmark: bool = False
    show_value_benchmark: bool = False
    need_rows: list[DemandRow] = field(default_factory=list)
    trial_row: DemandRow | None = None
    workflow_notes: list[str] = field(default_factory=list)
    task_notes: list[str] = field(default_factory=list)
    schedule_replacements: dict[int, object] = field(default_factory=dict)
    sync_message: str = ""
    sync_url: str = ""
    analysis_edits: dict[str, object] = field(default_factory=dict)
    trial_uploads: list[dict[str, str]] = field(default_factory=list)
    trial_rows: list[DemandRow] = field(default_factory=list)
    trial_parse_row: DemandRow | None = None
    trial_parse_uploads: list[dict[str, str]] = field(default_factory=list)
    trial_parse_rows: list[DemandRow] = field(default_factory=list)
    trial_derive_row: DemandRow | None = None
    trial_derive_uploads: list[dict[str, str]] = field(default_factory=list)
    trial_derive_rows: list[DemandRow] = field(default_factory=list)
    trial_derivative_candidates: list[DemandRow] = field(default_factory=list)
    trial_derivative_candidate_uploads: list[dict[str, str]] = field(default_factory=list)
    trial_derivative_prompt: str = ""
    trial_derivative_negative_prompt: str = ""
    trial_derivative_prompt_touched: bool = False
    trial_derivative_job_id: str = ""
    trial_derivative_job_status: str = ""
    trial_derivative_job_progress: int = 0
    trial_derivative_job_message: str = ""
    value_prediction_job_id: str = ""
    value_prediction_job_status: str = ""
    value_prediction_job_progress: int = 0
    value_prediction_job_message: str = ""
    harness_prelabel_job_id: str = ""
    harness_prelabel_job_status: str = ""
    harness_prelabel_job_progress: int = 0
    harness_prelabel_job_message: str = ""
    harness_approval_job_id: str = ""
    harness_approval_job_status: str = ""
    harness_approval_job_progress: int = 0
    harness_approval_job_message: str = ""
    generation_event: dict[str, str] = field(default_factory=dict)
    description_benchmarks: list[dict[str, str]] = field(default_factory=list)
    value_prediction_benchmarks: list[dict[str, str]] = field(default_factory=list)


def render_page(agent: PuzzleOpsAgent, state: AppState) -> str:
    normalize_session(state)
    if state.view == "login":
        return render_login(agent, state)
    normalize_state(agent, state)
    body = {
        "dashboard": render_dashboard,
        "regular": render_regular,
        "trial": render_trial,
        "analysis": render_analysis,
        "weekly_review": render_weekly_review,
        "value": render_value,
        "runtime": render_runtime,
        "eval": render_eval,
        "sync": render_sync,
    }[state.view](agent, state)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>拼图运营智能后台 Python版</title>
  {render_auto_refresh(state)}
  <style>{CSS}</style>
</head>
<body>
  <aside>
    <div class="brand"><div class="logo">🧩</div><strong>PuzzleOps Agent</strong><span>纯 Python 后台原型</span></div>
    {render_session_card(state)}
    {render_country_switch(agent, state)}
    {render_nav(state)}
    <p class="note">所有页面由 Python 标准库服务端渲染；业务逻辑在 <code>puzzle_ops/agents.py</code>。</p>
  </aside>
  <main>
    <header>
      <div><p>{escape(state.country)}市场</p><h1><span class="page-icon">{view_icon(state.view)}</span>{page_title(state.view)}</h1>{render_permission_strip(state)}</div>
      <div class="header-actions"><a class="button" href="{href(state, view='runtime')}">系统治理中心</a></div>
    </header>
    {body}
  </main>
</body>
</html>"""


def render_auto_refresh(state: AppState) -> str:
    if (
        state.trial_derivative_job_status in {"pending", "running"}
        or state.value_prediction_job_status in {"pending", "running"}
        or state.harness_prelabel_job_status in {"pending", "running"}
        or state.harness_approval_job_status in {"pending", "running"}
    ):
        return '<meta http-equiv="refresh" content="3">'
    return ""


def normalize_session(state: AppState) -> None:
    user_ids = {str(user["user_id"]) for user in OPERATOR_USERS}
    if state.user_id not in user_ids:
        state.user_id = DEFAULT_USER_ID
    if state.country not in {country for country, _ in LOGIN_COUNTRIES}:
        state.country = "日本"


def normalize_state(agent: PuzzleOpsAgent, state: AppState) -> None:
    valid_views = {"dashboard", "regular", "trial", "analysis", "weekly_review", "value", "runtime", "eval", "sync"}
    if state.view not in valid_views:
        state.view = "value"
    if state.country not in agent.countries():
        state.country = "日本"
    categories = agent.categories(state.country)
    if state.category not in categories:
        state.category = next(iter(categories))
    tags = agent.sorted_tags(state.country, state.category)
    if not tags:
        state.tag = ""
    elif state.tag not in {tag.tag for tag in tags}:
        state.tag = tags[0].tag
    if state.trial_row is None or state.trial_row.country != state.country:
        state.trial_row = agent.create_trial_demand(state.country, state.category, state.trial_mode)
    if not state.workflow_notes:
        state.workflow_notes = [text for _, text in workflow_items()]
    if not state.task_notes:
        state.task_notes = [task["body"] for task in agent.dashboard(state.country)["tasks"]]


def current_user(state: AppState) -> dict[str, object]:
    for user in OPERATOR_USERS:
        if user["user_id"] == state.user_id:
            return user
    return OPERATOR_USERS[0]


def user_label(user_id: str) -> str:
    for user in OPERATOR_USERS:
        if user["user_id"] == user_id:
            return str(user["name"])
    return str(OPERATOR_USERS[0]["name"])


def can_write_country(user_id: str, country: str) -> bool:
    if not is_production_write_country(country):
        return False
    for user in OPERATOR_USERS:
        if user["user_id"] == user_id:
            return country in tuple(user["writable_countries"])
    return False


def permission_label(state: AppState) -> str:
    return "可编辑" if can_write_country(state.user_id, state.country) else "只读"


def render_login(agent: PuzzleOpsAgent, state: AppState) -> str:
    normalize_session(state)
    user = current_user(state)
    user_rows = "".join(render_login_user_row(state, item) for item in OPERATOR_USERS)
    country_rows = "".join(render_login_country_row(state, country, flag, country in agent.countries()) for country, flag in LOGIN_COUNTRIES)
    editable_country = next((country for country in user["writable_countries"] if country in agent.countries()), "")
    switch_link = f'<a class="login-switch" href="{href(state, country=str(editable_country), view="dashboard")}">切换为可编辑国家：{escape(str(editable_country))}</a>' if editable_country and editable_country != state.country else ""
    mode = permission_label(state)
    button_class = "primary login-enter" if mode == "可编辑" else "login-enter readonly"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>选择身份与国家 - PuzzleOps Agent</title>
  <style>{CSS}</style>
</head>
<body class="login-body">
  <main class="login-main">
    <section class="login-brand">
      <div class="login-logo">🧩</div>
      <div><h1>PuzzleOps Agent</h1><p>出海拼图内容运营工作台</p></div>
    </section>
    <section class="login-grid">
      <div class="panel login-panel">
        <h2>选择身份与国家</h2>
        <div class="login-step"><span>1</span><strong>运营人员</strong></div>
        <div class="login-list">{user_rows}</div>
        <div class="login-step"><span>2</span><strong>本次工作国家</strong></div>
        <div class="login-list">{country_rows}</div>
        {render_login_warning(state)}
        <a class="button {button_class}" href="{href(state, view='dashboard')}">进入{escape(mode)}工作台</a>
        {switch_link}
      </div>
      <div class="panel login-panel">
        <h2>进入后的权限预览</h2>
        <div class="permission-grid">
          <article class="permission-card ok"><strong>可查看</strong><ul><li>Dashboard</li><li>数据分析</li><li>Memory Debug</li><li>RAG Citation</li><li>排图预览</li><li>历史同步记录</li></ul></article>
          <article class="permission-card locked"><strong>不可操作</strong><ul><li>新增/编辑提需</li><li>试新上传解析</li><li>价值观人工修正</li><li>Memory 晋升/停用</li><li>RAG Feedback 写入</li><li>飞书同步</li><li>RAG Patch / Reindex</li></ul></article>
        </div>
        <div class="session-preview">当前用户：{escape(str(user["name"]))}　|　当前国家：{escape(state.country)}　|　模式：{escape(mode)}</div>
        <div class="header-preview"><span class="menu-icon">☰</span><span class="mini-logo">🧩</span><strong>PuzzleOps Agent</strong><span>{escape(str(user["name"]))} · {escape(state.country)} · {escape(mode)}</span></div>
        <p class="note">实际进入后，会根据当前身份和国家自动隐藏或拦截不可操作入口。</p>
      </div>
    </section>
  </main>
</body>
</html>"""


def render_login_user_row(state: AppState, user: dict[str, object]) -> str:
    selected = " selected" if user["user_id"] == state.user_id else ""
    countries = "、".join(str(country) for country in user["writable_countries"])
    return (
        f'<a class="login-row{selected}" href="{href(state, user_id=str(user["user_id"]), view="login")}">'
        f'<span>👤</span><strong>{escape(str(user["name"]))}</strong><small>负责：{escape(countries)}</small></a>'
    )


def render_login_country_row(state: AppState, country: str, flag: str, supported: bool) -> str:
    selected = " selected" if state.country == country else ""
    writable = can_write_country(state.user_id, country)
    badge = "可编辑" if writable else "只读"
    badge_class = "edit" if writable else "readonly"
    copy = "可创建、晋升、停用 Memory，可同步飞书" if writable else "可查看，不可操作"
    if not supported:
        copy = "权限已配置，明日生产先只读，业务数据待接入"
    return (
        f'<a class="login-row country-row{selected}" href="{href(state, country=country, view="login")}">'
        f'<span>{escape(flag)}</span><strong>{escape(country)}</strong><em class="perm {badge_class}">{badge}</em><small>{escape(copy)}</small></a>'
    )


def render_login_warning(state: AppState) -> str:
    if can_write_country(state.user_id, state.country):
        return '<p class="login-hint ok">当前为可编辑模式：你可以操作当前国家的 Memory/RAG/同步链路。</p>'
    return f'<p class="login-hint readonly">当前为只读模式：你可以查看{escape(state.country)} Memory/RAG，但不能写入或同步。</p>'


def render_session_card(state: AppState) -> str:
    mode = permission_label(state)
    mode_class = "edit" if mode == "可编辑" else "readonly"
    return (
        '<section class="session-card">'
        f'<small>当前用户：{escape(user_label(state.user_id))}</small>'
        f'<strong>{escape(state.country)} · <span class="perm {mode_class}">{escape(mode)}</span></strong>'
        f'<a href="{href(state, view="login")}">切换身份/国家</a>'
        '</section>'
    )


def render_permission_strip(state: AppState) -> str:
    mode = permission_label(state)
    mode_class = "edit" if mode == "可编辑" else "readonly"
    warning = "" if mode == "可编辑" else '<span class="readonly-copy">只读模式：非负责国家，写操作已禁用。</span>'
    return f'<div class="permission-strip"><span>当前用户：{escape(user_label(state.user_id))}</span><span>当前国家：{escape(state.country)}</span><span class="perm {mode_class}">模式：{escape(mode)}</span>{warning}</div>'


def render_country_switch(agent: PuzzleOpsAgent, state: AppState) -> str:
    buttons = []
    for country in agent.countries():
        data = agent.dashboard(country)
        active = " active" if country == state.country else ""
        mode = "可编辑" if can_write_country(state.user_id, country) else "只读"
        mode_class = "edit" if mode == "可编辑" else "readonly"
        buttons.append(f'<a class="pill{active}" href="{href(state, country=country, view="dashboard")}">{escape(data["country_label"])} <span class="perm {mode_class}">{mode}</span></a>')
    return '<section class="switcher"><h2>当前国家</h2><div class="pills">' + "".join(buttons) + "</div></section>"


def render_nav(state: AppState) -> str:
    items = (
        ("dashboard", "🏠", "首页工作台"),
        ("regular", "📦", "常规提需"),
        ("trial", "✨", "试新提需"),
        ("analysis", "📈", "数据分析大师"),
        ("weekly_review", "🔎", "周三复盘"),
        ("value", "🔮", "价值观大师"),
        ("runtime", "🧠", "系统治理中心"),
        ("eval", "🧪", "上线验收中心"),
    )
    links = [f'<a class="nav {"active" if key == state.view else ""}" href="{href(state, view=key)}">{icon} {label}</a>' for key, icon, label in items]
    return "<nav>" + "".join(links) + "</nav>"


def render_dashboard(agent: PuzzleOpsAgent, state: AppState) -> str:
    dashboard = agent.dashboard(state.country)
    next_holiday = agent.next_holiday(state.country)
    holiday_days = next_holiday[0] if next_holiday else None
    holiday = next_holiday[1] if next_holiday else None
    tasks = "".join(
        f'<article><strong>{escape(task["title"])}</strong><textarea name="task_{index}">{escape(state.task_notes[index] if index < len(state.task_notes) else str(task["body"]))}</textarea></article>'
        for index, task in enumerate(dashboard["tasks"])
    )
    holiday_panel = render_holiday_panel(holiday) if holiday and state.show_holiday else ""
    holiday_status = ""
    holiday_body = ""
    if holiday and holiday_days is not None:
        if holiday_days <= 15:
            holiday_status = "已进入提前提需窗口"
            holiday_body = f"{holiday.name}：{holiday.date_range}，距离节日 {holiday_days} 天，建议现在准备节日营销提需。"
        else:
            holiday_status = "下一个节日预告"
            holiday_body = f"{holiday.name}：{holiday.date_range}，距离节日 {holiday_days} 天；未进入提前 15 天提需窗口，先展示维护表预告。"
    holiday_summary = (
        f"""
<section class="panel compact-panel">
  <h2>节日提需</h2>
  <p><strong>{escape(holiday_status)}</strong> · {escape(holiday_body)}</p>
  <p class="note">节日数据来自日本/法国维护表；进入窗口后会自动进入今日待办，完整建议会结合历史好坏图和价值观规则。</p>
  <a class="button primary-link" href="{href(state, view='dashboard', show_holiday='1')}">查看完整节日提需建议</a>
</section>
"""
        if holiday
        else """
<section class="panel compact-panel">
  <h2>节日提需</h2>
  <p>未来 90 天暂无已维护节日节点。</p>
</section>
"""
    )
    return f"""
<section class="metrics">
  <article><span>当前国家</span><strong>{escape(dashboard["country_label"])}</strong><small>{escape(dashboard["owner"])}</small></article>
  <article><span>本季度累计 SA 占比 / OKR</span><strong>{render_metric_ratio(str(dashboard["sa"]), higher_is_better=True)}</strong></article>
  <article><span>本季度累计 AI率 / OKR</span><strong>{render_ai_rate_ratio(str(dashboard["ai"]))}</strong></article>
</section>
<section class="grid two">
  <form class="panel" method="post" action="/save_dashboard"><h2>本周工作流</h2>{render_workflow(state)}<button>保存工作流</button></form>
  <form class="panel" method="post" action="/save_dashboard"><h2>今日待办 <span>🧸💦</span></h2><div class="tasks">{tasks}</div><button>保存待办</button></form>
</section>
{holiday_summary}
{holiday_panel}
"""


def render_holiday_panel(holiday) -> str:
    good_images = "".join(render_image_card(image) for image in holiday.history_good_images)
    bad_images = "".join(render_image_card(image) for image in holiday.history_bad_images)
    citations = "".join(f"<li>{escape(str(rule))}</li>" for rule in holiday.value_rule_citations)
    return f"""<section class="panel">
  <h2>节日提需建议：{escape(holiday.name)}</h2>
  <dl class="detail">
    <div><dt>日期范围</dt><dd>{escape(holiday.date_range)}</dd></div>
    <div><dt>节日含义</dt><dd>{escape(holiday.meaning)}</dd></div>
    <div><dt>主要内容</dt><dd>{escape(holiday.content)}</dd></div>
    <div><dt>历史依据</dt><dd>{escape(holiday.evidence_note)}</dd></div>
    <div><dt>AI推荐主题</dt><dd>{escape("；".join(holiday.ai_themes))}</dd></div>
    <div><dt>推荐元素</dt><dd>{escape("；".join(holiday.elements))}</dd></div>
    <div><dt>策划来源</dt><dd>{escape(holiday.llm_source or "本地规则 fallback")}</dd></div>
    <div><dt>策划建议</dt><dd>{escape(holiday.llm_planning_note)}</dd></div>
  </dl>
  <h3>真实历史好图参考</h3>
  <div class="cards">{good_images or '<p class="empty">暂无真实历史好图可引用。</p>'}</div>
  <h3>真实历史坏图避雷</h3>
  <div class="cards">{bad_images or '<p class="empty">暂无真实历史坏图可引用。</p>'}</div>
  <details><summary>价值观规则依据</summary><ul>{citations or '<li>暂无规则依据。</li>'}</ul></details>
</section>
"""


def workflow_items() -> tuple[tuple[str, str], ...]:
    return (
        ("🗓️ 周一", "排两个国家的图，优先处理低库存爆款 tag。"),
        ("🧩 周二", "补充排图、检查 5/10 分发位、确认上新节日素材。"),
        ("📈 周三", "数据分析大师回收上周期数据，输出 SA/CD/AI 趋势和明细备注。"),
        ("👀 周四", "过图会，结合复盘结论修改排图与提需优先级。"),
        ("✨ 周一到周五", "常规提需、试新提需持续进行，审核规则自动写入备注。"),
    )


def render_workflow(state: AppState) -> str:
    return "<ol class='timeline'>" + "".join(
        f'<li><strong>{day}</strong><textarea name="workflow_{index}">{escape(state.workflow_notes[index])}</textarea></li>'
        for index, (day, _) in enumerate(workflow_items())
    ) + "</ol>"


def render_description_benchmark(state: AppState) -> str:
    if not state.description_benchmarks:
        return ""
    cards = []
    score_names = ("主体准确性", "生产可执行性", "简洁度", "市场适配度", "备注有效性")
    for index, item in enumerate(state.description_benchmarks):
        template_scores = "".join(
            f'<label><span>{name}</span><input class="small-input" name="template_benchmark_score_{index}_{score_index}" placeholder="1-5"></label>'
            for score_index, name in enumerate(score_names)
        )
        prompt_scores = "".join(
            f'<label><span>{name}</span><input class="small-input" name="prompt_benchmark_score_{index}_{score_index}" placeholder="1-5"></label>'
            for score_index, name in enumerate(score_names)
        )
        prompt_status = str(item.get("prompt_status", ""))
        prompt_model = str(item.get("prompt_model", ""))
        prompt_remark = str(item.get("prompt_remark", ""))
        template_output = str(item.get("template_subject_description", ""))
        prompt_output = str(item.get("prompt_subject_description", ""))
        hidden = (
            f'<input type="hidden" name="image_name_{index}" value="{escape(str(item.get("image_name", "")))}">'
            + f'<input type="hidden" name="operation_tag_{index}" value="{escape(str(item.get("operation_tag", "")))}">'
            + f'<input type="hidden" name="template_output_{index}" value="{escape(template_output)}">'
            + f'<input type="hidden" name="prompt_output_{index}" value="{escape(prompt_output)}">'
            + f'<input type="hidden" name="prompt_model_{index}" value="{escape(prompt_model)}">'
            + f'<input type="hidden" name="prompt_status_{index}" value="{escape(prompt_status)}">'
        )
        cards.append(
            f"""
<article class="comparison-card">
  {hidden}
  <div class="benchmark-head">
    <div>
      <h3>{escape(str(item.get("operation_tag", "")))}</h3>
      <small>{escape(str(item.get("image_name", "")))}</small>
    </div>
    <span class="pill">Prompt baseline v3</span>
  </div>
  <div class="benchmark-output-grid">
    <div class="benchmark-output">
      <strong>当前模板输出</strong>
      <p>{escape(template_output)}</p>
    </div>
    <div class="benchmark-output">
      <strong>强 Prompt v3 输出</strong>
      <p>{escape(prompt_output or "未生成")}</p>
      <small>{escape(prompt_remark)}</small>
    </div>
  </div>
  <details><summary>查看强 Prompt 原文</summary><pre class="prompt-pre">{escape(str(item.get("prompt", "")))}</pre></details>
  <p class="note">Prompt baseline 状态：{escape(prompt_status)} · 模型：{escape(prompt_model)}</p>
  <details class="benchmark-score-details">
    <summary>填写 A/B 评分</summary>
    <div class="benchmark-score-grid">
      <div><h4>当前模板评分</h4><div class="score-grid">{template_scores}<label><span>最终标签</span>{select(f"template_benchmark_label_{index}", ("可直接用", "轻微修改", "需要大改", "不可用"), "轻微修改")}</label></div></div>
      <div><h4>强 Prompt评分</h4><div class="score-grid">{prompt_scores}<label><span>最终标签</span>{select(f"prompt_benchmark_label_{index}", ("可直接用", "轻微修改", "需要大改", "不可用"), "轻微修改")}</label></div></div>
    </div>
  </details>
</article>
"""
        )
    return f"""
<section class="panel">
  <h2>主体描述 Prompt Benchmark</h2>
  <p class="note">同一张图对比 A 当前固定模板 与 B 强 Prompt v3 生产详细版。这里的评分只用于人工评测，不会影响飞书同步。</p>
  <form method="post" action="/save_description_benchmark">
    {hidden_context(state, view="regular")}
    <input type="hidden" name="benchmark_count" value="{len(state.description_benchmarks)}">
    <div class="benchmark-list">{''.join(cards)}</div>
    <div class="section-line benchmark-save-line"><p class="note">可先逐张展开填写，全部完成后一次性保存。</p><button class="primary">批量保存全部评分</button></div>
  </form>
</section>
"""


def render_regular(agent: PuzzleOpsAgent, state: AppState) -> str:
    categories = "".join(f'<a class="choice {"active" if name == state.category else ""}" href="{href(state, view="regular", category=name)}">{escape(name)}</a>' for name in agent.categories(state.country))
    tag_items = agent.sorted_tags(state.country, state.category)
    tags = "".join(render_tag_choice(state, tag) for tag in tag_items) or '<p class="empty">暂无此 JS 分类的历史运营 tag。</p>'
    images = "".join(render_reference_image(state, image, index) for index, image in enumerate(agent.images_for_tag(state.country, state.tag)))
    rows = render_need_rows(state.need_rows)
    sync_message = render_sync_message(state)
    feishu_status = agent.feishu.config_status()
    if agent.feishu.is_real:
        feishu_copy = f"真实飞书：{escape(str(feishu_status.get('spreadsheet_token', '')))} · {escape(str(feishu_status.get('sheet_range', '')))}"
    else:
        feishu_copy = f"真实飞书未配置，缺少：{escape('、'.join(feishu_status['missing']))}"
    context = hidden_context(state, view="regular")
    benchmark = render_description_benchmark_entry(state)
    benchmark_button = '<button formaction="/generate_description_benchmark" formmethod="post">生成 Prompt 对比评测</button>' if state.show_prompt_benchmark else ""
    return f"""
<section class="grid three">
  <div class="panel"><h2>分类</h2>{categories}</div>
  <div class="panel"><h2>运营 tag + 历史表现</h2><p class="alert">历史表现来自真实已分发样本；库存为 demo 模拟字段。红色=历史爆款但模拟库存少。</p>{tags}</div>
  <div class="panel"><div class="section-line"><h2>已分发图片参考</h2><form method="post" action="/add_regular_all">{context}<button>加入当前tag全部参考图</button></form></div><div class="cards">{images or '<p class="empty">请选择有历史样本的运营 tag。</p>'}</div></div>
</section>
<section class="panel">
  <div class="section-line"><h2>批量提需清单</h2><label class="demand-check"><input type="checkbox" onclick="document.querySelectorAll('[name=selected_rows]').forEach(item=>item.checked=this.checked)">全选</label></div>
  <p class="note">{feishu_copy}</p>
  {sync_message}
  <form method="post" action="/save_needs">{context}{rows}<div class="section-line"><button class="primary">保存表格修改</button><button formaction="/generate_descriptions" formmethod="post">批量AI生成主体描述</button>{benchmark_button}<button formaction="/sync_needs_feishu" formmethod="post">一键同步到飞书表格</button></div></form>
</section>
{benchmark}
"""


def render_description_benchmark_entry(state: AppState) -> str:
    if state.show_prompt_benchmark:
        if state.description_benchmarks:
            return render_description_benchmark(state)
        return f"""
<section class="panel compact-panel">
  <div class="section-line">
    <div>
      <h2>Prompt 评测已开启</h2>
      <p class="note">先在提需清单勾选样本，再点击“生成 Prompt 对比评测”；这里用于对比当前线上生成版本和正在调试的新 Prompt/模型版本。</p>
    </div>
    <a class="button" href="{href(state, view='regular', show_prompt_benchmark='')}">收起 Prompt 评测</a>
  </div>
</section>
"""
    return f"""
<section class="panel compact-panel">
  <div class="section-line">
    <div>
      <h2>Prompt 评测</h2>
      <p class="note">日常提需默认不展开评测；只有调试新 Prompt、微调版本或模型版本时再打开。</p>
    </div>
    <a class="button" href="{href(state, view='regular', show_prompt_benchmark='1')}">展开 Prompt 评测</a>
  </div>
</section>
"""


def render_tag_choice(state: AppState, tag) -> str:
    hot = " " + PuzzleOpsAgent().stock_class(tag)
    active = " active" if tag.tag == state.tag else ""
    metric = tag.risk if str(tag.risk) else f"模拟库存 {tag.stock}"
    return f'<a class="choice{active}{hot}" href="{href(state, view="regular", tag=tag.tag)}"><strong>{escape(tag.tag)}</strong><span>{escape(metric)}</span></a>'


def render_reference_image(state: AppState, image, index: int) -> str:
    return f"""
<article class="image-card">
  {visual_thumb(image.thumb, image.title)}
  <strong><span class="grade {image.grade}">{escape(image.grade)}</span> {escape(image.title)}</strong>
  <small>开图 {escape(image.open_rate)} · 完成 {escape(image.finish_rate)} · {escape(image.finish_time)}</small>
  <form method="post" action="/add_regular">{hidden_context(state, view="regular")}<input type="hidden" name="image_index" value="{index}"><button>＋加入提需</button></form>
</article>
"""


def render_need_rows(rows: list[DemandRow]) -> str:
    if not rows:
        return "<p class='empty'>暂未加入提需。点击图片卡片的加号后，在这里批量提需。</p>"
    body = "".join(render_need_card(row, index, include_value=False, include_select=True) for index, row in enumerate(rows))
    return f'<div class="demand-card-list regular-demand-list">{body}</div>'


def render_trial(agent: PuzzleOpsAgent, state: AppState) -> str:
    vision_status = agent.vision_llm_status()
    generation_status = agent.generation_provider_status()
    mode_links = "".join(
        f'<a class="mode-card {"active" if state.trial_mode == mode else ""}" href="{href(state, view="trial", trial_mode=mode)}"><strong>{label}</strong><span>{copy}</span></a>'
        for mode, label, copy in (
            ("parse", "参考图解析提需", "上传 1-3 张参考图，AI解析主体/色彩/构图。"),
            ("derive", "好图衍生提需", "最多上传 3 张历史好图，先用 Qwen 视觉解析共同规律，再生成衍生参考图。"),
        )
    )
    if state.trial_mode == "derive":
        row = state.trial_derive_row or state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        rows = state.trial_derive_rows or state.trial_rows or []
        uploads = state.trial_derive_uploads or state.trial_uploads
    else:
        row = state.trial_parse_row or state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
        rows = state.trial_parse_rows or state.trial_rows or []
        uploads = state.trial_parse_uploads or state.trial_uploads
    visible_rows = rows or [row]
    is_derive_mode = state.trial_mode == "derive" or "衍生" in row.operation_tag or "衍生" in row.image_name
    row_html = (
        "".join(render_need_card(item, index, include_value=True) for index, item in enumerate(visible_rows))
        if rows
        else render_need_card(row, 0, include_value=True, prefix="")
    )
    upload_copy = "拖拽或选择 1-3 张参考图" if state.trial_mode == "parse" else "上传最多3张历史好图，解析共同衍生方向"
    previews = "".join(render_upload_preview(item, state=state, removable=True) for item in uploads) or '<div class="thumb">参考图 A</div><div class="thumb">参考图 B</div><div class="thumb">参考图 C</div>'
    sync_message = render_sync_message(state)
    context = hidden_context(state, view="trial")
    can_generate_derivative = is_derive_mode and bool(row.reference_image_path)
    derivative_form = render_derivative_prompt_panel(agent, row, state, context, can_generate_derivative) if is_derive_mode else ""
    parse_form = (
        f'<form method="post" action="/parse_trial_uploads">{context}<button class="primary">解析图片</button></form>'
        if uploads
        else ""
    )
    generation_diagnostic = render_generation_provider_diagnostic(generation_status)
    generation_event = render_generation_event(state.generation_event)
    rag_details = render_trial_value_rag_details(agent, tuple(visible_rows), state)
    value_correction = render_value_match_correction_panel(row, state)
    approval_form = ""
    pending_generated_rows = [
        item for item in rows if item.generation_review_status and not item.human_approved
    ]
    if pending_generated_rows and not state.trial_derivative_candidates:
        approval_form = f'<form method="post" action="/approve_generated_derivatives">{context}<button>确认加入提需表</button></form>'
    derivative_candidates = render_derivative_candidate_panel(state) if is_derive_mode else ""
    derivative_job_progress = render_derivative_job_progress(state) if is_derive_mode else ""
    return f"""
<section class="panel"><h2>试新模式</h2><div class="mode-grid">{mode_links}</div></section>
<section class="grid two">
  <div class="panel"><h2>上传参考图</h2><div class="mock-upload-zone"><strong>{upload_copy}</strong><span>先上传图片；确认图片池无误后，再点击解析图片调用 Qwen 视觉模型。</span><form method="post" action="/upload_trial_images" enctype="multipart/form-data">{context}<input type="file" name="trial_images" accept="image/*" multiple><button>上传图片</button></form>{parse_form}{derivative_form}</div><div class="reference-row">{previews}</div></div>
  <div class="panel"><h2>解析状态</h2><p class="alert">{trial_status_alert(state)}</p><dl class="detail"><div><dt>Qwen 视觉解析</dt><dd>{vision_mode_copy(vision_status)}</dd></div><div><dt>Qwen 图像生成</dt><dd>{generation_mode_copy(generation_status)}</dd></div><div><dt>当前图片</dt><dd>{escape(row.image_name)}</dd></div><div><dt>解析备注</dt><dd>{escape(compact_trial_remark(row.remark))}</dd></div></dl>{generation_diagnostic}{generation_event}<form method="post" action="/check_generation_provider">{context}<button>检查 Qwen 图像生成</button></form></div>
</section>
{derivative_candidates}
{derivative_job_progress}
<section class="panel">
  <div class="section-line"><h2>试新提需表预览</h2><div class="inline-actions"><form method="post" action="/apply_value_master">{context}<button>价值观大师</button></form>{approval_form}</div></div>
  {sync_message}
  <form method="post" action="/save_trial">{context}<div class="demand-card-list trial-demand-list">{row_html}</div><div class="section-line"><button class="primary">保存试新修改</button><button formaction="/sync_trial_feishu" formmethod="post">一键同步到飞书表格</button></div></form>
  {rag_details}
  {value_correction}
</section>
"""


def trial_status_alert(state: AppState) -> str:
    if state.trial_mode == "derive":
        return "上传后先形成衍生方向；生成AI效果图后，运营确认才会加入下方提需表。"
    return "解析结果已写入下方试新提需表，可在表格中继续编辑后同步飞书。"


def compact_trial_remark(remark: str) -> str:
    text = str(remark or "").strip()
    if not text:
        return "等待上传图片"
    if "后台解析中" in text:
        return "后台解析中，请稍后"
    if "调用失败" in text or "失败" in text or "错误" in text:
        return "处理失败，详情见表格"
    if "已生成" in text and "衍生参考图" in text:
        return "已生成衍生图，详情见表格"
    if "Prompt：" in text or "二次 VLM" in text:
        return "生成审核完成，详情见表格"
    if "本地图片解析" in text or "视觉LLM" in text or "已读取" in text:
        return "解析完成，详情见表格"
    if len(text) <= 30:
        return text
    return text[:27] + "..."


def render_derivative_prompt_panel(agent: PuzzleOpsAgent, row: DemandRow, state: AppState, context: str, can_generate: bool) -> str:
    recommended_prompt, recommended_negative = agent.derivative_generation_prompts(row)
    prompt = state.trial_derivative_prompt or recommended_prompt
    negative_prompt = state.trial_derivative_negative_prompt or recommended_negative
    if not can_generate:
        return f"""
<div class="prompt-panel">
  <h3>衍生 Prompt 设置</h3>
  <p class="note">核心约束：单张完整画面、单一主场景、单一季节氛围、清晰主体、禁止四宫格/拼贴/多场景合集。</p>
  <form method="post" action="/save_derivative_prompt">
    {context}
    <label>正向 prompt<textarea name="derivative_prompt" rows="5">{escape(prompt)}</textarea></label>
    <label>负向 prompt<textarea name="derivative_negative_prompt" rows="4">{escape(negative_prompt)}</textarea></label>
    <div class="section-line"><button>保存 Prompt</button><button disabled title="请先上传并解析图片">生成衍生参考图</button></div>
  </form>
  <p class="note">请先上传并解析 1-3 张真实历史好图；prompt 可先编辑并保存。</p>
</div>
"""
    return f"""
<div class="prompt-panel">
  <h3>衍生 Prompt 设置</h3>
  <p class="note">核心约束：单张完整画面、单一主场景、单一季节氛围、清晰主体、禁止四宫格/拼贴/多场景合集。</p>
  <form method="post" action="/generate_trial_derivatives">
    {context}
    <label>正向 prompt<textarea name="derivative_prompt" rows="5">{escape(prompt)}</textarea></label>
    <label>负向 prompt<textarea name="derivative_negative_prompt" rows="4">{escape(negative_prompt)}</textarea></label>
    <div class="section-line"><button>生成衍生参考图</button><button formaction="/reset_derivative_prompt" formmethod="post">恢复推荐 prompt</button></div>
  </form>
</div>
"""


def render_derivative_candidate_panel(state: AppState) -> str:
    if not state.trial_derivative_candidates:
        return ""
    cards = []
    for index, row in enumerate(state.trial_derivative_candidates):
        preview = render_image_preview(row.image_name, row.reference_image_url)
        cards.append(
            '<article class="candidate-card">'
            f"<h3>AI效果图候选 {index + 1}</h3>"
            f'<label class="choice"><input type="checkbox" name="selected_derivative_candidates" value="{index}"> 选中加入提需表</label>'
            f"{preview}"
            f"<p>{escape(row.subject_description)}</p>"
            f"<small>{escape(row.remark[:260])}</small>"
            "</article>"
        )
    return (
        '<section class="panel"><div class="section-line"><h2>衍生方向 + AI效果图候选</h2>'
        f'<form method="post" action="/clear_derivative_candidates">{hidden_context(state, view="trial")}<button>清空候选并重试</button></form></div>'
        f'<form method="post" action="/approve_generated_derivatives">{hidden_context(state, view="trial")}'
        '<div class="card-grid">'
        + "".join(cards)
        + '</div><div class="section-line"><button class="primary">确认加入提需表</button></div></form></section>'
    )


def render_derivative_job_progress(state: AppState) -> str:
    if not state.trial_derivative_job_status:
        return ""
    progress = max(0, min(100, int(state.trial_derivative_job_progress or 0)))
    status = state.trial_derivative_job_status
    message = state.trial_derivative_job_message or "后台生成任务处理中"
    return f"""
<section class="panel derivative-job-panel">
  <div class="section-line"><h2>衍生图生成进度</h2><span class="status-pill">{escape(status)}</span></div>
  <progress value="{progress}" max="100"></progress>
  <p class="note">{escape(message)}</p>
  <small>页面会每 3 秒自动刷新；生成完成后候选图会出现在“衍生方向 + AI效果图候选”。</small>
</section>
"""


def render_generation_provider_diagnostic(status: dict[str, object]) -> str:
    fields = [
        ("服务", generation_provider_ui_label(str(status.get("provider", "not_configured")))),
        ("配置状态", "可用" if status.get("configured", False) else "未配置"),
        ("就绪状态", "就绪" if status.get("ready", status.get("configured", False)) else "未就绪"),
        ("模型", status.get("model", "未配置")),
    ]
    if "workflow_path" in status:
        fields.append(("本地工作流", status.get("workflow_path", "未配置")))
    if "workflow_configured" in status:
        fields.append(("工作流状态", "已配置" if status.get("workflow_configured", False) else "未配置"))
    rows = "".join(f"<div><dt>{escape(key)}</dt><dd>{escape(str(value))}</dd></div>" for key, value in fields)
    return f"<h3>Qwen 图像生成诊断</h3><dl class=\"detail compact-detail\">{rows}</dl>"


def render_generation_event(event: dict[str, str]) -> str:
    if not event:
        return ""
    fields = (
        ("状态", event.get("status", "unknown")),
        ("生成服务", generation_provider_ui_label(event.get("provider", "unknown"))),
        ("模型", event.get("model", "未记录")),
        ("task_id", event.get("task_id", "")),
        ("来源tag", event.get("source_operation_tag", "")),
        ("生成图", event.get("generated_image_paths", "")),
        ("二次审核", event.get("second_review_status", "unknown")),
        ("飞书附件", event.get("feishu_attachment_status", "unknown")),
        ("错误类型", event.get("error_type", "无")),
        ("处理建议", event.get("recovery_hint", "")),
        ("说明", event.get("message", "")),
    )
    rows = "".join(f"<div><dt>{escape(key)}</dt><dd>{escape(str(value))}</dd></div>" for key, value in fields)
    return f"<h3>最近一次生成任务</h3><dl class=\"detail compact-detail generation-event\">{rows}</dl>"


def generation_provider_ui_label(provider: str) -> str:
    value = (provider or "").strip().lower()
    if value in {"dashscope", "wanx", "cloud", "qwen 图像生成"}:
        return "Qwen 图像生成"
    if value == "mock":
        return "本地模拟生成"
    if value == "comfyui":
        return "ComfyUI 本地图像生成"
    if value in {"not_configured", "missing", "未配置", ""}:
        return "未配置"
    return provider


def generation_mode_copy(status: dict[str, object]) -> str:
    provider = generation_provider_ui_label(str(status.get("provider", "not_configured")))
    model = str(status.get("model", "未配置"))
    ready = bool(status.get("ready", status.get("configured", False)))
    if provider == "未配置":
        return "未配置 Qwen 图像生成；好图衍生暂不可用"
    return f"{provider} · {model} · {'可用' if ready else '未就绪'}"


def render_sync_message(state: AppState) -> str:
    if not state.sync_message:
        return ""
    if state.sync_url:
        return f"""
<div class="sync-success-card">
  <p class="success">{escape(state.sync_message)}</p>
  <a class="button primary-link" href="{escape(state.sync_url)}" target="_blank" rel="noopener">已同步，打开飞书表格</a>
  <small>如果浏览器没有自动打开新页，可以点击这个按钮进入飞书表格。</small>
</div>
"""
    return f'<p class="success">{escape(state.sync_message)}</p>'


def render_trial_value_rag_details(agent: PuzzleOpsAgent, rows: tuple[DemandRow, ...], state: AppState) -> str:
    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        for item in agent.value_match_rag_citation_details(row):
            chunk_id = str(item.get("chunk_id", ""))
            if chunk_id and chunk_id not in seen:
                seen.add(chunk_id)
                details.append(item)
    if not details:
        return ""
    feedback_summary = agent.rag_feedback_summary(state.country)
    feedback_by_chunk = {
        str(item.get("chunk_id", "")): item
        for item in feedback_summary.get("top_chunks", ())
        if isinstance(item, dict)
    }
    rows_html = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('chunk_id', '')))}</td>"
        f"<td>{escape(str(item.get('source_type', '')))}</td>"
        f"<td>{escape(str(item.get('parent_id', '')))}</td>"
        f"<td>{escape(str(item.get('title', '')))}</td>"
        f"<td>{escape(str(item.get('text', '')))}</td>"
        f"<td>{render_rag_feedback_counts(feedback_by_chunk.get(str(item.get('chunk_id', '')), {}))}</td>"
        "</tr>"
        for item in details
    )
    feedback_form = render_rag_batch_feedback_form(details, state)
    return f"""
<section class="subpanel rag-detail-panel">
  <h3>价值观 RAG 依据明细</h3>
  <div class="table-wrap"><table><thead><tr><th>引用ID</th><th>知识来源</th><th>父文档</th><th>标题</th><th>内容</th><th>历史反馈</th></tr></thead><tbody>{rows_html}</tbody></table></div>
  {feedback_form}
</section>
"""


def render_value_match_correction_panel(row: DemandRow, state: AppState) -> str:
    return ""


def render_rag_feedback_counts(stats: dict[str, object] | None = None) -> str:
    stats = stats or {}
    useful_count = int(stats.get("useful_count", 0) or 0)
    not_useful_count = int(stats.get("not_useful_count", 0) or 0)
    net_score = int(stats.get("net_score", 0) or 0)
    return f'<small class="rag-feedback-counts">已反馈：useful={useful_count} / not_useful={not_useful_count} / net={net_score}</small>'


def render_rag_batch_feedback_form(details: list[dict[str, str]], state: AppState) -> str:
    rows = []
    for index, item in enumerate(details):
        chunk_id = str(item.get("chunk_id", ""))
        rows.append(
            "<tr>"
            f"<td>{escape(chunk_id)}<input type=\"hidden\" name=\"chunk_id_{index}\" value=\"{escape(chunk_id)}\"></td>"
            f"<td>{escape(str(item.get('title', '')))}</td>"
            f"<td><label><input type=\"radio\" name=\"usefulness_{index}\" value=\"\" checked> 未评价</label> "
            f"<label><input type=\"radio\" name=\"usefulness_{index}\" value=\"useful\"> 有用</label> "
            f"<label><input type=\"radio\" name=\"usefulness_{index}\" value=\"not_useful\"> 无用</label></td>"
            f"<td><input name=\"note_{index}\" placeholder=\"可选：为什么有用/无用\"></td>"
            "</tr>"
        )
    return f"""
<details class="rag-feedback-batch">
  <summary>展开反馈与评分</summary>
  <form method="post" action="/submit_rag_feedback_batch">
    {hidden_context(state, view="trial")}
    <input type="hidden" name="citation_count" value="{len(details)}">
    <div class="table-wrap"><table><thead><tr><th>引用ID</th><th>标题</th><th>评价</th><th>备注</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
    <div class="grid two">
      <label>整体人工评分<input class="small-input" name="satisfaction_score" type="number" min="1" max="5" placeholder="1-5，可不填"></label>
      <label>人工评价/修正<textarea name="human_correction" placeholder="可选：填写运营人工修正或评价。忙的时候可以留空。"></textarea></label>
    </div>
    <button>一次性提交反馈</button>
  </form>
  <small>不展开、不提交也不影响加入提需或同步飞书；未评价 citation 不会写入 Memory/RAG 反馈。</small>
</details>
"""


def need_colgroup(include_check: bool, include_value: bool) -> str:
    classes = [
        "col-type",
        "col-country",
        "col-category",
        "col-image",
        "col-tag",
        "col-subject",
        "col-count",
        "col-priority",
        "col-method",
        "col-date",
        "col-description wide-description" if include_value else "col-description",
        "col-remark",
    ]
    if include_check:
        classes.insert(0, "col-check")
    if include_value:
        classes.append("col-value")
    return "<colgroup>" + "".join(f'<col class="{klass}">' for klass in classes) + "</colgroup>"


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
        render_image_preview(row.image_name, row.reference_image_url),
        f'<input class="operation-tag-input" name="operation_tag{prefix}" value="{escape(row.operation_tag)}">',
        escape(row.subject),
        f'<input class="small-input" name="count{prefix}" value="{row.count}" size="3">',
        select(f"priority{prefix}", ("P0", "P1", "P2"), row.priority),
        select(f"method{prefix}", ("纯AI", "限素材网", "先照片后AI"), row.method),
        f'<input name="delivery_date{prefix}" value="{escape(row.delivery_date)}" placeholder="">',
        f'<textarea class="description-input" name="subject_description{prefix}">{escape(row.subject_description)}</textarea>',
        f'<textarea name="remark{prefix}">{escape(row.remark)}</textarea>',
    ]
    if include_value:
        cells.append(escape(row.value_match))
        return "<tr><td><input type='checkbox' checked></td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def render_need_card(row: DemandRow, index: int, include_value: bool, prefix: str | None = None, include_select: bool = False) -> str:
    prefix = f"_{index}" if prefix is None else prefix
    select_html = f'<label class="demand-check"><input type="checkbox" name="selected_rows" value="{index}" checked>选择</label>' if include_select else ""
    value_html = (
        f'<label class="span-2"><span>价值观匹配度</span><div class="readonly-long">{escape(row.value_match)}</div></label>'
        if include_value
        else ""
    )
    return f"""
<article class="demand-card">
  <div class="demand-card-head">
    <div>{select_html}<strong>{escape(row.need_type)} / {escape(row.country)} / {escape(row.js_category)}</strong></div>
    <small>第 {index + 1} 条</small>
  </div>
  <div class="demand-card-grid">
    <label class="image-field"><span>图片本身</span>{render_image_preview(row.image_name, row.reference_image_url or (local_image_url(Path(row.reference_image_path)) if row.reference_image_path else ""))}</label>
    <label><span>运营tag</span><input class="operation-tag-input" name="operation_tag{prefix}" value="{escape(row.operation_tag)}"></label>
    <label><span>主体内容</span><div class="readonly-field">{escape(row.subject)}</div></label>
    <label><span>张数</span><input class="small-input" name="count{prefix}" value="{row.count}" size="3"></label>
    <label><span>需求等级</span>{select(f"priority{prefix}", ("P0", "P1", "P2"), row.priority)}</label>
    <label><span>加工方式</span>{select(f"method{prefix}", ("纯AI", "限素材网", "先照片后AI"), row.method)}</label>
    <label><span>交付日期</span><input name="delivery_date{prefix}" value="{escape(row.delivery_date)}" placeholder=""></label>
  </div>
  <div class="demand-long-fields">
    <label><span>主体描述</span><textarea class="description-input" name="subject_description{prefix}">{escape(row.subject_description)}</textarea></label>
    <label><span>备注</span><textarea name="remark{prefix}">{escape(row.remark)}</textarea></label>
    {value_html}
  </div>
</article>
"""


def render_analysis(agent: PuzzleOpsAgent, state: AppState) -> str:
    report = agent.analysis_report(state.country)
    edited_remarks = state.analysis_edits.get("remarks", {})
    cycle_summary = str(state.analysis_edits.get("cycle_summary", report.cycle_summary))
    next_todo = str(state.analysis_edits.get("next_todo", report.next_todo))
    rows = "".join(
        f'<tr><td>{render_image_preview(row.image_name)}</td><td>{escape(row.source)}</td><td>{grade(row.grade)}</td><td>{escape(row.open_rate)}</td><td>{escape(row.finish_rate)}</td><td>{escape(row.finish_time)}</td><td>{position(row.position)}</td><td><textarea name="analysis_remark_{index}">{escape(edited_remarks.get(index, row.remark) if isinstance(edited_remarks, dict) else row.remark)}</textarea></td></tr>'
        for index, row in enumerate(report.rows)
    )
    return f"""
<section class="metrics">
  <article><span>SA 占比 {render_delta(report.sa_delta, higher_is_better=True)}</span><strong>{escape(report.sa_ratio)}</strong><small>历史均值 {escape(report.sa_history_avg)} · OKR {escape(report.sa_okr)}</small></article>
  <article><span>CD 占比 {render_delta(report.cd_delta, higher_is_better=False)}</span><strong>{escape(report.cd_ratio)}</strong><small>CD历史均值 {escape(report.cd_history_avg)}</small></article>
  <article><span>AI占比 {render_delta(report.ai_delta, higher_is_better=False)}</span><strong>{escape(report.ai_ratio)}</strong><small>AI历史均值 {escape(report.ai_history_avg)} · AI OKR {escape(report.ai_okr)}</small></article>
</section>
<section class="panel"><h2>趋势对比折线图</h2>{render_line_chart(report)}</section>
<form method="post" action="/save_analysis">{hidden_context(state)}
<section class="panel"><h2>图片明细与 AI 分析备注</h2><div class="table-wrap"><table><thead><tr><th>图片</th><th>来源</th><th>等级</th><th>开图率</th><th>完成率</th><th>时长</th><th>分发位置</th><th>备注</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section class="panel"><h2>周期内容分析</h2><textarea class="wide" name="cycle_summary">{escape(cycle_summary)}</textarea><h2>下一步 todo 和建议</h2><textarea class="wide" name="next_todo">{escape(next_todo)}</textarea><button class="primary">保存分析修改</button></section>
</form>
"""


def render_weekly_review(agent: PuzzleOpsAgent, state: AppState) -> str:
    review = agent.weekly_review_workbench(state.country)
    context = hidden_context(state, view="weekly_review")
    action = (
        f'<form method="post" action="/confirm_weekly_review_needs">{context}<button class="primary">确认生成提需清单</button></form>'
        if can_write_country(state.user_id, state.country)
        else ""
    )
    readonly_note = "" if can_write_country(state.user_id, state.country) else "<p class='muted'>当前国家只读，仅可查看复盘，不可生成提需。</p>"
    return f"""
<section class="panel">
  <div class="section-line"><h2>周三复盘工作台</h2><div class="inline-actions">{action}</div></div>
  {render_sync_message(state)}
  <p class="muted">数据源：{escape(str(review.get('source', '')))}；周期：{escape(str(review.get('period', '')))}</p>
  <p>{escape(str(review.get('summary', '')))}</p>
  {readonly_note}
</section>
<section class="grid two">
  <div class="panel"><h2>新增 S/A 图</h2>{render_weekly_review_items(review.get("new_sa_images", ()))}</div>
  <div class="panel"><h2>下降图</h2>{render_weekly_review_items(review.get("declining_images", ()))}</div>
</section>
<section class="grid two">
  <div class="panel"><h2>可复用 tag</h2>{render_tag_review_rows(review.get("reusable_tags", ()), mode="reuse")}</div>
  <div class="panel"><h2>应停用 tag</h2>{render_tag_review_rows(review.get("retire_tags", ()), mode="retire")}</div>
</section>
<section class="panel"><h2>国家差异</h2>{render_country_diff_rows(review.get("country_differences", ()))}</section>
<section class="panel"><h2>复盘提需建议</h2>{render_need_suggestion_rows(review.get("need_suggestions", ()))}</section>
"""


def render_weekly_review_items(items: object) -> str:
    rows = []
    for item in items if isinstance(items, (tuple, list)) else ():
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('image_id', '')))}</td>"
            f"<td>{grade(str(item.get('grade', '')))}</td>"
            f"<td>{escape(str(item.get('operation_tag', '')))}</td>"
            f"<td>{escape(str(item.get('subject', '')))}</td>"
            f"<td>{escape(str(item.get('js_category', '')))}</td>"
            f"<td>{position(int(item.get('position', 0) or 0))}</td>"
            f"<td>{escape(str(item.get('reason', '')))}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="empty">暂无。</p>'
    return "<div class='table-wrap'><table><thead><tr><th>图片</th><th>等级</th><th>运营tag</th><th>主体</th><th>JS分类</th><th>位置</th><th>原因</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def render_tag_review_rows(items: object, *, mode: str) -> str:
    rows = []
    for item in items if isinstance(items, (tuple, list)) else ():
        if not isinstance(item, dict):
            continue
        signal = f"SA {item.get('sa_count', 0)}" if mode == "reuse" else f"C/D {item.get('cd_count', 0)}"
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('operation_tag', '')))}</td>"
            f"<td>{escape(str(item.get('subject', '')))}</td>"
            f"<td>{escape(str(item.get('js_category', '')))}</td>"
            f"<td>{escape(signal)}</td>"
            f"<td>{escape(str(item.get('reason', '')))}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="empty">暂无。</p>'
    return "<div class='table-wrap'><table><thead><tr><th>运营tag</th><th>主体</th><th>JS分类</th><th>信号</th><th>建议</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def render_country_diff_rows(items: object) -> str:
    rows = []
    for item in items if isinstance(items, (tuple, list)) else ():
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('js_category', '')))}</td>"
            f"<td>{escape(str(item.get('country', '')))} SA {float(item.get('sa_rate', 0)):.0%}</td>"
            f"<td>{escape(str(item.get('compare_country', '')))} SA {float(item.get('compare_sa_rate', 0)):.0%}</td>"
            f"<td>{float(item.get('delta', 0)):+.0%}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="empty">暂无国家差异。</p>'
    return "<div class='table-wrap'><table><thead><tr><th>JS分类</th><th>当前国家</th><th>对比国家</th><th>差异</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def render_need_suggestion_rows(items: object) -> str:
    rows = []
    for item in items if isinstance(items, (tuple, list)) else ():
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('operation_tag', '')))}</td>"
            f"<td>{escape(str(item.get('subject', '')))}</td>"
            f"<td>{escape(str(item.get('js_category', '')))}</td>"
            f"<td>{escape(str(item.get('description', '')))}</td>"
            f"<td>{escape(str(item.get('reason', '')))}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="empty">暂无提需建议。</p>'
    return "<div class='table-wrap'><table><thead><tr><th>运营tag</th><th>主体</th><th>JS分类</th><th>主体描述</th><th>推荐原因</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def render_value(agent: PuzzleOpsAgent, state: AppState) -> str:
    grade_filter = "" if state.value_grade == "all" else state.value_grade
    tabs = "".join(f'<a class="pill {"active" if grade == state.value_grade else ""}" href="{href(state, view="value", value_grade=grade)}">{grade}</a>' for grade in ("all", "S", "A", "B", "C", "D"))
    candidates = agent.undistributed_value_candidates(state.country, grade_filter)
    decisions = {str(item.get("candidate_id", "")): item for item in agent.value_candidate_decisions(state.country)}
    cards = "".join(render_undistributed_candidate_card(agent, candidate, state, decisions.get(str(candidate.get("candidate_id", "")))) for candidate in candidates)
    selected_pool = render_value_selected_pool(agent, state, decisions)
    benchmark_entry = render_value_prediction_benchmark_entry(state, candidates)
    rules = "".join(f"<li><strong>{escape(title)}</strong>：{escape(body)}</li>" for title, body in agent.value_rules(state.country))
    context = hidden_context(state, view="value")
    progress = render_value_prediction_job_progress(state)
    write_controls = ""
    if can_write_country(state.user_id, state.country):
        write_controls = f"""
        <div class="section-line">
          <form method="post" action="/import_value_candidates_excel">{context}<button>导入候选图 Excel</button></form>
          <form method="post" action="/predict_value_candidates">{context}<button class="primary">批量预测当前国家</button></form>
        </div>
        """
    else:
        write_controls = '<p class="note">当前国家只读，可查看候选图与预测结果，不能触发预测或保存人工决策。</p>'
    return f"""
<section class='panel'>
  <h2>未分发候选排图池</h2>
  {render_sync_message(state)}
  <p class='note'>候选图来自桌面 Excel 真实未分发候选池；所有等级、SA概率、开图率、完成率、完成时长均为预测值，只用于人工排图参考。</p>
  {write_controls}
  {progress}
  {selected_pool}
  {benchmark_entry}
  <div class='pills'>{tabs}</div>
  <div class='cards value-candidate-grid'>{cards or '<p class="empty">当前等级暂无候选图。</p>'}</div>
</section>
<section class='panel'><details><summary>查看完整价值观规则库</summary><ul>{rules}</ul></details></section>
"""


def render_value_prediction_benchmark_entry(state: AppState, candidates: tuple[dict[str, object], ...]) -> str:
    if not state.show_value_benchmark:
        return f"""
<section class="panel compact-panel">
  <div class="section-line">
    <div>
      <h2>价值观预测评测</h2>
      <p class="note">日常排图默认不展开；只有调试价值观 Prompt、RAG 证据或微调模型时再打开。</p>
    </div>
    <a class="button" href="{href(state, view='value', show_value_benchmark='1')}">展开价值观预测评测</a>
  </div>
</section>
"""
    candidate_options = "".join(
        f'<label class="demand-check value-benchmark-option" data-search="{escape((str(candidate.get("candidate_id", "")) + " " + str(candidate.get("operation_tag", ""))).lower())}"><input type="checkbox" name="candidate_id" value="{escape(str(candidate.get("candidate_id", "")))}">'
        f'{escape(str(candidate.get("candidate_id", "")))} · {escape(str(candidate.get("operation_tag", "")))}</label>'
        for candidate in candidates
    )
    benchmark_results = render_value_prediction_benchmark_results(state)
    return f"""
<section class="panel compact-panel">
  <div class="section-line">
    <div>
      <h2>价值观预测评测已开启</h2>
      <p class="note">勾选 5-10 张候选图，生成单模型预测评分表；日常排图不需要操作这里。</p>
    </div>
    <a class="button" href="{href(state, view='value', show_value_benchmark='')}">收起价值观预测评测</a>
  </div>
  <form method="post" action="/generate_value_prediction_benchmark">
    {hidden_context(state, view='value')}
    <div class="inline-actions benchmark-filter-bar">
      <input id="value-benchmark-filter" placeholder="筛选候选ID或tag，例如 002、003、香水" oninput="filterValueBenchmarkCandidates(this.value)">
      <button type="button" onclick="selectVisibleValueBenchmarkCandidates(true)">全选可见候选</button>
      <button type="button" onclick="selectVisibleValueBenchmarkCandidates(false)">清空可见候选</button>
    </div>
    <div class="benchmark-select-list">{candidate_options or '<p class="empty">暂无候选图。</p>'}</div>
    <button class="primary">生成价值观预测评测</button>
  </form>
  {benchmark_results}
  <script>
  function filterValueBenchmarkCandidates(query) {{
    var needle = String(query || '').toLowerCase().trim();
    document.querySelectorAll('.value-benchmark-option').forEach(function(item) {{
      item.style.display = !needle || item.dataset.search.indexOf(needle) >= 0 ? 'inline-flex' : 'none';
    }});
  }}
  function selectVisibleValueBenchmarkCandidates(checked) {{
    document.querySelectorAll('.value-benchmark-option').forEach(function(item) {{
      if (item.style.display !== 'none') {{
        var input = item.querySelector('input[type="checkbox"]');
        if (input) input.checked = checked;
      }}
    }});
  }}
  </script>
</section>
"""


def render_value_prediction_benchmark_results(state: AppState) -> str:
    if not state.value_prediction_benchmarks:
        return ""
    score_names = ("图像主体准确性", "国家价值观适配", "历史依据合理性", "RAG citation 有用性", "风险识别", "预测等级可信度", "指标区间可信度", "排图建议可执行性")
    cards = []
    for index, item in enumerate(state.value_prediction_benchmarks):
        baseline_scores = "".join(
            f'<label><span>{name}</span><input class="small-input" name="baseline_value_score_{index}_{score_index}" placeholder="1-5"></label>'
            for score_index, name in enumerate(score_names)
        )
        baseline_output = str(item.get("baseline_output", ""))
        cards.append(
            f"""
<article class="comparison-card">
  <input type="hidden" name="candidate_id_{index}" value="{escape(str(item.get('candidate_id', '')))}">
  <input type="hidden" name="operation_tag_{index}" value="{escape(str(item.get('operation_tag', '')))}">
  <input type="hidden" name="baseline_output_{index}" value="{escape(baseline_output)}">
  <div class="benchmark-head"><div><h3>{escape(str(item.get('candidate_id', '')))}</h3><small>{escape(str(item.get('operation_tag', '')))}</small></div><span class="pill">value_model_current</span></div>
  <div class="benchmark-output"><strong>模型预测输出</strong><p>{escape(baseline_output)}</p></div>
  <details class="benchmark-score-details"><summary>填写 8 维评分</summary>
    <h4>模型预测评分</h4>
    <div class="score-grid">{baseline_scores}<label><span>最终标签</span>{select(f"baseline_label_{index}", ("可直接用", "轻微修改", "需要大改", "不可用"), "轻微修改")}</label></div>
  </details>
</article>
"""
        )
    return f"""
<form method="post" action="/save_value_prediction_benchmark">
  {hidden_context(state, view='value')}
  <input type="hidden" name="benchmark_count" value="{len(state.value_prediction_benchmarks)}">
  <div class="benchmark-list">{''.join(cards)}</div>
  <div class="section-line benchmark-save-line"><p class="note">可先逐张填写 8 维评分，全部完成后一次性保存。</p><button class="primary">批量保存价值观评分</button></div>
</form>
"""


def render_value_selected_pool(agent: PuzzleOpsAgent, state: AppState, decisions: dict[str, dict[str, object]]) -> str:
    selected_ids = {
        candidate_id
        for candidate_id, decision in decisions.items()
        if str(decision.get("decision", "")) == "优先排图"
    }
    if not selected_ids:
        return '<section class="value-selected-pool"><div class="section-line"><h3>本周排图候选池</h3><small>点击候选卡“加入下周排图池”后会出现在这里；最终排图仍在公司 CMS 完成。</small></div><p class="empty">暂无已加入候选。</p></section>'
    candidates = {str(item.get("candidate_id", "")): item for item in agent.undistributed_value_candidates(state.country)}
    cards = []
    for candidate_id in sorted(selected_ids):
        candidate = candidates.get(candidate_id)
        decision = decisions.get(candidate_id, {})
        note = str(decision.get("human_note", ""))
        if candidate:
            image = candidate["image"]
            cards.append(
                "<article class='value-selected-item'>"
                f"{visual_thumb(image.thumb, image.title)}"
                f"<strong>{escape(candidate_id)}</strong>"
                f"<span class='status-pill'>已加入排图池</span>"
                f"<small>{escape(str(candidate.get('operation_tag', '')))} · {escape(str(candidate.get('predicted_grade', '待预测')))} · {escape(str(decision.get('decision', '')))}</small>"
                f"<p>{escape(note or '无人工备注')}</p>"
                "</article>"
            )
        else:
            cards.append(
                "<article class='value-selected-item'>"
                f"<strong>{escape(candidate_id)}</strong><span class='status-pill'>已加入排图池</span><p>{escape(note or 'Excel 中暂未找到该候选图')}</p>"
                "</article>"
            )
    return f"<section class='value-selected-pool'><div class='section-line'><h3>本周排图候选池</h3><small>这里是价值观大师预测后的人工候选清单；最终排图仍在公司 CMS 完成。</small></div><div class='value-selected-grid'>{''.join(cards)}</div></section>"


def render_undistributed_candidate_card(agent: PuzzleOpsAgent, candidate: dict[str, object], state: AppState, decision: dict[str, object] | None = None) -> str:
    image = candidate["image"]
    probability = f"{float(candidate['sa_probability']) * 100:.0f}%"
    visual_subject = str(candidate.get("visual_subject") or candidate.get("subject") or "待视觉解析")
    visual_scene = str(candidate.get("visual_scene") or candidate.get("scene") or "")
    visual_style = str(candidate.get("visual_style") or candidate.get("style_keywords") or "")
    prediction_status = str(candidate.get("prediction_status") or "pending")
    status_label = {
        "predicted": "已预测",
        "pending": "待预测",
        "missing_image": "图片缺失/无法预测",
        "missing_vision_model": "未配置真实 Qwen3-VL",
        "failed": "预测失败",
    }.get(prediction_status, prediction_status)
    decision = decision or {}
    selected_badge = '<span class="status-pill">已加入排图池</span>' if str(decision.get("decision", "")) == "优先排图" else ""
    decision_note = render_value_candidate_decision_note(decision)
    rag_citations = candidate.get("rag_citations", ())
    citation_details = candidate.get("rag_citation_details", ())
    if not citation_details and isinstance(rag_citations, (tuple, list)):
        citation_details = agent.rag_citation_details(state.country, tuple(str(item) for item in rag_citations))
    citation_chips = render_citation_cards(rag_citations, citation_details)
    similar_good = candidate.get("similar_positive", ())
    similar_bad = candidate.get("similar_negative", ())
    similar_good_copy = render_similar_history_items(similar_good)
    similar_bad_copy = render_similar_history_items(similar_bad)
    visual_similarity_copy = render_visual_similarity_evidence(candidate.get("visual_similarity_evidence", {}))
    risk_badges = render_value_risk_badges(candidate.get("risk_points", ()))
    decision_actions = render_value_candidate_decision_actions(candidate, state, decision)
    retry_action = render_value_candidate_retry_action(candidate, state)
    evidence_summary = compact_text(str(candidate.get("evidence", "")), 92)
    metric_levels = candidate.get("metric_levels", {})
    metric_level_copy = ""
    if isinstance(metric_levels, dict) and metric_levels:
        metric_level_copy = (
            f"<small>指标分档 开图={escape(str(metric_levels.get('open_rate', '')))} · "
            f"完成={escape(str(metric_levels.get('completion_rate', '')))} · "
            f"时长={escape(str(metric_levels.get('avg_finish_time', '')))}</small>"
        )
    return f"""
<article class='image-card candidate-card'>
  {visual_thumb(image.thumb, image.title)}
  <div class="candidate-title"><strong>{escape(str(candidate['candidate_id']))}</strong>{selected_badge}</div>
  {decision_note}
  <p>{grade(str(candidate['predicted_grade']))} 预测等级 · 预测SA概率 {probability} · {escape(status_label)}</p>
  <small>预测开图率 {escape(str(candidate['open_rate_range']))} · 预测完成率 {escape(str(candidate['completion_rate_range']))} · 预测完成时长 {escape(str(candidate['finish_time_range']))}</small>
  {metric_level_copy}
  <p><strong>{escape(str(candidate.get('js_category', '')))}</strong> · {escape(str(candidate.get('operation_tag', '')))} · {escape(str(candidate.get('candidate_source', '')))}</p>
  <p class="candidate-summary">视觉主体：{escape(compact_text(visual_subject, 34))}</p>
  <p>{escape(str(candidate['action']))}</p>
  {risk_badges}
  <p class="candidate-evidence-summary">预测理由：{escape(evidence_summary)}</p>
  <details class="candidate-details"><summary>展开视觉解析</summary><dl><div><dt>主体</dt><dd>{escape(visual_subject)}</dd></div><div><dt>场景</dt><dd>{escape(visual_scene or '待预测后生成')}</dd></div><div><dt>风格</dt><dd>{escape(visual_style or '待预测后生成')}</dd></div></dl></details>
  {visual_similarity_copy}
  <details class="candidate-details"><summary>展开相似历史图</summary><div><strong>相似历史好图</strong>{similar_good_copy}</div><div><strong>相似历史风险图</strong>{similar_bad_copy}</div></details>
  <details class="candidate-details"><summary>展开 RAG 依据</summary>{citation_chips}<p>{escape(str(candidate['evidence']))}</p></details>
  {retry_action}
  {decision_actions}
</article>
"""


def render_visual_similarity_evidence(evidence: object) -> str:
    if not isinstance(evidence, dict) or not evidence:
        return ""
    message = str(evidence.get("message", "") or "")
    reliability = str(evidence.get("reliability", "") or evidence.get("status", ""))
    best_score = evidence.get("best_score", "")
    min_reference_score = evidence.get("min_reference_score", "")
    good = tuple(item for item in evidence.get("similar_good", ()) or () if isinstance(item, dict))
    risk = tuple(item for item in evidence.get("similar_risk", ()) or () if isinstance(item, dict))
    good_items = "".join(render_visual_similarity_hit(item) for item in good) or "<p>暂无可靠相似好图。</p>"
    risk_items = "".join(render_visual_similarity_hit(item) for item in risk) or "<p>暂无可靠相似风险图。</p>"
    score_line = ""
    if best_score != "":
        score_line = f"<p>最高相似分：{escape(str(best_score))} · 校准提示线：{escape(str(min_reference_score))}</p>"
    message_line = f"<p>{escape(message)}</p>" if message else ""
    return (
        '<details class="candidate-details">'
        "<summary>展开图像相似依据</summary>"
        f"<p>可靠性：{escape(reliability or '未评估')}</p>"
        f"{score_line}{message_line}"
        f"<div><strong>通过校准的相似好图</strong>{good_items}</div>"
        f"<div><strong>通过校准的相似风险图</strong>{risk_items}</div>"
        "</details>"
    )


def render_visual_similarity_hit(hit: dict[str, object]) -> str:
    return (
        "<p>"
        f"{escape(str(hit.get('operation_tag', '') or hit.get('image_id', '')))}"
        f" · 等级{escape(str(hit.get('grade', '')))}"
        f" · score={escape(str(hit.get('score', '')))}"
        f" · {escape(str(hit.get('gate_reason', '') or hit.get('reason', '')))}"
        "</p>"
    )


def render_value_candidate_decision_note(decision: dict[str, object]) -> str:
    decision_value = str(decision.get("decision", ""))
    note = str(decision.get("human_note", ""))
    if not decision_value:
        return ""
    return f'<p class="candidate-human-note">人工决策：{escape(decision_value)}{("；" + escape(note)) if note else ""}</p>'


def render_value_candidate_decision_actions(candidate: dict[str, object], state: AppState, decision: dict[str, object] | None = None) -> str:
    if not can_write_country(state.user_id, state.country):
        return '<div class="section-line"><small>只读国家不可保存人工决策</small></div>'
    current_decision = decision or {}
    context = hidden_context(state, view="value")
    candidate_id = escape(str(candidate.get("candidate_id", "")))
    note = '<input name="decision_note" placeholder="人工备注，可空">'
    buttons = (
        ("优先排图", "加入下周排图池"),
        ("人工看好", "标记人工看好"),
        ("人工复核", "要求修改"),
    )
    forms = []
    for decision_value, label in buttons:
        disabled = " disabled" if decision_value == "优先排图" and str(current_decision.get("decision", "")) == "优先排图" else ""
        button_label = "已加入" if disabled else label
        forms.append(
            f'<form method="post" action="/save_value_candidate_decision">{context}'
            f'<input type="hidden" name="candidate_id" value="{candidate_id}">'
            f'<input type="hidden" name="decision" value="{escape(decision_value)}">'
            f'{note}<button{disabled}>{escape(button_label)}</button></form>'
        )
    return '<div class="section-line">' + "".join(forms) + "</div>"


def render_value_candidate_retry_action(candidate: dict[str, object], state: AppState) -> str:
    if not can_write_country(state.user_id, state.country):
        return ""
    return (
        '<form class="single-retry-form" method="post" action="/predict_single_value_candidate">'
        f'{hidden_context(state, view="value")}'
        f'<input type="hidden" name="candidate_id" value="{escape(str(candidate.get("candidate_id", "")))}">'
        '<button>重新预测此图</button>'
        "</form>"
    )


def render_citation_chips(citations: object) -> str:
    if not isinstance(citations, (tuple, list)) or not citations:
        return '<p class="empty">待预测后生成</p>'
    chips = []
    for item in citations[:4]:
        text = str(item)
        label = text.split("#", 1)[0].replace("GLOBAL_KB_", "").replace("AUDIT_", "审核 ")
        chips.append(f'<span class="citation-chip" title="{escape(text)}">{escape(compact_text(label, 24))}</span>')
    return '<div class="citation-chip-row">' + "".join(chips) + "</div>"


def render_citation_cards(citations: object, details: object) -> str:
    if isinstance(details, (tuple, list)) and details:
        cards = []
        for item in details[:4]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or item.get("parent_id", "") or item.get("chunk_id", ""))
            source_type = str(item.get("source_type", ""))
            chunk_id = str(item.get("chunk_id", ""))
            text = compact_text(str(item.get("text", "")), 120)
            source_label = _citation_source_label(source_type)
            cards.append(
                "<article class='citation-card'>"
                f"<strong>{escape(source_label)}：{escape(title)}</strong>"
                f"<p>{escape(text or '暂无摘要；可用原始 chunk id 追踪。')}</p>"
                f"<small>{escape(chunk_id)}</small>"
                "</article>"
            )
        if cards:
            return "<div class='citation-card-list'>" + "".join(cards) + "</div>"
    return render_citation_chips(citations)


def _citation_source_label(source_type: str) -> str:
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
    }
    return labels.get(source_type, source_type or "RAG依据")


def render_similar_history_items(items: object) -> str:
    if not isinstance(items, (tuple, list)) or not items:
        return '<p class="empty">待预测后生成</p>'
    rows = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<li>"
            f"{escape(str(item.get('image_id', '')))} · {escape(str(item.get('operation_tag', '')))} · {escape(str(item.get('grade', '')))}"
            f"<small>开图 {escape(str(item.get('open_rate', '')))} · 完成 {escape(str(item.get('completion_rate', '')))} · {escape(str(item.get('avg_finish_time', '')))}</small>"
            "</li>"
        )
    return "<ul class='candidate-history-list'>" + "".join(rows) + "</ul>"


def render_value_risk_badges(risks: object) -> str:
    if not isinstance(risks, (tuple, list)) or not risks:
        return '<div class="risk-badges"><span>低风险</span></div>'
    return '<div class="risk-badges">' + "".join(f"<span>{escape(str(risk))}</span>" for risk in risks[:4]) + "</div>"


def compact_text(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def render_value_prediction_job_progress(state: AppState) -> str:
    if not state.value_prediction_job_status:
        return ""
    progress = max(0, min(100, int(state.value_prediction_job_progress or 0)))
    status = state.value_prediction_job_status
    message = state.value_prediction_job_message or "价值观大师后台预测处理中"
    return f"""
<section class="panel derivative-job-panel">
  <div class="section-line"><h2>价值观大师预测进度</h2><span class="status-pill">{escape(status)}</span></div>
  <progress value="{progress}" max="100"></progress>
  <p class="note">{escape(message)}</p>
  <small>页面会每 3 秒自动刷新；预测完成后候选卡会显示预测等级、指标区间、RAG citation 和相似历史依据。</small>
</section>
"""


def render_runtime(agent: PuzzleOpsAgent, state: AppState) -> str:
    profile = agent.multimodal_profile(state.country)
    feature = profile.feature
    vision_status = agent.vision_llm_status()
    candidates = agent.value_rule_candidates(state.country)
    approved_rules = agent.approved_value_rules(state.country)
    memories = agent.hitl_memories(state.country)
    memory_overview = agent.memory_overview(state.country)
    rag_summary = agent.value_audit_rag_summary(state.country)
    good = "".join(render_record_card(record) for record in profile.similar_good_cases)
    bad = "".join(render_record_card(record) for record in profile.similar_bad_cases)
    candidate_rows = "".join(
        f'<tr><td>{escape(candidate.rule_text)}</td><td>{escape(str(candidate.confidence))}</td><td>{candidate.support_count}</td><td>{candidate.counterexample_count}</td><td>{escape(candidate.status)}</td><td>{escape(candidate.agent_reason)}</td><td><form method="post" action="/approve_value_candidate">{hidden_context(state)}<input type="hidden" name="candidate_id" value="{escape(candidate.candidate_id)}"><input name="human_note" value="运营确认加入固定价值观"><button>通过</button></form></td></tr>'
        for candidate in candidates
    )
    approved_rows = "".join(
        f'<tr><td>{escape(str(rule["country"]))}</td><td>{escape(str(rule["rule_text"]))}</td><td>{escape(str(rule["status"]))}</td></tr>'
        for rule in approved_rules
    )
    memory_items = "".join(f'<li>{escape(str(memory["content"]))}</li>' for memory in memories)
    memory_overview_cards = render_memory_overview(memory_overview)
    memory_filters = memory_filter_values(state)
    memory_workbench_data = agent.memory_workbench(state.country, filters=memory_filters)
    memory_workbench = render_memory_workbench(memory_workbench_data, state, memory_filters)
    rag_cards = render_rag_summary(rag_summary, state)
    rag_actions = render_rag_runtime_actions(agent, state)
    memory_debug = agent.memory_debug(state.country, query=feature.main_subject)
    memory_debug_rows = render_memory_debug_rows(memory_debug, state)
    memory_conflicts = render_memory_conflicts(agent.memory_conflicts(state.country), state)
    provenance_root = int(memory_debug[0].get("memory_id", 0)) if memory_debug else 0
    memory_provenance = render_memory_provenance(agent.memory_provenance(state.country, provenance_root) if provenance_root else {})
    guarded_action_data = agent.guarded_action_workbench(state.country)
    guarded_actions = render_guarded_actions_workbench(guarded_action_data, state)
    skill_center = render_skill_center(agent.business_skill_contracts(), state)
    tools_console_data = agent.tools_console(state.country)
    tools_console = render_tools_console(tools_console_data)
    sync_history = render_feishu_lightweight_sync_history(agent, state)
    governance_overview = render_runtime_governance_overview(
        agent,
        state,
        vision_status,
        memory_overview,
        memory_workbench_data,
        rag_summary,
        guarded_action_data,
        tools_console_data,
    )
    return f"""
<section class="panel">
  <h2>系统治理中心</h2>
  <p class="note">这个页面用来确认 Agent 的知识、RAG、工具链和审批链路是否健康。</p>
  {render_sync_message(state)}
</section>
<details class="governance-section" open><summary>总览</summary>
  <p class="note">默认只看这里：系统健康、今日待处理、最近风险和快捷入口。</p>
  {governance_overview}
</details>
<details id="memory治理" class="governance-section"><summary>Memory 治理</summary>
  <p class="note">审核、批准、停用和排查 Memory，避免未确认或冲突知识进入 RAG。</p>
  <section class="panel"><h2>四层 Memory 概览</h2><div class="memory-grid">{memory_overview_cards}</div></section>
  <section class="panel"><h2>Memory 工作台</h2>{memory_workbench}</section>
  <section class="panel"><h2>Memory Conflict</h2>{memory_conflicts}</section>
  <section class="panel"><h2>Memory Provenance</h2>{memory_provenance}</section>
  <section class="panel"><h2>HITL Memory</h2><ul>{memory_items or '<li>暂无人工反馈记忆。</li>'}</ul></section>
</details>
<details id="rag治理" class="governance-section"><summary>RAG 治理</summary>
  <p class="note">查看 RAG 命中、反馈、补丁、重建和验收能力；高风险操作保留在这里。</p>
  <section class="panel"><div class="section-line"><h2>价值观与审核 RAG</h2><div class="actions">{rag_actions}</div></div>{rag_cards}</section>
</details>
<details id="toolsactions" class="governance-section"><summary>Tools / Actions</summary>
  <p class="note">查看工具注册、连接健康、最近调用和外部写入审批链路。</p>
  <section class="panel"><h2>Tools Console</h2>{tools_console}</section>
  <section class="panel"><h2>Guarded Actions</h2>{guarded_actions}</section>
  <details class="compact-tools"><summary>飞书同步轻量历史</summary>{sync_history}</details>
</details>
<details class="governance-section"><summary>Skill Center</summary>
  <p class="note">查看 5 个业务 Skill 的输入、RAG 来源、Memory 写入和验收指标。</p>
  <section class="panel"><h2>Skill Center</h2>{skill_center}</section>
</details>
<details id="debug" class="governance-section"><summary>Debug</summary>
  <p class="note">研发排查区：图像 profile、价值观候选、Memory 明细和底层证据。</p>
  <section class="panel">
    <h2>ImageProfile Debug</h2>
    <div class="grid two">
      <div><h3>ImageProfile</h3><dl class="detail">
      <div><dt>图片ID</dt><dd>{escape(profile.asset.image_id)}</dd></div>
      <div><dt>运营tag</dt><dd>{escape(profile.asset.operation_tag)}</dd></div>
      <div><dt>主体</dt><dd>{escape(feature.main_subject)}</dd></div>
      <div><dt>色彩</dt><dd>{escape('、'.join(feature.color_palette))}</dd></div>
      <div><dt>构图</dt><dd>{escape(feature.composition)}</dd></div>
      <div><dt>明暗/饱和/冷暖</dt><dd>{escape(feature.brightness_level)} · {escape(feature.saturation_level)} · {escape(feature.temperature)}</dd></div>
      <div><dt>拼图友好度</dt><dd>{escape(feature.puzzle_readability)}</dd></div>
      <div><dt>Caption</dt><dd>{escape(feature.caption)}</dd></div>
    </dl></div>
    <div><h3>图文融合指标</h3><dl class="detail">
      <div><dt>等级</dt><dd>{grade(str(profile.historical_metrics["grade"]))}</dd></div>
      <div><dt>开图率</dt><dd>{profile.historical_metrics["open_rate"]}</dd></div>
      <div><dt>完成率</dt><dd>{profile.historical_metrics["completion_rate"]}</dd></div>
      <div><dt>完成时长</dt><dd>{profile.historical_metrics["avg_finish_time"]}</dd></div>
      <div><dt>本地质量标签</dt><dd>{escape('、'.join(feature.visual_quality_tags) or '未发现明显本地质量风险')}</dd></div>
      <div><dt>风险标签</dt><dd>{escape('、'.join(feature.risk_tags) or '无')}</dd></div>
      <div><dt>视觉 LLM 适配器</dt><dd>{vision_mode_copy(vision_status)}</dd></div>
    </dl></div>
    </div>
  </section>
  <section class="grid two">
    <div class="panel"><h2>相似历史好图</h2><div class="cards">{good}</div></div>
    <div class="panel"><h2>相似历史坏图</h2><div class="cards">{bad}</div></div>
  </section>
  <section class="panel"><h2>价值观候选池</h2><p class="note">从历史样本中挖出的候选价值观规则，人工通过后进入长期治理链路。</p><div class="table-wrap"><table><thead><tr><th>候选价值观</th><th>置信度</th><th>支撑样本</th><th>反例样本</th><th>状态</th><th>Agent归因</th><th>运营审核</th></tr></thead><tbody>{candidate_rows}</tbody></table></div></section>
  <section class="panel"><h2>已审批价值观规则</h2><div class="table-wrap"><table><thead><tr><th>国家</th><th>规则</th><th>状态</th></tr></thead><tbody>{approved_rows or '<tr><td colspan="3">暂无已审批规则，点击上方候选池“通过”后会写入这里。</td></tr>'}</tbody></table></div></section>
  <section class="panel"><h2>Memory Debug</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>层级/类型</th><th>状态</th><th>审核状态</th><th>进入RAG</th><th>创建人</th><th>批准人</th><th>更新时间</th><th>RAG Source</th><th>命中分</th><th>冲突</th><th>来源</th><th>记忆内容</th><th>治理</th></tr></thead><tbody>{memory_debug_rows}</tbody></table></div></section>
</details>
"""


def render_runtime_governance_overview(
    agent: PuzzleOpsAgent,
    state: AppState,
    vision_status: dict[str, object],
    memory_overview: dict[str, dict[str, object]],
    memory_workbench: dict[str, object],
    rag_summary: dict[str, object],
    guarded_actions: dict[str, object],
    tools_console: dict[str, object],
) -> str:
    generation_status = agent.generation_provider_status()
    vector_provider = vector_store_label(agent.rag_vector_store_config.provider or "sqlite")
    feishu_status = "可用" if getattr(agent.feishu, "allow_real_sync", False) else "Mock / 未真实写入"
    tool_failed = len(tuple(item for item in tools_console.get("recent_invocations", ()) if isinstance(item, dict) and not item.get("success")))
    guarded_groups = guarded_actions.get("groups", {}) if isinstance(guarded_actions, dict) else {}
    pending_actions = len(guarded_groups.get("pending", ()) if isinstance(guarded_groups, dict) else ())
    pending_memory = len(memory_workbench.get("pending_review", ()) if isinstance(memory_workbench, dict) else ())
    conflict_count = len(memory_workbench.get("conflicts", ()) if isinstance(memory_workbench, dict) else ())
    feedback_summary = agent.rag_feedback_summary(state.country)
    not_useful = int(feedback_summary.get("not_useful_total", 0) or 0)
    rag_chunks = int(rag_summary.get("chunk_count", 0) or 0)
    memory_total = sum(int(item.get("count", 0) or 0) for item in memory_overview.values() if isinstance(item, dict))
    write_countries = production_write_countries()
    readonly_countries = tuple(country for country, _ in LOGIN_COUNTRIES if country not in write_countries)
    production_cards = (
        ("生产运行目录", str(agent._runtime_dir), "SQLite、候选图、上传图、预测缓存、Harness 和 RAG 反馈都应在这里持久化。"),
        ("明日可写国家", "、".join(write_countries), "日本、法国开放真实运营写入，其余国家先只读。"),
        ("灰度只读国家", f"{'、'.join(readonly_countries)}只读", "巴西、俄罗斯、美国只读，避免未接入业务数据时触发生产写入。"),
        ("备份策略", "手动/每日备份", "上线前先备份，运行后每天备份 runtime 目录。"),
    )
    health_cards = (
        ("Qwen 视觉", vision_mode_copy(vision_status), "试新解析、价值观大师和 Harness 真实评测依赖它。"),
        ("Qwen 图像生成", generation_mode_copy(generation_status), "好图衍生会产生费用，默认只在明确操作时调用。"),
        ("RAG / 向量库", f"{vector_provider} · chunks {rag_chunks}", "价值观规则、审核规则和 Memory facts 的检索状态。"),
        ("飞书", feishu_status, "常规/试新同步入口状态；真实写入仍走现有权限和审计。"),
        ("工具链", f"失败 {tool_failed} 次", "最近工具调用失败数量，详情在 Tools / Actions。"),
    )
    todo_cards = (
        ("待审核 Memory", pending_memory, "进入 Memory 治理处理。"),
        ("Memory 冲突", conflict_count, "冲突未处理前不应进入 RAG。"),
        ("RAG 无用反馈", not_useful, "进入 RAG 治理查看补丁候选。"),
        ("待确认 Actions", pending_actions, "进入 Tools / Actions 处理外部写入审批。"),
    )
    production_html = "".join(render_overview_card(title, value, detail) for title, value, detail in production_cards)
    health_html = "".join(render_overview_card(title, value, detail) for title, value, detail in health_cards)
    todo_html = "".join(render_overview_card(title, value, detail) for title, value, detail in todo_cards)
    backup_form = (
        f'<form method="post" action="/create_production_backup" class="inline-actions">'
        f'{hidden_context(state, view="runtime")}'
        '<input name="backup_label" value="manual_launch_backup">'
        '<button>立即备份生产数据</button>'
        '</form>'
        if can_write_country(state.user_id, state.country)
        else '<p class="note">只读模式不显示备份按钮；请切换到负责国家后执行备份。</p>'
    )
    return f"""
<div class="governance-overview">
  <section><div class="section-line"><h3>生产上线收口</h3>{backup_form}</div><div class="overview-grid">{production_html}</div></section>
  <section><h3>系统健康</h3><div class="overview-grid">{health_html}</div></section>
  <section><h3>今日待处理</h3><div class="overview-grid">{todo_html}</div></section>
  <section><h3>最近风险</h3><p class="note">工具失败 {tool_failed} 次；RAG 无用反馈 {not_useful} 条；Memory 冲突 {conflict_count} 组；当前 Memory active 总量 {memory_total} 条。</p></section>
  <section><h3>快捷入口</h3><div class="pills"><a class="pill" href="#memory治理">Memory治理</a><a class="pill" href="#rag治理">RAG治理</a><a class="pill" href="#toolsactions">Tools / Actions</a><a class="pill" href="#debug">Debug</a></div></section>
</div>
"""


def render_overview_card(title: str, value: object, detail: str) -> str:
    return f"<article class='overview-card'><strong>{escape(title)}</strong><span>{escape(str(value))}</span><small>{escape(detail)}</small></article>"


def render_tools_console(console: dict[str, object]) -> str:
    catalog = console.get("catalog", ())
    invocations = console.get("recent_invocations", ())
    health = console.get("connector_health", {})
    catalog_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('name', '')))}</td>"
        f"<td>{escape(str(item.get('target_system', '')))}</td>"
        f"<td>{escape(str(item.get('side_effect', '')))}</td>"
        f"<td>{'需要' if item.get('approval_required') else '不需要'}</td>"
        f"<td>{'是' if item.get('country_scoped') else '否'}</td>"
        f"<td>{escape('、'.join(str(skill) for skill in item.get('allowed_skill_ids', ())))}</td>"
        "</tr>"
        for item in catalog
        if isinstance(item, dict)
    )
    invocation_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('tool_name', '')))}</td>"
        f"<td>{escape(str(item.get('actor', '')))}</td>"
        f"<td>{escape(str(item.get('skill_id', '')))}</td>"
        f"<td>{'成功' if item.get('success') else '失败'}</td>"
        f"<td>{escape(str(item.get('latency_ms', '')))}ms</td>"
        f"<td>{escape(str(item.get('error_code', '')))}</td>"
        "</tr>"
        for item in invocations
        if isinstance(item, dict)
    )
    health_cards = ""
    if isinstance(health, dict):
        for name, status in health.items():
            text = status if isinstance(status, str) else json_dumps_compact(status)
            health_cards += f"<article class='memory-card'><strong>{escape(str(name))}</strong><small>{escape(text)}</small></article>"
    return f"""
<div class="grid two">
  <div>
    <h3>Tool Catalog</h3>
    <div class="table-wrap"><table><thead><tr><th>工具</th><th>系统</th><th>读写</th><th>审批</th><th>国家过滤</th><th>Skill 白名单</th></tr></thead><tbody>{catalog_rows or '<tr><td colspan="6">暂无工具。</td></tr>'}</tbody></table></div>
  </div>
  <div>
    <h3>Connector Health</h3>
    <div class="memory-grid">{health_cards}</div>
  </div>
</div>
<h3>Recent Invocations</h3>
<div class="table-wrap"><table><thead><tr><th>工具</th><th>Actor</th><th>Skill</th><th>状态</th><th>耗时</th><th>错误</th></tr></thead><tbody>{invocation_rows or '<tr><td colspan="6">暂无调用记录。</td></tr>'}</tbody></table></div>
"""


def json_dumps_compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def render_skill_center(skills: object, state: AppState) -> str:
    if not isinstance(skills, (list, tuple)) or not skills:
        return "<p class='empty'>暂无业务 Skill 契约。</p>"
    cards = []
    context = hidden_context(state, view="runtime")
    for skill in skills:
        rag_sources = "、".join(getattr(skill, "rag_source_types", ()))
        memory_policy = getattr(skill, "memory_write_policy", {})
        memory_text = f"{memory_policy.get('layer', '-')}/{memory_policy.get('memory_type', '-')}" if isinstance(memory_policy, dict) else "-"
        metrics = "、".join(getattr(skill, "acceptance_metrics", ()))
        guarded = "、".join(getattr(skill, "guarded_tools", ())) or "无外部写入"
        cards.append(
            "<article class='memory-card'>"
            f"<strong>{escape(getattr(skill, 'display_name', ''))}</strong>"
            f"<span>{escape(getattr(skill, 'skill_id', ''))}</span>"
            f"<small>{escape(getattr(skill, 'scenario', ''))}</small>"
            f"<small>RAG source：{escape(rag_sources or '-')}</small>"
            f"<small>Memory 写入：{escape(memory_text)}</small>"
            f"<small>Guarded Action：{escape(guarded)}</small>"
            f"<small>Harness 验收：{escape(metrics or '-')}</small>"
            f'<form method="post" action="/run_business_skill">{context}<input type="hidden" name="skill_id" value="{escape(getattr(skill, "skill_id", ""))}"><button>运行 Demo</button></form>'
            "</article>"
        )
    return "<div class='memory-grid'>" + "".join(cards) + "</div>"


def render_guarded_actions_workbench(workbench: dict[str, object], state: AppState) -> str:
    groups = workbench.get("groups", {})
    if not isinstance(groups, dict):
        groups = {}
    cards = (
        ("待我确认", "pending"),
        ("已批准待执行", "approved"),
        ("已执行", "executed"),
        ("执行失败", "failed"),
        ("已撤销 / 需人工处理", "reverted"),
    )
    card_html = "".join(
        f"<article class='memory-card'><strong>{escape(label)}</strong><span>{len(groups.get(key, ()) if isinstance(groups.get(key, ()), tuple) else ())} 条</span><small>{escape(_guarded_group_preview(groups.get(key, ())))}</small></article>"
        for label, key in cards
    )
    proposals = workbench.get("proposals", ())
    events_by_proposal = workbench.get("events_by_proposal", {})
    if not isinstance(events_by_proposal, dict):
        events_by_proposal = {}
    rows = "".join(render_guarded_action_row(item, state, events_by_proposal.get(item.proposal_id, ())) for item in proposals if hasattr(item, "proposal_id"))
    readonly = "" if can_write_country(state.user_id, state.country) else "<p class='muted'>只读国家仅展示 Guarded Action，不允许批准或执行。</p>"
    return f"""
{readonly}
<div class="memory-grid">{card_html}</div>
<div class="table-wrap"><table><thead><tr><th>Action</th><th>状态</th><th>影响</th><th>来源</th><th>Guard</th><th>审计</th><th>操作</th></tr></thead><tbody>{rows or '<tr><td colspan="7">暂无 Guarded Action。</td></tr>'}</tbody></table></div>
"""


def render_guarded_action_row(proposal, state: AppState, events: object = ()) -> str:
    preview = proposal.payload_preview if isinstance(proposal.payload_preview, dict) else {}
    reasons = "；".join(proposal.guard_reasons) or "待人工确认"
    event_items = ""
    if isinstance(events, (list, tuple)):
        event_items = "".join(
            f"<li>{escape(str(event.get('event_type', '')))}: {escape(str(event.get('new_status', '')))} · {escape(str(event.get('actor', '')))}</li>"
            for event in events
            if isinstance(event, dict)
        )
    event_details = f"<details><summary>查看审计链路</summary><ol>{event_items or '<li>暂无事件</li>'}</ol></details>"
    context = hidden_context(state, view=state.view)
    actions = ""
    if can_write_country(state.user_id, state.country):
        if proposal.guard_status in {"pending_approval", "blocked", "failed"}:
            actions += (
                f'<form method="post" action="/approve_guarded_action">{context}'
                f'<input type="hidden" name="proposal_id" value="{escape(proposal.proposal_id)}">'
                '<input type="hidden" name="execute_after_approval" value="1">'
                '<input name="approval_note" value="运营确认写入">'
                '<button>确认写入飞书</button></form>'
            )
        if proposal.guard_status in {"approved", "executed", "failed"}:
            actions += (
                f'<form method="post" action="/revert_guarded_action">{context}'
                f'<input type="hidden" name="proposal_id" value="{escape(proposal.proposal_id)}">'
                '<input name="revert_note" value="运营撤销">'
                '<button>取消草案</button></form>'
            )
    return (
        "<tr>"
        f"<td>{escape(proposal.proposal_id)}<br><small>{escape(proposal.target_system)} · {escape(proposal.action_type)}</small></td>"
        f"<td>{escape(proposal.guard_status)}<br><small>创建 {escape(proposal.created_at)}</small></td>"
        f"<td>{escape(str(preview.get('row_count', 0)))} 行<br><small>{escape(str(preview.get('first_operation_tag', '')))}</small></td>"
        f"<td>{escape(proposal.source_trace_id)}<br><small>创建 {escape(proposal.actor)}；批准 {escape(proposal.approved_by or '-')}</small></td>"
        f"<td>{escape(proposal.risk_level)}<br><small>{escape(reasons)}</small></td>"
        f"<td>{event_details}</td>"
        f"<td>{actions or '-'}</td>"
        "</tr>"
    )


def _guarded_group_preview(items: object) -> str:
    if not isinstance(items, tuple) or not items:
        return "暂无"
    return "；".join(f"{item.action_type}:{item.guard_status}" for item in items[:2] if hasattr(item, "action_type")) or "暂无"


def render_rag_runtime_actions(agent: PuzzleOpsAgent, state: AppState) -> str:
    context = hidden_context(state, view="runtime")
    provider = (agent.rag_vector_store_config.provider or "sqlite").strip().lower()
    label = vector_store_label(provider)
    actions = [
        f'<form method="post" action="/rebuild_rag_knowledge">{context}<button>重建RAG知识库</button></form>',
        f'<form method="post" action="/export_rag_acceptance_report">{context}<button>导出RAG验收报告</button></form>',
        f'<form method="post" action="/export_rag_ops_report">{context}<button>导出RAG Ops报告</button></form>',
        f'<form method="post" action="/export_rag_eval_failure_feedback">{context}<button>导出RAG失败反馈</button></form>',
        f'<form method="post" action="/export_rag_knowledge_patch_drafts">{context}<button>导出知识补丁草案</button></form>',
        f'<form method="post" action="/export_approved_rag_patch_markdown">{context}<button>导出已审Markdown补丁</button></form>',
        f'<form method="post" action="/apply_approved_rag_patch_markdown">{context}<button>应用已审补丁到raw</button></form>',
        f'<form method="post" action="/apply_approved_rag_patch_and_rebuild">{context}<button>应用补丁并重建RAG</button></form>',
        f'<form method="post" action="/apply_rag_patch_rebuild_and_reindex_vector_store">{context}<button>应用补丁并入库{escape(label)}</button></form>',
        f'<form method="post" action="/rollback_latest_rag_patch_and_rebuild">{context}<button>回滚最新补丁并重建</button></form>',
        f'<form method="post" action="/run_full_rag_acceptance">{context}<button>一键RAG全链路验收</button></form>',
        f'<form method="post" action="/reindex_rag_vector_store">{context}<button>重建并入库{escape(label)}</button></form>',
    ]
    if provider == "qdrant":
        actions.extend(
            (
                f'<form method="post" action="/apply_rag_patch_rebuild_and_reindex_qdrant">{context}<button>应用补丁并入库Qdrant</button></form>',
                f'<form method="post" action="/reindex_rag_qdrant">{context}<button>重建并入库Qdrant</button></form>',
                f'<form method="post" action="/qdrant_smoke_diagnostic">{context}<button>Qdrant Smoke</button></form>',
                f'<form method="post" action="/rollback_qdrant_manifest">{context}<input name="run_id" placeholder="run_id"><label class="inline-check"><input type="checkbox" name="restore_points" value="1">真实恢复 Qdrant points</label><button>回滚Qdrant Run</button></form>',
            )
        )
    elif provider == "milvus":
        actions.append(f'<form method="post" action="/milvus_smoke_diagnostic">{context}<button>Milvus Smoke</button></form>')
    return "".join(actions)


def vector_store_label(provider: str) -> str:
    labels = {"milvus": "Milvus", "qdrant": "Qdrant", "sqlite": "SQLite"}
    return labels.get((provider or "").strip().lower(), provider or "VectorStore")


def render_memory_overview(overview: dict[str, dict[str, object]]) -> str:
    cards = []
    for label in ("感知记忆", "短期记忆", "长期记忆", "结构化事实"):
        item = overview.get(label, {})
        latest = item.get("latest", {}) if isinstance(item, dict) else {}
        payload = latest.get("payload", {}) if isinstance(latest, dict) else {}
        summary = "；".join(f"{key}={value}" for key, value in list(payload.items())[:3]) if isinstance(payload, dict) else ""
        rag_ready = item.get("rag_ready_count", 0) if isinstance(item, dict) else 0
        inactive = item.get("inactive_count", 0) if isinstance(item, dict) else 0
        cards.append(
            f"<article class='memory-card'><strong>{escape(label)}</strong><span>{escape(str(item.get('count', 0) if isinstance(item, dict) else 0))} 条 active</span><small>归档 {escape(str(inactive))} 条；RAG Ready {escape(str(rag_ready))} 条；{escape(summary or '暂无记录')}</small></article>"
        )
    return "".join(cards)


def render_memory_debug_rows(rows: tuple[dict[str, object], ...], state: AppState) -> str:
    if not rows:
        return '<tr><td colspan="14">暂无四层 memory 记录。</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{escape(str(row.get('memory_id', '')))}</td>"
        f"<td>{escape(str(row.get('layer', '')))}<br><small>{escape(str(row.get('memory_type', '')))}</small></td>"
        f"<td>{escape(str(row.get('status', '')))}{' · 人工确认' if row.get('human_verified') else ''}</td>"
        f"<td>{escape(str(row.get('review_status', 'draft')))}</td>"
        f"<td>{'是' if row.get('rag_ready') else '否'}<br><small>{'已允许' if row.get('approved_for_rag') else '未允许'}</small></td>"
        f"<td>{escape(user_label(str(row.get('created_by', ''))) if row.get('created_by') else '-')}</td>"
        f"<td>{escape(user_label(str(row.get('approved_by', ''))) if row.get('approved_by') else '-')}</td>"
        f"<td>{escape(str(row.get('updated_at', '')))}</td>"
        f"<td>{escape(str(row.get('rag_source_type', '')))}</td>"
        f"<td>{escape(str(row.get('match_score', 0)))}<br><small>RAG命中 {escape(str(row.get('rag_hit_count', 0)))}；not useful {escape(str(row.get('not_useful_count', 0)))}</small></td>"
        f"<td>{render_memory_conflict_badges(row)}</td>"
        f"<td>{escape(str(row.get('source_memory_id') or '原始'))}</td>"
        f"<td>{escape(str(row.get('summary', '')))}</td>"
        f"<td>{render_memory_actions(row, state)}</td>"
        "</tr>"
        for row in rows
    )


def memory_filter_values(state: AppState) -> dict[str, str]:
    return {
        "layer": state.memory_layer,
        "review_status": state.memory_review_status,
        "approved_for_rag": state.memory_approved_for_rag,
        "conflict": state.memory_conflict,
        "created_by": state.memory_created_by,
        "subject": state.memory_subject,
        "operation_tag": state.memory_operation_tag,
    }


def render_memory_workbench(workbench: dict[str, object], state: AppState, filters: dict[str, str]) -> str:
    sections = (
        ("待我确认", "pending_review"),
        ("待处理冲突", "conflicts"),
        ("已进入 RAG", "approved_rag"),
        ("最近停用", "recently_retired"),
        ("最近被 RAG 引用", "recent_rag_hits"),
        ("低质量待清理", "cleanup"),
    )
    cards = []
    for label, key in sections:
        items = workbench.get(key, ())
        count = len(items) if isinstance(items, tuple) else 0
        preview = ""
        if key == "conflicts" and isinstance(items, tuple) and items:
            preview = "；".join(str(item.get("subject", "-")) for item in items[:3] if isinstance(item, dict))
        elif isinstance(items, tuple) and items:
            preview = "；".join(f"#{item.get('memory_id', '')} {str(item.get('summary', ''))[:28]}" for item in items[:3] if isinstance(item, dict))
        cards.append(
            f"<article class='memory-card'><strong>{escape(label)}</strong><span>{count} 条</span><small>{escape(preview or '暂无')}</small></article>"
        )
    readonly = "" if can_write_country(state.user_id, state.country) else "<p class='muted'>当前国家为只读，只展示治理队列。</p>"
    seed = ""
    if can_write_country(state.user_id, state.country):
        seed = f'<form method="post" action="/seed_memory_validation">{hidden_context(state, view="runtime")}<button>生成生产验收样例</button></form>'
    lifecycle = render_memory_lifecycle_summary(workbench.get("lifecycle", {}))
    return readonly + render_memory_workbench_filters(state, filters) + lifecycle + "<div class='memory-grid'>" + "".join(cards) + "</div><div class='actions'>" + seed + "</div>"


def render_memory_lifecycle_summary(lifecycle: object) -> str:
    if not isinstance(lifecycle, dict):
        return ""
    storage = lifecycle.get("storage_plan", {})
    if not isinstance(storage, dict):
        storage = {}
    cleanup = lifecycle.get("weekly_cleanup", ())
    conflict_prone = lifecycle.get("conflict_prone", ())
    superseded = lifecycle.get("superseded", ())
    cleanup_count = len(cleanup) if isinstance(cleanup, tuple) else 0
    conflict_count = len(conflict_prone) if isinstance(conflict_prone, tuple) else 0
    superseded_count = len(superseded) if isinstance(superseded, tuple) else 0
    storage_text = (
        f"SoT={storage.get('source_of_truth', 'SQLite/Postgres')}；"
        f"Vector={storage.get('vector_index', 'Milvus approved RAG chunks')}；"
        f"Cache={storage.get('cache', 'Redis')}"
    )
    return f"""
    <div class="memory-grid">
      <article class="memory-card"><strong>团队级生命周期</strong><span>周清理 {escape(str(cleanup_count))} 条</span><small>冲突频发 {escape(str(conflict_count))}；被新规则覆盖 {escape(str(superseded_count))}</small></article>
      <article class="memory-card"><strong>事实 / 个人偏好</strong><span>运营事实 {escape(str(lifecycle.get('operational_facts_count', 0)))} 条</span><small>个人偏好 {escape(str(lifecycle.get('personal_preferences_count', 0)))} 条；个人偏好不进入市场事实 RAG</small></article>
      <article class="memory-card"><strong>生产存储分配</strong><span>SQLite/Postgres + Milvus + Redis</span><small>{escape(storage_text)}</small></article>
    </div>
    """


def render_memory_workbench_filters(state: AppState, filters: dict[str, str]) -> str:
    context = hidden_context(state, view="runtime")
    layer_options = render_select_options(("", "全部层级"), ("perception", "perception"), ("working", "working"), ("long_term", "long_term"), ("facts", "facts"), selected=filters.get("layer", ""))
    status_options = render_select_options(("", "全部状态"), ("draft", "draft"), ("approved", "approved"), ("rejected", "rejected"), ("conflict_locked", "conflict_locked"), ("retired", "retired"), selected=filters.get("review_status", ""))
    rag_options = render_select_options(("", "RAG不限"), ("true", "允许进RAG"), ("false", "不进RAG"), selected=filters.get("approved_for_rag", ""))
    conflict_options = render_select_options(("", "冲突不限"), ("true", "有冲突"), ("false", "无冲突"), selected=filters.get("conflict", ""))
    return f"""
    <form class="filter-bar" method="get" action="/">
      {context}
      <strong>Memory 工作台筛选</strong>
      <select name="memory_layer">{layer_options}</select>
      <select name="memory_review_status">{status_options}</select>
      <select name="memory_approved_for_rag">{rag_options}</select>
      <select name="memory_conflict">{conflict_options}</select>
      <input name="memory_created_by" value="{escape(filters.get('created_by', ''))}" placeholder="创建人ID">
      <input name="memory_subject" value="{escape(filters.get('subject', ''))}" placeholder="主体">
      <input name="memory_operation_tag" value="{escape(filters.get('operation_tag', ''))}" placeholder="运营tag">
      <button>筛选</button>
    </form>
    """


def render_select_options(*options: tuple[str, str], selected: str) -> str:
    return "".join(
        f'<option value="{escape(value)}"{" selected" if value == selected else ""}>{escape(label)}</option>'
        for value, label in options
    )


def render_memory_conflict_badges(row: dict[str, object]) -> str:
    conflict_ids = row.get("conflict_ids", ())
    if not conflict_ids:
        return "无"
    return "".join(f"<span class='badge danger'>冲突 {escape(str(conflict_id))}</span>" for conflict_id in conflict_ids)


def render_memory_conflicts(conflicts: tuple[dict[str, object], ...], state: AppState) -> str:
    if not conflicts:
        return '<p class="empty">暂无同一主体/tag 的正反向 memory 冲突。</p>'
    rows = []
    for conflict in conflicts:
        stances = conflict.get("stances", {})
        if not isinstance(stances, dict):
            stances = {}
        actions = render_memory_conflict_actions(conflict, state)
        evidence = "<br>".join(
            f"#{escape(str(item.get('memory_id', '')))} {escape(str(item.get('stance', '')))}：{escape(str(item.get('summary', ''))[:80])}"
            for item in conflict.get("evidence", ())
            if isinstance(item, dict)
        )
        rows.append(
            "<tr>"
            f"<td>{escape(str(conflict.get('subject', '') or '-'))}</td>"
            f"<td>{escape(str(conflict.get('operation_tag', '') or '-'))}</td>"
            f"<td>{escape(', '.join(str(item) for item in conflict.get('memory_ids', ())))}</td>"
            f"<td>positive={escape(str(stances.get('positive', [])))}；negative={escape(str(stances.get('negative', [])))}；risk={escape(str(stances.get('risk', [])))}<br><small>{evidence}</small></td>"
            f"<td>{escape(str(conflict.get('message', '同一主体/tag 下需要人工复核。')))}</td>"
            f"<td>{actions}</td>"
            "</tr>"
        )
    return "<div class='table-wrap'><table><thead><tr><th>主体</th><th>运营tag</th><th>Memory IDs</th><th>立场/证据</th><th>处理建议</th><th>闭环操作</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def render_memory_conflict_actions(conflict: dict[str, object], state: AppState) -> str:
    if not can_write_country(state.user_id, state.country):
        return "只读"
    conflict_id = escape(str(conflict.get("conflict_id", "")))
    context = hidden_context(state, view="runtime")
    buttons = (
        ("keep_first", "保留 A，停用 B"),
        ("keep_second", "保留 B，停用 A"),
        ("merge", "合并为新 memory"),
        ("retire_all", "全部停用"),
        ("defer", "暂不处理"),
    )
    forms = []
    for action, label in buttons:
        extra = '<input name="merge_text" placeholder="合并后的结论">' if action == "merge" else ""
        forms.append(
            f'<form method="post" action="/resolve_memory_conflict">{context}<input type="hidden" name="conflict_id" value="{conflict_id}"><input type="hidden" name="resolution_action" value="{escape(action)}">{extra}<input name="resolution_note" placeholder="处理备注"><button>{escape(label)}</button></form>'
        )
    return '<div class="memory-actions">' + "".join(forms) + "</div>"


def render_memory_provenance(provenance: dict[str, object]) -> str:
    steps = provenance.get("steps", ()) if isinstance(provenance, dict) else ()
    if not steps:
        return '<p class="empty">暂无可展示的 Memory provenance 链路。</p>'
    rows = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(str(step.get('step_type', '')))}</td>"
            f"<td>{escape(str(step.get('memory_id', '')))}</td>"
            f"<td>{escape(str(step.get('memory_layer', '')))}<br><small>{escape(str(step.get('memory_type', '')))}</small></td>"
            f"<td>{escape(str(step.get('status', '')))}{' · 人工确认' if step.get('human_verified') else ''}</td>"
            f"<td>{escape(str(step.get('source_memory_id') or '原始'))}</td>"
            f"<td>{escape(str(step.get('summary', '')))}</td>"
            "</tr>"
        )
    subject = escape(str(provenance.get("subject", "") or "-"))
    operation_tag = escape(str(provenance.get("operation_tag", "") or "-"))
    header = f"<p class='muted'>当前链路：subject={subject}；operation_tag={operation_tag}</p>"
    return header + "<div class='table-wrap'><table><thead><tr><th>步骤</th><th>ID</th><th>层级/类型</th><th>状态</th><th>来源</th><th>内容</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def render_memory_actions(row: dict[str, object], state: AppState) -> str:
    if row.get("status") != "active":
        return "已归档"
    if not can_write_country(state.user_id, state.country):
        return "只读"
    memory_id = escape(str(row.get("memory_id", "")))
    context = hidden_context(state, view="runtime")
    forms = []
    forms.append(
        f'<form method="post" action="/review_memory">{context}<input type="hidden" name="memory_id" value="{memory_id}"><input type="hidden" name="review_action" value="approve_rag"><button>批准并进入 RAG</button></form>'
    )
    forms.append(
        f'<form method="post" action="/review_memory">{context}<input type="hidden" name="memory_id" value="{memory_id}"><input type="hidden" name="review_action" value="approve_no_rag"><button>批准但不进 RAG</button></form>'
    )
    forms.append(
        f'<form method="post" action="/review_memory">{context}<input type="hidden" name="memory_id" value="{memory_id}"><input type="hidden" name="review_action" value="reject"><button>驳回</button></form>'
    )
    if row.get("layer") in {"perception", "working"}:
        forms.append(
            f'<form method="post" action="/promote_memory">{context}<input type="hidden" name="memory_id" value="{memory_id}"><input type="hidden" name="target_layer" value="facts"><input name="human_note" value="运营确认事实"><button>晋升为事实</button></form>'
        )
    if row.get("layer") in {"working", "facts"}:
        forms.append(
            f'<form method="post" action="/promote_memory">{context}<input type="hidden" name="memory_id" value="{memory_id}"><input type="hidden" name="target_layer" value="long_term"><input name="human_note" value="运营确认长期有效"><button>晋升为长期记忆</button></form>'
        )
    forms.append(
        f'<form method="post" action="/retire_memory">{context}<input type="hidden" name="memory_id" value="{memory_id}"><button>停用</button></form>'
    )
    forms.append(
        f'<form method="post" action="/migrate_memory_country">{context}<input type="hidden" name="memory_id" value="{memory_id}"><input name="target_country" placeholder="目标国家"><input name="migration_note" placeholder="迁移备注"><button>迁移国家</button></form>'
    )
    return '<div class="memory-actions">' + "".join(forms) + "</div>"


def render_rag_summary(summary: dict[str, object], state: AppState | None = None) -> str:
    source_counts = summary.get("source_counts", {})
    if not isinstance(source_counts, dict):
        source_counts = {}
    source_text = "；".join(f"{key}:{value}" for key, value in source_counts.items()) or "暂无知识块"
    citations = summary.get("citations", ())
    citation_text = "、".join(str(item) for item in citations) if isinstance(citations, tuple) else str(citations)
    context = str(summary.get("context", ""))
    embedding = f"{summary.get('embedding_provider', 'local')} / {summary.get('embedding_model', 'local-token-cosine')}"
    rerank = f"{summary.get('rerank_provider', 'local')} / {summary.get('rerank_model', 'local-rule-rerank')}"
    provider_status = str(summary.get("provider_status", "本地 fallback"))
    offline_pipeline = (
        f"Loader：{summary.get('offline_loader', 'StaticDocumentLoaderAdapter')}；"
        f"Splitter：{summary.get('splitter', 'sentence_token')}；"
        f"chunk={summary.get('chunk_size_tokens', 600)}；"
        f"overlap={summary.get('chunk_overlap_tokens', 100)}；"
        f"store={summary.get('vector_store', 'sqlite')}；"
        f"collection={summary.get('vector_store_collection', 'puzzle_ops_rag')}；"
        f"{summary.get('vector_store_status', 'SQLite 本地 chunk store + embedding cache')}"
    )
    online_pipeline = (
        f"BM25 top-k {summary.get('bm25_top_k', 30)}；"
        f"Vector top-k {summary.get('vector_top_k', 30)}；"
        f"Rerank top-k {summary.get('rerank_top_k', 5)}；"
        f"query rewrite：{summary.get('rewritten_query', '')}"
    )
    stats = (
        f"cache hit {summary.get('embedding_cache_hits', 0)}；"
        f"embedding remote {summary.get('embedding_remote_calls', 0)}；"
        f"embedding fallback {summary.get('embedding_fallbacks', 0)}；"
        f"rerank remote {summary.get('rerank_remote_calls', 0)}；"
        f"rerank fallback {summary.get('rerank_fallbacks', 0)}"
    )
    task_index = str(summary.get("task_index", "value_master"))
    task_label = str(summary.get("task_label", "价值观大师"))
    task_source_types = summary.get("task_source_types", ())
    if isinstance(task_source_types, (tuple, list)):
        task_sources_text = "、".join(str(item) for item in task_source_types)
    else:
        task_sources_text = str(task_source_types or "all")
    runtime_status = summary.get("rag_retrieval_runtime_status", {})
    if not isinstance(runtime_status, dict):
        runtime_status = {}
    retrieval_mode = str(runtime_status.get("mode") or summary.get("vector_store_mode") or "fallback")
    milvus_mode = "primary" if runtime_status.get("milvus_primary") or summary.get("milvus_primary") else "fallback"
    milvus_reason = str(runtime_status.get("fallback_reason") or runtime_status.get("status_text") or summary.get("vector_store_status", ""))
    trace = summary.get("retrieval_trace", {})
    if not isinstance(trace, dict):
        trace = {}
    eval_report = summary.get("retrieval_eval_report", {})
    if not isinstance(eval_report, dict):
        eval_report = {}
    trace_text = (
        f"候选池 {trace.get('merged_candidate_count', 0)}；"
        f"eligible {trace.get('eligible_chunk_count', 0)}；"
        f"向量库={trace.get('vector_store_provider', 'local')}；"
        f"BM25候选 {len(trace.get('bm25_candidates', ()) if isinstance(trace.get('bm25_candidates', ()), (tuple, list)) else ())}；"
        f"向量候选 {len(trace.get('vector_candidates', ()) if isinstance(trace.get('vector_candidates', ()), (tuple, list)) else ())}"
    )
    eval_text = (
        f"hit@5={eval_report.get('hit@5', 0)}；"
        f"mrr@5={eval_report.get('mrr@5', 0)}；"
        f"threshold={eval_report.get('threshold', 0.8)}；"
        f"passed={eval_report.get('passed_threshold', False)}"
    )
    knowledge = summary.get("knowledge_base", {})
    if not isinstance(knowledge, dict):
        knowledge = {}
    vector_manifest_label = vector_store_label(str(knowledge.get("vector_store_manifest_provider") or summary.get("vector_store", "sqlite")))
    knowledge_text = (
        f"raw={knowledge.get('raw_file_count', 0)}；"
        f"documents={knowledge.get('file_document_count', 0)}；"
        f"eval cases={knowledge.get('file_eval_case_count', 0)}；"
        f"向量库 manifest={knowledge.get('vector_store_manifest_status') or 'none'}；"
        f"provider={vector_manifest_label}；"
        f"run_id={knowledge.get('vector_store_manifest_run_id') or 'none'}；"
        f"hit@5={knowledge.get('vector_store_manifest_hit@5', 0)}；"
        f"mrr@5={knowledge.get('vector_store_manifest_mrr@5', 0)}；"
        f"precision@5={knowledge.get('vector_store_manifest_precision@5', 0)}；"
        f"recall@5={knowledge.get('vector_store_manifest_recall@5', 0)}；"
        f"ndcg@5={knowledge.get('vector_store_manifest_ndcg@5', 0)}；"
        f"vector_size={knowledge.get('vector_store_manifest_vector_size', 0)}；"
        f"points={knowledge.get('vector_store_manifest_upserted_points', 0)}；"
        f"{Path(str(knowledge.get('documents_path', ''))).name}；"
        f"{Path(str(knowledge.get('eval_cases_path', ''))).name}"
    )
    citation_rows = render_rag_citation_details(summary.get("citation_details", ()))
    trace_details = render_rag_retrieval_trace_details(trace)
    eval_case_evidence = render_rag_eval_case_evidence(summary.get("rag_eval_case_evidence", {}), state)
    failure_feedback = render_rag_failure_feedback_queue(summary.get("rag_eval_failure_feedback", {}))
    patch_drafts = render_rag_knowledge_patch_drafts(summary.get("rag_knowledge_patch_drafts", {}), state)
    patch_ops = summary.get("rag_patch_ops", {})
    patch_ops_card = render_rag_patch_ops_card(patch_ops)
    patch_compare_card = render_rag_patch_compare_card(patch_ops)
    patch_runs = render_rag_patch_runs(patch_ops)
    recent_trace_rows = render_recent_rag_traces(summary.get("recent_traces", ()))
    feedback = summary.get("feedback_summary", {})
    feedback_card = render_rag_feedback_summary(feedback if isinstance(feedback, dict) else {})
    acceptance = summary.get("latest_acceptance_summary", {})
    acceptance_card = render_rag_acceptance_preflight(acceptance if isinstance(acceptance, dict) else {})
    live_model_ops_card = render_rag_live_model_ops(summary.get("rag_live_model_ops", {}))
    eval_dataset = summary.get("rag_eval_dataset", {})
    eval_dataset_payload = dict(eval_dataset) if isinstance(eval_dataset, dict) else {}
    if eval_report.get("business_sample_gate"):
        eval_dataset_payload["business_sample_gate"] = eval_report.get("business_sample_gate")
    eval_dataset_card = render_rag_eval_dataset(eval_dataset_payload)
    chunk_eval_card = render_rag_chunk_eval_dataset(summary.get("rag_chunk_eval_dataset", {}))
    governance = render_rag_quality_governance(summary.get("rag_quality_governance", {}), state)
    vector_store_search = "on" if summary.get("vector_store_search_enabled") else "off"
    return f"""
<div class="rag-grid">
  <article><strong>父子知识块</strong><span>{escape(str(summary.get("chunk_count", 0)))} 个 chunk</span><small>{escape(source_text)}</small></article>
  <article><strong>任务索引</strong><span>{escape(task_index)} · {escape(task_label)}</span><small>{escape(task_sources_text[:220])}</small></article>
  <article><strong>Milvus 主检索</strong><span>{escape(milvus_mode)}</span><small>mode={escape(retrieval_mode)}；provider={escape(str(runtime_status.get("primary_provider", summary.get("vector_store", "SQLite"))))}；{escape(milvus_reason[:180])}</small></article>
  <article><strong>多路召回</strong><span>BM25 + Embedding + Rerank</span><small>Embedding：{escape(embedding)}；Rerank：{escape(rerank)}。{escape(provider_status)}</small></article>
  <article><strong>引用依据</strong><span>{escape(citation_text or "暂无引用")}</span><small>{escape(context[:140] or "暂无召回上下文")}；{escape(stats)}</small></article>
  <article><strong>离线建库</strong><span>DocumentLoader + Chunk + Store</span><small>{escape(offline_pipeline)}</small></article>
  <article><strong>在线检索</strong><span>Rewrite + Hybrid Recall + Rerank</span><small>VectorStore search={escape(vector_store_search)}；{escape(online_pipeline[:220])}</small></article>
  <article><strong>RAG 检索评测</strong><span>hit@5 / mrr@5</span><small>{escape(eval_text)}；{escape(trace_text)}</small></article>
  {eval_dataset_card}
  {chunk_eval_card}
  {acceptance_card}
  {live_model_ops_card}
  <article><strong>版本化知识库</strong><span>Documents + Eval Cases</span><small>{escape(knowledge_text[:260])}</small></article>
  {render_rag_failure_feedback_card(summary.get("rag_eval_failure_feedback", {}))}
  {render_rag_knowledge_patch_card(summary.get("rag_knowledge_patch_drafts", {}))}
  {patch_ops_card}
  {patch_compare_card}
  {feedback_card}
</div>
<h3>引用明细</h3>
<div class="table-wrap"><table><thead><tr><th>引用ID</th><th>知识来源</th><th>父文档</th><th>标题</th><th>内容</th></tr></thead><tbody>{citation_rows}</tbody></table></div>
{eval_case_evidence}
{governance}
{failure_feedback}
{patch_drafts}
{patch_runs}
<h3>RAG 检索 Trace</h3>
{trace_details}
<h3>最近 RAG Trace</h3>
<div class="table-wrap"><table><thead><tr><th>Trace</th><th>Query</th><th>引用</th><th>可回放 prompt</th><th>详情</th></tr></thead><tbody>{recent_trace_rows}</tbody></table></div>
"""


def render_rag_knowledge_patch_card(summary: object) -> str:
    if not isinstance(summary, dict):
        summary = {}
    priority = summary.get("priority_summary", {})
    if not isinstance(priority, dict):
        priority = {}
    priority_text = (
        f"P0={priority.get('P0', 0)}；"
        f"P1={priority.get('P1', 0)}；"
        f"P2={priority.get('P2', 0)}"
    )
    return (
        "<article><strong>RAG知识补丁草案</strong>"
        f"<span>草案={escape(str(summary.get('draft_count', 0)))}</span>"
        f"<small>{escape(priority_text)}；草案不会自动进入知识库，需要人工审核后再补充 raw 文档或晋升为长期记忆。</small></article>"
    )


def render_rag_patch_ops_card(summary: object) -> str:
    if not isinstance(summary, dict):
        summary = {}
    status = str(summary.get("status", "none") or "none")
    text = (
        f"patches={summary.get('patch_count', 0)}；"
        f"hit@5={summary.get('rebuild_hit@5', 0)}；"
        f"mrr@5={summary.get('rebuild_mrr@5', 0)}；"
        f"qdrant={summary.get('qdrant_status', 'none')}；"
        f"points={summary.get('qdrant_points', 0)}；"
        f"vector_size={summary.get('qdrant_vector_size', 0)}；"
        f"{summary.get('raw_patch_file', '')}"
    )
    return (
        "<article><strong>RAG Patch Ops</strong>"
        f"<span>{escape(status)}</span>"
        f"<small>{escape(text[:260])}</small></article>"
    )


def render_rag_patch_compare_card(summary: object) -> str:
    if not isinstance(summary, dict):
        summary = {}
    comparison = summary.get("run_comparison", {})
    if not isinstance(comparison, dict):
        comparison = {}
    impact = summary.get("priority_impact", {})
    if not isinstance(impact, dict):
        impact = {}
    current = str(comparison.get("current_run_id", ""))
    previous = str(comparison.get("previous_run_id", ""))
    label = f"{current} vs {previous}" if current or previous else "暂无对比"
    text = (
        f"hit@5 Δ={comparison.get('hit@5_delta', 0)}；"
        f"mrr@5 Δ={comparison.get('mrr@5_delta', 0)}；"
        f"points Δ={comparison.get('qdrant_points_delta', 0)}；"
        f"status_changed={comparison.get('status_changed', False)}；"
        f"pending_P0={impact.get('pending_P0', 0)}；"
        f"effect={impact.get('effect', 'unknown')}；"
        f"{impact.get('recommended_action', '')}；"
        f"fixed={comparison.get('fixed_failure_count', 0)}；"
        f"new_failures={comparison.get('new_failure_count', 0)}；"
        f"fixed_ids={','.join(str(item) for item in comparison.get('fixed_failures', ())[:3]) if isinstance(comparison.get('fixed_failures', ()), (tuple, list)) else ''}"
    )
    return (
        "<article><strong>RAG Patch Compare</strong>"
        f"<span>{escape(label)}</span>"
        f"<small>{escape(text[:220])}</small></article>"
    )


def render_rag_patch_runs(summary: object) -> str:
    if not isinstance(summary, dict):
        summary = {}
    runs = summary.get("recent_runs", ())
    if not isinstance(runs, (list, tuple)):
        runs = ()
    rows = []
    for item in runs[:8]:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        patch_ids = evidence.get("patch_ids", ())
        if isinstance(patch_ids, (list, tuple)):
            patch_ids_text = "、".join(str(value) for value in patch_ids)
        else:
            patch_ids_text = str(patch_ids)
        evidence_text = (
            f"patch_ids={patch_ids_text}\n"
            f"raw={evidence.get('raw_patch_path', '')}\n"
            f"processed={evidence.get('processed_path', '')}\n"
            f"patch_manifest={evidence.get('patch_manifest_path', '')}\n"
            f"qdrant_manifest={evidence.get('qdrant_manifest_path', '')}\n"
            f"rollback={evidence.get('rollback_removed', '')}"
        )
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('run_id', '')))}</td>"
            f"<td>{escape(str(item.get('status', '')))}</td>"
            f"<td>{escape(str(item.get('patch_count', 0)))}</td>"
            f"<td>{escape(str(item.get('rebuild_hit@5', 0)))}</td>"
            f"<td>{escape(str(item.get('qdrant_status', 'none')))}</td>"
            f"<td>{escape(str(item.get('qdrant_points', 0)))}</td>"
            f"<td>{escape(str(item.get('rollback_removed', '')))}</td>"
            f"<td><details><summary>证据</summary><pre>{escape(evidence_text)}</pre></details></td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="8">暂无 RAG patch run 记录。</td></tr>'
    return (
        "<h3>RAG Patch Runs</h3>"
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>Run</th><th>状态</th><th>Patch数</th><th>hit@5</th><th>Qdrant</th><th>Points</th><th>Rollback</th><th>证据</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_rag_knowledge_patch_drafts(summary: object, state: AppState | None = None) -> str:
    if not isinstance(summary, dict):
        summary = {}
    items = summary.get("items", ())
    if not isinstance(items, (list, tuple)):
        items = ()
    rows = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        action = ""
        if state is not None:
            action = (
                '<form method="post" action="/approve_rag_knowledge_patch_draft">'
                f"{hidden_context(state, view='runtime')}"
                f'<input type="hidden" name="patch_id" value="{escape(str(item.get("patch_id", "")))}">'
                '<input name="human_note" value="运营审核通过，进入长期RAG记忆">'
                "<button>审核通过草案</button></form>"
            )
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('patch_id', '')))}</td>"
            f"<td>{escape(str(item.get('priority_band', 'P2')))}<br><small>priority_score={escape(str(item.get('priority_score', 0)))}；{escape(str(item.get('priority_reason', ''))[:90])}</small></td>"
            f"<td>{escape(str(item.get('source_type', '')))}</td>"
            f"<td>{escape(str(item.get('expected_parent_id', '')))}</td>"
            f"<td>{escape(str(item.get('review_status', '')))}</td>"
            f"<td>{escape(str(item.get('draft_text', ''))[:220])}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="7">暂无 RAG 知识补丁草案。</td></tr>'
    return (
        "<h3>RAG知识补丁草案</h3>"
        f"<p class=\"muted\">草案={escape(str(summary.get('draft_count', 0)))}</p>"
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>Patch</th><th>优先级</th><th>Source Type</th><th>Expected Parent</th><th>审核状态</th><th>草案内容</th><th>HITL</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_rag_quality_governance(summary: object, state: AppState | None = None) -> str:
    if not isinstance(summary, dict):
        summary = {}
    feedback_pool = summary.get("feedback_pool", {}) if isinstance(summary.get("feedback_pool", {}), dict) else {}
    weekly = summary.get("weekly_anomalies", {}) if isinstance(summary.get("weekly_anomalies", {}), dict) else {}
    monthly = summary.get("monthly_patch_plan", {}) if isinstance(summary.get("monthly_patch_plan", {}), dict) else {}
    emergency = summary.get("emergency_patch_flow", {}) if isinstance(summary.get("emergency_patch_flow", {}), dict) else {}
    context = hidden_context(state, view="runtime") if state is not None else ""
    monthly_items = monthly.get("items", ())
    if not isinstance(monthly_items, (list, tuple)):
        monthly_items = ()
    emergency_items = emergency.get("items", ())
    if not isinstance(emergency_items, (list, tuple)):
        emergency_items = ()
    rows = []
    for item in monthly_items[:6]:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("source_memory_id", ""))
        monthly_form = (
            f'<form method="post" action="/mark_rag_feedback_monthly">{context}'
            f'<input type="hidden" name="memory_id" value="{escape(memory_id)}">'
            '<input name="review_note" value="纳入月度知识补丁审核"><button>标记月度处理</button></form>'
        )
        emergency_form = (
            f'<form method="post" action="/mark_rag_feedback_emergency">{context}'
            f'<input type="hidden" name="memory_id" value="{escape(memory_id)}">'
            '<input name="review_note" value="标记紧急补丁"><button>标记紧急补丁</button></form>'
        )
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('patch_id', '')))}</td>"
            f"<td>{escape(str(item.get('priority_band', '')))}</td>"
            f"<td>{escape(str(item.get('query', ''))[:120])}</td>"
            f"<td>{monthly_form}{emergency_form}</td>"
            "</tr>"
        )
    emergency_rows = []
    for item in emergency_items[:4]:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("source_memory_id", ""))
        apply_form = (
            f'<form method="post" action="/apply_emergency_rag_patch_and_rebuild">{context}'
            f'<input type="hidden" name="memory_id" value="{escape(memory_id)}">'
            '<input name="review_note" value="负责人确认紧急补丁"><button>应用紧急补丁并重建</button></form>'
        )
        emergency_rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('patch_id', '')))}</td>"
            f"<td>{escape(str(item.get('reason', '')))}</td>"
            f"<td>{escape(str(item.get('draft_text', ''))[:160])}</td>"
            f"<td>{apply_form}</td>"
            "</tr>"
        )
    rows_html = "".join(rows) or '<tr><td colspan="4">暂无月度草案候选。</td></tr>'
    emergency_html = "".join(emergency_rows) or '<tr><td colspan="4">暂无紧急补丁候选。</td></tr>'
    return f"""
<section class="subpanel rag-governance-panel">
  <h3>RAG质量治理工作台</h3>
  <div class="rag-grid">
    <article><strong>治理节奏</strong><span>{escape(str(summary.get('cadence_label', '月度重建 + 紧急补丁')))}</span><small>日常可选反馈；每周看异常；每月审核补丁并重建；紧急风险随时处理。</small></article>
    <article><strong>反馈池</strong><span>citation={escape(str(feedback_pool.get('citation_feedback_count', 0)))} / low_score={escape(str(feedback_pool.get('low_score_count', 0)))}</span><small>not_useful={escape(str(feedback_pool.get('not_useful_count', 0)))}；failure={escape(str(feedback_pool.get('failure_feedback_count', 0)))}</small></article>
    <article><strong>本周异常巡检</strong><span>紧急候选 {escape(str(weekly.get('emergency_candidate_count', 0)))}</span><small>只做标记，不强制每周重建。</small></article>
    <article><strong>月度计划</strong><span>草案 {escape(str(monthly.get('draft_count', 0)))}</span><small>recommended_action={escape(str(monthly.get('recommended_action', 'collect_more_feedback')))}</small></article>
  </div>
  <div class="section-line"><h3>生成月度知识补丁草案</h3><form method="post" action="/export_rag_knowledge_patch_drafts">{context}<button>导出月度草案</button></form></div>
  <div class="table-wrap"><table><thead><tr><th>Patch</th><th>优先级</th><th>Query</th><th>标记</th></tr></thead><tbody>{rows_html}</tbody></table></div>
  <h3>紧急补丁通道</h3>
  <div class="table-wrap"><table><thead><tr><th>Patch</th><th>原因</th><th>补丁摘要</th><th>动作</th></tr></thead><tbody>{emergency_html}</tbody></table></div>
</section>
"""


def render_rag_failure_feedback_card(summary: object) -> str:
    if not isinstance(summary, dict):
        summary = {}
    pending = summary.get("pending_count", 0)
    return (
        "<article><strong>RAG失败反馈队列</strong>"
        f"<span>待处理={escape(str(pending))}</span>"
        "<small>失败 case 会作为 hard_negative_or_knowledge_patch 候选导出，用于补知识库或调 rerank。</small></article>"
    )


def render_rag_failure_feedback_queue(summary: object) -> str:
    if not isinstance(summary, dict):
        summary = {}
    items = summary.get("items", ())
    if not isinstance(items, (list, tuple)):
        items = ()
    rows = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        retrieved = item.get("retrieved_parent_ids", ())
        if isinstance(retrieved, (list, tuple)):
            retrieved_text = "、".join(str(value) for value in retrieved[:5])
        else:
            retrieved_text = str(retrieved)
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('memory_id', '')))}</td>"
            f"<td>{escape(str(item.get('query', ''))[:120])}</td>"
            f"<td>{escape(str(item.get('expected_parent_id', '')))}</td>"
            f"<td>{escape(retrieved_text or '无')}</td>"
            f"<td>{escape(str(item.get('optimization_use', '')))}</td>"
            f"<td>{escape(str(item.get('note', '')))}</td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="6">暂无 RAG 失败反馈。</td></tr>'
    return (
        "<h3>RAG失败反馈队列</h3>"
        f"<p class=\"muted\">待处理={escape(str(summary.get('pending_count', 0)))}</p>"
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>Memory</th><th>Query</th><th>Expected Parent</th><th>Retrieved Parents</th><th>用途</th><th>备注</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_rag_eval_case_evidence(summary: object, state: AppState | None = None) -> str:
    if not isinstance(summary, dict):
        summary = {}
    cases = summary.get("cases", ())
    if not isinstance(cases, (list, tuple)):
        cases = ()
    rows = []
    for item in cases[:8]:
        if not isinstance(item, dict):
            continue
        retrieved = item.get("retrieved_parent_ids", ())
        if isinstance(retrieved, (list, tuple)):
            retrieved_text = "、".join(str(value) for value in retrieved[:5])
        else:
            retrieved_text = str(retrieved)
        action = "命中"
        if str(item.get("status", "")) == "FAIL" and state is not None:
            action = (
                '<form method="post" action="/record_rag_eval_failure_feedback">'
                f"{hidden_context(state, view='runtime')}"
                f'<input type="hidden" name="query" value="{escape(str(item.get("query", "")))}">'
                f'<input type="hidden" name="expected_parent_id" value="{escape(str(item.get("expected_parent_id", "")))}">'
                f'<input type="hidden" name="retrieved_parent_ids" value="{escape(retrieved_text)}">'
                '<input name="note" value="补充知识或 hard negative">'
                "<button>记录失败case</button></form>"
            )
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('status', '')))}</td>"
            f"<td>{escape(str(item.get('query', ''))[:120])}</td>"
            f"<td>{escape(str(item.get('expected_parent_id', '')))}</td>"
            f"<td>{escape(retrieved_text or '无')}</td>"
            f"<td>{escape(str(item.get('rank', 0)))}</td>"
            f"<td>{escape(str(item.get('failure_reason', '')) or '命中')}</td>"
            f"<td>{escape(str(item.get('diagnosis', '')) or 'passed')}</td>"
            f"<td>{escape(str(item.get('suggested_action', '')))}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="9">暂无 RAG eval case。</td></tr>'
    headline = (
        f"dataset={summary.get('dataset_name', '')}；"
        f"hit@5={summary.get('hit@5', 0)}；"
        f"failed={summary.get('failed_count', 0)}/{summary.get('total', 0)}；"
        f"threshold={summary.get('threshold', 0.8)}"
    )
    return (
        "<h3>RAG Eval Case 证据</h3>"
        f"<p class=\"muted\">{escape(headline)}</p>"
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>状态</th><th>Query</th><th>Expected Parent</th><th>Retrieved Parents</th><th>Rank</th><th>失败原因</th><th>诊断</th><th>建议动作</th><th>HITL</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_rag_eval_dataset(summary: dict[str, object]) -> str:
    business_gate = summary.get("business_sample_gate", {})
    if not isinstance(business_gate, dict):
        business_gate = {}
    business_status = business_gate.get("status", "not_evaluable")
    if business_gate.get("passed_threshold"):
        business_status = "passed"
    headline = (
        f"real={summary.get('real_sample_count', 0)}；"
        f"human_gold={summary.get('human_gold_count', 0)}；"
        f"cases={summary.get('total_eval_case_count', 0)}"
    )
    detail = (
        f"file cases={summary.get('file_eval_case_count', 0)}；"
        f"harness cases={summary.get('harness_eval_case_count', 0)}；"
        f"ai_silver={summary.get('ai_silver_count', 0)}；"
        f"manual_grade={summary.get('manual_grade_count', 0)}；"
        f"target={summary.get('target_real_sample_range', '30-50')}；"
        f"hit@5 threshold={summary.get('hit_at_five_threshold', 0.8)}；"
        f"status={summary.get('status', '')}；"
        f"business cases={business_gate.get('case_count', 0)}；"
        f"business_hit@5={business_gate.get('hit@5', 0)}；"
        f"business_gate={business_status}"
    )
    return (
        "<article><strong>真实 Eval Dataset</strong>"
        f"<span>{escape(headline)}</span>"
        f"<small>{escape(detail)}</small></article>"
    )


def render_rag_chunk_eval_dataset(summary: object) -> str:
    if not isinstance(summary, dict):
        return ""
    metrics = summary.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    hybrid = summary.get("hybrid_search", {})
    if not isinstance(hybrid, dict):
        hybrid = {}
    headline = (
        f"queries={summary.get('query_count', 0)}；"
        f"docs={summary.get('document_count', 0)}；"
        f"chunks={summary.get('chunk_count', 0)}"
    )
    detail = (
        f"target={summary.get('target_query_range', '30-50')}；"
        f"recall@5={metrics.get('recall@5', 0)}；"
        f"mrr@5={metrics.get('mrr@5', 0)}；"
        f"citation_precision@5={metrics.get('citation_precision@5', 0)}；"
        f"risk_miss_rate@5={metrics.get('risk_miss_rate@5', 0)}；"
        f"hybrid=BM25+dense+rerank:{hybrid.get('bm25_dense_rerank', False)}"
    )
    return (
        "<article><strong>业务对象 Chunk Eval</strong>"
        f"<span>{escape(headline)}</span>"
        f"<small>{escape(detail)}</small></article>"
    )


def render_rag_acceptance_preflight(summary: dict[str, object]) -> str:
    if not summary.get("exists"):
        return (
            "<article><strong>RAG Preflight</strong>"
            "<span>尚未运行一键验收</span>"
            "<small>点击“一键RAG全链路验收”后，这里会展示 Qwen embedding、Qdrant、BGE rerank 的真实检查结果。</small></article>"
        )
    preflight = summary.get("preflight", {})
    if not isinstance(preflight, dict):
        preflight = {}
    runtime_stats = summary.get("runtime_stats", {})
    if not isinstance(runtime_stats, dict):
        runtime_stats = {}
    stage = str(summary.get("failure_stage", "") or "none")
    status_line = (
        f"mode={summary.get('mode', '')}；"
        f"status={summary.get('status', '')}；"
        f"stage={stage}"
    )
    component_text = "；".join(
        _rag_preflight_component_text(name, preflight.get(name, {}) if isinstance(preflight.get(name), dict) else {})
        for name in ("embedding", "qdrant", "rerank")
    )
    metric_text = (
        f"full hit@5={summary.get('hit@5', 0)}；"
        f"mrr@5={summary.get('mrr@5', 0)}；"
        f"qdrant_hit={summary.get('qdrant_vector_hits', False)}；"
        f"embedding remote {runtime_stats.get('embedding_remote_calls', 0)}/fallback {runtime_stats.get('embedding_fallbacks', 0)}；"
        f"rerank remote {runtime_stats.get('rerank_remote_calls', 0)}/fallback {runtime_stats.get('rerank_fallbacks', 0)}"
    )
    error = str(summary.get("error", ""))
    detail = "；".join(item for item in (component_text, metric_text, f"error={error}" if error else "") if item)
    return (
        "<article><strong>RAG Preflight</strong>"
        f"<span>{escape(status_line)}</span>"
        f"<small>{escape(detail)}</small></article>"
    )


def render_rag_live_model_ops(summary: object) -> str:
    if not isinstance(summary, dict):
        summary = {}
    ready_text = (
        f"embedding={'ready' if summary.get('embedding_ready') else 'not_ready'}；"
        f"qdrant={'ready' if summary.get('qdrant_ready') else 'not_ready'}；"
        f"rerank={'ready' if summary.get('rerank_ready') else 'not_ready'}"
    )
    metric_text = (
        f"remote embedding={summary.get('embedding_remote_calls', 0)}；"
        f"remote rerank={summary.get('rerank_remote_calls', 0)}；"
        f"fallback embedding={summary.get('embedding_fallbacks', 0)}；"
        f"fallback rerank={summary.get('rerank_fallbacks', 0)}；"
        f"qdrant_hit={summary.get('qdrant_vector_hits', False)}；"
        f"hit@5={summary.get('hit@5', 0)}"
    )
    provider_text = (
        f"{summary.get('embedding_provider', '')}；"
        f"{summary.get('qdrant_provider', '')}；"
        f"{summary.get('rerank_provider', '')}"
    )
    return (
        "<article><strong>RAG Live Model Ops</strong>"
        f"<span>mode={escape(str(summary.get('mode', 'not_run')))}；{escape(ready_text)}</span>"
        f"<small>{escape(metric_text)}；{escape(provider_text[:160])}</small></article>"
    )


def _rag_preflight_component_text(name: str, status: dict[str, object]) -> str:
    ready = "ready" if status.get("ready") else "not ready"
    provider = str(status.get("provider") or status.get("provider_name") or "")
    details = [f"{name} {ready}"]
    if provider:
        details.append(provider)
    for key in ("model", "collection", "vector_size"):
        if status.get(key) not in (None, ""):
            details.append(f"{key}={status.get(key)}")
    if status.get("error"):
        details.append(f"error={status.get('error')}")
    return " ".join(str(item) for item in details)


def render_rag_feedback_summary(summary: dict[str, object]) -> str:
    top_chunks = summary.get("top_chunks", ())
    if not isinstance(top_chunks, (list, tuple)) or not top_chunks:
        return "<article><strong>RAG 人工反馈</strong><span>暂无反馈</span><small>运营在试新页标记依据有用/无用后，会在这里汇总。</small></article>"
    lines = []
    for item in top_chunks[:3]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"{item.get('chunk_id', '')}: useful={item.get('useful_count', 0)}, "
            f"not_useful={item.get('not_useful_count', 0)}, net={item.get('net_score', 0)}"
        )
    return (
        "<article><strong>RAG 人工反馈</strong>"
        f"<span>useful={escape(str(summary.get('useful_count', 0)))} / not_useful={escape(str(summary.get('not_useful_count', 0)))}</span>"
        f"<small>{escape('；'.join(lines))}</small></article>"
    )


def render_rag_citation_details(details: object) -> str:
    if not isinstance(details, (list, tuple)) or not details:
        return '<tr><td colspan="5">暂无可溯源引用明细。</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{escape(str(item.get('chunk_id', '')))}</td>"
        f"<td>{escape(str(item.get('source_type', '')))}</td>"
        f"<td>{escape(str(item.get('parent_id', '')))}</td>"
        f"<td>{escape(str(item.get('title', '')))}</td>"
        f"<td>{escape(str(item.get('text', '')))}</td>"
        "</tr>"
        for item in details
        if isinstance(item, dict)
    )


def render_rag_retrieval_trace_details(trace: dict[str, object]) -> str:
    bm25 = trace.get("bm25_candidates", ())
    vector = trace.get("vector_candidates", ())
    exact = trace.get("exact_match_candidates", ())
    hits = trace.get("final_hits", ())
    bm25_text = _trace_id_list(bm25)
    vector_text = _trace_id_list(vector)
    exact_text = _trace_id_list(exact)
    final_rows = _trace_final_hit_rows(hits)
    meta = (
        f"query={trace.get('query', '')}；"
        f"eligible={trace.get('eligible_chunk_count', 0)}；"
        f"merged={trace.get('merged_candidate_count', 0)}；"
        f"bm25_top_k={trace.get('bm25_top_k', 0)}；"
        f"vector_top_k={trace.get('vector_top_k', 0)}；"
        f"rerank_top_k={trace.get('rerank_top_k', 0)}"
    )
    return f"""
<div class="trace-grid">
  <article><strong>检索参数</strong><small>{escape(meta)}</small></article>
  <article><strong>BM25 召回候选</strong><small>{escape(bm25_text)}</small></article>
  <article><strong>向量召回候选</strong><small>{escape(vector_text)}</small></article>
  <article><strong>精确规则候选</strong><small>{escape(exact_text)}</small></article>
</div>
<div class="table-wrap"><table><thead><tr><th>精排最终命中</th><th>父文档</th><th>来源</th><th>BM25</th><th>向量</th><th>Rerank</th><th>原因</th></tr></thead><tbody>{final_rows}</tbody></table></div>
"""


def render_recent_rag_traces(traces: object) -> str:
    if not isinstance(traces, (list, tuple)) or not traces:
        return '<tr><td colspan="5">暂无可回放 RAG trace。</td></tr>'
    rows = []
    for item in traces:
        if not isinstance(item, dict):
            continue
        citations = item.get("citations", ())
        if isinstance(citations, (list, tuple)):
            citation_text = "、".join(str(citation) for citation in citations[:5])
        else:
            citation_text = str(citations)
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('trace_id', '')))}</td>"
            f"<td>{escape(str(item.get('original_query', ''))[:180])}</td>"
            f"<td>{escape(citation_text or '无')}</td>"
            f"<td>{escape(str(item.get('trace_path', '')))}</td>"
            f"<td>{render_rag_trace_replay_details(item)}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="5">暂无可回放 RAG trace。</td></tr>'


def render_rag_trace_replay_details(item: dict[str, object]) -> str:
    prompt = str(item.get("prompt", ""))
    context = str(item.get("context", ""))
    retrieval_trace = item.get("retrieval_trace", {})
    final_hits = retrieval_trace.get("final_hits", ()) if isinstance(retrieval_trace, dict) else ()
    hit_summary = _trace_replay_hit_summary(final_hits)
    return (
        "<details class='trace-replay'><summary>Prompt 回放详情</summary>"
        f"<h4>引用上下文</h4><pre>{escape(context[:1600] or '暂无引用上下文')}</pre>"
        f"<h4>Prompt</h4><pre>{escape(prompt[:2200] or '暂无 prompt')}</pre>"
        f"<h4>检索命中详情</h4><pre>{escape(hit_summary)}</pre>"
        "</details>"
    )


def _trace_replay_hit_summary(value: object) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return "暂无 final hits"
    lines = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"{item.get('chunk_id', '')} | parent={item.get('parent_id', '')} | "
            f"source={item.get('source_type', '')} | rerank={round(float(item.get('rerank_score', 0) or 0), 4)}"
        )
    return "\n".join(lines) or "暂无 final hits"


def _trace_id_list(value: object) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return "无"
    return "、".join(str(item) for item in value[:8])


def _trace_final_hit_rows(value: object) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return '<tr><td colspan="7">暂无精排命中。</td></tr>'
    rows = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('chunk_id', '')))}</td>"
            f"<td>{escape(str(item.get('parent_id', '')))}</td>"
            f"<td>{escape(str(item.get('source_type', '')))}</td>"
            f"<td>{escape(str(round(float(item.get('bm25_score', 0) or 0), 4)))}</td>"
            f"<td>{escape(str(round(float(item.get('vector_score', 0) or 0), 4)))}</td>"
            f"<td>{escape(str(round(float(item.get('rerank_score', 0) or 0), 4)))}</td>"
            f"<td>{escape(str(item.get('reason', '')))}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="7">暂无精排命中。</td></tr>'


def render_eval(agent: PuzzleOpsAgent, state: AppState) -> str:
    metrics = agent.eval_dashboard(state.country)
    trace = agent.run_agent_task(state.country, "value_judge")
    report = agent.eval_report(state.country)
    harness_summary = agent.harness_summary(state.country)
    baseline_summary = agent.harness_baseline_summary(state.country)
    gold_coverage = agent.harness_gold_coverage(state.country)
    readiness = agent.harness_readiness(state.country)
    business_acceptance = agent.harness_business_acceptance(state.country)
    front_two_layers = agent.front_two_layers_readiness(state.country)
    harness_samples = agent.harness_samples(state.country)
    harness_run = agent.harness_display_run(state.country)
    version_compare = agent.harness_compare(harness_run)
    sample_by_id = {sample.sample_id: sample for sample in harness_samples}
    sync_message = render_sync_message(state)
    context = hidden_context(state, view="eval")
    metric_cards = "".join(f"<article><span>{escape(key)}</span><strong>{escape(value)}</strong></article>" for key, value in metrics.items())
    harness_metric_cards = "".join(
        f"<article><span>{escape(key)}</span><strong>{escape(_harness_metric_text(harness_run, key, value))}</strong></article>"
        for key, value in harness_run.metrics.items()
    )
    summary_rows = "".join(
        f"<tr><td>{escape(key)}</td><td>{render_summary_value(value)}</td></tr>"
        for key, value in harness_summary.items()
    )
    baseline_cards = "".join(
        f"<article><span>{escape(key)}</span><strong>{escape(str(value))}</strong></article>"
        for key, value in baseline_summary.items()
        if key not in {"run_id"}
    )
    readiness_panel = render_harness_readiness(readiness)
    business_acceptance_panel = render_harness_business_acceptance(business_acceptance)
    front_two_layers_panel = render_front_two_layers_readiness(front_two_layers)
    real_harness_sample_count = sum(1 for sample in harness_samples if sample.is_real)
    gold_rows = render_harness_gold_workbench_rows(harness_samples, state)
    review_cases = list(harness_run.failures)
    seen_cases = {(case.sample_id, case.task_type) for case in review_cases}
    for case in harness_run.cases:
        key = (case.sample_id, case.task_type)
        has_skipped_score = any(score == "not_evaluable" for score in case.scores.values())
        if key not in seen_cases and has_skipped_score and case.task_type in {"trial_parse_eval", "value_match_eval", "audit_eval"}:
            review_cases.append(case)
            seen_cases.add(key)
    failure_cards = "".join(
        render_harness_failure_row(case, sample_by_id.get(case.sample_id))
        for case in review_cases[:6]
    )
    generation_failure_rows = render_generation_failure_distribution(agent.generation_events(state.country))
    case_evidence_rows = render_harness_case_evidence_rows(harness_run.cases)
    rag_artifact_rows = render_harness_rag_artifact_rows(harness_run.rag_trace_artifacts)
    failure_category_rows = render_harness_failure_categories(harness_run.failures)
    compare_rows = "".join(
        f"<tr><td>{escape(key)}</td><td>{escape(value)}</td></tr>"
        for key, value in version_compare.items()
    )
    eval_metric_rows = "".join(
        f"<tr><td>{escape(metric.name)}</td><td>{metric.score:.2f}</td><td>{metric.threshold:.2f}</td><td>{escape(metric.status)}</td><td>{escape(metric.reason)}</td></tr>"
        for metric in report.metric_results
    )
    case_rows = "".join(
        f"<tr><td>{escape(case.case_id)}</td><td>{escape(case.task_type)}</td><td>{escape('、'.join(case.expected_tools))}</td><td>{escape('、'.join(case.actual_tools))}</td><td>{escape(case.judge_reason)}</td></tr>"
        for case in report.cases
    )
    plan = "".join(f"<li>{escape(step)}</li>" for step in trace.plan)
    tools = "".join(f"<li>{escape(tool)}</li>" for tool in trace.tool_calls)
    observations = "".join(f"<li>{escape(item)}</li>" for item in trace.observations)
    acceptance_overview = render_eval_acceptance_overview(gold_coverage, business_acceptance, readiness, harness_run)
    prompt_benchmark_panel = render_prompt_benchmark_eval_panel(agent)
    value_benchmark_panel = render_value_prediction_benchmark_eval_panel(agent)
    return f"""
<section class="panel">
  <div class="section-line"><h2>上线验收中心</h2><div class="inline-actions"><form method="post" action="/export_harness_gold_skeleton">{context}<button>生成 Gold 骨架CSV</button></form><form method="post" action="/export_harness_overrides">{context}<button>导出人工修正CSV</button></form><form method="post" action="/export_harness_annotations">{context}<button>导出标注平台文件</button></form><form method="post" action="/export_harness_external_eval">{context}<button>导出外部评测文件</button></form></div></div>
  <h3>Harness Dashboard</h3>
  <p class="note">当前 Agent 是否具备上线验收条件：先看 human_gold、S/A预测准确率、RAG citation precision、风险漏召回和工具调用成功率；细节按分类展开。</p>
  <form class="harness-run-form" method="post" action="/run_harness">{context}<input type="hidden" name="run_real_models" value="1"><button class="primary">运行真实 VLM Harness</button><label><input type="checkbox" name="include_generation" value="1">包含付费生成评测</label><small>真实 VLM 会按图片样本调用模型并产生少量费用；默认不调用图像生成模型，勾选后会额外生成参考图。</small></form>
  {sync_message}
</section>
<details class="governance-section" open><summary>验收总览</summary>
  <p class="note">默认只保留核心上线状态；底层 gate 和指标表需要时再展开。</p>
  {acceptance_overview}
  <details class="compact-tools"><summary>查看前两层落地 gate</summary>{front_two_layers_panel}</details>
  <details class="compact-tools"><summary>查看业务上线验收表</summary>{business_acceptance_panel}</details>
  <details class="compact-tools"><summary>查看 Harness 指标明细</summary><section class="metrics">{harness_metric_cards}</section></details>
</details>
{prompt_benchmark_panel}
{value_benchmark_panel}
<details class="governance-section gold-dataset-section" open><summary>Gold Dataset</summary>
  <p class="note">登记真实样本、补 human_gold、审核 AI silver label；这是 Harness 的人工标准答案入口。</p>
  <section class="panel gold-dataset-guide">
    <h2>Gold Dataset 是什么</h2>
    <p>Gold Dataset 是上线验收标准答案集，用来检查 Agent 对真实拼图的等级预测、价值观判断、RAG 引用和工具链是否可靠。</p>
    <div class="overview-grid">
      <article class="overview-card"><strong>你需要做什么</strong><span>补充真实样本</span><small>把真实历史图、人工等级、开图率、完成率、完成时长登记进去。</small></article>
      <article class="overview-card"><strong>什么时候做</strong><span>验收前 / 复盘后</span><small>日常运营不需要每天维护，主要在上线验收、月度复盘或修正失败样本时使用。</small></article>
      <article class="overview-card"><strong>为什么要确认</strong><span>human_gold</span><small>AI 预标注只是 silver，人工确认后才会成为可信标准答案。</small></article>
    </div>
  </section>
  <section class="panel">
    <div class="section-line"><h2>Gold Dataset 工作台</h2><span class="status-pill">gold 完成率 {escape(str(gold_coverage.get("gold 完成率", "0%")))}</span></div>
    <div class="gold-coverage">
      <article><span>真实样本</span><strong>{escape(str(gold_coverage.get("真实样本数", 0)))}</strong></article>
      <article><span>完整 gold</span><strong>{escape(str(gold_coverage.get("完整 gold 样本数", 0)))}</strong></article>
      <article><span>缺失字段</span><strong>{escape(str(gold_coverage.get("缺失字段摘要", "无")))}</strong></article>
      <article><span>业务指标完成率</span><strong>{escape(str(gold_coverage.get("业务指标完成率", "0%")))}</strong></article>
      <article><span>缺失业务指标</span><strong>{escape(str(gold_coverage.get("缺失业务指标摘要", "无")))}</strong></article>
      <article><span>AI 预标注进度</span><strong>待预标注 {escape(str(gold_coverage.get("待 AI 预标注", 0)))} · 待审核 silver {escape(str(gold_coverage.get("待审核 silver", 0)))} · human_gold {escape(str(gold_coverage.get("human_gold 样本数", 0)))}</strong></article>
    </div>
    {readiness_panel}
    {render_harness_prelabel_job_progress(state)}
    {render_harness_approval_job_progress(state)}
    {render_gold_dataset_progress(gold_coverage)}
    <div class="gold-select-toolbar">
      <button type="button" data-select-form="prelabel-selected-form" onclick="selectGoldDatasetRows(this.dataset.selectForm, true)">全选待解析</button>
      <button type="button" data-select-form="prelabel-selected-form" onclick="selectGoldDatasetRows(this.dataset.selectForm, false)">清空待解析</button>
      <button type="button" data-select-form="approve-silver-form" onclick="selectGoldDatasetRows(this.dataset.selectForm, true)">全选待确认</button>
      <button type="button" data-select-form="approve-silver-form" onclick="selectGoldDatasetRows(this.dataset.selectForm, false)">清空待确认</button>
      <small>全选只作用于当前页面中可操作的样本，提交前仍可手动取消单条。</small>
    </div>
    <form id="prelabel-selected-form" class="harness-run-form gold-batch-form" method="post" action="/auto_prelabeled_harness_gold">{context}<input name="max_count" value="" inputmode="numeric" aria-label="本次最多预标注张数，可留空"><button class="primary">勾选样本 AI 预标注</button><small>先在下方图片前勾选“待解析”的样本，再一次性调用 Qwen 视觉模型；留空表示解析全部勾选项。</small></form>
    <form id="approve-silver-form" class="harness-run-form gold-batch-form" method="post" action="/approve_harness_silver_labels">{context}<input name="reviewer_note" value="人工抽查通过"><button class="primary">批量确认勾选 silver 为 human_gold</button><small>确认 AI 预标注为 human_gold：先在下方勾选已抽查通过的 silver label；确认后写入 facts memory，作为可信评测标准答案。</small></form>
    <details class="compact-tools"><summary>新增真实样本入口</summary>
      <form class="harness-run-form bulk-sample-form" method="post" action="/register_harness_real_samples">{context}<textarea name="samples_text" placeholder="A /Users/you/Desktop/france picnic.png&#10;/Users/you/Desktop/lavender.png,S,landscape,4,0.36,0.91,42,试新_法国_薰衣草风车0624,薰衣草风车"></textarea><button>批量登记真实样本</button><label><input type="checkbox" name="auto_prelabeled" value="1">登记后立即 AI 预标注</label><small>每行一张图；支持“等级 图片绝对路径”或“图片绝对路径,等级,分类,位置,开图率,完成率,平均完成时长,运营tag,主体”。图片只保存本机路径，不提交进 Git。</small></form>
      <form class="harness-run-form bulk-sample-form" method="post" action="/register_harness_real_samples">{context}<input name="image_dir" placeholder="~/Desktop/图片"><input name="directory_grade_text" placeholder="1A 2A 3B 4S 5C 或 文件名=A"><input name="directory_js_category" value="real_sample"><button>按目录登记真实样本</button><label><input type="checkbox" name="auto_prelabeled" value="1">登记后立即 AI 预标注</label><small>适合一批图片已放在同一文件夹的情况；序号按文件名排序，也可用“文件名=A”精确指定等级。只登记本机路径和人工等级，图片文件不提交进 Git。</small></form>
    </details>
    <small>Gold label 是 Harness 的人工标准答案；AI 预标注只是 silver label。运营保存确认后才会作为人工确认事实进入 memory/RAG。</small>
    <p class="note">当前显示 {real_harness_sample_count} / {real_harness_sample_count} 条真实样本。</p>
    <div class="table-wrap"><table class="gold-workbench"><thead><tr><th>批量选择</th><th>样本</th><th>等级</th><th>主体</th><th>色彩氛围</th><th>构图环境</th><th>价值观/风险</th><th>标注状态</th><th>操作</th></tr></thead><tbody>{gold_rows}</tbody></table></div>
  </section>
</details>
<details class="governance-section"><summary>失败样本</summary>
  <p class="note">集中看失败样本、失败分类和生成失败原因，方便做人工修正和下一轮治理。</p>
  <section class="grid two">
    <div class="panel"><h2>失败样本复盘</h2><div class="failure-review-list">{failure_cards or '<p class="empty">暂无失败样本。</p>'}</div></div>
    <div class="panel"><h2>失败分类</h2><div class="table-wrap"><table><thead><tr><th>分类</th><th>次数</th></tr></thead><tbody>{failure_category_rows}</tbody></table></div></div>
  </section>
  <section class="panel"><h2>生成失败类型分布</h2><div class="table-wrap"><table><thead><tr><th>错误类型</th><th>次数</th><th>处理建议</th></tr></thead><tbody>{generation_failure_rows}</tbody></table></div></section>
</details>
<details class="governance-section"><summary>RAG 证据</summary>
  <p class="note">查看每个评测 case 的 citation、trace、视觉证据和 Memory 证据。</p>
  <section class="panel"><h2>Case 证据链</h2><div class="table-wrap"><table><thead><tr><th>样本/任务</th><th>RAG 引用</th><th>RAG Trace</th><th>视觉证据</th><th>Memory 证据</th></tr></thead><tbody>{case_evidence_rows}</tbody></table></div></section>
  <section class="panel"><h2>Harness RAG Artifacts</h2><div class="table-wrap"><table><thead><tr><th>国家</th><th>Trace</th><th>Query</th><th>引用</th><th>文件</th><th>详情</th></tr></thead><tbody>{rag_artifact_rows}</tbody></table></div></section>
</details>
<details class="governance-section"><summary>运行历史</summary>
  <p class="note">查看数据集概览、本次运行、真实 baseline、版本对比和旧 Dashboard 指标。</p>
  <section class="grid two">
    <div class="panel"><h2>数据集概览</h2><div class="table-wrap"><table><tbody>{summary_rows}</tbody></table></div></div>
    <div class="panel"><h2>本次运行</h2><dl class="detail">
      <div><dt>run_id</dt><dd>{escape(harness_run.run_id)}</dd></div>
      <div><dt>版本</dt><dd>{escape(harness_run.version)}</dd></div>
      <div><dt>模型</dt><dd>{escape(harness_run.model_provider)}</dd></div>
      <div><dt>生成 provider</dt><dd>{escape(harness_run.generator_provider)}</dd></div>
      <div><dt>执行模式</dt><dd>{escape(harness_run.execution_mode)}</dd></div>
    </dl></div>
  </section>
  <section class="panel"><div class="section-line"><h2>真实 Baseline 复盘</h2><span class="status-pill">run {escape(str(baseline_summary.get('run_id', '')))}</span></div><div class="gold-coverage">{baseline_cards}</div></section>
  <section class="metrics">{metric_cards}</section>
  <section class="panel"><h2>版本对比</h2><div class="table-wrap"><table><tbody>{compare_rows}</tbody></table></div></section>
</details>
<details class="governance-section"><summary>Debug Trace</summary>
  <p class="note">研发排查区：任务目标、输入上下文、调用过程、Eval Dataset、Case 明细和 Agent Trace。</p>
  <section class="panel"><h2>任务目标</h2><p>验证内容运营 Agent 是否能围绕 {escape(state.country)} 市场完成价值观判断、历史样本检索、规则审核和同步前检查。</p></section>
  <section class="panel"><h2>输入与上下文</h2><dl class="detail"><div><dt>Skill</dt><dd>{escape(trace.skill_name)}</dd></div><div><dt>上下文</dt><dd>{escape(trace.context_summary)}</dd></div><div><dt>输出</dt><dd>{escape(trace.final_output)}</dd></div></dl></section>
  <section class="panel"><h2>工具调用链路</h2><div class="grid three"><div><h3>Plan</h3><ol>{plan}</ol></div><div><h3>Tool Calls</h3><ol>{tools}</ol></div><div><h3>Observations</h3><ol>{observations}</ol></div></div></section>
  <section class="panel"><h2>指标与结论</h2><h2>Eval Dataset</h2><p>{escape(report.dataset_name)} · {escape(report.country)} · 评测 RAG 召回、工具调用、计划遵循、步骤效率。</p><div class="table-wrap"><table><thead><tr><th>Metric</th><th>Score</th><th>Threshold</th><th>Pass/Fail</th><th>Reason</th></tr></thead><tbody>{eval_metric_rows}</tbody></table></div></section>
  <section class="panel"><h2>Case 明细</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>任务</th><th>期望工具</th><th>实际工具</th><th>Judge Reason</th></tr></thead><tbody>{case_rows}</tbody></table></div></section>
  <section class="panel"><h2>Agent Trace</h2><p>Trace 已在上方按输入、工具调用和指标结论拆解。</p></section>
</details>
<script>
function selectGoldDatasetRows(formId, checked) {{
  document.querySelectorAll('input[type="checkbox"][name="sample_id"][form="' + formId + '"]').forEach(function(item) {{
    item.checked = checked;
  }});
}}
</script>
"""


def render_prompt_benchmark_eval_panel(agent: PuzzleOpsAgent) -> str:
    country_rows = []
    all_records = []
    for country in agent.countries():
        records = agent.repository.description_benchmark_scores(country, limit=10_000)
        all_records.extend(records)
        summary = agent.repository.description_benchmark_summary(country)
        if not records:
            continue
        country_rows.append(
            "<tr>"
            f"<td>{escape(country)}</td>"
            f"<td>{escape(str(summary['count']))}</td>"
            f"<td>{escape(str(summary['template_average']))}</td>"
            f"<td>{escape(str(summary['prompt_average']))}</td>"
            f"<td>{int(float(summary['prompt_light_or_direct_rate']) * 100)}%</td>"
            "</tr>"
        )
    version_groups: dict[str, list[dict[str, object]]] = {}
    for record in all_records:
        version = _benchmark_prompt_version_label(record)
        version_groups.setdefault(version, []).append(record)
    version_rows = []
    for version, records in sorted(version_groups.items()):
        template_avg = _benchmark_record_average(records, "template_scores")
        prompt_avg = _benchmark_record_average(records, "prompt_scores")
        light_labels = {"可直接用", "轻微修改"}
        direct_rate = round(sum(1 for record in records if record.get("prompt_label") in light_labels) / len(records) * 100) if records else 0
        version_rows.append(
            "<tr>"
            f"<td>{escape(version)}</td>"
            f"<td>{len(records)}</td>"
            f"<td>{template_avg}</td>"
            f"<td>{prompt_avg}</td>"
            f"<td>{direct_rate}%</td>"
            "</tr>"
        )
    latest_records = sorted(all_records, key=lambda item: str(item.get("created_at", "")), reverse=True)[:8]
    history_rows = "".join(
        "<tr>"
        f"<td>{escape(str(record.get('created_at', '')))}</td>"
        f"<td>{escape(str(record.get('country', '')))}</td>"
        f"<td>{escape(str(record.get('operation_tag', '')))}</td>"
        f"<td>{_benchmark_single_average(record, 'template_scores')}</td>"
        f"<td>{_benchmark_single_average(record, 'prompt_scores')}</td>"
        f"<td>{escape(str(record.get('prompt_label', '')))}</td>"
        "</tr>"
        for record in latest_records
    )
    return f"""
<details class="governance-section"><summary>Prompt Benchmark</summary>
  <p class="note">主体描述 Prompt Benchmark 用来比较当前线上生成版本、Prompt baseline 和后续微调/模型候选版本，判断 prompt engineering 是否已足够，是否需要进入 post-training。</p>
  <section class="grid two">
    <div class="panel"><h2>主体描述 Prompt Benchmark</h2><div class="gold-coverage">
      <article><span>当前强 baseline</span><strong>Prompt baseline v3</strong><small>生产详细版：保留可执行画面细节，避免过度压缩。</small></article>
      <article><span>验收规则</span><strong>均分 ≥ 4.0</strong><small>生产可执行性 ≥ 4.0，可直接用/轻微修改 ≥ 80%。</small></article>
    </div></div>
    <div class="panel"><h2>版本对比</h2><div class="table-wrap"><table><thead><tr><th>版本</th><th>样本数</th><th>模板均分</th><th>Prompt均分</th><th>轻改/直用率</th></tr></thead><tbody>{''.join(version_rows) or '<tr><td colspan="5">暂无评分记录。</td></tr>'}</tbody></table></div></div>
  </section>
  <section class="panel"><h2>国家对比</h2><div class="table-wrap"><table><thead><tr><th>国家</th><th>样本数</th><th>模板均分</th><th>Prompt均分</th><th>轻改/直用率</th></tr></thead><tbody>{''.join(country_rows) or '<tr><td colspan="5">暂无评分记录。</td></tr>'}</tbody></table></div></section>
  <section class="panel"><h2>历史评分</h2><div class="table-wrap"><table><thead><tr><th>时间</th><th>国家</th><th>运营tag</th><th>模板均分</th><th>Prompt均分</th><th>Prompt标签</th></tr></thead><tbody>{history_rows or '<tr><td colspan="6">暂无评分记录。</td></tr>'}</tbody></table></div></section>
</details>
"""


def render_value_prediction_benchmark_eval_panel(agent: PuzzleOpsAgent) -> str:
    country_rows = []
    all_records = []
    for country in agent.countries():
        records = agent.repository.value_prediction_benchmark_scores(country, limit=10_000)
        all_records.extend(records)
        summary = agent.repository.value_prediction_benchmark_summary(country)
        if not records:
            continue
        country_rows.append(
            "<tr>"
            f"<td>{escape(country)}</td>"
            f"<td>{escape(str(summary['count']))}</td>"
            f"<td>{escape(str(summary['baseline_average']))}</td>"
            f"<td>{int(float(summary['candidate_light_or_direct_rate']) * 100)}%</td>"
            "</tr>"
        )
    latest_rows = "".join(
        "<tr>"
        f"<td>{escape(str(record.get('created_at', '')))}</td>"
        f"<td>{escape(str(record.get('country', '')))}</td>"
        f"<td>{escape(str(record.get('operation_tag', '')))}</td>"
        f"<td>{_benchmark_single_average(record, 'baseline_scores')}</td>"
        f"<td>{escape(str(record.get('baseline_label', record.get('candidate_label', ''))))}</td>"
        "</tr>"
        for record in sorted(all_records, key=lambda item: str(item.get("created_at", "")), reverse=True)[:8]
    )
    version_groups: dict[str, list[dict[str, object]]] = {}
    for record in all_records:
        metadata = record.get("metadata", {})
        version = str(metadata.get("candidate_version", "value_prompt_v1")) if isinstance(metadata, dict) else "value_prompt_v1"
        version_groups.setdefault(version, []).append(record)
    version_rows = "".join(
        "<tr>"
        f"<td>{escape(version)}</td>"
        f"<td>{len(records)}</td>"
        f"<td>{_benchmark_record_average(records, 'baseline_scores')}</td>"
        "</tr>"
        for version, records in sorted(version_groups.items())
    )
    return f"""
<details class="governance-section"><summary>价值观预测 Benchmark</summary>
  <p class="note">评测价值观大师是否真正看懂图片、引用对规则、找到合理历史依据，并输出可信等级、指标区间和排图建议。</p>
  <section class="grid two">
    <div class="panel"><h2>评分维度</h2><div class="gold-coverage">
      <article><span>视觉/RAG</span><strong>图像主体准确性</strong><small>国家价值观适配、历史依据合理性、RAG citation 有用性。</small></article>
      <article><span>预测/运营</span><strong>预测等级可信度</strong><small>风险识别、指标区间可信度、排图建议可执行性。</small></article>
    </div></div>
    <div class="panel"><h2>版本对比</h2><div class="table-wrap"><table><thead><tr><th>版本</th><th>样本数</th><th>模型均分</th></tr></thead><tbody>{version_rows or '<tr><td colspan="3">暂无评分记录。</td></tr>'}</tbody></table></div></div>
  </section>
  <section class="panel"><h2>国家对比</h2><div class="table-wrap"><table><thead><tr><th>国家</th><th>样本数</th><th>模型均分</th><th>轻改/直用率</th></tr></thead><tbody>{''.join(country_rows) or '<tr><td colspan="4">暂无评分记录。</td></tr>'}</tbody></table></div></section>
  <section class="panel"><h2>历史评分</h2><div class="table-wrap"><table><thead><tr><th>时间</th><th>国家</th><th>运营tag</th><th>模型均分</th><th>人工标签</th></tr></thead><tbody>{latest_rows or '<tr><td colspan="5">暂无评分记录。</td></tr>'}</tbody></table></div></section>
</details>
"""


def _benchmark_record_average(records: list[dict[str, object]], score_key: str) -> float:
    values = [_benchmark_single_average(record, score_key) for record in records]
    return round(sum(values) / len(values), 2) if values else 0.0


def _benchmark_prompt_version_label(record: dict[str, object]) -> str:
    metadata = record.get("metadata", {})
    if isinstance(metadata, dict) and metadata.get("prompt_version"):
        return f"Prompt baseline {metadata['prompt_version']}"
    prompt_output = str(record.get("prompt_output", ""))
    if len(prompt_output) >= 80 and _benchmark_single_average(record, "prompt_scores") >= 4.0:
        return "Prompt baseline v3（历史推断）"
    return "历史未标版本"


def _benchmark_single_average(record: dict[str, object], score_key: str) -> float:
    scores = record.get(score_key, {})
    if not isinstance(scores, dict):
        return 0.0
    values = []
    for value in scores.values():
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return round(sum(values) / len(values), 2) if values else 0.0


def render_generation_failure_distribution(events: tuple[dict[str, str], ...]) -> str:
    counts: dict[str, int] = {}
    hints: dict[str, str] = {}
    for event in events:
        if event.get("status") != "failed":
            continue
        error_type = event.get("error_type", "unknown") or "unknown"
        counts[error_type] = counts.get(error_type, 0) + 1
        if event.get("recovery_hint"):
            hints.setdefault(error_type, str(event.get("recovery_hint", "")))
    if not counts:
        return '<tr><td colspan="3">暂无生成失败记录。</td></tr>'
    return "".join(
        f"<tr><td>{escape(error_type)}</td><td>{count}</td><td>{escape(hints.get(error_type, ''))}</td></tr>"
        for error_type, count in sorted(counts.items())
    )


def render_eval_acceptance_overview(
    gold_coverage: dict[str, object],
    business_acceptance: dict[str, object],
    readiness: dict[str, object],
    harness_run,
) -> str:
    gates = business_acceptance.get("gates", ())
    if not isinstance(gates, (list, tuple)):
        gates = ()
    gate_by_name = {str(gate.get("name", "")): gate for gate in gates if isinstance(gate, dict)}
    status = "可验收" if business_acceptance.get("overall_passed") else "数据不足 / 风险较高"
    human_gold = gold_coverage.get("human_gold 样本数", 0)
    target = "30-50"
    sa_accuracy = _eval_metric_from_gate(gate_by_name, ("S/A 预测准确率", "S/A预测准确率"), harness_run.metrics.get("sa_prediction_accuracy"))
    citation_precision = _eval_metric_from_gate(gate_by_name, ("RAG citation precision",), harness_run.metrics.get("rag_citation_precision"))
    risk_miss = _eval_metric_from_gate(gate_by_name, ("风险漏召回", "国家文化风险漏召回"), harness_run.metrics.get("risk_miss_rate"))
    tool_success = _eval_metric_from_gate(gate_by_name, ("工具调用成功率",), harness_run.metrics.get("tool_success_rate"))
    failure_count = len(tuple(getattr(harness_run, "failures", ()) or ()))
    cards = (
        ("当前上线状态", status, "上线 gate 由真实样本、预测质量、RAG 证据和工具链共同决定。"),
        ("human_gold", f"{human_gold} / {target}", "生产验收建议每个国家 30-50 张真实 human_gold。"),
        ("S/A预测准确率", sa_accuracy, "价值观大师预测 S/A 的核心验收指标。"),
        ("RAG citation precision", citation_precision, "引用证据是否真的支撑判断。"),
        ("风险漏召回", risk_miss, "文化/IP/审核风险是否被漏掉。"),
        ("工具调用成功率", tool_success, "飞书、RAG、VLM 等工具链稳定性。"),
        ("失败样本数量", failure_count, "进入失败样本区复盘和修正。"),
        ("Readiness", "可真实评测" if readiness.get("ready_for_real_eval") else "待补数据", "Gold Dataset 和 Memory/RAG 基线是否足够。"),
    )
    return '<section class="panel"><h2>验收总览</h2><div class="overview-grid">' + "".join(render_overview_card(title, value, detail) for title, value, detail in cards) + "</div></section>"


def _eval_metric_from_gate(gates: dict[str, dict[str, object]], names: tuple[str, ...], fallback: object) -> str:
    for name in names:
        gate = gates.get(name)
        if gate:
            value = str(gate.get("value", ""))
            return value or "未评测"
    if isinstance(fallback, (float, int)):
        return _pct_text(float(fallback))
    return "未评测" if fallback is None else str(fallback)


def render_harness_readiness(readiness: dict[str, object]) -> str:
    ready = bool(readiness.get("ready_for_real_eval"))
    status = "可作为真实评测基线" if ready else "尚不能证明真实业务效果"
    next_actions = readiness.get("next_actions", ())
    if not isinstance(next_actions, (list, tuple)):
        next_actions = (str(next_actions),)
    action_items = "".join(f"<li>{escape(str(action))}</li>" for action in next_actions)
    return f"""
  <div class="readiness-panel {'ready' if ready else 'not-ready'}">
    <div><span>Harness Readiness</span><strong>{escape(status)}</strong></div>
    <div class="readiness-stats">
      <span>human_gold {escape(str(readiness.get("human_gold 样本数", 0)))}</span>
      <span>silver待审 {escape(str(readiness.get("待人工审核 silver", 0)))}</span>
      <span>待AI预标注 {escape(str(readiness.get("待 AI 预标注", 0)))}</span>
      <span>RAG gold文档 {escape(str(readiness.get("RAG human_gold 文档数", 0)))}</span>
      <span>Facts {escape(str(readiness.get("Facts memory gold 数", 0)))}</span>
    </div>
    <ol>{action_items}</ol>
  </div>
"""


def render_harness_business_acceptance(summary: dict[str, object]) -> str:
    gates = summary.get("gates", ())
    if not isinstance(gates, (list, tuple)):
        gates = ()
    rows = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        passed = bool(gate.get("passed"))
        rows.append(
            "<tr>"
            f"<td>{escape(str(gate.get('name', '')))}</td>"
            f"<td><span class=\"gate-status {'passed' if passed else 'failed'}\">{'通过' if passed else '待处理'}</span></td>"
            f"<td>{escape(str(gate.get('value', '')))}</td>"
            f"<td>{escape(str(gate.get('threshold', '')))}</td>"
            f"<td>{escape(str(gate.get('next_action', '')))}</td>"
            "</tr>"
        )
    status = "可进入上线验收" if summary.get("overall_passed") else "尚未满足上线验收"
    return f"""
<section class="panel">
  <div class="section-line"><h2>业务上线验收</h2><span class="status-pill">{escape(status)}</span></div>
  <p>上线集目标：30-50 张真实 human_gold 样本，每周滚动更新；当前真实样本 {escape(str(summary.get("real_sample_count", 0)))}，human_gold {escape(str(summary.get("human_gold_count", 0)))}。</p>
  <div class="table-wrap"><table class="readiness-table"><thead><tr><th>指标</th><th>状态</th><th>当前值</th><th>阈值</th><th>动作</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5">暂无验收指标。</td></tr>'}</tbody></table></div>
</section>
"""


def render_front_two_layers_readiness(readiness: dict[str, object]) -> str:
    layer1 = readiness.get("layer1_gates", ())
    layer2 = readiness.get("layer2_gates", ())
    rows = render_front_layer_gate_rows("第一层：闭环稳定", layer1) + render_front_layer_gate_rows("第二层：RAG / Memory", layer2)
    return f"""
<section class="panel">
  <div class="section-line"><h2>前两层落地验收</h2><span class="status-pill">{escape(str(readiness.get("overall_status", "unknown")))}</span></div>
  <p>{escape(str(readiness.get("waiting_for_third_layer", "")))}</p>
  <div class="table-wrap"><table class="readiness-table"><thead><tr><th>层级</th><th>Gate</th><th>状态</th><th>证据</th><th>后续动作</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>
"""


def render_front_layer_gate_rows(layer_name: str, gates: object) -> str:
    if not isinstance(gates, (list, tuple)) or not gates:
        return f'<tr><td>{escape(layer_name)}</td><td colspan="4">暂无 gate。</td></tr>'
    rows = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        passed = bool(gate.get("passed"))
        rows.append(
            "<tr>"
            f"<td>{escape(layer_name)}</td>"
            f"<td>{escape(str(gate.get('name', '')))}</td>"
            f"<td><span class=\"gate-status {'passed' if passed else 'failed'}\">{'通过' if passed else '待处理'}</span></td>"
            f"<td>{escape(str(gate.get('evidence', '')))}</td>"
            f"<td>{escape(str(gate.get('next_action', '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _harness_metric_text(run, key: str, value: float) -> str:
    if key in run.metric_evaluable_counts and run.metric_evaluable_counts[key] == 0:
        return "未评测"
    return _pct_text(value)


def render_harness_case_evidence_rows(cases) -> str:
    rows = []
    for case in cases[:12]:
        evidence = case.evidence_trace if isinstance(case.evidence_trace, dict) else {}
        citations = evidence.get("rag_citations", ())
        citation_text = "、".join(str(item) for item in citations) if isinstance(citations, (list, tuple)) else str(citations)
        rag_trace = str(evidence.get("rag_trace_path", "") or evidence.get("rag_trace_id", "") or "")
        memories = evidence.get("memory_evidence", ())
        memory_text = "；".join(str(item) for item in memories) if isinstance(memories, (list, tuple)) else str(memories)
        rows.append(
            "<tr>"
            f"<td>{escape(case.sample_id)}<br><small>{escape(case.task_type)}</small></td>"
            f"<td>{escape(citation_text or '无引用')}</td>"
            f"<td>{escape(rag_trace or '未记录')}</td>"
            f"<td>{escape(str(evidence.get('visual_evidence', '未记录')))}</td>"
            f"<td>{escape(memory_text or '未使用')}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="5">暂无 case trace。</td></tr>'


def render_harness_rag_artifact_rows(artifacts) -> str:
    if not isinstance(artifacts, (list, tuple)) or not artifacts:
        return '<tr><td colspan="6">暂无 Harness RAG artifacts。</td></tr>'
    rows = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        citations = item.get("citations", ())
        citation_text = "、".join(str(citation) for citation in citations[:6]) if isinstance(citations, (list, tuple)) else str(citations)
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('country', '')))}</td>"
            f"<td>{escape(str(item.get('trace_id', '')))}</td>"
            f"<td>{escape(str(item.get('original_query', ''))[:180])}</td>"
            f"<td>{escape(citation_text or '无')}</td>"
            f"<td>{escape(str(item.get('trace_path', '')))}</td>"
            f"<td>{render_rag_trace_replay_details(item)}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="6">暂无 Harness RAG artifacts。</td></tr>'


def render_harness_failure_categories(failures) -> str:
    counts: dict[str, int] = {}
    for case in failures:
        for category in case.failure_categories or ("uncategorized",):
            counts[category] = counts.get(category, 0) + 1
    if not counts:
        return '<tr><td colspan="2">暂无失败分类。</td></tr>'
    return "".join(f"<tr><td>{escape(category)}</td><td>{count}</td></tr>" for category, count in sorted(counts.items()))


def render_harness_gold_workbench_rows(samples, state: AppState) -> str:
    real_samples = [sample for sample in samples if sample.is_real]
    if not real_samples:
        return '<tr><td colspan="9">暂无真实图片样本。请先上传真实拼图或生成 Gold 骨架 CSV。</td></tr>'
    rows = []
    for sample in real_samples:
        prelabel_check = ""
        if sample.label_source == "manual_grade" and sample.label_status == "needs_ai_prelabeled":
            prelabel_check = (
                "<label class=\"inline-check\">"
                f"<input type=\"checkbox\" name=\"sample_id\" value=\"{escape(sample.sample_id)}\" form=\"prelabel-selected-form\">选中解析"
                "</label>"
            )
        approval_check = ""
        if sample.label_source == "ai_silver" and sample.label_status == "pending_review":
            approval_check = (
                "<label class=\"inline-check\">"
                f"<input type=\"checkbox\" name=\"sample_id\" value=\"{escape(sample.sample_id)}\" form=\"approve-silver-form\">选中确认"
                "</label>"
            )
        metric_status = render_harness_metric_status(sample)
        metric_inputs = render_harness_metric_inputs(sample)
        rows.append(
            "<tr>"
            f"<td><div class=\"gold-row-actions\">{prelabel_check}{approval_check or '<small>无需批量操作</small>'}</div></td>"
            f"<td>{render_harness_sample_cell(sample.sample_id, sample)}<input form=\"gold-{escape(sample.sample_id)}\" type=\"hidden\" name=\"sample_id\" value=\"{escape(sample.sample_id)}\"></td>"
            f"<td><input form=\"gold-{escape(sample.sample_id)}\" class=\"tiny-input\" name=\"gold_grade\" value=\"{escape(sample.gold_grade)}\" placeholder=\"S/A/B/C/D\"></td>"
            f"<td><textarea form=\"gold-{escape(sample.sample_id)}\" name=\"gold_subject\" placeholder=\"主体内容\">{escape(sample.gold_subject)}</textarea></td>"
            f"<td><textarea form=\"gold-{escape(sample.sample_id)}\" name=\"gold_color_mood\" placeholder=\"色彩氛围\">{escape(sample.gold_color_mood)}</textarea></td>"
            f"<td><textarea form=\"gold-{escape(sample.sample_id)}\" name=\"gold_composition\" placeholder=\"构图环境\">{escape(sample.gold_composition)}</textarea></td>"
            f"<td><textarea form=\"gold-{escape(sample.sample_id)}\" name=\"gold_value_labels\" placeholder=\"价值观标签；用分号分隔\">{escape(';'.join(sample.gold_value_labels))}</textarea>"
            f"<textarea form=\"gold-{escape(sample.sample_id)}\" name=\"gold_risk_labels\" placeholder=\"风险标签；可留空\">{escape(';'.join(sample.gold_risk_labels))}</textarea>"
            f"<textarea form=\"gold-{escape(sample.sample_id)}\" name=\"human_note\" placeholder=\"人工备注\">{escape(sample.human_note)}</textarea></td>"
            f"<td><span class=\"status-pill\">{escape(sample.label_source or 'unknown')}</span><br><small>{escape(sample.label_status or '未记录')}</small>{metric_status}{metric_inputs}</td>"
            f"<td><form id=\"gold-{escape(sample.sample_id)}\" method=\"post\" action=\"/save_harness_gold_label\">{hidden_context(state, view='eval')}<button>保存</button></form></td>"
            "</tr>"
        )
    return "".join(rows)


def render_gold_dataset_progress(gold_coverage: dict[str, object]) -> str:
    total = int(gold_coverage.get("真实样本数", 0) or 0)
    pending = int(gold_coverage.get("待 AI 预标注", 0) or 0)
    silver = int(gold_coverage.get("待审核 silver", 0) or 0)
    human_gold = int(gold_coverage.get("human_gold 样本数", 0) or 0)
    done = human_gold
    progress = int((done / total) * 100) if total else 0
    return f"""
<section class="gold-progress-panel">
  <div class="section-line"><h3>预标注进度</h3><span class="status-pill">{progress}% human_gold</span></div>
  <progress value="{progress}" max="100"></progress>
  <div class="readiness-stats"><span>真实样本 {total}</span><span>待解析 {pending}</span><span>待确认 silver {silver}</span><span>human_gold {human_gold}</span></div>
  <p class="note">推荐流程：先勾选待解析图片做 AI 预标注，再人工抽查 silver，最后批量确认 human_gold。</p>
</section>
"""


def render_harness_prelabel_job_progress(state: AppState) -> str:
    if not state.harness_prelabel_job_status:
        return ""
    progress = max(0, min(100, int(state.harness_prelabel_job_progress or 0)))
    status = state.harness_prelabel_job_status
    message = state.harness_prelabel_job_message or "Qwen 预标注任务处理中"
    return f"""
<section class="panel derivative-job-panel">
  <div class="section-line"><h2>Qwen 预标注进度</h2><span class="status-pill">{escape(status)}</span></div>
  <progress value="{progress}" max="100"></progress>
  <p class="note">{escape(message)}</p>
  <small>页面会每 3 秒自动刷新；解析完成后样本会变成 ai_silver，抽查后可批量确认 human_gold。</small>
</section>
"""


def render_harness_approval_job_progress(state: AppState) -> str:
    if not state.harness_approval_job_status:
        return ""
    progress = max(0, min(100, int(state.harness_approval_job_progress or 0)))
    status = state.harness_approval_job_status
    message = state.harness_approval_job_message or "human_gold 批量确认任务处理中"
    return f"""
<section class="panel derivative-job-panel">
  <div class="section-line"><h2>human_gold 批量确认进度</h2><span class="status-pill">{escape(status)}</span></div>
  <progress value="{progress}" max="100"></progress>
  <p class="note">{escape(message)}</p>
  <small>页面会每 3 秒自动刷新；确认完成后 ai_silver 会写入 facts memory 和 RAG human_gold。</small>
</section>
"""


def render_harness_metric_status(sample) -> str:
    missing = []
    if not sample.position:
        missing.append("position")
    metrics = sample.metrics or {}
    for field in ("open_rate", "completion_rate", "avg_finish_time"):
        if not metrics.get(field):
            missing.append(field)
    if missing:
        return f"<small class=\"metric-status metric-missing\">缺业务指标：{escape('、'.join(missing))}</small>"
    return "<small class=\"metric-status metric-complete\">业务指标齐全</small>"


def render_harness_metric_inputs(sample) -> str:
    metrics = sample.metrics or {}
    fields = (
        ("position", sample.position or ""),
        ("open_rate", metrics.get("open_rate", "")),
        ("completion_rate", metrics.get("completion_rate", "")),
        ("avg_finish_time", metrics.get("avg_finish_time", "")),
    )
    inputs = "".join(
        f"<label><span>{escape(label)}</span><input form=\"gold-{escape(sample.sample_id)}\" name=\"{escape(label)}\" value=\"{escape(_metric_input_value(value))}\"></label>"
        for label, value in fields
    )
    return f"<div class=\"metric-inputs\">{inputs}</div>"


def _metric_input_value(value: object) -> str:
    if value in ("", None):
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def render_summary_value(value: object) -> str:
    if isinstance(value, dict):
        return escape("；".join(f"{key}:{item}" for key, item in value.items()) or "无")
    return escape(str(value))


def render_harness_failure_row(case, sample) -> str:
    sample_cell = render_harness_sample_cell(case.sample_id, sample)
    gold = render_harness_gold_label(sample)
    failure_reasons = "；".join(case.failure_reasons) or "待运行真实模型"
    correction_form = f"""
<form method="post" action="/save_harness_override">
  <input type="hidden" name="sample_id" value="{escape(case.sample_id)}">
  <input type="hidden" name="task_type" value="{escape(case.task_type)}">
  <textarea name="human_override" placeholder="记录人工修正主体、色彩、构图、风险或价值观标签">{escape(case.human_override)}</textarea>
  <button>保存修正</button>
</form>
"""
    return f"""
<article class="failure-review-card">
  <div class="failure-review-sample">{sample_cell}<span class="status-pill">{escape(case.task_type)}</span></div>
  <dl class="failure-review-detail">
    <div><dt>Gold Label</dt><dd>{gold}</dd></div>
    <div><dt>Agent 输出</dt><dd>{escape(case.agent_output)}</dd></div>
    <div><dt>失败原因</dt><dd>{escape(failure_reasons)}</dd></div>
  </dl>
  <div class="failure-review-action">
    <h3>HITL 修正入口</h3>
    {correction_form}
  </div>
</article>
"""


def render_harness_sample_cell(sample_id: str, sample) -> str:
    if sample is None:
        return escape(sample_id)
    image_name = Path(sample.local_image_path).name if sample.local_image_path else sample.subject
    return (
        '<div class="harness-sample-cell">'
        f"{harness_sample_thumb(sample)}"
        f"<strong>{escape(sample.sample_id)}</strong>"
        f"<span>{escape(image_name)}</span>"
        f"<small>{escape(sample.operation_tag)}</small>"
        "</div>"
    )


def render_harness_gold_label(sample) -> str:
    if sample is None:
        return "Gold Label：未找到样本"
    parts = [
        f"gold_subject={sample.gold_subject or '待标注'}",
        f"gold_color_mood={sample.gold_color_mood or '待标注'}",
        f"gold_composition={sample.gold_composition or '待标注'}",
    ]
    if sample.gold_value_labels:
        parts.append(f"gold_value_labels={','.join(sample.gold_value_labels)}")
    if sample.gold_risk_labels:
        parts.append(f"gold_risk_labels={','.join(sample.gold_risk_labels)}")
    return "Gold Label：" + escape("；".join(parts))


def harness_sample_thumb(sample) -> str:
    path = Path(sample.local_image_path) if sample and sample.local_image_path else None
    if path and path.exists():
        src = local_image_url(path)
        return f'<div class="mini-thumb"><img src="{escape(src)}" class="mini-thumb-img" alt="{escape(path.name)}"></div>'
    return visual_thumb(sample.subject if sample else "missing", sample.subject if sample else "missing")


def local_image_url(path: Path) -> str:
    return "/local_image?" + urlencode({"path": str(path)})


def _pct_text(value: object) -> str:
    if isinstance(value, float):
        return f"{round(value * 100)}%"
    return str(value)


def render_record_card(record) -> str:
    return f"<article class='image-card'>{visual_thumb(record.subject_tag, record.subject_tag)}<strong>{grade(record.grade)} {escape(record.operation_tag)}</strong><small>开图 {record.open_rate:.2%} · 完成 {record.completion_rate:.2%} · {record.avg_finish_time}</small></article>"


def render_sync(agent: PuzzleOpsAgent, state: AppState) -> str:
    generation_rows = "".join(
        f"<tr><td>{escape(event.get('status', 'unknown'))}</td><td>{escape(event.get('provider', 'unknown'))}</td><td>{escape(event.get('model', '未记录'))}</td><td>{escape(event.get('task_id', ''))}</td><td>{escape(event.get('source_operation_tag', ''))}</td><td>{escape(event.get('second_review_status', 'unknown'))}</td><td>{escape(event.get('feishu_attachment_status', 'unknown'))}</td><td>{escape(event.get('error_type', 'unknown'))}</td><td>{escape(event.get('message', ''))}</td></tr>"
        for event in reversed(agent.generation_events(state.country)[-8:])
    )
    return f"""
<section class='panel'><h2>同步记录</h2>{render_feishu_lightweight_sync_history(agent, state)}</section>
<section class='panel'><h2>生成任务回放</h2><table><thead><tr><th>状态</th><th>Provider</th><th>模型</th><th>Task</th><th>来源tag</th><th>二次审核</th><th>飞书附件</th><th>错误类型</th><th>说明</th></tr></thead><tbody>{generation_rows or '<tr><td colspan="9">暂无生成任务记录。</td></tr>'}</tbody></table></section>
"""


def render_feishu_lightweight_sync_history(agent: PuzzleOpsAgent, state: AppState) -> str:
    rows = "".join(
        f"<tr><td>{escape(time)}</td><td>{escape(country)}</td><td>{escape(action)}</td><td>{escape(target)}</td><td>{escape(status)}</td></tr>"
        for time, country, action, target, status in agent.sync_rows()
    )
    return (
        "<p class=\"note\">兼容旧版同步日志；生产审计请以 Guarded Actions 和 Tools Console 为准。</p>"
        "<div class=\"table-wrap\"><table><thead><tr><th>时间</th><th>国家</th><th>动作</th><th>目标</th><th>状态</th></tr></thead>"
        f"<tbody>{rows or '<tr><td colspan=\"5\">暂无同步记录。</td></tr>'}</tbody></table></div>"
    )


def render_image_card(image) -> str:
    return f"<article class='image-card'>{visual_thumb(image.thumb, image.title)}<strong>{grade(image.grade)} {escape(image.title)}</strong><small>开图 {escape(image.open_rate)} · 完成 {escape(image.finish_rate)} · {escape(image.finish_time)}</small></article>"


def render_image_preview(image_name: str, image_url: str = "") -> str:
    src = image_url or image_data_uri(image_name, image_name)
    return f'<div class="image-preview-cell"><img src="{escape(src)}" class="mini-thumb-img" alt="{escape(image_name)}"><span>{escape(image_name)}</span></div>'


def visual_thumb(seed: str, label: str) -> str:
    path = Path(str(seed)).expanduser()
    src = local_image_url(path) if path.is_file() else image_data_uri(seed, label)
    return f'<div class="thumb visual-thumb"><img src="{escape(src)}" alt="{escape(label)}"></div>'


def render_upload_preview(item: dict[str, str], *, state: AppState | None = None, removable: bool = False) -> str:
    remove = ""
    if removable and state is not None:
        remove = (
            '<form method="post" action="/clear_trial_uploads" class="thumb-remove">'
            f'{hidden_context(state, view="trial")}'
            '<button title="取消当前上传">×</button></form>'
        )
    return f'<div class="thumb upload-thumb">{remove}<img src="{escape(item["url"])}" alt="{escape(item["filename"])}"><span>{escape(item["filename"])}</span></div>'


def render_line_chart(report) -> str:
    okr = int(report.sa_okr.rstrip("%"))
    return f"""<svg class="line-chart" viewBox="0 0 620 220" role="img" aria-label="SA CD AI 趋势对比折线图">
  <line x1="45" y1="40" x2="580" y2="40"></line><line x1="45" y1="95" x2="580" y2="95"></line><line x1="45" y1="150" x2="580" y2="150"></line>
  <line class="okr-line" x1="45" y1="{170 - okr}" x2="580" y2="{170 - okr}"></line>
  <polyline class="sa-line" points="55,94 170,82 285,74 400,68 545,62"></polyline>
  <polyline class="cd-line" points="55,152 170,148 285,145 400,140 545,137"></polyline>
  <polyline class="ai-line" points="55,132 170,126 285,123 400,118 545,114"></polyline>
  <text x="48" y="24">SA占比 {escape(report.sa_ratio)}</text><text x="250" y="24">CD占比 {escape(report.cd_ratio)}</text><text x="445" y="24">AI率 {escape(report.ai_ratio)}</text>
  <text x="470" y="{165 - okr}">OKR {escape(report.sa_okr)}</text>
</svg>"""


def render_metric_ratio(raw: str, higher_is_better: bool) -> str:
    current_text, okr_text = [part.strip() for part in raw.split("/", 1)]
    current = int(current_text.rstrip("%"))
    okr = int(okr_text.rstrip("%"))
    reached = current >= okr if higher_is_better else current <= okr
    status_class = "metric-ok" if reached else "metric-miss"
    alert = '<span class="metric-alert">!</span>' if abs(current - okr) > 10 else ""
    return (
        f'<span class="metric-value {status_class}">{escape(current_text)}</span>'
        f'<span class="metric-sep">/</span>'
        f'<span class="okr-value">{escape(okr_text)}</span>'
        f"{alert}"
    )


def render_ai_rate_ratio(raw: str) -> str:
    current_text, okr_text = [part.strip() for part in raw.split("/", 1)]
    current = int(current_text.rstrip("%"))
    okr = int(okr_text.rstrip("%"))
    status_class = "metric-bad" if current >= okr else "metric-ok"
    alert = '<span class="metric-alert">!</span>' if current - okr > 10 else ""
    return (
        f'<span class="metric-value {status_class}">{escape(current_text)}</span>'
        f'<span class="metric-sep">/</span>'
        f'<span class="okr-value">{escape(okr_text)}</span>'
        f"{alert}"
    )


def render_delta(raw: str, higher_is_better: bool) -> str:
    is_up = raw.strip().startswith("↑")
    good = is_up if higher_is_better else not is_up
    delta_class = "delta-good" if good else "delta-bad"
    return f'<em class="delta {delta_class}">{escape(raw)}</em>'


def select(name: str, options: tuple[str, ...], value: str) -> str:
    return f'<select class="small-input" name="{name}">' + "".join(f'<option value="{escape(option)}" {"selected" if option == value else ""}>{escape(option)}</option>' for option in options) + "</select>"


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
        "weekly_review": "周三复盘工作台",
        "value": "价值观大师",
        "runtime": "系统治理中心",
        "eval": "上线验收中心",
        "sync": "同步记录",
    }[view]


def view_icon(view: str) -> str:
    return {
        "dashboard": "🏠",
        "regular": "📦",
        "trial": "✨",
        "analysis": "📈",
        "weekly_review": "🔎",
        "value": "🔮",
        "runtime": "🧠",
        "eval": "🧪",
        "sync": "🔁",
    }[view]


def hidden_context(state: AppState, **overrides: str) -> str:
    values = {
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
        "show_prompt_benchmark": "1" if state.show_prompt_benchmark else "",
        "show_value_benchmark": "1" if state.show_value_benchmark else "",
    }
    values.update(overrides)
    return "".join(f'<input type="hidden" name="{key}" value="{escape(value)}">' for key, value in values.items())


def href(state: AppState, **changes: str) -> str:
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
        "show_holiday": "1" if state.show_holiday else "",
        "show_prompt_benchmark": "1" if state.show_prompt_benchmark else "",
        "show_value_benchmark": "1" if state.show_value_benchmark else "",
    }
    params.update({key: value for key, value in changes.items() if value is not None})
    return "/?" + urlencode(params)


def vision_mode_copy(status: dict[str, object]) -> str:
    provider = str(status.get("provider", "qwen"))
    model = str(status.get("model", ""))
    mode = str(status.get("mode", "missing"))
    if mode == "missing":
        missing = "、".join(str(item) for item in status.get("missing", ("QWEN_API_KEY",)))
        return escape(f"需要配置真实视觉 LLM：{missing}；当前不会进行语义解析")
    if mode == "real":
        return escape(f"真实 {provider} · {model}")
    return escape(f"非真实模式已禁用：{model}")


CSS = """
:root { --ink:#21313a; --muted:#65747e; --brand:#2f8f74; --soft:#f2f7f5; --line:#dbe5e3; --red:#d84a3a; --orange:#d78b24; }
* { box-sizing: border-box; }
body { margin:0; display:grid; grid-template-columns:280px 1fr; min-height:100vh; color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#f7f2e8; }
body.login-body { display:block; background:#f6f8fa; }
aside { padding:22px; background:#fffaf0; border-right:1px solid var(--line); }
main { padding:22px; min-width:0; overflow-x:hidden; }
header { display:flex; align-items:center; justify-content:space-between; margin-bottom:18px; }
header p { margin:0 0 4px; color:var(--muted); font-weight:800; }
h1, h2 { margin:0 0 12px; }
a { color:inherit; text-decoration:none; }
.brand { display:grid; gap:6px; margin-bottom:18px; }
.logo { width:52px; height:52px; display:grid; place-items:center; border-radius:14px; background:#e7f4ee; font-size:30px; }
.note { color:var(--muted); font-size:13px; line-height:1.6; }
.login-main { max-width:1420px; margin:0 auto; padding:26px; }
.login-brand { display:flex; align-items:center; gap:14px; margin-bottom:22px; }
.login-brand h1 { margin:0; font-size:30px; }
.login-brand p { margin:4px 0 0; color:var(--muted); font-weight:800; }
.login-logo { width:52px; height:52px; display:grid; place-items:center; border-radius:14px; background:#e7f4ee; font-size:30px; }
.login-grid { display:grid; grid-template-columns:1fr 1fr; gap:28px; }
.login-panel { box-shadow:none; }
.login-step { display:flex; align-items:center; gap:8px; margin:16px 0 10px; }
.login-step span { display:grid; place-items:center; width:26px; height:26px; border-radius:999px; background:#dff1ea; color:#17644e; font-weight:900; }
.login-list { display:grid; gap:8px; }
.login-row { display:grid; grid-template-columns:34px minmax(150px,1fr) auto; gap:10px; align-items:center; padding:11px 12px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.login-row.selected { border-color:var(--brand); background:#eefaf5; box-shadow:0 0 0 2px rgba(47,143,116,.08); }
.login-row small { grid-column:2 / 4; }
.country-row { grid-template-columns:34px minmax(90px,.45fr) 88px minmax(190px,1fr); }
.country-row small { grid-column:4; }
.perm { display:inline-flex; align-items:center; width:max-content; border-radius:999px; padding:3px 8px; font-size:12px; font-style:normal; font-weight:900; }
.perm.edit { background:#e7f4ee; color:#17644e; border:1px solid #b8d9ce; }
.perm.readonly { background:#eef1f4; color:#5f6c76; border:1px solid #d6dde3; }
.login-hint { margin:14px 0; padding:12px; border-radius:8px; font-weight:800; }
.login-hint.ok { background:#eefaf5; border:1px solid #b8d9ce; color:#17644e; }
.login-hint.readonly { background:#fff7d8; border:1px solid #e8c35b; color:#7a4a00; }
.login-enter { display:flex; justify-content:center; align-items:center; min-height:48px; margin-top:10px; }
.login-enter.readonly { background:#f0f3f5; color:#596873; }
.login-switch { display:block; margin-top:14px; color:#17644e; font-weight:900; text-align:center; }
.permission-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:12px 0 16px; }
.permission-card { border:1px solid var(--line); border-radius:8px; padding:12px; }
.permission-card.ok { background:#f1faf5; border-color:#b8d9ce; }
.permission-card.locked { background:#fff5f3; border-color:#efc8c2; }
.permission-card ul { margin:10px 0 0; padding-left:18px; line-height:2; }
.session-preview { padding:12px; border:1px solid #cbd8ee; border-radius:8px; background:#f4f8ff; font-weight:900; }
.header-preview { display:flex; align-items:center; gap:10px; margin-top:16px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.menu-icon, .mini-logo { display:grid; place-items:center; width:32px; height:32px; border-radius:8px; background:#e7f4ee; }
.session-card { display:grid; gap:6px; padding:12px; margin-bottom:14px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.session-card a { color:#17644e; font-weight:900; font-size:13px; }
.permission-strip { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:6px; color:var(--muted); font-weight:800; font-size:13px; }
.readonly-copy { color:#9b4d00; }
.pills { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
.pill, .button, button { border:1px solid var(--line); background:#fff; border-radius:8px; padding:9px 12px; cursor:pointer; font-weight:800; }
.pill.active, .nav.active, button.primary, .primary-link { background:#dff1ea; border-color:var(--brand); color:#17644e; }
nav { display:grid; gap:8px; margin:18px 0; }
.nav { padding:12px; border-radius:8px; }
.nav:hover, .choice:hover { background:#fff; }
.metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin-bottom:14px; }
.metrics article, .panel { background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px; box-shadow:0 10px 24px rgba(53,67,75,.06); }
.metrics span, small { color:var(--muted); }
.metrics strong { display:block; margin:8px 0; font-size:28px; }
.governance-section { margin:14px 0; border:1px solid var(--line); border-radius:10px; background:#fff; box-shadow:0 10px 24px rgba(53,67,75,.06); overflow:hidden; }
.governance-section > summary { cursor:pointer; padding:14px 16px; font-weight:900; font-size:18px; color:var(--accent); border-bottom:1px solid var(--line); list-style:none; }
.governance-section > summary::-webkit-details-marker { display:none; }
.governance-section > summary::before { content:"▸"; display:inline-block; margin-right:8px; color:var(--muted); }
.governance-section[open] > summary::before { transform:rotate(90deg); }
.governance-section:not([open]) > summary { border-bottom:0; }
.governance-section > .note, .governance-section > section, .governance-section > .grid, .governance-overview { margin:14px 16px; }
.governance-overview { display:grid; gap:14px; }
.governance-overview section { min-width:0; }
.overview-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
.overview-card { display:grid; gap:5px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#f8fbfa; min-width:0; }
.overview-card span { color:var(--accent); font-size:20px; font-weight:900; overflow-wrap:anywhere; }
.overview-card small { line-height:1.45; overflow-wrap:anywhere; }
.metric-value.metric-ok { color:#1f9d68; }
.metric-value.metric-miss { color:#d84a3a; }
.metric-value.metric-bad { color:#d84a3a; }
.metric-sep { margin:0 8px; color:#7d8991; }
.okr-value { color:#111; }
.metric-alert { display:inline-grid; place-items:center; width:24px; height:24px; margin-left:8px; border-radius:999px; background:#ffe1de; color:#d84a3a; font-size:16px; font-weight:900; vertical-align:middle; }
.delta { margin-left:6px; font-style:normal; font-weight:900; }
.delta-good { color:#1f9d68; }
.delta-bad { color:#d84a3a; }
.grid { display:grid; gap:14px; margin-bottom:14px; }
.grid > *, .panel { min-width:0; }
.grid.two { grid-template-columns:1.1fr .9fr; }
.grid.three { grid-template-columns:.75fr 1fr 1.4fr; }
.page-icon { display:inline-grid; place-items:center; width:42px; height:42px; margin-right:10px; border-radius:12px; background:#e7f4ee; vertical-align:middle; }
.timeline { display:grid; gap:10px; padding-left:20px; }
.timeline li textarea { margin-top:6px; min-height:54px; }
.tasks { display:grid; gap:10px; }
.tasks article { padding:12px; border-radius:8px; background:var(--soft); }
.tasks textarea { min-height:76px; }
.detail { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.detail div { padding:10px; background:var(--soft); border-radius:8px; }
.detail dt { font-weight:900; }
.detail dd { margin:4px 0 0; color:var(--muted); }
.compact-detail { margin:8px 0 12px; font-size:13px; }
.compact-detail div { padding:8px; }
.cards, .schedule { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }
.value-candidate-grid { grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); align-items:start; }
.value-selected-pool { margin:12px 0 16px; padding:12px; border:1px solid #b8d9ce; border-radius:8px; background:#f4fbf7; }
.value-selected-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; }
.value-selected-item { display:grid; gap:7px; padding:10px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.value-selected-item .visual-thumb { min-height:80px; }
.candidate-title { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; }
.candidate-summary, .candidate-evidence-summary { line-height:1.55; overflow-wrap:anywhere; }
.candidate-evidence-summary { padding:8px; border-radius:8px; background:#f8fbfa; color:#33434a; }
.candidate-human-note { margin:0; padding:7px 8px; border-radius:8px; background:#fff8de; color:#7a4a00; font-size:13px; font-weight:800; overflow-wrap:anywhere; }
.candidate-details { border:1px solid var(--line); border-radius:8px; background:#fff; padding:8px; }
.candidate-details summary { cursor:pointer; font-weight:900; color:#17644e; }
.candidate-details dl { display:grid; gap:6px; margin:8px 0 0; }
.candidate-details dl div { display:grid; grid-template-columns:70px minmax(0,1fr); gap:8px; padding:6px 0; border-top:1px solid #edf2ef; }
.candidate-details dt { font-weight:900; color:#1f2f36; }
.candidate-details dd { margin:0; color:var(--muted); overflow-wrap:anywhere; }
.candidate-details p { line-height:1.55; overflow-wrap:anywhere; }
.citation-chip-row, .risk-badges { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }
.citation-chip, .risk-badges span { display:inline-flex; align-items:center; max-width:100%; padding:4px 8px; border-radius:999px; border:1px solid #c7d9d1; background:#f1faf5; color:#17644e; font-size:12px; font-weight:900; overflow-wrap:anywhere; }
.citation-card-list { display:grid; gap:8px; margin-top:8px; }
.citation-card { display:grid; gap:5px; padding:9px 10px; border:1px solid #c7d9d1; border-radius:8px; background:#f8fbfa; }
.citation-card strong { color:#17644e; font-size:13px; line-height:1.35; overflow-wrap:anywhere; }
.citation-card p { margin:0; color:#33434a; font-size:13px; line-height:1.45; }
.citation-card small { color:var(--muted); font-size:11px; overflow-wrap:anywhere; }
.risk-badges span { border-color:#ecd19a; background:#fff8de; color:#7a4a00; }
.candidate-history-list { margin:8px 0; padding-left:18px; line-height:1.55; }
.candidate-history-list li { margin:6px 0; overflow-wrap:anywhere; }
.candidate-history-list small { display:block; }
.memory-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
.memory-card { display:grid; gap:6px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.memory-card span { color:var(--brand); font-weight:900; }
.memory-card small { line-height:1.5; overflow-wrap:anywhere; }
.memory-actions { display:grid; gap:6px; min-width:150px; }
.memory-actions form { display:grid; gap:4px; }
.memory-actions input { min-width:0; width:100%; }
.rag-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
.rag-grid article { display:grid; gap:6px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#f6faf8; }
.rag-grid span { color:var(--brand); font-weight:900; overflow-wrap:anywhere; }
.rag-grid small { line-height:1.5; overflow-wrap:anywhere; }
.trace-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:8px 0 12px; }
.trace-grid article { display:grid; gap:4px; padding:10px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.trace-grid small { line-height:1.45; overflow-wrap:anywhere; }
.trace-replay { min-width:260px; }
.trace-replay summary { cursor:pointer; font-weight:900; color:#17644e; }
.trace-replay pre { max-width:620px; max-height:260px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; padding:10px; border:1px solid var(--line); border-radius:8px; background:#f8fbfa; font-size:12px; line-height:1.45; }
.benchmark-list { display:grid; gap:14px; }
.comparison-card { display:grid; gap:12px; padding:14px; border:1px solid var(--line); border-radius:8px; background:#fbfdfc; min-width:0; }
.benchmark-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
.benchmark-head h3 { margin:0 0 4px; overflow-wrap:anywhere; }
.benchmark-output-grid, .benchmark-score-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.benchmark-output { min-width:0; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.benchmark-output p { margin:8px 0 0; line-height:1.6; overflow-wrap:anywhere; word-break:break-word; }
.benchmark-output small { display:block; margin-top:8px; line-height:1.45; overflow-wrap:anywhere; color:var(--muted); }
.benchmark-score-details summary { cursor:pointer; font-weight:900; color:#17644e; }
.prompt-pre { max-width:100%; max-height:260px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; padding:10px; border:1px solid var(--line); border-radius:8px; background:#f8fbfa; font-size:12px; line-height:1.45; }
.mode-grid, .reference-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.reference-row { grid-template-columns:repeat(3,minmax(0,1fr)); margin-top:12px; }
.mode-card { display:grid; gap:6px; padding:14px; border:1px solid var(--line); border-radius:10px; background:#fffdf7; }
.mode-card.active { border-color:var(--brand); background:#e7f4ee; }
.mode-card span { color:var(--muted); font-size:13px; }
.mock-upload-zone { min-height:150px; display:grid; place-items:center; padding:18px; border:2px dashed #b7c9c1; border-radius:10px; background:#f6faf8; text-align:center; }
.mock-upload-zone span { display:block; color:var(--muted); margin-top:6px; }
.image-card, .slot { display:grid; gap:8px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.thumb { min-height:95px; display:grid; place-items:center; padding:12px; border-radius:8px; background:linear-gradient(135deg,#e6f4ee,#fff0cb); text-align:center; font-weight:900; overflow:hidden; }
.visual-thumb { padding:0; aspect-ratio:3 / 2; background:#f1f5f3; }
.visual-thumb img { width:100%; height:100%; object-fit:cover; display:block; }
.upload-thumb img { max-width:100%; max-height:120px; border-radius:8px; object-fit:cover; }
.upload-thumb span { font-size:12px; color:var(--muted); }
.upload-thumb { position:relative; }
.thumb-remove { position:absolute; top:6px; right:6px; margin:0; }
.thumb-remove button { width:28px; height:28px; border-radius:50%; padding:0; display:grid; place-items:center; border:1px solid rgba(25,55,44,.22); background:#fff; color:#9c2f2f; font-size:18px; line-height:1; }
.candidate-card { display:grid; gap:8px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.card-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
.prompt-panel { display:grid; gap:10px; margin-top:12px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#f8fbfa; }
.prompt-panel h3 { margin:0; }
.prompt-panel label { display:grid; gap:6px; color:var(--muted); font-weight:900; }
.prompt-panel textarea { min-height:96px; color:var(--text); font-weight:700; line-height:1.5; }
.candidate-card .choice { justify-content:flex-start; align-items:center; margin:0; }
.candidate-card .choice input { width:auto; }
.candidate-card > .section-line { flex-wrap:wrap; justify-content:flex-start; align-items:stretch; }
.candidate-card > .section-line form { display:grid; grid-template-columns:minmax(120px,1fr) auto; gap:6px; flex:1 1 230px; }
.candidate-card > .section-line input { min-width:0; width:100%; }
.single-retry-form { display:flex; justify-content:flex-end; }
.mini-thumb { width:92px; min-height:64px; display:grid; place-items:center; padding:8px; border-radius:8px; background:linear-gradient(135deg,#f4efe2,#dff1ea); font-size:12px; font-weight:900; text-align:center; }
.mini-thumb-img { width:92px; height:64px; border-radius:8px; object-fit:cover; background:#f1f5f3; }
.harness-sample-cell { display:grid; grid-template-columns:92px minmax(150px,1fr); gap:8px 10px; align-items:center; min-width:260px; }
.harness-sample-cell .mini-thumb, .harness-sample-cell .visual-thumb { grid-row:1 / 4; width:92px; min-height:64px; }
.harness-sample-cell strong, .harness-sample-cell span, .harness-sample-cell small { overflow-wrap:anywhere; }
.failure-review-list { display:grid; gap:12px; }
.failure-review-card { display:grid; gap:12px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fbfdfc; min-width:0; }
.failure-review-sample { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.failure-review-sample .harness-sample-cell { min-width:min(100%, 360px); flex:1 1 260px; }
.failure-review-detail { display:grid; gap:8px; margin:0; }
.failure-review-detail div { display:grid; gap:5px; padding:10px; border-radius:8px; background:#fff; border:1px solid #edf2ef; }
.failure-review-detail dt { font-weight:900; color:var(--text); }
.failure-review-detail dd { margin:0; color:var(--muted); line-height:1.55; overflow-wrap:anywhere; word-break:break-word; }
.failure-review-action { display:grid; gap:8px; }
.failure-review-action h3 { margin:0; }
.failure-review-action textarea { min-height:70px; }
.status-pill { display:inline-flex; align-items:center; min-height:34px; padding:6px 10px; border:1px solid var(--brand); border-radius:999px; background:#e7f4ee; color:#17644e; font-weight:900; }
.compact-tools { margin:10px 0; border:1px solid var(--line); border-radius:8px; background:#fbfdfc; padding:0; }
.compact-tools > summary { cursor:pointer; padding:10px 12px; font-weight:900; color:#17644e; }
.compact-tools > .panel, .compact-tools > section, .compact-tools > form, .compact-tools > .metrics { margin:10px 12px 12px; }
.gold-progress-panel { display:grid; gap:8px; margin:10px 0 12px; padding:12px; border:1px solid #cfe2da; border-radius:8px; background:#f6fbf8; }
.gold-progress-panel progress { width:100%; height:14px; }
.gold-select-toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:10px 0 12px; padding:10px; border:1px solid var(--line); border-radius:8px; background:#fbfdfc; }
.gold-select-toolbar small { flex:1 1 260px; }
.gold-batch-form { grid-template-columns:minmax(110px, 160px) auto minmax(240px, 1fr); align-items:center; }
.gold-batch-form input[name="max_count"] { width:100%; }
.gold-row-actions { display:grid; gap:6px; min-width:110px; }
.gold-row-actions .inline-check { justify-content:flex-start; }
.gold-coverage { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin:8px 0 12px; }
.gold-coverage article { display:grid; gap:4px; padding:10px; border:1px solid var(--line); border-radius:8px; background:#f6faf8; }
.gold-coverage span { color:var(--muted); font-size:13px; font-weight:800; }
.gold-coverage strong { overflow-wrap:anywhere; }
.readiness-panel { display:grid; gap:10px; margin:10px 0 14px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.readiness-panel.ready { border-color:#4d8f72; background:#f1faf5; }
.readiness-panel.not-ready { border-color:#d6b45c; background:#fff8de; }
.readiness-panel > div:first-child { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.readiness-panel span { color:var(--muted); font-weight:800; }
.readiness-panel strong { color:var(--text); font-size:18px; overflow-wrap:anywhere; }
.readiness-stats { display:flex; gap:8px; flex-wrap:wrap; }
.readiness-stats span { padding:4px 8px; border-radius:999px; background:#fff; border:1px solid var(--line); font-size:12px; color:#2f5c4f; }
.readiness-panel ol { margin:0; padding-left:22px; line-height:1.55; }
.readiness-table td { vertical-align:top; }
.gate-status { display:inline-flex; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:900; }
.gate-status.passed { background:#e7f4ee; color:#17644e; }
.gate-status.failed { background:#fff0cb; color:#8a5a00; }
.harness-run-form { margin:10px 0; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.harness-run-form small { color:var(--muted); }
.bulk-sample-form { align-items:flex-start; }
.bulk-sample-form textarea { min-height:86px; flex:1 1 520px; }
.gold-workbench th, .gold-workbench td { vertical-align:top; }
.gold-workbench textarea { min-height:74px; min-width:160px; resize:vertical; }
.gold-workbench .tiny-input { width:72px; min-width:72px; }
.gold-workbench .harness-sample-cell { min-width:230px; }
.inline-check { display:flex; align-items:center; gap:6px; margin-top:8px; font-size:12px; color:#2f5c4f; }
.metric-status { display:block; margin-top:6px; font-weight:800; }
.metric-missing { color:#9b4d00; }
.metric-complete { color:#17644e; }
.metric-inputs { display:grid; grid-template-columns:repeat(2,minmax(82px,1fr)); gap:6px; margin-top:8px; }
.metric-inputs label { display:grid; gap:3px; font-size:11px; color:var(--muted); font-weight:800; }
.metric-inputs input { min-width:0; padding:6px; }
.image-preview-cell { display:grid; grid-template-columns:92px minmax(140px,1fr); align-items:center; gap:10px; min-width:260px; }
.choice { display:flex; justify-content:space-between; gap:10px; padding:10px; margin-bottom:8px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.choice.stock-hot { border-color:#e26357; background:#ffe9e5; color:#9b281f; }
.choice.stock-low { border-color:#e8c35b; background:#fff7d8; color:#7a4a00; }
.choice.stock-normal { background:#fffdf7; }
.alert { color:#996b00; background:#fff7d8; border-radius:8px; padding:10px; }
.section-line { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.inline-actions { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
.table-wrap { overflow-x:auto; }
.sync-success-card { display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap; margin-bottom:14px; padding:12px; border:1px solid #b8d9ce; border-radius:8px; background:#eefaf5; }
.sync-success-card p { margin:0; }
.sync-success-card small { color:var(--muted); }
.demand-card-list { display:grid; gap:12px; }
.demand-card { border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px; }
.demand-card-head { display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:10px; }
.demand-card-head strong { font-size:14px; }
.demand-check { display:inline-flex; align-items:center; gap:6px; width:auto; margin-right:10px; font-weight:800; }
.demand-check input { width:auto; }
.demand-card-grid { display:grid; grid-template-columns:minmax(180px,1.1fr) minmax(220px,1.4fr) minmax(140px,.8fr) 72px 118px 150px 110px; gap:10px; align-items:start; }
.demand-card label { display:grid; gap:6px; min-width:0; font-weight:800; color:var(--muted); }
.demand-card label > span { font-size:12px; }
.demand-card input, .demand-card select, .demand-card textarea { color:var(--text); font-weight:700; }
.image-field .image-preview-cell { min-width:0; grid-template-columns:78px minmax(0,1fr); }
.image-field .mini-thumb-img, .image-field .mini-thumb { width:78px; height:54px; min-height:54px; }
.readonly-field { min-height:38px; padding:8px; border:1px solid var(--line); border-radius:6px; background:#f8fbfa; color:var(--text); font-weight:800; overflow-wrap:anywhere; }
.readonly-long { min-height:92px; padding:10px; border:1px solid var(--line); border-radius:6px; background:#f8fbfa; color:var(--text); font-weight:700; line-height:1.55; white-space:pre-wrap; }
.demand-long-fields { display:grid; grid-template-columns:1.35fr .85fr; gap:10px; margin-top:10px; }
.demand-long-fields .span-2 { grid-column:1 / -1; }
.demand-table { width:100%; min-width:2200px; table-layout:fixed; border-collapse:collapse; }
.regular-demand-table { min-width:1980px; }
.col-check { width:48px; }
.col-type { width:76px; }
.col-country { width:76px; }
.col-category { width:86px; }
.col-image { width:260px; }
.col-tag { width:310px; }
.col-subject { width:150px; }
.col-count { width:72px; }
.col-priority { width:118px; }
.col-method { width:150px; }
.col-date { width:92px; }
.col-description { width:520px; }
.wide-description { width:620px; }
.col-remark { width:220px; }
.col-value { width:760px; }
table { width:100%; border-collapse:collapse; }
th, td { padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:13px; overflow-wrap:anywhere; word-break:break-word; }
th { background:#eef5f2; }
input, select, textarea { width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; font:inherit; background:#fff; }
.operation-tag-input { min-width:280px; font-size:13px; }
.small-input { min-width:0; }
textarea { min-height:58px; resize:vertical; }
.description-input { min-height:132px; line-height:1.55; }
.wide { min-height:120px; }
@media (max-width: 1180px) {
  .demand-card-grid { grid-template-columns:repeat(3, minmax(0,1fr)); }
  .image-field { grid-column:1 / -1; }
}
@media (max-width: 760px) {
  .demand-card-grid, .demand-long-fields { grid-template-columns:1fr; }
  .demand-card-head { align-items:flex-start; flex-direction:column; }
}
.line-chart { width:100%; max-width:760px; height:auto; }
.line-chart line { stroke:#d7e1df; stroke-width:2; }
.line-chart .okr-line { stroke:#e8c35b; stroke-width:3; stroke-dasharray:8 8; }
.line-chart polyline { fill:none; stroke-width:4; stroke-linecap:round; stroke-linejoin:round; }
.line-chart .sa-line { stroke:#2f8f74; }
.line-chart .cd-line { stroke:#d84a3a; }
.line-chart .ai-line { stroke:#5c7cfa; }
.line-chart text { fill:#50616b; font-size:15px; font-weight:800; }
.grade { display:inline-block; min-width:24px; padding:2px 7px; border-radius:999px; text-align:center; font-weight:900; }
.grade.S { background:#1f9d68; color:#fff; }
.grade.A { background:#b8ebbb; color:#1d5d31; }
.grade.B { background:#ffd188; color:#7a4a00; }
.grade.C { background:#ffd8d5; color:#9b281f; }
.grade.D { background:#ef6b5b; color:#fff; }
.pos { display:inline-block; padding:3px 8px; border-radius:999px; background:#ffe1de; color:var(--red); font-weight:900; }
.empty { color:var(--muted); background:#f8faf9; padding:12px; border-radius:8px; }
@media (max-width: 900px) { body { grid-template-columns:1fr; } aside { border-right:0; border-bottom:1px solid var(--line); } .metrics, .grid.two, .grid.three, .detail, .memory-grid, .rag-grid, .trace-grid, .gold-coverage, .benchmark-output-grid, .benchmark-score-grid { grid-template-columns:1fr; } }
"""
