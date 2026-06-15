from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import urlencode
import base64

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.models import DemandRow
from puzzle_ops.visual_assets import image_data_uri


@dataclass
class AppState:
    country: str = "日本"
    view: str = "dashboard"
    category: str = "人物"
    tag: str = "常规_日本_传统浴袍美女0604"
    trial_mode: str = "parse"
    schedule_day: str = "周一"
    value_grade: str = "S"
    show_holiday: bool = False
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
    generation_event: dict[str, str] = field(default_factory=dict)


def render_page(agent: PuzzleOpsAgent, state: AppState) -> str:
    normalize_state(agent, state)
    body = {
        "dashboard": render_dashboard,
        "regular": render_regular,
        "trial": render_trial,
        "analysis": render_analysis,
        "value": render_value,
        "runtime": render_runtime,
        "eval": render_eval,
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
      <div><p>{escape(state.country)}市场</p><h1><span class="page-icon">{view_icon(state.view)}</span>{page_title(state.view)}</h1></div>
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
    if not state.workflow_notes:
        state.workflow_notes = [text for _, text in workflow_items()]
    if not state.task_notes:
        state.task_notes = [task["body"] for task in agent.dashboard(state.country)["tasks"]]


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
        ("runtime", "🧠", "多模态底座"),
        ("eval", "🧪", "Agent 评测"),
        ("schedule", "🗓️", "排图工作台"),
        ("sync", "🔁", "同步记录"),
    )
    links = [f'<a class="nav {"active" if key == state.view else ""}" href="{href(state, view=key)}">{icon} {label}</a>' for key, icon, label in items]
    return "<nav>" + "".join(links) + "</nav>"


def render_dashboard(agent: PuzzleOpsAgent, state: AppState) -> str:
    dashboard = agent.dashboard(state.country)
    holiday = agent.holiday_recommendation(state.country)
    tasks = "".join(
        f'<article><strong>{escape(task["title"])}</strong><textarea name="task_{index}">{escape(state.task_notes[index])}</textarea></article>'
        for index, task in enumerate(dashboard["tasks"])
    )
    images = "".join(render_image_card(image) for image in holiday.history_good_images)
    holiday_panel = render_holiday_panel(holiday, images) if state.show_holiday else ""
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
<section class="panel compact-panel">
  <h2>节日提醒</h2>
  <p>{escape(holiday.name)}：{escape(holiday.date_range)}，点击按钮查看完整节日提需建议。</p>
  <a class="button primary-link" href="{href(state, view='dashboard', show_holiday='1')}">查看完整节日提需建议</a>
</section>
{holiday_panel}
"""


def render_holiday_panel(holiday, images: str) -> str:
    return f"""<section class="panel">
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


def render_regular(agent: PuzzleOpsAgent, state: AppState) -> str:
    categories = "".join(f'<a class="choice {"active" if name == state.category else ""}" href="{href(state, view="regular", category=name)}">{escape(name)}</a>' for name in agent.categories(state.country))
    tags = "".join(render_tag_choice(state, tag) for tag in agent.sorted_tags(state.country, state.category))
    images = "".join(render_reference_image(state, image, index) for index, image in enumerate(agent.images_for_tag(state.country, state.tag)))
    rows = render_need_rows(state.need_rows)
    sync_message = render_sync_message(state)
    feishu_status = agent.feishu.config_status()
    if agent.feishu.is_real:
        feishu_copy = f"真实飞书：{escape(str(feishu_status.get('spreadsheet_token', '')))} · {escape(str(feishu_status.get('sheet_range', '')))}"
    else:
        feishu_copy = f"真实飞书未配置，缺少：{escape('、'.join(feishu_status['missing']))}"
    context = hidden_context(state, view="regular")
    return f"""
<section class="grid three">
  <div class="panel"><h2>分类</h2>{categories}</div>
  <div class="panel"><h2>运营 tag + 库存</h2><p class="alert">红色=低库存爆款；黄色=低库存稳定款；其他=正常库存。</p>{tags}</div>
  <div class="panel"><h2>已分发图片参考</h2><div class="cards">{images}</div></div>
</section>
<section class="panel">
  <div class="section-line"><h2>批量提需清单</h2>
    <form method="post" action="/generate_descriptions">{context}<button>AI生成描述</button></form>
  </div>
  <p class="note">{feishu_copy}</p>
  {sync_message}
  <form method="post" action="/save_needs">{context}{rows}<div class="section-line"><button class="primary">保存表格修改</button><button formaction="/sync_needs_feishu" formmethod="post">一键同步到飞书表格</button></div></form>
</section>
"""


