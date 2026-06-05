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
    show_holiday: bool = False
    need_rows: list[DemandRow] = field(default_factory=list)
    trial_row: DemandRow | None = None
    workflow_notes: list[str] = field(default_factory=list)
    task_notes: list[str] = field(default_factory=list)
    schedule_replacements: dict[int, object] = field(default_factory=dict)
    sync_message: str = ""
    analysis_edits: dict[str, object] = field(default_factory=dict)
    trial_uploads: list[dict[str, str]] = field(default_factory=list)


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
    sync_message = f'<p class="success">{escape(state.sync_message)}</p>' if state.sync_message else ""
    feishu_status = agent.feishu.config_status()
    if agent.feishu.is_real:
        feishu_copy = f"真实飞书：{escape(str(feishu_status.get('spreadsheet_token', '')))} · {escape(str(feishu_status.get('sheet_range', '')))}"
    else:
        feishu_copy = f"真实飞书未配置，缺少：{escape('、'.join(feishu_status['missing']))}"
    return f"""
<section class="grid three">
  <div class="panel"><h2>分类</h2>{categories}</div>
  <div class="panel"><h2>运营 tag + 库存</h2><p class="alert">红色=低库存爆款；黄色=低库存稳定款；其他=正常库存。</p>{tags}</div>
  <div class="panel"><h2>已分发图片参考</h2><div class="cards">{images}</div></div>
</section>
<section class="panel">
  <div class="section-line"><h2>批量提需清单</h2>
    <form method="post" action="/generate_descriptions">{hidden_context(state)}<button>AI生成描述</button></form>
  </div>
  <p class="note">{feishu_copy}</p>
  {sync_message}
  <form method="post" action="/save_needs">{hidden_context(state)}{rows}<div class="section-line"><button class="primary">保存表格修改</button><button formaction="/sync_needs_feishu" formmethod="post">一键同步到飞书表格</button></div></form>
</section>
"""


def render_tag_choice(state: AppState, tag) -> str:
    hot = " " + PuzzleOpsAgent().stock_class(tag)
    active = " active" if tag.tag == state.tag else ""
    return f'<a class="choice{active}{hot}" href="{href(state, view="regular", tag=tag.tag)}"><strong>{escape(tag.tag)}</strong><span>库存 {tag.stock}</span></a>'


def render_reference_image(state: AppState, image, index: int) -> str:
    return f"""
<article class="image-card">
  <div class="thumb">{escape(image.title)}</div>
  <strong><span class="grade {image.grade}">{escape(image.grade)}</span> {escape(image.title)}</strong>
  <small>开图 {escape(image.open_rate)} · 完成 {escape(image.finish_rate)} · {escape(image.finish_time)}</small>
  <form method="post" action="/add_regular">{hidden_context(state)}<input type="hidden" name="image_index" value="{index}"><button>＋加入提需</button></form>
</article>
"""


def render_need_rows(rows: list[DemandRow]) -> str:
    if not rows:
        return "<p class='empty'>暂未加入提需。点击图片卡片的加号后，在这里批量提需。</p>"
    body = "".join(render_need_row(row, index, include_value=False) for index, row in enumerate(rows))
    return f"<div class='table-wrap'><table>{need_header(include_check=False, include_value=False)}<tbody>{body}</tbody></table></div>"


