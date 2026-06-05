from puzzle_ops.runtime import SkillLibrary, ToolRegistry
from puzzle_ops.synthetic_data import SyntheticDataGenerator


def test_synthetic_generator_creates_139_rows_per_country_week_with_images(tmp_path):
    generator = SyntheticDataGenerator(tmp_path)

    records = generator.generate_country_history("日本", weeks=2)

    assert len(records) == 278
    assert len([record for record in records if record.distribution_cycle == "W1"]) == 139
    assert all(record.image_id for record in records)
    assert all(record.local_image_path for record in records)
    assert all(record.thumbnail_path == record.local_image_path for record in records)
    assert {record.js_category for record in records} <= generator.allowed_categories


def test_synthetic_generator_can_create_two_country_dataset(tmp_path):
    generator = SyntheticDataGenerator(tmp_path)

    records = generator.generate_dataset(("日本", "法国"), weeks=1)

    assert len(records) == 278
    assert {record.country for record in records} == {"日本", "法国"}


def test_tool_registry_invokes_registered_function_with_tool_result():
    registry = ToolRegistry()
    registry.register("math.add", lambda a, b: {"value": a + b})

    result = registry.call("math.add", a=2, b=3)

    assert result.success
    assert result.data["value"] == 5
    assert result.message == "math.add 调用成功"


def test_skill_library_defines_business_skills_and_required_tools():
    library = SkillLibrary.default()

    skill = library.get("value_judge_skill")

    assert skill.name == "value_judge_skill"
    assert "image.extract_features" in skill.required_tools
    assert "audit.retrieve_policy" in skill.required_tools
    assert "输出格式" in skill.instructions