def render_tag_choice(state: AppState, tag) -> str:
    hot = " " + PuzzleOpsAgent().stock_class(tag)
    active = " active" if tag.tag == state.tag else ""
    return f'<a class="choice{active}{hot}" href="{href(state, view="regular", tag=tag.tag)}"><strong>{escape(tag.tag)}</strong><span>库存 {tag.stock}</span></a>'


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
    body = "".join(render_need_card(row, index, include_value=False) for index, row in enumerate(rows))
    return f'<div class="demand-card-list regular-demand-list">{body}</div>'


def render_trial(agent: PuzzleOpsAgent, state: AppState) -> str:
    vision_status = agent.vision_llm_status()
    generation_status = agent.generation_provider_status()
    mode_links = "".join(
        f'<a class="mode-card {"active" if state.trial_mode == mode else ""}" href="{href(state, view="trial", trial_mode=mode)}"><strong>{label}</strong><span>{copy}</span></a>'
        for mode, label, copy in (
            ("parse", "参考图解析提需", "上传 1-3 张参考图，AI解析主体/色彩/构图。"),
            ("derive", "好图衍生提需", "上传 1 张历史好图，解析可复用视觉特征并整理衍生方向。"),
        )
    )
    row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
    rows = state.trial_rows or [row]
    is_derive_mode = state.trial_mode == "derive" or "衍生" in row.operation_tag or "衍生" in row.image_name
    row_html = (
        "".join(render_need_card(item, index, include_value=True) for index, item in enumerate(rows))
        if state.trial_rows
        else render_need_card(row, 0, include_value=True, prefix="")
    )
    upload_copy = "拖拽或选择 1-3 张参考图" if state.trial_mode == "parse" else "上传单张历史好图，解析衍生方向"
    previews = "".join(render_upload_preview(item) for item in state.trial_uploads) or '<div class="thumb">参考图 A</div><div class="thumb">参考图 B</div><div class="thumb">参考图 C</div>'
    sync_message = render_sync_message(state)
    context = hidden_context(state, view="trial")
    derivative_form = (
        f'<form method="post" action="/generate_trial_derivatives">{context}<button>生成衍生参考图</button></form>'
        if is_derive_mode
        else ""
    )
    generation_diagnostic = render_generation_provider_diagnostic(generation_status)
    generation_event = render_generation_event(state.generation_event)
    return f"""
<section class="panel"><h2>试新模式</h2><div class="mode-grid">{mode_links}</div></section>
<section class="grid two">
  <div class="panel"><h2>上传参考图</h2><div class="mock-upload-zone"><strong>{upload_copy}</strong><span>可上传本地图片进行结构化解析；未接 LLM 时使用本地解析适配层。</span><form method="post" action="/upload_trial_images" enctype="multipart/form-data">{context}<input type="file" name="trial_images" accept="image/*" multiple><button>上传并解析图片</button></form><form method="post" action="/simulate_trial_upload">{context}<button>模拟上传并解析</button></form>{derivative_form}</div><div class="reference-row">{previews}</div></div>
  <div class="panel"><h2>解析状态</h2><p class="alert">解析结果已写入下方试新提需表，可在表格中继续编辑后同步飞书。</p><dl class="detail"><div><dt>视觉 LLM 语义解析</dt><dd>{vision_mode_copy(vision_status)}</dd></div><div><dt>图像生成 Provider</dt><dd>{escape(str(generation_status.get("message", "生成 provider 未配置")))}</dd></div><div><dt>当前图片</dt><dd>{escape(row.image_name)}</dd></div><div><dt>解析备注</dt><dd>{escape(row.remark or "等待上传图片")}</dd></div></dl>{generation_diagnostic}{generation_event}<form method="post" action="/check_generation_provider">{context}<button>检查生成 Provider</button></form></div>
</section>
<section class="panel">
  <div class="section-line"><h2>试新提需表预览</h2><form method="post" action="/apply_value_master">{context}<button>价值观大师</button></form></div>
  {sync_message}
  <form method="post" action="/save_trial">{context}<div class="demand-card-list trial-demand-list">{row_html}</div><div class="section-line"><button class="primary">保存试新修改</button><button formaction="/sync_trial_feishu" formmethod="post">一键同步到飞书表格</button></div></form>
</section>
"""


