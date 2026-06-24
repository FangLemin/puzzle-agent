from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
import ssl
from typing import Callable
from urllib import request

from puzzle_ops.visual_analysis import LocalVisualSummary


@dataclass(frozen=True)
class VisionLLMResult:
    subject: str
    scene: str
    culture_elements: tuple[str, ...]
    style: str
    risk_tags: tuple[str, ...]
    prompt_keywords: tuple[str, ...]
    confidence: float
    provider: str
    raw_text: str


class MissingVisionLLMConfig(RuntimeError):
    def __init__(self, missing: tuple[str, ...], provider: str = "qwen"):
        self.missing = missing
        self.provider = provider
        super().__init__("缺少真实视觉 LLM 配置：" + "、".join(missing))

    def config_status(self) -> dict[str, object]:
        return {"provider": self.provider, "mode": "missing", "model": "", "missing": self.missing}


class OpenAIVisionLLMClient:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1-mini",
        detail: str = "low",
        transport: Callable[[dict[str, object], str], dict[str, object]] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.detail = detail
        self.transport = transport or _openai_transport

    def config_status(self) -> dict[str, object]:
        return {"provider": self.provider, "mode": "real", "model": self.model, "detail": self.detail}

    def analyze(self, images: list[dict[str, object]], country: str, category: str, local_summary: LocalVisualSummary) -> VisionLLMResult:
        payload = self._payload(images, country, category, local_summary)
        response = self.transport(payload, self.api_key)
        output_text = str(response.get("output_text", ""))
        data = json.loads(output_text) if output_text else {}
        return VisionLLMResult(
            subject=str(data.get("subject", "")) or _subject_from_category(category),
            scene=str(data.get("scene", "")),
            culture_elements=_tuple_field(data.get("culture_elements", [])),
            style=str(data.get("style", "")),
            risk_tags=_tuple_field(data.get("risk_tags", [])),
            prompt_keywords=_tuple_field(data.get("prompt_keywords", [])),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            provider=self.provider,
            raw_text=str(data.get("analysis", output_text)),
        )

    def judge_value_match(self, row: dict[str, object], value_rules: tuple[tuple[str, str], ...]) -> str:
        payload = self._value_payload(row, value_rules)
        response = self.transport(payload, self.api_key)
        output_text = str(response.get("output_text", ""))
        data = json.loads(output_text) if output_text else {}
        return _value_match_text(data, output_text, self.provider)

    def _payload(self, images: list[dict[str, object]], country: str, category: str, local_summary: LocalVisualSummary) -> dict[str, object]:
        content: list[dict[str, object]] = [
            {
                "type": "input_text",
                "text": (
                    "你是 PuzzleOps 出海拼图内容运营 Agent 的视觉语义解析器。"
                    "请只输出 JSON，字段为 subject, scene, culture_elements, style, risk_tags, "
                    "prompt_keywords, confidence, analysis。"
                    f"国家={country}，品类={category}。"
                    f"本地视觉特征={local_summary.palette_summary}；{local_summary.visual_summary}；"
                    f"{local_summary.composition_summary}；质量={local_summary.quality_summary}。"
                    "重点判断：主体、场景、法国/日本文化元素、拼图风格、版权/IP/文化混淆风险。"
                ),
            }
        ]
        for image in images[:3]:
            data_url = _image_data_url(image)
            if data_url:
                content.append({"type": "input_image", "image_url": data_url, "detail": self.detail})
        return {"model": self.model, "input": [{"role": "user", "content": content}]}

    def _value_payload(self, row: dict[str, object], value_rules: tuple[tuple[str, str], ...]) -> dict[str, object]:
        return {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": _value_match_prompt(row, value_rules)}],
                }
            ],
        }


