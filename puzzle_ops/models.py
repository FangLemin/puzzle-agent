from dataclasses import dataclass, replace


JS_CATEGORIES = {
    "houses",
    "home",
    "food",
    "flowers",
    "pets",
    "animal",
    "travel",
    "ontheway",
    "zen",
    "objects",
    "patterns",
    "handcrafted",
    "streetview",
    "human",
}


@dataclass(frozen=True)
class Task:
    title: str
    body: str


@dataclass(frozen=True)
class ImageAsset:
    title: str
    grade: str
    open_rate: str
    finish_rate: str
    finish_time: str
    source: str
    thumb: str
    remark: str = ""


@dataclass(frozen=True)
class TagMeta:
    tag: str
    subject: str
    stock: int
    hot: bool
    risk: str = ""


@dataclass(frozen=True)
class HolidayRecommendation:
    name: str
    date_range: str
    meaning: str
    content: str
    ai_themes: tuple[str, ...]
    elements: tuple[str, ...]
    history_good_images: tuple[ImageAsset, ...]


@dataclass(frozen=True)
class DemandRow:
    need_type: str
    country: str
    js_category: str
    image_name: str
    operation_tag: str
    subject: str
    count: int
    priority: str
    method: str
    delivery_date: str
    subject_description: str
    remark: str
    value_match: str = ""
    reference_image_url: str = ""
    reference_image_path: str = ""
    reference_image_content_type: str = ""

    def edited(self, **changes: object) -> "DemandRow":
        return replace(self, **changes)


@dataclass(frozen=True)
class AnalysisRow:
    image_name: str
    source: str
    grade: str
    open_rate: str
    finish_rate: str
    finish_time: str
    position: int
    remark: str
    remark_editable: bool = True

    @property
    def position_is_red(self) -> bool:
        return self.position in {5, 10}


@dataclass(frozen=True)
class AnalysisReport:
    country: str
    sa_ratio: str
    cd_ratio: str
    ai_ratio: str
    sa_delta: str
    cd_delta: str
    ai_delta: str
    sa_history_avg: str
    sa_okr: str
    cd_history_avg: str
    ai_history_avg: str
    ai_okr: str
    cycle_summary: str
    next_todo: str
    rows: tuple[AnalysisRow, ...]


@dataclass(frozen=True)
class ValuePredictionCard:
    operation_tag: str
    image: ImageAsset
    prediction_remark: str


@dataclass(frozen=True)
class ScheduleItem:
    day: str
    position: int
    image_name: str
    operation_tag: str
    grade: str
    open_rate: str
    finish_rate: str
    finish_time: str


@dataclass(frozen=True)
class HistoricalRecord:
    grade: str
    image_formula: str
    image_id: str
    image_url: str
    local_image_path: str
    thumbnail_path: str
    position: int
    dimension_grade: str
    open_rate: float
    completion_rate: float
    avg_finish_time: float
    operation_tag: str
    subject_tag: str
    js_category: str
    source: str
    remark: str
    distribution_date: str
    distribution_cycle: str
    country: str


@dataclass(frozen=True)
class ToolResult:
    success: bool
    data: dict[str, object]
    message: str
    evidence: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ImageFeature:
    image_id: str
    main_subject: str
    secondary_subjects: tuple[str, ...]
    color_palette: tuple[str, ...]
    composition: str
    style: str
    culture_elements: tuple[str, ...]
    festival_elements: tuple[str, ...]
    ai_artifacts: tuple[str, ...]
    risk_tags: tuple[str, ...]
    caption: str
    feature_confidence: float
    visual_quality_tags: tuple[str, ...] = ()
    brightness_level: str = ""
    saturation_level: str = ""
    temperature: str = ""
    palette_summary: str = ""
    puzzle_readability: str = ""


@dataclass(frozen=True)
class ImageProfile:
    asset: HistoricalRecord
    feature: ImageFeature
    historical_metrics: dict[str, object]
    similar_good_cases: tuple[HistoricalRecord, ...]
    similar_bad_cases: tuple[HistoricalRecord, ...]
    matched_value_rules: tuple[str, ...]
    matched_audit_rules: tuple[str, ...]


@dataclass(frozen=True)
class ValueRuleCandidate:
    candidate_id: str
    country: str
    rule_text: str
    confidence: float
    support_count: int
    counterexample_count: int
    evidence_image_ids: tuple[str, ...]
    status: str
    agent_reason: str
    human_note: str = ""


@dataclass(frozen=True)
class AuditPolicyHit:
    rule_id: str
    text: str
    risk_level: str


@dataclass(frozen=True)
class AuditReviewResult:
    risk_level: str
    reason: str
    evidence: tuple[str, ...]
    suggestion: str


@dataclass(frozen=True)
class AgentTrace:
    trace_id: str
    task_type: str
    country: str
    plan: tuple[str, ...]
    skill_name: str
    tool_calls: tuple[str, ...]
    observations: tuple[str, ...]
    memory_hits: tuple[str, ...]
    context_summary: str
    final_output: str
    eval_result: dict[str, float]
