from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from puzzle_ops.grading import dimension_grade, expected_grade
from puzzle_ops.models import HistoricalRecord, JS_CATEGORIES
from puzzle_ops.visual_assets import image_bytes


class SyntheticDataGenerator:
    allowed_categories = JS_CATEGORIES

    def __init__(self, output_dir: Path | str):
        self.output_dir = Path(output_dir)
        self.image_dir = self.output_dir / "static" / "images" / "synthetic"
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def generate_dataset(self, countries: tuple[str, ...], weeks: int) -> tuple[HistoricalRecord, ...]:
        records = []
        for country in countries:
            records.extend(self.generate_country_history(country, weeks))
        return tuple(records)

    def generate_country_history(self, country: str, weeks: int) -> tuple[HistoricalRecord, ...]:
        records = []
        categories = tuple(sorted(JS_CATEGORIES))
        base_date = date(2026, 4, 1)
        for week in range(1, weeks + 1):
            for index in range(139):
                category = categories[(index + week) % len(categories)]
                source = "AI" if index % 5 == 0 else "素材网"
                open_rate, completion_rate, finish_time = self._metrics(country, index)
                dims = dimension_grade(country, open_rate, completion_rate, finish_time)
                grade = expected_grade(dims)
                image_id = f"{country.lower()}_w{week:02d}_{index + 1:03d}"
                subject = self._subject(country, category, index)
                image_path = self._write_visual_image(image_id, subject)
                need_type = "试新" if index % 7 == 0 else "常规"
                records.append(
                    HistoricalRecord(
                        grade=grade,
                        image_formula="",
                        image_id=image_id,
                        image_url=f"mock://cms/{image_id}.png",
                        local_image_path=str(image_path),
                        thumbnail_path=str(image_path),
                        position=self._position(index),
                        dimension_grade=dims,
                        open_rate=open_rate,
                        completion_rate=completion_rate,
                        avg_finish_time=finish_time,
                        operation_tag=f"{need_type}_{country}_{subject}{base_date:%m%d}",
                        subject_tag=subject,
                        js_category=category,
                        source=source,
                        remark="" if grade in {"S", "A", "B"} else "模拟低表现样本，用于坏图证据检索。",
                        distribution_date=(base_date + timedelta(days=(week - 1) * 7 + index // 20)).isoformat(),
                        distribution_cycle=f"W{week}",
                        country=country,
                    )
                )
        return tuple(records)

    def _write_visual_image(self, image_id: str, subject: str) -> Path:
        path = self.image_dir / f"{image_id}.png"
        if not path.exists():
            path.write_bytes(image_bytes(image_id, subject))
        return path

    def _metrics(self, country: str, index: int) -> tuple[float, float, float]:
        pattern = index % 5
        if country == "日本":
            return (
                (0.16, 0.12, 0.10, 0.05, 0.03)[pattern],
                (0.94, 0.90, 0.89, 0.88, 0.84)[pattern],
                (21.2, 18.5, 17.0, 15.5, 14.2)[pattern],
            )
        return (
            (0.13, 0.09, 0.08, 0.05, 0.03)[pattern],
            (0.94, 0.90, 0.88, 0.86, 0.83)[pattern],
            (20.1, 17.9, 16.2, 15.2, 14.0)[pattern],
        )

    def _subject(self, country: str, category: str, index: int) -> str:
        subjects = {
            "日本": ("猫咪鲤鱼", "天桥立", "寿司", "抹茶", "怀旧场景", "樱花庭院"),
            "法国": ("薰衣草", "铃兰花", "法式甜点", "石屋花园", "巴黎面包店", "乡村窗台"),
        }[country]
        return subjects[(index + len(category)) % len(subjects)]

    def _position(self, index: int) -> int:
        positions = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18)
        return positions[index % len(positions)]
