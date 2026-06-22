from io import BytesIO
import json

from PIL import Image

from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient, QwenVisionLLMClient, VisionLLMClientFactory
from puzzle_ops.visual_analysis import LocalImageAnalyzer


def png_bytes(color: tuple[int, int, int] = (180, 80, 60)) -> bytes:
    image = Image.new("RGB", (80, 80), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_vision_llm_factory_requires_real_qwen_key_by_default(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("VISION_LLM_PROVIDER", raising=False)

    try:
        VisionLLMClientFactory.create(load_env=False)
    except MissingVisionLLMConfig as exc:
        status = exc.config_status()
    else:
        raise AssertionError("缺少真实视觉 LLM 配置时不能回退 Mock")

    assert status["mode"] == "missing"
    assert status["provider"] == "qwen"
    assert "QWEN_API_KEY" in status["missing"]


def test_vision_llm_factory_creates_qwen_client_when_configured(monkeypatch):
    monkeypatch.setenv("VISION_LLM_PROVIDER", "qwen")
    monkeypatch.setenv("QWEN_API_KEY", "qwen-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = VisionLLMClientFactory.create(load_env=False)

    assert isinstance(client, QwenVisionLLMClient)
    assert client.config_status()["model"] == "qwen3.7-plus"


def test_openai_vision_client_builds_responses_payload_with_data_url():
    captured = {}

    def fake_transport(payload, api_key):
        captured["payload"] = payload
        captured["api_key"] = api_key
        return {
            "output_text": json.dumps(
                {
                    "subject": "法式窗台薰衣草",
                    "scene": "法国乡村窗台",
                    "culture_elements": ["薰衣草", "法式石屋"],
                    "style": "明亮写实",
                    "risk_tags": [],
                    "prompt_keywords": ["法国", "薰衣草"],
                    "confidence": 0.86,
                    "analysis": "适合法国市场的浪漫生活艺术方向。",
                },
                ensure_ascii=False,
            )
        }

    client = OpenAIVisionLLMClient(api_key="sk-test", model="gpt-4.1-mini", transport=fake_transport)
    local = LocalImageAnalyzer().summarize_bytes((png_bytes((210, 190, 80)),))

    result = client.analyze(
        [{"filename": "lavender.png", "content": png_bytes((210, 190, 80)), "content_type": "image/png"}],
        country="法国",
        category="花卉",
        local_summary=local,
    )

    assert captured["api_key"] == "sk-test"
    assert captured["payload"]["model"] == "gpt-4.1-mini"
    content = captured["payload"]["input"][0]["content"]
    assert any(item["type"] == "input_image" and item["image_url"].startswith("data:image/png;base64,") for item in content)
    assert result.provider == "openai"
    assert result.subject == "法式窗台薰衣草"
    assert "薰衣草" in result.culture_elements


def test_openai_client_judges_value_match_with_current_visual_context():
    captured = {}

    def fake_transport(payload, api_key):
        captured["payload"] = payload
        return {
            "output_text": json.dumps(
                {
                    "value_match": "LLM判断：寿司拼盘符合日本饮食文化与清爽色彩价值观，不涉及动物互动。",
                    "confidence": 0.89,
                    "evidence": ["主体内容：寿司拼盘", "已有价值观：文化真实性"],
                    "risk_tags": [],
                },
                ensure_ascii=False,
            )
        }

    client = OpenAIVisionLLMClient(api_key="sk-test", transport=fake_transport)

    result = client.judge_value_match(
        {
            "country": "日本",
            "subject": "寿司拼盘",
            "subject_description": "主体内容：寿司拼盘；色彩氛围：米白、鲑鱼橙；构图环境：日式料理店铺餐桌俯拍。",
            "operation_tag": "试新_日本_寿司拼盘0609",
            "remark": "视觉LLM：真实qwen，置信度0.92",
        },
        (("文化真实性", "优先日本本土元素，避免文化混淆。"),),
    )

    assert "寿司拼盘" in result
    assert "动物互动" in result
    assert "LLM判断" in result
    prompt = captured["payload"]["input"][0]["content"][0]["text"]
    assert "主体内容：寿司拼盘" in prompt
    assert "文化真实性" in prompt


def test_qwen_vision_client_builds_chat_completions_payload_with_data_url():
    captured = {}

    def fake_transport(payload, api_key, base_url):
        captured["payload"] = payload
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "subject": "柴犬樱花",
                                "scene": "日本樱花季庭院",
                                "culture_elements": ["柴犬", "樱花"],
                                "style": "治愈写实",
                                "risk_tags": [],
                                "prompt_keywords": ["日本", "柴犬", "樱花"],
                                "confidence": 0.88,
                                "analysis": "适合日本市场的季节感动物图。",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    client = QwenVisionLLMClient(api_key="qwen-test", model="qwen3-vl-flash", transport=fake_transport)
    local = LocalImageAnalyzer().summarize_bytes((png_bytes(),))

    result = client.analyze(
        [{"filename": "shiba-sakura.png", "content": png_bytes(), "content_type": "image/png"}],
        country="日本",
        category="动物",
        local_summary=local,
    )

    assert captured["api_key"] == "qwen-test"
    assert captured["base_url"].endswith("/v1/chat/completions")
    assert captured["payload"]["model"] == "qwen3-vl-flash"
    content = captured["payload"]["messages"][0]["content"]
    assert any(item["type"] == "image_url" and item["image_url"]["url"].startswith("data:image/png;base64,") for item in content)
    assert result.provider == "qwen"
    assert result.subject == "柴犬樱花"


def test_qwen_client_judges_value_match_with_chat_completions_payload():
    captured = {}

    def fake_transport(payload, api_key, base_url):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "value_match": "LLM判断：日式火车店铺少女符合日常故事感与街景氛围价值观。",
                                "confidence": 0.9,
                                "evidence": ["主体内容：日式火车店铺少女"],
                                "risk_tags": ["版权/IP风格需规避"],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    client = QwenVisionLLMClient(api_key="qwen-test", transport=fake_transport)

    result = client.judge_value_match(
        {
            "country": "日本",
            "subject": "日式火车店铺少女",
            "subject_description": "主体内容：日式火车店铺少女；色彩氛围：暖色；构图环境：复古站台店铺。",
            "operation_tag": "试新_日本_日式火车店铺少女0609",
            "remark": "视觉LLM：真实qwen",
        },
        (("版权与风格风险", "禁止点名或高度复刻知名动画工作室。"),),
    )

    assert "日式火车店铺少女" in result
    content = captured["payload"]["messages"][0]["content"][0]["text"]
    assert "复古站台店铺" in content
    assert "版权与风格风险" in content


def test_value_match_formats_structured_conclusion_evidence_citations_and_review():
    def fake_transport(payload, api_key, base_url):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "conclusion": "符合日本本土饮食文化",
                                "visual_evidence": ["主体为寿司拼盘", "色彩清爽明亮", "料理桌面近景"],
                                "citation_ids": ["JP_VALUE_001#chunk-1"],
                                "risk_tags": [],
                                "manual_review": "确认食材呈现与来源授权",
                                "confidence": 0.92,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    client = QwenVisionLLMClient("test-key", transport=fake_transport)
    result = client.judge_value_match(
        {"country": "日本", "subject": "寿司", "subject_description": "主体内容：寿司拼盘；色彩氛围：清爽明亮；构图环境：料理桌面近景。"},
        (("JP_VALUE_001#chunk-1", "寿司属于日本本土饮食文化"),),
    )

    assert "结论：符合日本本土饮食文化" in result
    assert "图像证据：主体为寿司拼盘、色彩清爽明亮、料理桌面近景" in result
    assert "RAG依据：JP_VALUE_001#chunk-1" in result
    assert "风险提示：未发现明确风险" in result
    assert "人工复核：确认食材呈现与来源授权" in result