def render_generation_provider_diagnostic(status: dict[str, object]) -> str:
    fields = (
        ("provider", status.get("provider", "not_configured")),
        ("configured", status.get("configured", False)),
        ("model", status.get("model", "未配置")),
        ("endpoint", status.get("base_url") or status.get("submit_url") or "未配置"),
    )
    rows = "".join(f"<div><dt>{escape(key)}</dt><dd>{escape(str(value))}</dd></div>" for key, value in fields)
    return f"<h3>生成 Provider 诊断</h3><dl class=\"detail compact-detail\">{rows}</dl>"


def render_generation_event(event: dict[str, str]) -> str:
    if not event:
        return ""
    fields = (
        ("状态", event.get("status", "unknown")),
        ("provider", event.get("provider", "unknown")),
        ("model", event.get("model", "未记录")),
        ("task_id", event.get("task_id", "")),
        ("来源tag", event.get("source_operation_tag", "")),
        ("生成图", event.get("generated_image_paths", "")),
        ("二次审核", event.get("second_review_status", "unknown")),
        ("飞书附件", event.get("feishu_attachment_status", "unknown")),
        ("错误类型", event.get("error_type", "无")),
        ("说明", event.get("message", "")),
    )
    rows = "".join(f"<div><dt>{escape(key)}</dt><dd>{escape(str(value))}</dd></div>" for key, value in fields)
    return f"<h3>最近一次生成任务</h3><dl class=\"detail compact-detail generation-event\">{rows}</dl>"


def render_sync_message(state: AppState) -> str:
    if not state.sync_message:
        return ""
    if state.sync_url and state.sync_message.startswith("同步成功"):
        return f"""
<div class="sync-success-card">
  <p class="success">{escape(state.sync_message)}</p>
  <a class="button primary-link" href="{escape(state.sync_url)}" target="_blank" rel="noopener">已同步，打开飞书表格</a>
  <small>如果浏览器没有自动打开新页，可以点击这个按钮进入飞书表格。</small>
</div>
"""
    return f'<p class="success">{escape(state.sync_message)}</p>'


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


def render_need_card(row: DemandRow, index: int, include_value: bool, prefix: str | None = None) -> str:
    prefix = f"_{index}" if prefix is None else prefix
    select_html = '<label class="demand-check"><input type="checkbox" checked>选择</label>' if include_value else ""
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
    <label class="image-field"><span>图片本身</span>{render_image_preview(row.image_name, row.reference_image_url)}</label>
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


def render_value(agent: PuzzleOpsAgent, state: AppState) -> str:
    tabs = "".join(f'<a class="pill {"active" if grade == state.value_grade else ""}" href="{href(state, view="value", value_grade=grade)}">{grade}</a>' for grade in ("S", "A", "B", "C", "D"))
    cards = "".join(f"<article class='image-card'>{visual_thumb(card.image.thumb, card.image.title)}<strong>{escape(card.operation_tag)}</strong><p>{grade(card.image.grade)} 预测等级</p><small>开图 {escape(card.image.open_rate)} · 完成 {escape(card.image.finish_rate)} · {escape(card.image.finish_time)}</small><p>{escape(card.prediction_remark)}</p></article>" for card in agent.value_predictions(state.country, state.value_grade))
    rules = "".join(f"<li><strong>{escape(title)}</strong>：{escape(body)}</li>" for title, body in agent.value_rules(state.country))
    return f"<section class='panel'><h2>SABCD 预测</h2><div class='pills'>{tabs}</div><div class='cards'>{cards or '<p class=\"empty\">当前等级暂无样例。</p>'}</div></section><section class='panel'><details><summary>查看完整价值观规则库</summary><ul>{rules}</ul></details></section>"


