"""LLM provider protocol, result types and shared helpers.

Every provider implements :class:`LLMProvider`: it receives a system prompt, the user text (transcript,
frame index, student context), a list of labelled images and the target JSON schema, and returns an
:class:`LLMResult` whose ``data`` is a JSON object that *should* validate against ``LessonAnalysis``.
Final validation happens in :mod:`app.pipeline.analysis`; providers use :func:`validation_errors` to retry
once with the pydantic error text when the first answer does not validate.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from app.logging import get_logger
from app.schemas.analysis import LessonAnalysis

log = get_logger("app.providers.llm")

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class LLMError(RuntimeError):
    """The provider failed (network, HTTP error, refusal, malformed or non-validating output)."""


class ProviderNotConfigured(LLMError):
    """The selected provider lacks credentials or an optional dependency; the message says how to fix it."""


class LLMHTTPError(LLMError):
    """An HTTP-level failure with the status code (``None`` for timeouts / connection errors) and the
    server's ``Retry-After`` in seconds when it sent one. Used by the revolver to pick a cooldown."""

    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class FreeLimitsExhausted(LLMError):
    """Every free endpoint of the revolver is cooling down or failed; ``retry_after_sec`` is when the first one
    becomes available again (the worker re-queues the lesson for then)."""

    def __init__(self, message: str, *, retry_after_sec: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


@dataclass(slots=True)
class ImageInput:
    """A keyframe handed to the model: ``label`` is shown right before the image (index + timecode)."""

    path: Path
    label: str


@dataclass(slots=True)
class LLMResult:
    """Provider-independent analysis response."""

    data: dict
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int = 0
    extra: dict = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """Multimodal JSON-producing adapter. ``analyze`` is a coroutine."""

    name: str

    async def analyze(
        self, *, system: str, user_text: str, images: list[ImageInput], json_schema: dict
    ) -> LLMResult: ...


# ---------------------------------------------------------------- shared helpers


def media_type_for(path: Path) -> str:
    """Return the MIME type of an image by extension (``image/jpeg`` by default)."""
    return _MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")


def encode_image_base64(path: Path, *, max_px: int | None = None) -> tuple[str, str]:
    """Read an image and return ``(media_type, base64_without_newlines)``.

    With ``max_px`` the image is downscaled (longest side ≤ ``max_px``) and re-encoded as JPEG — free API tiers
    cap request size and count image tokens by resolution. Smaller images are sent as is.
    """
    path = Path(path)
    if max_px and max_px > 0:
        from PIL import Image

        with Image.open(path) as im:
            if max(im.size) > max_px:
                im = im.convert("RGB")
                im.thumbnail((max_px, max_px))
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=82)
                return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return media_type_for(path), base64.standard_b64encode(path.read_bytes()).decode("ascii")


def extract_json(text: str) -> dict:
    """Parse the model's text as a JSON object, tolerating code fences and leading/trailing prose."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError("model output is not a JSON object") from None
        try:
            obj = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise LLMError("model output is JSON but not an object")
    return obj


def validation_errors(data: dict) -> str | None:
    """Return a compact human-readable pydantic error text if ``data`` is not a valid ``LessonAnalysis``."""
    try:
        LessonAnalysis.model_validate(data)
    except ValidationError as exc:
        lines = []
        for err in exc.errors()[:30]:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            lines.append(f"- {loc}: {err.get('msg')}")
        return "\n".join(lines)
    return None


def correction_request(errors: str) -> str:
    """User turn asking the model to fix a non-validating answer (Russian, as the rest of the prompt)."""
    return (
        "Предыдущий JSON не прошёл валидацию по схеме. Ошибки:\n"
        f"{errors}\n\n"
        "Верни исправленный JSON целиком, строго по схеме, без пояснений и без markdown."
    )


def available_images(images: list[ImageInput]) -> list[ImageInput]:
    """Drop images whose file is missing (logged by label, never by content)."""
    kept: list[ImageInput] = []
    for img in images:
        if Path(img.path).is_file():
            kept.append(img)
        else:
            log.warning("llm_image_missing", label=img.label)
    return kept
