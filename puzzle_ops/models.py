from dataclasses import dataclass, replace


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

