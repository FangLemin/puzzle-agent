from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib


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


def _tuple_from_constraint(style_constraints: dict[str, str], key: str) -> tuple[str, ...]:
    value = style_constraints.get(key, "")
    if isinstance(value, tuple):
        return value
    return tuple(part.strip() for part in str(value).split("；") if part.strip())


def _placeholder_png_bytes() -> bytes:
    # 1x1 transparent PNG; mock provider stores records only and never claims real generated content.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

