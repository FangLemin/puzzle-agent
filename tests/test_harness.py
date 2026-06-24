from pathlib import Path
import base64

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.adapters import ArgillaExporter, DeepEvalAdapter, PhoenixExporter, PromptfooExporter
from puzzle_ops.harness import EvalSample, AgentHarness, load_eval_samples_csv
from puzzle_ops.image_generation import (
    ImageGenerationProviderFactory,
    MockImageGenerationProvider,
    CloudImageGenerationProvider,
    DashScopeImageGenerationProvider,
    _dashscope_images_from_response,
)
from puzzle_ops.storage import PuzzleRepository
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import VisionLLMResult
from PIL import Image


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


class FakeHarnessVisionClient:
    provider = "fake-qwen"

    def __init__(self):
        self.analyze_calls = []
        self.value_calls = []

    def config_status(self):
        return {"provider": self.provider, "mode": "real", "model": "fake-vlm"}

    def analyze(self, images, country, category, local_summary):
        self.analyze_calls.append((images, country, category, local_summary))
        return VisionLLMResult(
            subject="寿司拼盘",
            scene="日式料理桌面近景",
            culture_elements=("寿司", "日式餐具"),
            style="米白与鲑鱼橙，明亮清爽",
            risk_tags=(),
            prompt_keywords=("寿司", "日式料理"),
            confidence=0.92,
            provider=self.provider,
            raw_text="真实模型解析结果",
        )

    def judge_value_match(self, row, value_rules):
        self.value_calls.append((row, value_rules))
        return "价值观判断：符合本土饮食文化；图像证据为寿司拼盘、日式料理桌面和明亮清爽色彩。"


def test_harness_real_model_mode_scores_actual_vlm_output_against_gold(tmp_path):
    image_path = tmp_path / "real-sushi.png"
    Image.new("RGB", (80, 60), (220, 150, 90)).save(image_path)
    client = FakeHarnessVisionClient()
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "harness.db"))
    agent.trial_uploads = TrialImageUploadService(tmp_path / "uploads", vision_client=client)
    sample = EvalSample(
        sample_id="real-vlm-001",
        country="日本",
        local_image_path=str(image_path),
        operation_tag="试新_日本_待解析0622",
        subject="上传前旧文本",
        js_category="food",
        source="real",
        position=5,
        metrics={"open_rate": 0.31, "completion_rate": 0.93, "avg_finish_time": 42.0},
        gold_grade="S",
        gold_subject="寿司拼盘",
        gold_color_mood="米白与鲑鱼橙，明亮清爽",
        gold_composition="日式料理桌面近景",
        gold_value_labels=("本土饮食文化",),
        gold_risk_labels=(),
        human_note="真实运营 gold label",
    )

    run = AgentHarness(agent, execute_model_calls=True).run((sample,), dataset_name="real-vlm", version="0.3.55")

    parse_case = next(case for case in run.cases if case.task_type == "trial_parse_eval")
    value_case = next(case for case in run.cases if case.task_type == "value_match_eval")
    assert run.execution_mode == "real_vlm"
    assert "主体内容：寿司拼盘" in parse_case.agent_output
    assert "上传前旧文本" not in parse_case.agent_output
    assert parse_case.scores["主体匹配"] == 1.0
    assert parse_case.scores["色彩氛围匹配"] == 1.0
    assert parse_case.scores["构图环境匹配"] == 1.0
    assert value_case.scores["价值观一致"] == 1.0
    assert run.metrics["工具调用正确率"] == 1.0
    assert run.metric_evaluable_counts["工具调用正确率"] == 5
    assert "本土饮食文化" in value_case.agent_output
    assert len(client.analyze_calls) == 1
    assert len(client.value_calls) == 1


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