class QwenVisionLLMClient:
    provider = "qwen"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3.7-plus",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        transport: Callable[[dict[str, object], str, str], dict[str, object]] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.transport = transport or _qwen_transport

    def config_status(self) -> dict[str, object]:
        return {"provider": self.provider, "mode": "real", "model": self.model, "base_url": self.base_url}

    def analyze(self, images: list[dict[str, object]], country: str, category: str, local_summary: LocalVisualSummary) -> VisionLLMResult:
        payload = self._payload(images, country, category, local_summary)
        response = self.transport(payload, self.api_key, self.base_url)
        output_text = _extract_chat_completion_text(response)
        data = json.loads(output_text) if output_text else {}
        return VisionLLMResult(
            subject=str(data.get("subject", "")) or _subject_from_category(category),
            scene=str(data.get("scene", "")),
            culture_elements=_tuple_field(data.get("culture_elements", [])),
            style=str(data.get("style", "")),
            risk_tags=_tuple_field(data.get("risk_tags", [])),
            prompt_keywords=_tuple_field(data.get("prompt_keywords", [])),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            provider=self.provider,
            raw_text=str(data.get("analysis", output_text)),
        )

    def judge_value_match(self, row: dict[str, object], value_rules: tuple[tuple[str, str], ...]) -> str:
        payload = self._value_payload(row, value_rules)
        response = self.transport(payload, self.api_key, self.base_url)
        output_text = _extract_chat_completion_text(response)
        data = json.loads(output_text) if output_text else {}
        return _value_match_text(data, output_text, self.provider)

    def _payload(self, images: list[dict[str, object]], country: str, category: str, local_summary: LocalVisualSummary) -> dict[str, object]:
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    "你是 PuzzleOps 出海拼图内容运营 Agent 的视觉语义解析器。"
                    "请只输出 JSON，字段为 subject, scene, culture_elements, style, risk_tags, "
                    "prompt_keywords, confidence, analysis。"
                    f"国家={country}，品类={category}。"
                    f"本地视觉特征={local_summary.palette_summary}；{local_summary.visual_summary}；"
                    f"{local_summary.composition_summary}；质量={local_summary.quality_summary}。"
                    "重点判断：主体、场景、法国/日本文化元素、拼图风格、版权/IP/文化混淆风险。"
                ),
            }
        ]
        for image in images[:3]:
            data_url = _image_data_url(image)
            if data_url:
                content.append({"type": "image_url", "image_url": {"url": data_url}})
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }

    def _value_payload(self, row: dict[str, object], value_rules: tuple[tuple[str, str], ...]) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": _value_match_prompt(row, value_rules)}]}],
            "response_format": {"type": "json_object"},
        }


class VisionLLMClientFactory:
    @classmethod
    def create(cls, load_env: bool = True) -> OpenAIVisionLLMClient | QwenVisionLLMClient:
        if load_env:
            _load_env_file(Path.cwd() / ".env")
        provider = os.getenv("VISION_LLM_PROVIDER", "qwen").strip().lower()
        missing = []
        if provider == "qwen":
            if not os.getenv("QWEN_API_KEY"):
                missing.append("QWEN_API_KEY")
            if missing:
                raise MissingVisionLLMConfig(tuple(missing), provider="qwen")
            return QwenVisionLLMClient(
                api_key=str(os.getenv("QWEN_API_KEY")),
                model=os.getenv("QWEN_VISION_MODEL", "qwen3.7-plus"),
                base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"),
            )
        if provider == "openai":
            if not os.getenv("OPENAI_API_KEY"):
                missing.append("OPENAI_API_KEY")
            if missing:
                raise MissingVisionLLMConfig(tuple(missing), provider="openai")
            return OpenAIVisionLLMClient(
                api_key=str(os.getenv("OPENAI_API_KEY")),
                model=os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
                detail=os.getenv("OPENAI_VISION_DETAIL", "low"),
            )
        raise MissingVisionLLMConfig((f"VISION_LLM_PROVIDER=qwen 或 openai，当前为 {provider}",), provider=provider)


def _openai_transport(payload: dict[str, object], api_key: str) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        "https://api.openai.com/v1/responses",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with request.urlopen(req, timeout=30, context=_https_context()) as response:
        raw = json.loads(response.read().decode("utf-8"))
    output_text = raw.get("output_text")
    if output_text is None:
        output_text = _extract_output_text(raw)
    return {"output_text": output_text or ""}


