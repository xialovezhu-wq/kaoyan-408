"""Synthetic coverage for the canonical 408 image-integrity contract."""

from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path

from PIL import Image


_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "image_integrity_408.py"
_SPEC = importlib.util.spec_from_file_location("image_integrity_408", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - test setup failure
    raise RuntimeError(f"cannot load {_MODULE_PATH}")
_INTEGRITY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_INTEGRITY)


class ImageIntegrity408Tests(unittest.TestCase):
    @staticmethod
    def _encode(image: Image.Image, image_format: str, **options: object) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format=image_format, **options)
        return buffer.getvalue()

    @staticmethod
    def _webp_chunk_types(data: bytes) -> list[bytes]:
        types: list[bytes] = []
        offset = 12
        while offset < len(data):
            chunk_length = int.from_bytes(data[offset + 4 : offset + 8], "little")
            types.append(data[offset : offset + 4])
            offset += 8 + chunk_length + (chunk_length & 1)
        return types

    def test_algorithm_constants_are_stable(self) -> None:
        self.assertEqual(
            _INTEGRITY.IMAGE_INTEGRITY_ALGORITHM,
            "png-jpeg-webp-full-decode-v1",
        )
        self.assertEqual(
            _INTEGRITY.SUPPORTED_IMAGE_FORMATS,
            {
                "png": "image/png",
                "jpeg": "image/jpeg",
                "webp": "image/webp",
            },
        )

    def test_valid_png_jpeg_and_webp(self) -> None:
        cases = (
            (
                "png",
                self._encode(Image.new("RGBA", (9, 7), (12, 34, 56, 128)), "PNG"),
                "image/png",
            ),
            (
                "jpeg",
                self._encode(
                    Image.new("RGB", (9, 7), (12, 34, 56)),
                    "JPEG",
                    quality=93,
                ),
                "image/jpeg",
            ),
            (
                "webp",
                self._encode(
                    Image.new("RGB", (9, 7), (12, 34, 56)),
                    "WEBP",
                    lossless=True,
                ),
                "image/webp",
            ),
        )
        for expected_format, data, expected_mime in cases:
            with self.subTest(expected_format=expected_format):
                self.assertEqual(
                    _INTEGRITY.validate_image_bytes(data),
                    (expected_format, expected_mime),
                )

    def test_truncated_png_jpeg_and_webp_are_rejected(self) -> None:
        cases = (
            (
                "png",
                self._encode(Image.new("RGBA", (9, 7), (12, 34, 56, 128)), "PNG"),
            ),
            (
                "jpeg",
                self._encode(Image.new("RGB", (9, 7), (12, 34, 56)), "JPEG"),
            ),
            (
                "webp",
                self._encode(
                    Image.new("RGB", (9, 7), (12, 34, 56)),
                    "WEBP",
                    lossless=True,
                ),
            ),
        )
        for expected_format, data in cases:
            with self.subTest(expected_format=expected_format):
                with self.assertRaises(_INTEGRITY.ImageIntegrityError):
                    _INTEGRITY.validate_image_bytes(data[:-1])

    def test_alpha_webp_is_valid(self) -> None:
        data = self._encode(
            Image.new("RGBA", (16, 16), (255, 0, 0, 96)),
            "WEBP",
            lossless=False,
            quality=75,
        )
        self.assertIn(b"ALPH", self._webp_chunk_types(data))
        self.assertEqual(
            _INTEGRITY.validate_image_bytes(data),
            ("webp", "image/webp"),
        )

    def test_webp_metadata_chunks_are_valid(self) -> None:
        data = self._encode(
            Image.new("RGB", (9, 7), (12, 34, 56)),
            "WEBP",
            icc_profile=b"synthetic-icc-profile",
            exif=b"Exif\x00\x00synthetic-exif",
            xmp=b"<xmpmeta>synthetic-xmp</xmpmeta>",
        )
        chunk_types = self._webp_chunk_types(data)
        for metadata_type in (b"ICCP", b"EXIF", b"XMP "):
            with self.subTest(metadata_type=metadata_type):
                self.assertIn(metadata_type, chunk_types)
        self.assertEqual(
            _INTEGRITY.validate_image_bytes(data),
            ("webp", "image/webp"),
        )

    def test_animated_webp_decodes_every_frame(self) -> None:
        frames = [
            Image.new("RGBA", (9, 7), (255, 0, 0, 255)),
            Image.new("RGBA", (9, 7), (0, 255, 0, 128)),
        ]
        data = self._encode(
            frames[0],
            "WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
        chunk_types = self._webp_chunk_types(data)
        self.assertIn(b"ANIM", chunk_types)
        self.assertGreaterEqual(chunk_types.count(b"ANMF"), 2)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.n_frames, 2)
        self.assertEqual(
            _INTEGRITY.validate_image_bytes(data),
            ("webp", "image/webp"),
        )

    def test_non_bytes_are_rejected_fail_closed(self) -> None:
        with self.assertRaises(_INTEGRITY.ImageIntegrityError) as context:
            _INTEGRITY.validate_image_bytes(bytearray(b"not-an-image"))
        self.assertEqual(context.exception.code, "bytes_invalid")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
