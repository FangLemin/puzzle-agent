from pathlib import Path

import pytest

from puzzle_ops.excel_importer import ExcelImageExtractor, import_history_workbook
from puzzle_ops.excel_importer import import_undistributed_candidate_workbook
from puzzle_ops.grading import classify_dimension, expected_grade
from puzzle_ops.models import JS_CATEGORIES


FIXTURE = Path.home() / "Desktop" / "数据示例.xlsx"


def test_import_history_workbook_preserves_real_business_columns_and_images(tmp_path):
    records = import_history_workbook(FIXTURE, "日本", tmp_path)

    assert len(records) == 25
    first = records[0]
    assert first.grade == "S"
    assert first.image_id == "550e8400-e29b-41d4-a716-446655440000"
    assert first.image_url == ""
    assert first.position == 1
    assert first.dimension_grade == "高高高"
    assert first.open_rate == pytest.approx(0.2845)
    assert first.completion_rate == pytest.approx(0.9573)
    assert first.avg_finish_time == pytest.approx(21.36)
    assert first.operation_tag == "常规_日本_猫咪鲤鱼0605"
    assert first.subject_tag == "猫"
    assert first.js_category == "animal"
    assert first.source == "AI"
    assert first.distribution_cycle == "W1"
    assert first.local_image_path
    assert Path(first.local_image_path).exists()
    assert first.thumbnail_path == first.local_image_path


def test_import_undistributed_candidate_workbook_extracts_wps_dispimg_images(tmp_path):
    workbook = Path.home() / "Desktop" / "未分发候选拼图_价值观大师填写模板.xlsx"
    candidates = import_undistributed_candidate_workbook(workbook, "日本", tmp_path)

    assert len(candidates) == 15
    first = candidates[0]
    assert first["candidate_id"] == "JP_CAND_001"
    assert first["country"] == "日本"
    assert first["js_category"] == "travel"
    assert first["operation_tag"] == "常规_日本_庭院0721"
    assert first["include_for_prediction"] is True
    assert first["local_image_path"]
    assert Path(str(first["local_image_path"])).exists()
    assert first["image_hash"]


def test_import_undistributed_candidate_workbook_normalizes_js_category_aliases(tmp_path):
    workbook = Path.home() / "Desktop" / "未分发候选拼图_价值观大师填写模板.xlsx"
    candidates = import_undistributed_candidate_workbook(workbook, "法国", tmp_path)

    assert len(candidates) == 15
    assert candidates[-1]["candidate_id"] == "FR_CAND_015"
    assert candidates[-1]["js_category"] == "objects"


def test_import_history_workbook_validates_allowed_js_categories(tmp_path):
    records = import_history_workbook(FIXTURE, "日本", tmp_path)

    assert {record.js_category for record in records}.issubset(JS_CATEGORIES)
    assert {"animal", "travel", "food"}.issubset({record.js_category for record in records})


def test_js_categories_match_business_taxonomy_exactly():
    assert JS_CATEGORIES == {
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
        "drawing",
    }


def test_country_grade_thresholds_match_user_business_rules():
    assert classify_dimension("日本", "open_rate", 0.1379) == "高"
    assert classify_dimension("日本", "open_rate", 0.0788) == "低"
    assert classify_dimension("日本", "completion_rate", 0.9199) == "高"
    assert classify_dimension("日本", "completion_rate", 0.8672) == "低"
    assert classify_dimension("日本", "avg_finish_time", 19.74) == "高"
    assert classify_dimension("日本", "avg_finish_time", 15.05) == "低"
    assert classify_dimension("法国", "open_rate", 0.1079) == "高"
    assert classify_dimension("法国", "open_rate", 0.0588) == "低"


@pytest.mark.parametrize(
    ("dimension_grade", "grade"),
    [
        ("高高高", "S"),
        ("高中高", "A"),
        ("中中中", "B"),
        ("高低高", "B"),
        ("低中高", "C"),
        ("低中低", "D"),
        ("低低中", "D"),
    ],
)
def test_expected_grade_from_multi_dimension_grade(dimension_grade, grade):
    assert expected_grade(dimension_grade) == grade


def test_excel_image_extractor_maps_dispimg_ids_to_media_files(tmp_path):
    extractor = ExcelImageExtractor(FIXTURE)

    mapping = extractor.extract(tmp_path)

    assert set(mapping) >= {
        "ID_C5EFF1CD27774171A5021588C78A65BA",
        "ID_884682E4B2224D84BF30D38126599B71",
    }
    assert Path(mapping["ID_C5EFF1CD27774171A5021588C78A65BA"]).exists()


def test_import_history_workbook_filters_mixed_country_sheet_and_extracts_real_images(tmp_path):
    records = import_history_workbook(FIXTURE, "法国", tmp_path)

    assert len(records) == 20
    assert {record.country for record in records} == {"法国"}
    assert {record.distribution_cycle for record in records} == {"W2"}
    assert all(Path(record.local_image_path).exists() for record in records)
    assert {"houses", "objects", "drawing"}.issubset({record.js_category for record in records})


def test_import_history_workbook_normalizes_real_business_js_category_aliases(tmp_path):
    records = import_history_workbook(FIXTURE, "日本", tmp_path)

    assert len(records) == 25
    assert {record.country for record in records} == {"日本"}
    assert {"objects", "flowers", "drawing"}.issubset({record.js_category for record in records})
    assert "object" not in {record.js_category for record in records}
    assert "flower" not in {record.js_category for record in records}