def render_runtime(agent: PuzzleOpsAgent, state: AppState) -> str:
    profile = agent.multimodal_profile(state.country)
    vision_status = agent.vision_llm_status()
    candidates = agent.value_rule_candidates(state.country)
    approved_rules = agent.approved_value_rules(state.country)
    memories = agent.hitl_memories(state.country)
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
    feature = profile.feature
    return f"""
<section class="panel">
  <h2>多模态底座</h2>
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
<section class="panel"><h2>价值观候选池</h2><div class="table-wrap"><table><thead><tr><th>候选价值观</th><th>置信度</th><th>支撑样本</th><th>反例样本</th><th>状态</th><th>Agent归因</th><th>运营审核</th></tr></thead><tbody>{candidate_rows}</tbody></table></div></section>
<section class="grid two">
  <div class="panel"><h2>已审批价值观规则</h2><div class="table-wrap"><table><thead><tr><th>国家</th><th>规则</th><th>状态</th></tr></thead><tbody>{approved_rows or '<tr><td colspan="3">暂无已审批规则，点击上方候选池“通过”后会写入这里。</td></tr>'}</tbody></table></div></div>
  <div class="panel"><h2>HITL Memory</h2><ul>{memory_items or '<li>暂无人工反馈记忆。</li>'}</ul></div>
</section>
"""