def render_trial(agent: PuzzleOpsAgent, state: AppState) -> str:
    mode_links = "".join(
        f'<a class="mode-card {"active" if state.trial_mode == mode else ""}" href="{href(state, view="trial", trial_mode=mode)}"><strong>{label}</strong><span>{copy}</span></a>'
        for mode, label, copy in (
            ("parse", "参考图解析提需", "上传 1-3 张参考图，AI解析主体/色彩/构图。"),
            ("derive", "好图衍生提需", "上传 1 张历史好图，生成 2 张相似参考图后再提需。"),
        )
    )
    row = state.trial_row or agent.create_trial_demand(state.country, state.category, state.trial_mode)
    row_html = render_need_row(row, 0, include_value=True, prefix="")
    upload_copy = "拖拽或选择 1-3 张参考图" if state.trial_mode == "parse" else "上传单张历史好图，自动衍生 2 张类似参考图"
    previews = "".join(render_upload_preview(item) for item in state.trial_uploads) or '<div class="thumb">参考图 A</div><div class="thumb">参考图 B</div><div class="thumb">参考图 C</div>'
    return f"""
<section class="panel"><h2>试新模式</h2><div class="mode-grid">{mode_links}</div></section>
<section class="grid two">
  <div class="panel"><h2>上传参考图</h2><div class="mock-upload-zone"><strong>{upload_copy}</strong><span>可上传本地图片进行结构化解析；未接 LLM 时使用本地解析适配层。</span><form method="post" action="/upload_trial_images" enctype="multipart/form-data">{hidden_context(state)}<input type="file" name="trial_images" accept="image/*" multiple><button>上传并解析图片</button></form><form method="post" action="/simulate_trial_upload">{hidden_context(state)}<button>模拟上传并解析</button></form></div><div class="reference-row">{previews}</div></div>
  <div class="panel"><h2>Agent 解析结果</h2><dl class="detail"><div><dt>主体</dt><dd>{escape(row.subject)}</dd></div><div><dt>运营tag</dt><dd>{escape(row.operation_tag)}</dd></div><div><dt>加工方式</dt><dd>{escape(row.method)}</dd></div><div><dt>审核备注</dt><dd>{escape(row.remark or "无")}</dd></div></dl></div>
</section>
<section class="panel">
  <div class="section-line"><h2>试新提需表预览</h2><form method="post" action="/apply_value_master">{hidden_context(state)}<button>价值观大师</button></form></div>
  <form method="post" action="/save_trial">{hidden_context(state)}<div class="table-wrap"><table>{need_header(include_check=True, include_value=True)}<tbody>{row_html}</tbody></table></div><button class="primary">保存试新修改</button></form>
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
        render_image_preview(row.image_name),
        f'<input name="operation_tag{prefix}" value="{escape(row.operation_tag)}">',
        escape(row.subject),
        f'<input class="small-input" name="count{prefix}" value="{row.count}" size="3">',
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
    edited_remarks = state.analysis_edits.get("remarks", {})
    cycle_summary = str(state.analysis_edits.get("cycle_summary", report.cycle_summary))
    next_todo = str(state.analysis_edits.get("next_todo", report.next_todo))
    rows = "".join(
        f'<tr><td>{escape(row.image_name)}</td><td>{escape(row.source)}</td><td>{grade(row.grade)}</td><td>{escape(row.open_rate)}</td><td>{escape(row.finish_rate)}</td><td>{escape(row.finish_time)}</td><td>{position(row.position)}</td><td><textarea name="analysis_remark_{index}">{escape(edited_remarks.get(index, row.remark) if isinstance(edited_remarks, dict) else row.remark)}</textarea></td></tr>'
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
    cards = "".join(f"<article class='image-card'><div class='thumb'>{escape(card.image.title)}</div><strong>{escape(card.operation_tag)}</strong><p>{grade(card.image.grade)} 预测等级</p><small>开图 {escape(card.image.open_rate)} · 完成 {escape(card.image.finish_rate)} · {escape(card.image.finish_time)}</small><p>{escape(card.prediction_remark)}</p></article>" for card in agent.value_predictions(state.country, state.value_grade))
    rules = "".join(f"<li><strong>{escape(title)}</strong>：{escape(body)}</li>" for title, body in agent.value_rules(state.country))
    return f"<section class='panel'><h2>SABCD 预测</h2><div class='pills'>{tabs}</div><div class='cards'>{cards or '<p class=\"empty\">当前等级暂无样例。</p>'}</div></section><section class='panel'><details><summary>查看完整价值观规则库</summary><ul>{rules}</ul></details></section>"


def render_runtime(agent: PuzzleOpsAgent, state: AppState) -> str:
    profile = agent.multimodal_profile(state.country)
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
      <div><dt>Caption</dt><dd>{escape(feature.caption)}</dd></div>
    </dl></div>
    <div><h3>图文融合指标</h3><dl class="detail">
      <div><dt>等级</dt><dd>{grade(str(profile.historical_metrics["grade"]))}</dd></div>
      <div><dt>开图率</dt><dd>{profile.historical_metrics["open_rate"]}</dd></div>
      <div><dt>完成率</dt><dd>{profile.historical_metrics["completion_rate"]}</dd></div>
      <div><dt>完成时长</dt><dd>{profile.historical_metrics["avg_finish_time"]}</dd></div>
      <div><dt>风险标签</dt><dd>{escape('、'.join(feature.risk_tags) or '无')}</dd></div>
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
    metric_cards = "".join(f"<article><span>{escape(key)}</span><strong>{escape(value)}</strong></article>" for key, value in metrics.items())
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
<section class="metrics">{metric_cards}</section>
<section class="panel">
  <h2>Eval Dataset</h2>
  <p>{escape(report.dataset_name)} · {escape(report.country)} · 评测 RAG 召回、工具调用、计划遵循、步骤效率。</p>
  <div class="table-wrap"><table><thead><tr><th>Metric</th><th>Score</th><th>Threshold</th><th>Pass/Fail</th><th>Reason</th></tr></thead><tbody>{eval_metric_rows}</tbody></table></div>
</section>
<section class="panel">
  <h2>Case 明细</h2>
  <div class="table-wrap"><table><thead><tr><th>Case</th><th>任务</th><th>期望工具</th><th>实际工具</th><th>Judge Reason</th></tr></thead><tbody>{case_rows}</tbody></table></div>
</section>
<section class="panel">
  <h2>Agent Trace</h2>
  <dl class="detail">
    <div><dt>Skill</dt><dd>{escape(trace.skill_name)}</dd></div>
    <div><dt>上下文</dt><dd>{escape(trace.context_summary)}</dd></div>
    <div><dt>输出</dt><dd>{escape(trace.final_output)}</dd></div>
  </dl>
  <div class="grid three">
    <div><h3>Plan</h3><ol>{plan}</ol></div>
    <div><h3>Tool Calls</h3><ol>{tools}</ol></div>
    <div><h3>Observations</h3><ol>{observations}</ol></div>
  </div>
</section>
"""


