from dataclasses import replace

from puzzle_ops.data import COUNTRIES, SYNC_ROWS
from puzzle_ops.models import AnalysisReport, DemandRow, HolidayRecommendation, ScheduleItem, TagMeta, ValuePredictionCard


class PuzzleOpsAgent:
    """Business-facing Agent service for outbound puzzle content operations."""

    editable_priorities = {"P0", "P1", "P2"}
    editable_methods = {"纯AI", "限素材网", "先照片后AI"}
    workday_positions = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)
    weekend_positions = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12)

    def countries(self) -> tuple[str, ...]:
        return tuple(COUNTRIES.keys())

    def dashboard(self, country: str) -> dict[str, object]:
        data = self._country(country)
        return {
            "country": country,
            "country_label": f"{data['flag']} {country}",
            "owner": data["owner"],
            "sa": data["sa"],
            "ai": data["ai"],
            "tasks": [{"title": task.title, "body": task.body} for task in data["tasks"]],
        }

    def categories(self, country: str) -> dict[str, tuple[TagMeta, ...]]:
        return self._country(country)["categories"]

    def sorted_tags(self, country: str, category: str) -> tuple[TagMeta, ...]:
        tags = self.categories(country)[category]
        return tuple(sorted(tags, key=lambda tag: (self.stock_rank(tag), tag.stock)))

    def stock_rank(self, tag: TagMeta) -> int:
        if tag.hot and tag.stock <= 5:
            return 0
        if not tag.hot and tag.stock <= 5:
            return 1
        return 2

    def stock_class(self, tag: TagMeta) -> str:
        return ("stock-hot", "stock-low", "stock-normal")[self.stock_rank(tag)]

    def images_for_tag(self, country: str, operation_tag: str):
        return self._country(country)["images"].get(operation_tag, ())

    def add_regular_demand(self, country: str, category: str, operation_tag: str, image_index: int) -> DemandRow:
        tag_meta = self._tag_meta(country, category, operation_tag)
        image = self.images_for_tag(country, operation_tag)[image_index]
        return DemandRow(
            need_type="常规",
            country=country,
            js_category=category,
            image_name=image.title,
            operation_tag=operation_tag,
            subject=tag_meta.subject,
            count=7,
            priority="P1",
            method="限素材网" if image.source == "素材网" else "纯AI",
            delivery_date="",
            subject_description="",
            remark=image.remark or tag_meta.risk,
        )

    def edit_demand_row(
        self,
        row: DemandRow,
        *,
        priority: str | None = None,
        count: int | None = None,
        method: str | None = None,
        delivery_date: str | None = None,
        remark: str | None = None,
    ) -> DemandRow:
        changes: dict[str, object] = {}
        if priority is not None:
            if priority not in self.editable_priorities:
                raise ValueError("需求等级只能是 P0、P1 或 P2")
            changes["priority"] = priority
        if count is not None:
            if count <= 0:
                raise ValueError("张数必须大于 0")
            changes["count"] = count
        if method is not None:
            if method not in self.editable_methods:
                raise ValueError("加工方式只能是 纯AI、限素材网 或 先照片后AI")
            changes["method"] = method
        if delivery_date is not None:
            changes["delivery_date"] = delivery_date
        if remark is not None:
            changes["remark"] = remark
        return row.edited(**changes)

    def generate_subject_description(self, row: DemandRow) -> DemandRow:
        description = f"主体：{row.subject}；色彩：贴合{row.country}市场偏好；构图：前景主体清晰，中景场景丰富，远景保留空间层次。"
        return row.edited(subject_description=description)

    def create_trial_demand(self, country: str, category: str, mode: str) -> DemandRow:
        data = self._country(country)["trial"]
        if mode not in {"parse", "derive"}:
            raise ValueError("试新模式只能是 parse 或 derive")
        tag = data["derive_tag"] if mode == "derive" else data["parse_tag"]
        image_name = "上传好图 + 自动衍生2张参考图" if mode == "derive" else "上传参考图1-3张"
        risk = "" if data["risk"].startswith("未发现明显") else f"! {data['risk']}"
        return DemandRow(
            need_type="试新",
            country=country,
            js_category=category,
            image_name=image_name,
            operation_tag=tag,
            subject=data["subject"],
            count=3,
            priority="P1",
            method="先照片后AI",
            delivery_date="",
            subject_description=f"{data['subject']}；{data['colors']}；{data['composition']}",
            remark=risk,
            value_match="",
        )

    def apply_value_master(self, row: DemandRow) -> DemandRow:
        value_match = self._country(row.country)["trial"]["value_match"]
        return row.edited(value_match=value_match)

    def holiday_recommendation(self, country: str) -> HolidayRecommendation:
        return self._country(country)["holiday"]

    def analysis_report(self, country: str) -> AnalysisReport:
        data = self._country(country)["analysis"]
        return AnalysisReport(
            country=country,
            sa_ratio=data["sa_ratio"],
            cd_ratio=data["cd_ratio"],
            ai_ratio=data["ai_ratio"],
            sa_delta=data["sa_delta"],
            cd_delta=data["cd_delta"],
            ai_delta=data["ai_delta"],
            sa_history_avg=data["sa_history_avg"],
            sa_okr=data["sa_okr"],
            cycle_summary=data["cycle_summary"],
            next_todo=data["next_todo"],
            rows=data["rows"],
        )

    def value_rules(self, country: str):
        return self._country(country)["value_rules"]

    def value_predictions(self, country: str, grade: str) -> tuple[ValuePredictionCard, ...]:
        cards: list[ValuePredictionCard] = []
        for operation_tag, images in self._country(country)["images"].items():
            for image in images:
                if image.grade == grade:
                    remark = image.remark or "预测备注：价值观匹配度较高，可进入排图池。"
                    cards.append(ValuePredictionCard(operation_tag, image, remark))
        return tuple(cards)

    def schedule(self, country: str, day: str, replacements: dict[int, ScheduleItem] | None = None) -> tuple[ScheduleItem, ...]:
        if day not in {"周一", "周二", "周三", "周四", "周五", "周六", "周日"}:
            raise ValueError("排图日期只能是周一到周日")
        positions = self.weekend_positions if day in {"周六", "周日"} else self.workday_positions
        source = []
        for operation_tag, images in self._country(country)["images"].items():
            for image in images:
                source.append((operation_tag, image))
        items = []
        for index in range(10):
            operation_tag, image = source[index % len(source)]
            items.append(
                ScheduleItem(
                    day=day,
                    position=positions[index],
                    image_name=image.title,
                    operation_tag=operation_tag,
                    grade=image.grade,
                    open_rate=image.open_rate,
                    finish_rate=image.finish_rate,
                    finish_time=image.finish_time,
                )
            )
        if replacements:
            for index, replacement in replacements.items():
                if 0 <= index < len(items):
                    items[index] = replace(replacement, day=day, position=items[index].position)
        return tuple(items)

    def replacement_for_slot(self, country: str, current_image_name: str) -> ScheduleItem:
        source = []
        for operation_tag, images in self._country(country)["images"].items():
            for image in images:
                source.append((operation_tag, image))
        for operation_tag, image in source:
            if image.title != current_image_name and image.grade in {"S", "A", "B"}:
                return ScheduleItem(
                    day="候补",
                    position=0,
                    image_name=f"未分发候补图：{image.title}",
                    operation_tag=operation_tag,
                    grade=image.grade,
                    open_rate=image.open_rate,
                    finish_rate=image.finish_rate,
                    finish_time=image.finish_time,
                )
        operation_tag, image = source[0]
        return ScheduleItem("候补", 0, f"未分发候补图：{image.title}", operation_tag, image.grade, image.open_rate, image.finish_rate, image.finish_time)

    def sync_rows(self):
        return SYNC_ROWS

    def _country(self, country: str) -> dict[str, object]:
        try:
            return COUNTRIES[country]
        except KeyError as exc:
            raise ValueError(f"未知国家：{country}") from exc

    def _tag_meta(self, country: str, category: str, operation_tag: str) -> TagMeta:
        for tag in self.categories(country)[category]:
            if tag.tag == operation_tag:
                return tag
        raise ValueError(f"找不到运营 tag：{operation_tag}")