def render_eval(agent: PuzzleOpsAgent, state: AppState) -> str:
    metrics = agent.eval_dashboard(state.country)
    trace = agent.run_agent_task(state.country, "value_judge")
    report = agent.eval_report(state.country)
    harness_summary = agent.harness_summary(state.country)
    harness_samples = agent.harness_samples(state.country)
    harness_run = agent.harness_run(state.country)
    version_compare = agent.harness_compare(harness_run)
    sample_by_id = {sample.sample_id: sample for sample in harness_samples}
    sync_message = render_sync_message(state)
    context = hidden_context(state, view="eval")
    metric_cards = "".join(f"<article><span>{escape(key)}</span><strong>{escape(value)}</strong></article>" for key, value in metrics.items())
    harness_metric_cards = "".join(
        f"<article><span>{escape(key)}</span><strong>{escape(_pct_text(value))}</strong></article>"
        for key, value in harness_run.metrics.items()
    )
    summary_rows = "".join(
        f"<tr><td>{escape(key)}</td><td>{render_summary_value(value)}</td></tr>"
        for key, value in harness_summary.items()
    )
    failure_rows = "".join(
        render_harness_failure_row(case, sample_by_id.get(case.sample_id))
        for case in harness_run.failures[:6]
    )
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
    return f"""
<section class="panel">
  <div class="section-line"><h2>Harness Dashboard</h2><div class="inline-actions"><form method="post" action="/export_harness_overrides">{context}<button>导出人工修正CSV</button></form><form method="post" action="/export_harness_annotations">{context}<button>导出标注平台文件</button></form></div></div>
  <p>内置轻量 Harness：按真实样本与合成 demo 分开统计，批量运行 trial_parse_eval、value_match_eval、audit_eval、grade_predict_eval、derive_generation_eval 和 feishu_sync_eval。</p>
  {sync_message}
</section>
<section class="grid two">
  <div class="panel"><h2>数据集概览</h2><div class="table-wrap"><table><tbody>{summary_rows}</tbody></table></div></div>
  <div class="panel"><h2>本次运行</h2><dl class="detail">
    <div><dt>run_id</dt><dd>{escape(harness_run.run_id)}</dd></div>
    <div><dt>版本</dt><dd>{escape(harness_run.version)}</dd></div>
    <div><dt>模型</dt><dd>{escape(harness_run.model_provider)}</dd></div>
    <div><dt>生成 provider</dt><dd>{escape(harness_run.generator_provider)}</dd></div>
  </dl></div>
</section>
<section class="metrics">{harness_metric_cards}</section>
<section class="grid two">
  <div class="panel"><h2>失败样本复盘</h2><div class="table-wrap"><table><thead><tr><th>样本</th><th>Gold Label</th><th>任务</th><th>Agent 输出</th><th>失败原因</th><th>HITL 修正入口</th></tr></thead><tbody>{failure_rows or '<tr><td colspan="6">暂无失败样本。</td></tr>'}</tbody></table></div></div>
  <div class="panel"><h2>版本对比</h2><div class="table-wrap"><table><tbody>{compare_rows}</tbody></table></div></div>
</section>
<section class="metrics">{metric_cards}</section>
<section class="panel">
  <h2>任务目标</h2>
  <p>验证内容运营 Agent 是否能围绕 {escape(state.country)} 市场完成价值观判断、历史样本检索、规则审核和同步前检查。</p>
</section>
<section class="panel">
  <h2>输入与上下文</h2>
  <dl class="detail">
    <div><dt>Skill</dt><dd>{escape(trace.skill_name)}</dd></div>
    <div><dt>上下文</dt><dd>{escape(trace.context_summary)}</dd></div>
    <div><dt>输出</dt><dd>{escape(trace.final_output)}</dd></div>
  </dl>
</section>
<section class="panel">
  <h2>工具调用链路</h2>
  <div class="grid three">
    <div><h3>Plan</h3><ol>{plan}</ol></div>
    <div><h3>Tool Calls</h3><ol>{tools}</ol></div>
    <div><h3>Observations</h3><ol>{observations}</ol></div>
  </div>
</section>
<section class="panel">
  <h2>指标与结论</h2>
  <h2>Eval Dataset</h2>
  <p>{escape(report.dataset_name)} · {escape(report.country)} · 评测 RAG 召回、工具调用、计划遵循、步骤效率。</p>
  <div class="table-wrap"><table><thead><tr><th>Metric</th><th>Score</th><th>Threshold</th><th>Pass/Fail</th><th>Reason</th></tr></thead><tbody>{eval_metric_rows}</tbody></table></div>
</section>
<section class="panel">
  <h2>Case 明细</h2>
  <div class="table-wrap"><table><thead><tr><th>Case</th><th>任务</th><th>期望工具</th><th>实际工具</th><th>Judge Reason</th></tr></thead><tbody>{case_rows}</tbody></table></div>
</section>
<section class="panel"><h2>Agent Trace</h2><p>Trace 已在上方按输入、工具调用和指标结论拆解。</p></section>
"""


def render_summary_value(value: object) -> str:
    if isinstance(value, dict):
        return escape("；".join(f"{key}:{item}" for key, item in value.items()) or "无")
    return escape(str(value))


def render_harness_failure_row(case, sample) -> str:
    sample_cell = render_harness_sample_cell(case.sample_id, sample)
    gold = render_harness_gold_label(sample)
    correction_form = f"""
<form method="post" action="/save_harness_override">
  <input type="hidden" name="sample_id" value="{escape(case.sample_id)}">
  <input type="hidden" name="task_type" value="{escape(case.task_type)}">
  <textarea name="human_override" placeholder="记录人工修正主体、色彩、构图、风险或价值观标签">{escape(case.human_override)}</textarea>
  <button>保存修正</button>
</form>
"""
    return (
        "<tr>"
        f"<td>{sample_cell}</td>"
        f"<td>{gold}</td>"
        f"<td>{escape(case.task_type)}</td>"
        f"<td>{escape(case.agent_output)}</td>"
        f"<td>{escape('；'.join(case.failure_reasons))}</td>"
        f"<td>{correction_form}</td>"
        "</tr>"
    )


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
        src = local_image_data_uri(path)
        return f'<div class="mini-thumb"><img src="{escape(src)}" class="mini-thumb-img" alt="{escape(path.name)}"></div>'
    return visual_thumb(sample.subject if sample else "missing", sample.subject if sample else "missing")