def render_record_card(record) -> str:
    return f"<article class='image-card'><div class='thumb'>{escape(record.subject_tag)}</div><strong>{grade(record.grade)} {escape(record.operation_tag)}</strong><small>开图 {record.open_rate:.2%} · 完成 {record.completion_rate:.2%} · {record.avg_finish_time}</small></article>"


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
    return f"<section class='panel'><h2>同步记录</h2><table><thead><tr><th>时间</th><th>国家</th><th>动作</th><th>目标</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table></section>"


def render_image_card(image) -> str:
    return f"<article class='image-card'><div class='thumb'>{escape(image.title)}</div><strong>{grade(image.grade)} {escape(image.title)}</strong><small>开图 {escape(image.open_rate)} · 完成 {escape(image.finish_rate)} · {escape(image.finish_time)}</small></article>"


def render_image_preview(image_name: str) -> str:
    return f'<div class="image-preview-cell"><div class="mini-thumb">{escape(image_name)}</div><span>{escape(image_name)}</span></div>'


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


def hidden_context(state: AppState) -> str:
    values = {
        "country": state.country,
        "view": state.view,
        "category": state.category,
        "tag": state.tag,
        "trial_mode": state.trial_mode,
        "schedule_day": state.schedule_day,
        "value_grade": state.value_grade,
    }
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
.cards, .schedule { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }
.mode-grid, .reference-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.reference-row { grid-template-columns:repeat(3,minmax(0,1fr)); margin-top:12px; }
.mode-card { display:grid; gap:6px; padding:14px; border:1px solid var(--line); border-radius:10px; background:#fffdf7; }
.mode-card.active { border-color:var(--brand); background:#e7f4ee; }
.mode-card span { color:var(--muted); font-size:13px; }
.mock-upload-zone { min-height:150px; display:grid; place-items:center; padding:18px; border:2px dashed #b7c9c1; border-radius:10px; background:#f6faf8; text-align:center; }
.mock-upload-zone span { display:block; color:var(--muted); margin-top:6px; }
.image-card, .slot { display:grid; gap:8px; padding:12px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.thumb { min-height:95px; display:grid; place-items:center; padding:12px; border-radius:8px; background:linear-gradient(135deg,#e6f4ee,#fff0cb); text-align:center; font-weight:900; }
.upload-thumb img { max-width:100%; max-height:120px; border-radius:8px; object-fit:cover; }
.upload-thumb span { font-size:12px; color:var(--muted); }
.mini-thumb { width:92px; min-height:64px; display:grid; place-items:center; padding:8px; border-radius:8px; background:linear-gradient(135deg,#f4efe2,#dff1ea); font-size:12px; font-weight:900; text-align:center; }
.image-preview-cell { display:grid; grid-template-columns:92px minmax(140px,1fr); align-items:center; gap:10px; min-width:260px; }
.choice { display:flex; justify-content:space-between; gap:10px; padding:10px; margin-bottom:8px; border:1px solid var(--line); border-radius:8px; background:#fffdf7; }
.choice.stock-hot { border-color:#e26357; background:#ffe9e5; color:#9b281f; }
.choice.stock-low { border-color:#e8c35b; background:#fff7d8; color:#7a4a00; }
.choice.stock-normal { background:#fffdf7; }
.alert { color:#996b00; background:#fff7d8; border-radius:8px; padding:10px; }
.section-line { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.table-wrap { overflow-x:auto; }
table { width:100%; min-width:1680px; border-collapse:collapse; }
th, td { padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:13px; }
th { background:#eef5f2; }
input, select, textarea { width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; font:inherit; background:#fff; }
.small-input { min-width:92px; }
textarea { min-height:58px; resize:vertical; }
.wide { min-height:120px; }
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