def test_harness_value_case_records_rag_and_memory_evidence(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_long_term_memory(
        "日本",
        "approved_value_rule",
        {"rule": "本土饮食文化应保留真实食材与日常用餐语境"},
    )
    sample = EvalSample(
        sample_id="real-sushi",
        country="日本",
        local_image_path="",
        operation_tag="试新_日本_寿司0622",
        subject="寿司",
        js_category="食物",
        source="synthetic_demo",
        position=5,
        metrics={},
        gold_grade="",
        gold_subject="寿司",
        gold_color_mood="清爽明亮",
        gold_composition="料理桌面近景",
        gold_value_labels=("本土饮食文化",),
        gold_risk_labels=(),
        human_note="",
    )

    run = AgentHarness(agent).run((sample,), dataset_name="trace-set", version="0.3.51")
    case = next(item for item in run.cases if item.task_type == "value_match_eval")

    assert case.evidence_trace["rag_citations"]
    assert "寿司" in case.evidence_trace["visual_evidence"]
    assert case.evidence_trace["memory_evidence"]
    assert case.failure_categories == ()


def test_harness_failures_have_business_categories():
    sample = EvalSample.synthetic_demo(
        sample_id="missing-gold",
        country="日本",
        operation_tag="试新_日本_猫咪0622",
        subject="猫咪",
        gold_grade="",
    )

    run = AgentHarness(PuzzleOpsAgent()).run((sample,), dataset_name="demo", version="0.3.51")

    assert any("missing_gold" in case.failure_categories for case in run.failures)
    assert any("missing_image" in case.failure_categories for case in run.failures)


def test_load_eval_samples_csv_imports_real_gold_dataset_and_skips_invalid_images(tmp_path):
    real_image = tmp_path / "sushi.png"
    real_image.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note",
                "real-001,日本,sushi.png,试新_日本_寿司0615,寿司,food,real,5,0.31,0.93,42,S,寿司,米白与鲑鱼橙,日式料理桌面近景,本土饮食文化;治愈食物,,真实运营样本",
                "real-002,日本,missing.png,试新_日本_塔楼游客0615,塔楼游客,travel,real,3,0.22,0.88,51,A,塔楼游客,清透蓝,海边步道远景,旅游场景,版权/IP风险,图片路径缺失样本",
            )
        ),
        encoding="utf-8",
    )

    samples, issues = load_eval_samples_csv(dataset, image_root=tmp_path)

    assert len(samples) == 1
    assert samples[0].sample_id == "real-001"
    assert samples[0].is_real
    assert samples[0].metrics["open_rate"] == 0.31
    assert samples[0].gold_value_labels == ("本土饮食文化", "治愈食物")
    assert len(issues) == 1
    assert issues[0].sample_id == "real-002"
    assert "图片路径不存在" in issues[0].reason


def test_load_eval_samples_csv_keeps_missing_gold_as_not_evaluable(tmp_path):
    real_image = tmp_path / "tower.png"
    real_image.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note",
                "real-003,日本,tower.png,试新_日本_塔楼游客0615,塔楼游客,travel,real,3,0.22,0.88,51,,,,,,,待补人工gold label",
            )
        ),
        encoding="utf-8",
    )

    samples, issues = load_eval_samples_csv(dataset, image_root=tmp_path)
    run = AgentHarness(PuzzleOpsAgent()).run(samples, dataset_name="real-gold-csv", version="0.3.33")

    assert issues == ()
    assert len(samples) == 1
    assert samples[0].gold_subject == ""
    assert any("not_evaluable" in case.scores.values() for case in run.cases)


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

    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "dashscope")
    monkeypatch.delenv("IMAGE_GENERATION_API_KEY", raising=False)
    monkeypatch.setenv("QWEN_API_KEY", "shared-qwen-key")
    monkeypatch.delenv("IMAGE_GENERATION_MODEL", raising=False)
    dashscope = ImageGenerationProviderFactory.create(tmp_path, transport=lambda **kwargs: {"images": []})
    assert isinstance(dashscope, DashScopeImageGenerationProvider)
    assert dashscope.healthcheck()["provider"] == "dashscope"
    assert dashscope.healthcheck()["model"] == "wan2.6-image"
    assert dashscope.api_key == "shared-qwen-key"
    assert dashscope.healthcheck()["api_key_source"] == "QWEN_API_KEY"
    assert "sdk_available" in dashscope.healthcheck()


def test_dashscope_generation_provider_healthcheck_survives_missing_sdk(monkeypatch, tmp_path):
    def missing_sdk(_name):
        raise ModuleNotFoundError("No module named 'dashscope'")

    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "dashscope")
    monkeypatch.setenv("QWEN_API_KEY", "shared-qwen-key")
    monkeypatch.setattr("puzzle_ops.image_generation.importlib.util.find_spec", missing_sdk)

    provider = ImageGenerationProviderFactory.create(tmp_path, transport=lambda **kwargs: {"images": []})

    status = provider.healthcheck()
    assert status["provider"] == "dashscope"
    assert status["configured"] is True
    assert status["ready"] is False
    assert status["sdk_available"] is False


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


def test_harness_skips_configured_generation_when_reference_image_is_missing(tmp_path):
    sample = EvalSample.synthetic_demo(
        sample_id="syn-no-image",
        country="日本",
        operation_tag="常规_日本_猫咪鲤鱼0609",
        subject="猫咪鲤鱼",
        gold_grade="B",
    )
    provider = MockImageGenerationProvider(tmp_path)

    run = AgentHarness(PuzzleOpsAgent(), generator_provider=provider).run((sample,), dataset_name="demo-set", version="0.3.54")

    derive_case = next(case for case in run.cases if case.task_type == "derive_generation_eval")
    assert derive_case.scores["生成图审核通过"] == "not_evaluable"
    assert "参考图" in derive_case.failure_reasons[0]
    assert not list(tmp_path.glob("derivative_*.png"))