def local_image_data_uri(path: Path) -> str:
    content_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _pct_text(value: object) -> str:
    if isinstance(value, float):
        return f"{round(value * 100)}%"
    return str(value)


def render_record_card(record) -> str:
    return f"<article class='image-card'>{visual_thumb(record.subject_tag, record.subject_tag)}<strong>{grade(record.grade)} {escape(record.operation_tag)}</strong><small>开图 {record.open_rate:.2%} · 完成 {record.completion_rate:.2%} · {record.avg_finish_time}</small></article>"


def render_schedule(agent: PuzzleOpsAgent, state: AppState) -> str:
    days = "".join(f'<a class="pill {"active" if day == state.schedule_day else ""}" href="{href(state, view="schedule", schedule_day=day)}">{day}</a>' for day in ("周一", "周二", "周三", "周四", "周五", "周六", "周日"))
    rule = "周末允许 1-9、12-18 位" if state.schedule_day in {"周六", "周日"} else "工作日允许 1-9、12-15 位"
    items = agent.schedule(state.country, state.schedule_day, state.schedule_replacements)
    slots = "".join(render_schedule_slot(state, item, index) for index, item in enumerate(items))
    return f"<section class='panel'><h2>排图工作台</h2><div class='pills'>{days}</div><p>{rule}，一天 10 张，共 70 张推荐排图。</p><div class='schedule'>{slots}</div></section>"


def render_schedule_slot(state: AppState, item, index: int) -> str:
    return f"""<article class='slot'>
  <strong>排图位 {position(item.position)}</strong>
  <span>{escape(item.image_name)}</span>
  <small>{escape(item.operation_tag)}</small>
  <p>{grade(item.grade)} 开图 {escape(item.open_rate)} · 完成 {escape(item.finish_rate)} · {escape(item.finish_time)}</p>
  <form method="post" action="/replace_schedule">{hidden_context(state)}<input type="hidden" name="slot_index" value="{index}"><input type="hidden" name="image_name" value="{escape(item.image_name)}"><button>－替换</button></form>
</article>"""


def render_sync(agent: PuzzleOpsAgent, state: AppState) -> str:
    rows = "".join(f"<tr><td>{escape(time)}</td><td>{escape(country)}</td><td>{escape(action)}</td><td>{escape(target)}</td><td>{escape(status)}</td></tr>" for time, country, action, target, status in agent.sync_rows())
    generation_rows = "".join(
        f"<tr><td>{escape(event.get('status', 'unknown'))}</td><td>{escape(event.get('provider', 'unknown'))}</td><td>{escape(event.get('model', '未记录'))}</td><td>{escape(event.get('task_id', ''))}</td><td>{escape(event.get('source_operation_tag', ''))}</td><td>{escape(event.get('second_review_status', 'unknown'))}</td><td>{escape(event.get('feishu_attachment_status', 'unknown'))}</td><td>{escape(event.get('error_type', 'unknown'))}</td><td>{escape(event.get('message', ''))}</td></tr>"
        for event in reversed(agent.generation_events(state.country)[-8:])
    )
    return f"""
<section class='panel'><h2>同步记录</h2><table><thead><tr><th>时间</th><th>国家</th><th>动作</th><th>目标</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table></section>
<section class='panel'><h2>生成任务回放</h2><table><thead><tr><th>状态</th><th>Provider</th><th>模型</th><th>Task</th><th>来源tag</th><th>二次审核</th><th>飞书附件</th><th>错误类型</th><th>说明</th></tr></thead><tbody>{generation_rows or '<tr><td colspan="9">暂无生成任务记录。</td></tr>'}</tbody></table></section>
"""


