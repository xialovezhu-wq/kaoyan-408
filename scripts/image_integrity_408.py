"""Small, dependency-light image signature and integrity checks for 408 input."""

from __future__ import annotations

import io


class ImageIntegrityError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def validate_image_bytes(data: bytes) -> tuple[str, str]:
    """Return ``(format, mime)`` for one complete PNG/JPEG/WebP object.

    The current-question writer intentionally validates the complete byte
    payload before staging it.  Pillow is available in the test/runtime image,
    but the signatures below keep rejection deterministic when it is not.
    """

    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ImageIntegrityError("signature_unsupported", "image bytes required")
    raw = bytes(data)
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        fmt, mime = "png", "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        fmt, mime = "jpeg", "image/jpeg"
    elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        fmt, mime = "webp", "image/webp"
    else:
        raise ImageIntegrityError("signature_unsupported", "unsupported image signature")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
    except ImportError as exc:  # pragma: no cover - deployment fallback
        raise ImageIntegrityError("decoder_unavailable", "image decoder unavailable") from exc
    except Exception as exc:
        raise ImageIntegrityError("decode_failed", "image bytes are not decodable") from exc
    return fmt, mime