def test_harness_metrics_include_generation_trace_replay_events():
    agent = PuzzleOpsAgent()
    agent.record_generation_event(
        "测试国",
        {
            "status": "succeeded",
            "provider": "cloud",
            "model": "wanx-test",
            "task_id": "img-1,img-2",
            "source_operation_tag": "试新_日本_塔楼游客0615",
            "generated_image_paths": "/tmp/a.png,/tmp/b.png",
            "second_review_status": "passed",
            "feishu_attachment_status": "ready",
            "error_type": "none",
            "message": "已生成2张衍生参考图",
        },
    )
    agent.record_generation_event(
        "测试国",
        {
            "status": "failed",
            "provider": "dashscope",
            "model": "wanx-test",
            "task_id": "",
            "source_operation_tag": "试新_日本_寿司0615",
            "generated_image_paths": "",
            "second_review_status": "not_started",
            "feishu_attachment_status": "blocked",
            "error_type": "quota_exceeded",
            "message": "DashScope 图像生成失败：quota exceeded",
        },
    )
    sample = EvalSample.synthetic_demo(
        sample_id="syn-001",
        country="测试国",
        operation_tag="常规_日本_猫咪鲤鱼0609",
        subject="猫咪鲤鱼",
        gold_grade="B",
    )

    run = AgentHarness(agent).run((sample,), dataset_name="generation-trace", version="0.3.43")

    assert run.metrics["生成Trace完整率"] == 1.0
    assert run.metrics["二次审核通过率"] == 0.5
    assert run.metrics["飞书附件Ready率"] == 0.5
    assert run.metrics["生成失败可分类率"] == 1.0


def test_harness_metrics_include_rag_runtime_stats():
    agent = PuzzleOpsAgent()
    agent.value_audit_rag_answer("日本", "寿司是否符合日本价值观，并检查文字水印风险")
    sample = EvalSample.synthetic_demo(
        sample_id="syn-001",
        country="日本",
        operation_tag="试新_日本_寿司0616",
        subject="寿司",
        gold_grade="B",
    )

    run = AgentHarness(agent).run((sample,), dataset_name="demo-set", version="0.3.49")

    assert "RAG缓存命中率" in run.metrics
    assert "RAG远程调用率" in run.metrics
    assert "RAG降级率" in run.metrics
    assert run.metrics["RAG缓存命中率"] >= 0.0
    assert run.metrics["RAG远程调用率"] >= 0.0
    assert run.metrics["RAG降级率"] >= 0.0


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


def test_dashscope_generation_provider_uses_reference_image_and_downloads_sdk_result(tmp_path):
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/"
        "pLvAAAAAElFTkSuQmCC"
    )
    calls = []

    def fake_sdk_generate(**kwargs):
        calls.append(kwargs)
        return {
            "images": [
                {"url": "https://example.test/generated-1.png", "prompt": "春季寿司便当"},
                {"b64_json": png_b64, "prompt": "夏季寿司店铺"},
            ]
        }

    def fake_download(url):
        assert url == "https://example.test/generated-1.png"
        return base64.b64decode(png_b64)

    provider = DashScopeImageGenerationProvider(
        output_dir=tmp_path,
        api_key="gen-test",
        model="wan2.6-image",
        sdk_generate=fake_sdk_generate,
        image_downloader=fake_download,
    )

    images = provider.generate_derivatives(
        reference_image="real-sushi.png",
        prompt="保留寿司主体和日式餐桌，换成春季便当场景",
        negative_prompt="避免品牌logo、文字水印、知名动漫风格",
        count=2,
        seed=615,
        style_constraints={"source_sample_id": "sample-1", "retained_features": "寿司；明亮清爽"},
    )

    assert len(calls) == 1
    assert calls[0]["reference_image"] == "real-sushi.png"
    assert calls[0]["prompt"] == "保留寿司主体和日式餐桌，换成春季便当场景"
    assert calls[0]["count"] == 2
    assert len(images) == 2
    assert images[0].provider == "dashscope"
    assert images[0].source_sample_id == "sample-1"
    assert Path(images[0].local_image_path).exists()


def test_dashscope_response_parser_accepts_output_results_url_shape():
    class FakeResponse:
        status_code = 200
        output = {
            "task_id": "task-123",
            "results": [
                {"url": "https://example.test/generated-1.png"},
                {"url": "https://example.test/generated-2.png"},
            ],
        }

    images = _dashscope_images_from_response(FakeResponse(), prompt="法式海滩野餐")

    assert images == (
        {"url": "https://example.test/generated-1.png", "prompt": "法式海滩野餐", "task_id": "task-123"},
        {"url": "https://example.test/generated-2.png", "prompt": "法式海滩野餐", "task_id": "task-123"},
    )


def test_dashscope_generation_provider_raises_clear_error_on_failed_task(tmp_path):
    def fake_sdk_generate(**kwargs):
        raise RuntimeError("quota exceeded")

    provider = DashScopeImageGenerationProvider(
        output_dir=tmp_path,
        api_key="gen-test",
        model="wan2.6-image",
        sdk_generate=fake_sdk_generate,
    )

    try:
        provider.generate_derivatives(
            reference_image="real-sushi.png",
            prompt="寿司",
            negative_prompt="避免品牌logo",
            count=1,
            seed=615,
            style_constraints={},
        )
    except RuntimeError as exc:
        assert "DashScope 图像生成失败" in str(exc)
        assert "quota exceeded" in str(exc)
    else:
        raise AssertionError("expected failed DashScope task to raise RuntimeError")


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