def render_image_card(image) -> str:
    return f"<article class='image-card'>{visual_thumb(image.thumb, image.title)}<strong>{grade(image.grade)} {escape(image.title)}</strong><small>开图 {escape(image.open_rate)} · 完成 {escape(image.finish_rate)} · {escape(image.finish_time)}</small></article>"


def render_image_preview(image_name: str, image_url: str = "") -> str:
    src = image_url or image_data_uri(image_name, image_name)
    return f'<div class="image-preview-cell"><img src="{escape(src)}" class="mini-thumb-img" alt="{escape(image_name)}"><span>{escape(image_name)}</span></div>'


def visual_thumb(seed: str, label: str) -> str:
    src = image_data_uri(seed, label)
    return f'<div class="thumb visual-thumb"><img src="{escape(src)}" alt="{escape(label)}"></div>'


def render_upload_preview(item: dict[str, str]) -> str:
    return f'<div class="thumb upload-thumb"><img src="{escape(item["url"])}" alt="{escape(item["filename"])}"><span>{escape(item["filename"])}</span></div>'


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
        "value": "价值观大师",
        "runtime": "多模态底座",
        "eval": "Agent 评测",
        "schedule": "排图工作台",
        "sync": "同步记录",
    }[view]


def view_icon(view: str) -> str:
    return {
        "dashboard": "🏠",
        "regular": "📦",
        "trial": "✨",
        "analysis": "📈",
        "value": "🔮",
        "runtime": "🧠",
        "eval": "🧪",
        "schedule": "🗓️",
        "sync": "🔁",
    }[view]


def hidden_context(state: AppState, **overrides: str) -> str:
    values = {
        "country": state.country,
        "view": state.view,
        "category": state.category,
        "tag": state.tag,
        "trial_mode": state.trial_mode,
        "schedule_day": state.schedule_day,
        "value_grade": state.value_grade,
    }
    values.update(overrides)
    return "".join(f'<input type="hidden" name="{key}" value="{escape(value)}">' for key, value in values.items())


def href(state: AppState, **changes: str) -> str:
    params = {
        "country": state.country,
        "view": state.view,
        "category": state.category,
        "tag": state.tag,
        "trial_mode": state.trial_mode,
        "schedule_day": state.schedule_day,
        "value_grade": state.value_grade,
        "show_holiday": "1" if state.show_holiday else "",
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
.pill.active, .nav.active, button.primary, .primary-link { background:#dff1ea; border-color:var(--brand); color:#17644e; }
nav { display:grid; gap:8px; margin:18px 0; }
.nav { padding:12px; border-radius:8px; }
.nav:hover, .choice:hover { background:#fff; }
.metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin-bottom:14px; }
.metrics article, .panel { background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px; box-shadow:0 10px 24px rgba(53,67,75,.06); }
.metrics span, small { color:var(--muted); }
.metrics strong { display:block; margin:8px 0; font-size:28px; }
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
.mini-thumb { width:92px; min-height:64px; display:grid; place-items:center; padding:8px; border-radius:8px; background:linear-gradient(135deg,#f4efe2,#dff1ea); font-size:12px; font-weight:900; text-align:center; }
.mini-thumb-img { width:92px; height:64px; border-radius:8px; object-fit:cover; background:#f1f5f3; }
.harness-sample-cell { display:grid; grid-template-columns:92px minmax(150px,1fr); gap:8px 10px; align-items:center; min-width:260px; }
.harness-sample-cell .mini-thumb, .harness-sample-cell .visual-thumb { grid-row:1 / 4; width:92px; min-height:64px; }
.harness-sample-cell strong, .harness-sample-cell span, .harness-sample-cell small { overflow-wrap:anywhere; }
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
th, td { padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:13px; }
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
@media (max-width: 900px) { body { grid-template-columns:1fr; } aside { border-right:0; border-bottom:1px solid var(--line); } .metrics, .grid.two, .grid.three, .detail { grid-template-columns:1fr; } }
"""
