from pathlib import Path

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.adapters import ArgillaExporter, DeepEvalAdapter, PhoenixExporter, PromptfooExporter
from puzzle_ops.harness import EvalSample, AgentHarness
from puzzle_ops.image_generation import ImageGenerationProviderFactory, MockImageGenerationProvider, CloudImageGenerationProvider
from puzzle_ops.storage import PuzzleRepository


def test_harness_builds_real_and_synthetic_dataset_summary(tmp_path):
    image_path = tmp_path / "real-sushi.png"
    image_path.write_bytes(b"fake-png")
    samples = (
        EvalSample(
            sample_id="real-001",
            country="日本",
            local_image_path=str(image_path),
            operation_tag="试新_日本_寿司0609",
            subject="寿司",
            js_category="food",
            source="real",
            position=5,
            metrics={"open_rate": 0.31, "completion_rate": 0.93},
            gold_grade="S",
            gold_subject="寿司",
            gold_color_mood="米白与鲑鱼橙，明亮清爽",
            gold_composition="日式料理桌面近景",
            gold_value_labels=("本土饮食文化",),
            gold_risk_labels=(),
            human_note="真实运营小样本",
        ),
        EvalSample.synthetic_demo(
            sample_id="syn-001",
            country="日本",
            operation_tag="常规_日本_猫咪鲤鱼0609",
            subject="猫咪鲤鱼",
            gold_grade="B",
        ),
    )

    summary = AgentHarness(PuzzleOpsAgent()).dataset_summary(samples)

    assert summary["真实样本数"] == 1
    assert summary["合成样本数"] == 1
    assert summary["国家分布"]["日本"] == 2
    assert summary["等级分布"]["S"] == 1
    assert summary["等级分布"]["B"] == 1


def test_harness_run_records_case_results_failures_and_skips_missing_gold(tmp_path):
    image_path = tmp_path / "real-sushi.png"
    image_path.write_bytes(b"fake-png")
    samples = (
        EvalSample(
            sample_id="real-001",
            country="日本",
            local_image_path=str(image_path),
            operation_tag="试新_日本_寿司0609",
            subject="寿司",
            js_category="food",
            source="real",
            position=5,
            metrics={"open_rate": 0.31, "completion_rate": 0.93},
            gold_grade="S",
            gold_subject="寿司",
            gold_color_mood="米白与鲑鱼橙，明亮清爽",
            gold_composition="日式料理桌面近景",
            gold_value_labels=("本土饮食文化",),
            gold_risk_labels=("品牌露出",),
            human_note="真实运营小样本",
        ),
        EvalSample.synthetic_demo(
            sample_id="syn-001",
            country="日本",
            operation_tag="常规_日本_猫咪鲤鱼0609",
            subject="猫咪鲤鱼",
            gold_grade="",
        ),
    )

    run = AgentHarness(PuzzleOpsAgent()).run(samples, dataset_name="small-real-set", version="0.3.28")

    assert run.dataset_name == "small-real-set"
    assert run.version == "0.3.28"
    assert len(run.cases) >= 5
    assert run.metrics["真实样本占比"] == 0.5
    assert "三段式描述合规率" in run.metrics
    assert any(case.task_type == "trial_parse_eval" for case in run.cases)
    assert any("not_evaluable" in case.scores.values() for case in run.cases)
    assert run.failures


def test_mock_generation_provider_returns_reproducible_derivative_records(tmp_path):
    provider = MockImageGenerationProvider(tmp_path)

    images = provider.generate_derivatives(
        reference_image="real-sushi.png",
        prompt="保留寿司主体和日式餐桌，换成春季便当场景",
        negative_prompt="避免品牌logo、文字水印、知名动漫风格",
        count=2,
        seed=609,
        style_constraints={"country": "日本", "color_mood": "明亮清爽"},
    )

    assert len(images) == 2
    assert images[0].provider == "mock"
    assert Path(images[0].local_image_path).exists()
    assert images[0].seed == 609
    assert images[1].seed == 610
    assert "寿司" in images[0].prompt
    assert "品牌logo" in images[0].negative_prompt


def test_image_generation_factory_reports_unconfigured_mock_and_cloud(monkeypatch, tmp_path):
    monkeypatch.delenv("IMAGE_GENERATION_PROVIDER", raising=False)
    missing = ImageGenerationProviderFactory.create(tmp_path)
    assert missing.healthcheck()["configured"] is False

    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "mock")
    mock = ImageGenerationProviderFactory.create(tmp_path)
    assert mock.healthcheck()["provider"] == "mock"

    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "cloud")
    monkeypatch.setenv("IMAGE_GENERATION_API_KEY", "gen-test")
    monkeypatch.setenv("IMAGE_GENERATION_MODEL", "wanx2.1-t2i-plus")
    cloud = ImageGenerationProviderFactory.create(tmp_path, transport=lambda payload, api_key, base_url: {"images": []})
    assert isinstance(cloud, CloudImageGenerationProvider)
    assert cloud.healthcheck()["configured"] is True
    assert cloud.healthcheck()["model"] == "wanx2.1-t2i-plus"