def _qwen_transport(payload: dict[str, object], api_key: str, base_url: str) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        base_url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with request.urlopen(req, timeout=_qwen_timeout_seconds(), context=_https_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _https_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _qwen_timeout_seconds() -> float:
    try:
        return min(max(float(os.getenv("QWEN_TIMEOUT_SECONDS", "90")), 10.0), 300.0)
    except ValueError:
        return 90.0


def _extract_chat_completion_text(raw: dict[str, object]) -> str:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def _extract_output_text(raw: dict[str, object]) -> str:
    texts = []
    for item in raw.get("output", []) if isinstance(raw.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                texts.append(str(content.get("text", "")))
    return "".join(texts)


def _image_data_url(image: dict[str, object]) -> str:
    content_type = str(image.get("content_type", "image/png") or "image/png")
    content = image.get("content")
    if isinstance(content, bytes) and content:
        data = content
    elif image.get("path"):
        data = Path(str(image["path"])).read_bytes()
    else:
        return ""
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


def _subject_from_text(text: str) -> str:
    mapping = {
        "shiba": "柴犬",
        "dog": "柴犬",
        "cat": "猫咪",
        "koi": "锦鲤",
        "lavender": "薰衣草",
        "sakura": "樱花",
        "flower": "花卉",
        "house": "法式乡村石屋",
        "window": "法式窗台",
    }
    for key, value in mapping.items():
        if key in text:
            return value
    return ""


def _subject_from_category(category: str) -> str:
    if "动物" in category:
        return "动物主体"
    if "花" in category:
        return "花卉"
    if "人物" in category:
        return "人物"
    return category or "待确认主体"


def _culture_from_text(text: str, country: str) -> tuple[str, ...]:
    elements = []
    if "sakura" in text or "樱花" in text:
        elements.append("樱花")
    if "lavender" in text or "薰衣草" in text:
        elements.append("薰衣草")
    if "koi" in text:
        elements.append("锦鲤")
    if "house" in text and country == "法国":
        elements.append("法式石屋")
    if not elements:
        elements.append(f"{country}市场文化语境")
    return tuple(elements)


def _risk_from_text(text: str) -> tuple[str, ...]:
    risks = []
    if any(word in text for word in ("ghibli", "miyazaki", "totoro", "logo", "brand")):
        risks.append("版权/IP风险")
    return tuple(risks)


def _scene(country: str, subject: str, culture: tuple[str, ...]) -> str:
    if country == "法国":
        return f"{subject}与法式生活艺术场景，包含{'、'.join(culture)}"
    return f"{subject}与日式季节感场景，包含{'、'.join(culture)}"


def _value_match_prompt(row: dict[str, object], value_rules: tuple[tuple[str, str], ...]) -> str:
    rules = "\n".join(f"- {title}: {body}" for title, body in value_rules)
    return (
        "你是 PuzzleOps 出海拼图内容运营 Agent 的价值观大师。"
        "请基于当前图片的视觉LLM解析结果和 RAG 召回的价值观/审核引用依据，判断这张试新图是否符合市场价值观。"
        "不要套用默认模板；必须引用当前主体、色彩氛围、构图环境中的证据。"
        "如果引用依据不足，请明确要求人工复核，不要编造不存在的规则。"
        "只输出 JSON，字段为 conclusion, visual_evidence, citation_ids, risk_tags, manual_review, confidence。"
        f"\n国家：{row.get('country', '')}"
        f"\nJS分类：{row.get('js_category', '')}"
        f"\n运营tag：{row.get('operation_tag', '')}"
        f"\n主体：{row.get('subject', '')}"
        f"\n主体描述：{row.get('subject_description', '')}"
        f"\n解析备注：{row.get('remark', '')}"
        f"\nRAG引用依据：\n{rules}"
        "\n输出要求：conclusion 只写符合/部分符合/不符合及简短理由；visual_evidence 必须来自当前图片解析；"
        "citation_ids 只能填写上方真实存在的 RAG 引用 ID；risk_tags 无风险时返回空数组；"
        "manual_review 写运营需要复核的具体事项。"
    )


def _value_match_text(data: dict[str, object], output_text: str, provider: str) -> str:
    conclusion = str(data.get("conclusion", "") or data.get("value_match", "") or output_text).strip()
    confidence = data.get("confidence")
    visual_source = data.get("visual_evidence", data.get("evidence", []))
    visual_evidence = _tuple_field(visual_source)
    citation_source = data.get("citation_ids", [])
    citations = _tuple_field(citation_source)
    risks = _tuple_field(data.get("risk_tags", []))
    manual_review = str(data.get("manual_review", "") or "运营需复核图像细节、文化准确性与素材授权").strip()
    lines = [
        f"结论：{conclusion or '依据不足，暂不能判断'}",
        f"图像证据：{'、'.join(visual_evidence) or '未提供可核验的当前图片证据'}",
        f"RAG依据：{'、'.join(citations) or '未提供可溯源引用'}",
        f"风险提示：{'、'.join(risks) or '未发现明确风险'}",
        f"人工复核：{manual_review}",
    ]
    model_detail = f"价值观LLM：真实{provider}"
    if confidence not in (None, ""):
        try:
            model_detail += f"，置信度{float(confidence):.2f}"
        except (TypeError, ValueError):
            model_detail += f"，置信度{confidence}"
    lines.append(f"模型记录：{model_detail}")
    return "；".join(lines)


def _tuple_field(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    return (text,) if text else ()


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
