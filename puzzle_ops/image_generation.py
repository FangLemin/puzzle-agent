from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib import request
import base64
import hashlib
import importlib.util
import json
import os
import ssl


@dataclass(frozen=True)
class DerivativeImage:
    image_id: str
    local_image_path: str
    provider: str
    prompt: str
    negative_prompt: str
    seed: int
    source_sample_id: str
    retained_features: tuple[str, ...]
    changed_features: tuple[str, ...]
    risk_notes: tuple[str, ...]
    generated_at: str


class ImageGenerationProvider:
    provider_name = "base"

    def healthcheck(self) -> dict[str, object]:
        return {"provider": self.provider_name, "configured": False, "message": "生成 provider 未配置"}

    def generate_derivatives(
        self,
        reference_image: str,
        prompt: str,
        negative_prompt: str,
        count: int,
        seed: int,
        style_constraints: dict[str, str],
    ) -> tuple[DerivativeImage, ...]:
        raise NotImplementedError


class MissingImageGenerationProvider(ImageGenerationProvider):
    provider_name = "not_configured"


class MockImageGenerationProvider(ImageGenerationProvider):
    """Local deterministic provider for Harness tests and UI flow, not real image generation."""

    provider_name = "mock"

    def __init__(self, output_dir: Path | str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def healthcheck(self) -> dict[str, object]:
        return {"provider": self.provider_name, "configured": True, "message": "Mock provider 仅生成占位记录"}

    def generate_derivatives(
        self,
        reference_image: str,
        prompt: str,
        negative_prompt: str,
        count: int,
        seed: int,
        style_constraints: dict[str, str],
    ) -> tuple[DerivativeImage, ...]:
        images: list[DerivativeImage] = []
        for index in range(count):
            item_seed = seed + index
            digest = hashlib.sha1(f"{reference_image}:{prompt}:{item_seed}".encode("utf-8")).hexdigest()[:12]
            path = self.output_dir / f"derivative_{digest}.png"
            path.write_bytes(_placeholder_png_bytes())
            images.append(
                DerivativeImage(
                    image_id=f"mock-{digest}",
                    local_image_path=str(path),
                    provider=self.provider_name,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    seed=item_seed,
                    source_sample_id=str(style_constraints.get("source_sample_id", "")),
                    retained_features=_tuple_from_constraint(style_constraints, "retained_features"),
                    changed_features=_tuple_from_constraint(style_constraints, "changed_features"),
                    risk_notes=_tuple_from_constraint(style_constraints, "risk_notes") or ("需二次 VLM 解析与人工审核",),
                    generated_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
        return tuple(images)


class CloudImageGenerationProvider(ImageGenerationProvider):
    provider_name = "cloud"

    def __init__(self, output_dir: Path | str, api_key: str, model: str, base_url: str, transport=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.transport = transport or _cloud_transport

    def healthcheck(self) -> dict[str, object]:
        return {
            "provider": self.provider_name,
            "configured": bool(self.api_key and self.model and self.base_url),
            "message": f"真实生成 provider 已配置：{self.model}",
            "model": self.model,
            "base_url": self.base_url,
        }

    def generate_derivatives(
        self,
        reference_image: str,
        prompt: str,
        negative_prompt: str,
        count: int,
        seed: int,
        style_constraints: dict[str, str],
    ) -> tuple[DerivativeImage, ...]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "count": count,
            "seed": seed,
            "reference_image": reference_image,
            "style_constraints": style_constraints,
        }
        response = self.transport(payload, self.api_key, self.base_url)
        items = response.get("images", [])
        images: list[DerivativeImage] = []
        for index, item in enumerate(items[:count]):
            image_bytes = _image_bytes_from_response_item(item)
            item_seed = seed + index
            digest = hashlib.sha1(image_bytes + f":{item_seed}".encode("utf-8")).hexdigest()[:12]
            path = self.output_dir / f"cloud_derivative_{digest}.png"
            path.write_bytes(image_bytes)
            images.append(
                DerivativeImage(
                    image_id=f"cloud-{digest}",
                    local_image_path=str(path),
                    provider=self.provider_name,
                    prompt=str(item.get("revised_prompt") or prompt) if isinstance(item, dict) else prompt,
                    negative_prompt=negative_prompt,
                    seed=item_seed,
                    source_sample_id=str(style_constraints.get("source_sample_id", "")),
                    retained_features=_tuple_from_constraint(style_constraints, "retained_features"),
                    changed_features=_tuple_from_constraint(style_constraints, "changed_features"),
                    risk_notes=("生成图需二次 VLM 解析与审核",),
                    generated_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
        return tuple(images)


class DashScopeImageGenerationProvider(ImageGenerationProvider):
    provider_name = "dashscope"

    def __init__(
        self,
        output_dir: Path | str,
        api_key: str,
        model: str,
        api_key_source: str = "IMAGE_GENERATION_API_KEY",
        sdk_available: bool | None = None,
        sdk_generate=None,
        image_downloader=None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.api_key_source = api_key_source
        self.model = model
        self.sdk_available = _dashscope_sdk_available() if sdk_available is None else sdk_available
        self.sdk_generate = sdk_generate or _dashscope_sdk_generate
        self.image_downloader = image_downloader or _download_image

    def healthcheck(self) -> dict[str, object]:
        configured = bool(self.api_key and self.model)
        message = f"DashScope 参考图生成 provider 已配置：{self.model}"
        if not self.sdk_available:
            message += "；SDK 未安装或不可导入"
        return {
            "provider": self.provider_name,
            "configured": configured,
            "ready": configured and self.sdk_available,
            "message": message,
            "model": self.model,
            "base_url": "DashScope SDK ImageGeneration",
            "api_key_source": self.api_key_source,
            "sdk_available": self.sdk_available,
        }

    def generate_derivatives(
        self,
        reference_image: str,
        prompt: str,
        negative_prompt: str,
        count: int,
        seed: int,
        style_constraints: dict[str, str],
    ) -> tuple[DerivativeImage, ...]:
        try:
            response = self.sdk_generate(
                model=self.model,
                api_key=self.api_key,
                reference_image=reference_image,
                prompt=prompt,
                negative_prompt=negative_prompt,
                count=count,
                seed=seed,
                style_constraints=style_constraints,
            )
        except Exception as exc:
            raise RuntimeError(f"DashScope 图像生成失败：{exc}") from exc
        results = response.get("images", []) if isinstance(response, dict) else []
        if not results:
            raise RuntimeError("DashScope 图像生成失败：响应中没有生成图片")
        images: list[DerivativeImage] = []
        for index, item in enumerate(results[:count]):
            image_bytes = _image_bytes_from_response_item(item, self.image_downloader)
            item_seed = seed + index
            digest = hashlib.sha1(image_bytes + f":{item_seed}".encode("utf-8")).hexdigest()[:12]
            path = self.output_dir / f"dashscope_derivative_{digest}.png"
            path.write_bytes(image_bytes)
            images.append(
                DerivativeImage(
                    image_id=f"dashscope-{digest}",
                    local_image_path=str(path),
                    provider=self.provider_name,
                    prompt=str(item.get("prompt") or item.get("revised_prompt") or prompt) if isinstance(item, dict) else prompt,
                    negative_prompt=negative_prompt,
                    seed=item_seed,
                    source_sample_id=str(style_constraints.get("source_sample_id", "")),
                    retained_features=_tuple_from_constraint(style_constraints, "retained_features"),
                    changed_features=_tuple_from_constraint(style_constraints, "changed_features"),
                    risk_notes=("生成图需二次 VLM 解析与审核",),
                    generated_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
        return tuple(images)


class ComfyUIImageGenerationProvider(ImageGenerationProvider):
    provider_name = "comfyui"

    def __init__(
        self,
        output_dir: Path | str,
        base_url: str = "http://127.0.0.1:8188",
        workflow_path: str = "",
        transport=None,
        image_downloader=None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.workflow_path = workflow_path
        self.transport = transport or _comfyui_transport
        self.image_downloader = image_downloader or _download_image

    def healthcheck(self) -> dict[str, object]:
        workflow_configured = bool(self.workflow_path and Path(self.workflow_path).expanduser().is_file())
        return {
            "provider": self.provider_name,
            "configured": bool(self.base_url),
            "ready": bool(self.base_url and workflow_configured),
            "message": (
                f"ComfyUI 生成 provider 已配置：{self.base_url}"
                if workflow_configured
                else f"ComfyUI 生成 provider 已配置：{self.base_url}；缺少 COMFYUI_WORKFLOW_PATH"
            ),
            "model": "ComfyUI workflow",
            "base_url": self.base_url,
            "workflow_path": self.workflow_path or "未配置",
            "workflow_configured": workflow_configured,
        }

    def generate_derivatives(
        self,
        reference_image: str,
        prompt: str,
        negative_prompt: str,
        count: int,
        seed: int,
        style_constraints: dict[str, str],
    ) -> tuple[DerivativeImage, ...]:
        workflow = self._workflow(prompt, negative_prompt, seed, reference_image, style_constraints)
        payload = {
            "workflow": workflow,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "count": count,
            "seed": seed,
            "reference_image": reference_image,
            "style_constraints": style_constraints,
        }
        try:
            response = self.transport(payload, self.base_url)
        except Exception as exc:
            raise RuntimeError(f"ComfyUI 图像生成失败：{exc}") from exc
        results = response.get("images", []) if isinstance(response, dict) else []
        if not results:
            raise RuntimeError("ComfyUI 图像生成失败：响应中没有生成图片")
        images: list[DerivativeImage] = []
        prompt_id = str(response.get("prompt_id", "")) if isinstance(response, dict) else ""
        for index, item in enumerate(results[:count]):
            image_bytes = _image_bytes_from_response_item(item, self.image_downloader)
            item_seed = seed + index
            digest = hashlib.sha1(image_bytes + f":{item_seed}:{prompt_id}".encode("utf-8")).hexdigest()[:12]
            path = self.output_dir / f"comfyui_derivative_{digest}.png"
            path.write_bytes(image_bytes)
            images.append(
                DerivativeImage(
                    image_id=f"comfyui-{digest}",
                    local_image_path=str(path),
                    provider=self.provider_name,
                    prompt=str(item.get("prompt") or prompt) if isinstance(item, dict) else prompt,
                    negative_prompt=negative_prompt,
                    seed=item_seed,
                    source_sample_id=str(style_constraints.get("source_sample_id", "")),
                    retained_features=_tuple_from_constraint(style_constraints, "retained_features"),
                    changed_features=_tuple_from_constraint(style_constraints, "changed_features"),
                    risk_notes=("生成图需二次 VLM 解析与审核",),
                    generated_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
        return tuple(images)

    def _workflow(self, prompt: str, negative_prompt: str, seed: int, reference_image: str, style_constraints: dict[str, str]) -> dict[str, object]:
        workflow = _load_comfyui_workflow(self.workflow_path)
        return _inject_comfyui_workflow_inputs(workflow, prompt, negative_prompt, seed, reference_image, style_constraints)


class ImageGenerationProviderFactory:
    @staticmethod
    def create(output_dir: Path | str, transport=None) -> ImageGenerationProvider:
        provider = os.getenv("IMAGE_GENERATION_PROVIDER", "").strip().lower()
        if provider in {"", "none", "disabled"}:
            return MissingImageGenerationProvider()
        if provider == "mock":
            return MockImageGenerationProvider(output_dir)
        if provider == "cloud":
            api_key = os.getenv("IMAGE_GENERATION_API_KEY", "")
            if not api_key:
                return MissingImageGenerationProvider()
            return CloudImageGenerationProvider(
                output_dir=output_dir,
                api_key=api_key,
                model=os.getenv("IMAGE_GENERATION_MODEL", "wanx2.1-t2i-plus"),
                base_url=os.getenv("IMAGE_GENERATION_BASE_URL", "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"),
                transport=transport,
            )
        if provider == "comfyui":
            return ComfyUIImageGenerationProvider(
                output_dir=output_dir,
                base_url=os.getenv("COMFYUI_BASE_URL", os.getenv("IMAGE_GENERATION_BASE_URL", "http://127.0.0.1:8188")),
                workflow_path=os.getenv("COMFYUI_WORKFLOW_PATH", ""),
                transport=transport,
            )
        if provider in {"dashscope", "wanx"}:
            api_key = os.getenv("IMAGE_GENERATION_API_KEY", "")
            api_key_source = "IMAGE_GENERATION_API_KEY"
            if not api_key:
                api_key = os.getenv("QWEN_API_KEY", "")
                api_key_source = "QWEN_API_KEY"
            if not api_key:
                return MissingImageGenerationProvider()
            return DashScopeImageGenerationProvider(
                output_dir=output_dir,
                api_key=api_key,
                api_key_source=api_key_source,
                model=os.getenv("IMAGE_GENERATION_MODEL", "wan2.6-image"),
                sdk_generate=transport,
            )
        return MissingImageGenerationProvider()


def _tuple_from_constraint(style_constraints: dict[str, str], key: str) -> tuple[str, ...]:
    value = style_constraints.get(key, "")
    if isinstance(value, tuple):
        return value
    return tuple(part.strip() for part in str(value).split("；") if part.strip())


def _dashscope_sdk_available() -> bool:
    try:
        return importlib.util.find_spec("dashscope.aigc.image_generation") is not None
    except ModuleNotFoundError:
        return False


def _placeholder_png_bytes() -> bytes:
    # 1x1 transparent PNG; mock provider stores records only and never claims real generated content.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _image_bytes_from_response_item(item: object, image_downloader=None) -> bytes:
    if not isinstance(item, dict):
        raise ValueError("图像生成响应 item 必须是 dict")
    encoded = item.get("b64_json") or item.get("image_base64")
    if isinstance(encoded, str) and encoded:
        return base64.b64decode(encoded)
    local_path = item.get("local_image_path")
    if isinstance(local_path, str) and Path(local_path).exists():
        return Path(local_path).read_bytes()
    url = item.get("url")
    if isinstance(url, str) and url.startswith("data:image"):
        _, encoded = url.split(",", 1)
        return base64.b64decode(encoded)
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return (image_downloader or _download_image)(url)
    raise ValueError("图像生成响应缺少 b64_json/image_base64/local_image_path/url")


def _download_image(url: str) -> bytes:
    with request.urlopen(url, timeout=90, context=_https_context()) as response:
        return response.read()


def _https_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


DASHSCOPE_HTTP_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


def _normalize_dashscope_http_base_url() -> None:
    import dashscope

    os.environ["DASHSCOPE_HTTP_BASE_URL"] = DASHSCOPE_HTTP_BASE_URL
    dashscope.base_http_api_url = DASHSCOPE_HTTP_BASE_URL


def _dashscope_sdk_generate(
    *, model: str, api_key: str, reference_image: str, prompt: str,
    negative_prompt: str, count: int, seed: int, style_constraints: dict[str, str],
) -> dict[str, object]:
    try:
        from dashscope.aigc.image_generation import ImageGeneration
        from dashscope.api_entities.dashscope_response import Message, Role
    except ImportError as exc:
        raise RuntimeError("缺少 dashscope Python SDK，请安装 requirements.txt") from exc
    _normalize_dashscope_http_base_url()

    if not reference_image.strip():
        raise ValueError("参考图路径或 URL 不能为空")
    candidate = Path(reference_image).expanduser()
    if candidate.is_file():
        reference = str(candidate.resolve())
    elif reference_image.startswith(("http://", "https://", "oss://")):
        reference = reference_image
    else:
        raise ValueError(f"参考图不存在：{reference_image}")
    instruction = f"{prompt}\n必须规避：{negative_prompt}"
    message = Message(role=Role.USER, content=[{"text": instruction}, {"image": reference}])
    response = ImageGeneration.call(model=model, api_key=api_key, messages=[message], n=count, seed=seed)
    if int(getattr(response, "status_code", 500)) != 200:
        raise RuntimeError(f"{getattr(response, 'code', 'unknown')}：{getattr(response, 'message', '调用失败')}")
    return {"images": list(_dashscope_images_from_response(response, prompt))}


def _dashscope_images_from_response(response: object, prompt: str) -> tuple[dict[str, object], ...]:
    output = _object_get(response, "output", {}) or {}
    task_id = str(_object_get(output, "task_id", "") or "")
    images: list[dict[str, object]] = []
    for key in ("results", "images"):
        for item in _object_get(output, key, ()) or ():
            image = _image_url_from_dashscope_item(item)
            if image:
                record: dict[str, object] = {"url": image, "prompt": prompt}
                if task_id:
                    record["task_id"] = task_id
                images.append(record)
    for choice in _object_get(output, "choices", ()) or ():
        message_value = _object_get(choice, "message", {}) or {}
        content = _object_get(message_value, "content", ()) or ()
        if isinstance(content, dict):
            content = (content,)
        for item in content:
            image = _image_url_from_dashscope_item(item)
            if image:
                record = {"url": image, "prompt": prompt}
                if task_id:
                    record["task_id"] = task_id
                images.append(record)
    return tuple(images)


def _load_comfyui_workflow(workflow_path: str) -> dict[str, object]:
    if not workflow_path.strip():
        return {}
    path = Path(workflow_path).expanduser()
    if not path.is_file():
        raise ValueError(f"ComfyUI workflow 不存在：{workflow_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _inject_comfyui_workflow_inputs(
    workflow: dict[str, object],
    prompt: str,
    negative_prompt: str,
    seed: int,
    reference_image: str,
    style_constraints: dict[str, str],
) -> dict[str, object]:
    injected = json.loads(json.dumps(workflow, ensure_ascii=False))
    prompt_done = False
    negative_done = False
    for node in injected.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type", ""))
        if "seed" in inputs:
            inputs["seed"] = seed
        if "noise_seed" in inputs:
            inputs["noise_seed"] = seed
        if "image" in inputs and reference_image:
            inputs["image"] = reference_image
        if "text" in inputs and "CLIPTextEncode" in class_type:
            if not prompt_done:
                inputs["text"] = prompt
                prompt_done = True
            elif not negative_done:
                inputs["text"] = negative_prompt
                negative_done = True
    injected.setdefault("_puzzleops", {})
    if isinstance(injected["_puzzleops"], dict):
        injected["_puzzleops"].update(
            {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "seed": seed,
                "reference_image": reference_image,
                "style_constraints": style_constraints,
            }
        )
    return injected


def _comfyui_transport(payload: dict[str, object], base_url: str) -> dict[str, object]:
    client_id = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    submit_payload = {"prompt": payload.get("workflow", {}), "client_id": client_id}
    prompt_response = _json_request(f"{base_url.rstrip('/')}/prompt", submit_payload)
    prompt_id = str(prompt_response.get("prompt_id", ""))
    if not prompt_id:
        raise RuntimeError("ComfyUI 未返回 prompt_id")
    history = _json_get(f"{base_url.rstrip('/')}/history/{prompt_id}")
    images: list[dict[str, object]] = []
    for item in _comfyui_history_images(history, prompt_id):
        images.append({"url": f"{base_url.rstrip('/')}/view?filename={item['filename']}&subfolder={item.get('subfolder', '')}&type={item.get('type', 'output')}", "prompt": payload.get("prompt", "")})
    return {"prompt_id": prompt_id, "images": images}


def _comfyui_history_images(history: dict[str, object], prompt_id: str) -> tuple[dict[str, str], ...]:
    node = history.get(prompt_id, history)
    outputs = node.get("outputs", {}) if isinstance(node, dict) else {}
    images: list[dict[str, str]] = []
    if isinstance(outputs, dict):
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            for image in output.get("images", ()) or ():
                if isinstance(image, dict) and image.get("filename"):
                    images.append(
                        {
                            "filename": str(image.get("filename", "")),
                            "subfolder": str(image.get("subfolder", "")),
                            "type": str(image.get("type", "output")),
                        }
                    )
    return tuple(images)


def _image_url_from_dashscope_item(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    image = item.get("image") or item.get("url") or item.get("image_url")
    return str(image) if image else ""


def _object_get(value: object, key: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(key, default)
    if hasattr(value, "get"):
        try:
            return value.get(key, default)  # type: ignore[attr-defined]
        except TypeError:
            pass
    return getattr(value, key, default)


def _cloud_transport(payload: dict[str, object], api_key: str, base_url: str) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        base_url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with request.urlopen(req, timeout=90, context=_https_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_request(url: str, payload: dict[str, object]) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=90, context=_https_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_get(url: str) -> dict[str, object]:
    with request.urlopen(url, timeout=90, context=_https_context()) as response:
        return json.loads(response.read().decode("utf-8"))