def test_harness_skips_unconfigured_generation_provider(tmp_path):
    sample = EvalSample.synthetic_demo(
        sample_id="syn-001",
        country="日本",
        operation_tag="常规_日本_猫咪鲤鱼0609",
        subject="猫咪鲤鱼",
        gold_grade="B",
    )
    provider = ImageGenerationProviderFactory.create(tmp_path)

    run = AgentHarness(PuzzleOpsAgent(), generator_provider=provider).run((sample,), dataset_name="demo-set", version="0.3.31")

    derive_case = next(case for case in run.cases if case.task_type == "derive_generation_eval")
    assert derive_case.scores["生成图审核通过"] == "not_evaluable"
    assert "生成 provider 未配置" in derive_case.failure_reasons


def test_cloud_generation_provider_writes_returned_images_with_generation_metadata(tmp_path):
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/"
        "pLvAAAAAElFTkSuQmCC"
    )
    captured = {}

    def fake_transport(payload, api_key, base_url):
        captured.update(payload=payload, api_key=api_key, base_url=base_url)
        return {
            "images": [
                {"b64_json": png_b64, "revised_prompt": "春季寿司便当"},
                {"b64_json": png_b64, "revised_prompt": "夏季寿司店铺"},
            ]
        }

    provider = CloudImageGenerationProvider(
        output_dir=tmp_path,
        api_key="gen-test",
        model="wanx2.1-t2i-plus",
        base_url="https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
        transport=fake_transport,
    )

    images = provider.generate_derivatives(
        reference_image="real-sushi.png",
        prompt="保留寿司主体和日式餐桌，换成春季便当场景",
        negative_prompt="避免品牌logo、文字水印、知名动漫风格",
        count=2,
        seed=614,
        style_constraints={"source_sample_id": "sample-1", "retained_features": "寿司；明亮清爽"},
    )

    assert captured["api_key"] == "gen-test"
    assert captured["payload"]["model"] == "wanx2.1-t2i-plus"
    assert captured["payload"]["count"] == 2
    assert len(images) == 2
    assert images[0].provider == "cloud"
    assert images[0].source_sample_id == "sample-1"
    assert Path(images[0].local_image_path).exists()
    assert images[0].risk_notes == ("生成图需二次 VLM 解析与审核",)


def test_repository_saves_and_reads_harness_runs(tmp_path):
    sample = EvalSample.synthetic_demo(
        sample_id="syn-001",
        country="日本",
        operation_tag="常规_日本_猫咪鲤鱼0609",
        subject="猫咪鲤鱼",
        gold_grade="B",
    )
    run = AgentHarness(PuzzleOpsAgent()).run((sample,), dataset_name="demo-set", version="0.3.28")
    repo = PuzzleRepository(tmp_path / "puzzle.db")

    repo.save_harness_run(run)
    runs = repo.harness_runs()

    assert len(runs) == 1
    assert runs[0].run_id == run.run_id
    assert runs[0].dataset_name == "demo-set"
    assert runs[0].metrics["三段式描述合规率"] == 1.0
    assert any(case.task_type == "derive_generation_eval" for case in runs[0].cases)


def test_harness_external_adapters_export_open_source_payloads():
    sample = EvalSample.synthetic_demo(
        sample_id="syn-001",
        country="日本",
        operation_tag="常规_日本_猫咪鲤鱼0609",
        subject="猫咪鲤鱼",
        gold_grade="B",
    )
    run = AgentHarness(PuzzleOpsAgent()).run((sample,), dataset_name="demo-set", version="0.3.28")

    phoenix = PhoenixExporter().export(run)
    deepeval = DeepEvalAdapter().export(run)
    promptfoo = PromptfooExporter().export(run)
    argilla = ArgillaExporter().export(run)

    assert phoenix["project_name"] == "puzzle_ops_agent_harness"
    assert phoenix["traces"][0]["span_name"] in {"trial_parse_eval", "value_match_eval", "audit_eval", "grade_predict_eval", "derive_generation_eval", "feishu_sync_eval"}
    assert deepeval["test_cases"][0]["input"]
    assert "assert" in deepeval["pytest_hint"]
    assert promptfoo["providers"] == ["qwen-vl", "openai-compatible-vlm"]
    assert argilla["records"][0]["fields"]["sample_id"] == "syn-001"
