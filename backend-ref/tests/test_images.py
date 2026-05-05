"""Tests for images.py — image processing middleware.

Tests the resize and compress pipeline:
  - Images over 1MP are scaled down proportionally
  - PNG images are JPEG-compressed even when under 1MP
  - JPEG images already under 1MP pass through unchanged
  - Non-image blocks pass through unchanged
  - URL-sourced images pass through unchanged
  - Corrupt base64 passes through unchanged
  - Transparency is flattened to white background
  - Output pixel count stays at or under 1MP
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from alpha_app.images import process_image_block, process_image_blocks, _MAX_PIXELS


# ---------------------------------------------------------------------------
# Helpers to build synthetic content blocks
# ---------------------------------------------------------------------------


def _make_image_block(width: int, height: int, fmt: str = "PNG") -> dict:
    """Create a base64 image content block with a synthetic image."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    data = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    media_type = "image/png" if fmt == "PNG" else "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _decode_block(block: dict) -> Image.Image:
    """Decode a processed image block back to a PIL Image."""
    data = block["source"]["data"]
    return Image.open(io.BytesIO(base64.standard_b64decode(data)))


def _pixel_count(block: dict) -> int:
    img = _decode_block(block)
    return img.width * img.height


# ---------------------------------------------------------------------------
# Tests: pass-through cases
# ---------------------------------------------------------------------------


class TestPassThrough:
    def test_non_image_block_unchanged(self):
        block = {"type": "text", "text": "Hello"}
        result = process_image_block(block)
        assert result is block  # Same object — not copied

    def test_url_sourced_image_unchanged(self):
        block = {
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/img.png"},
        }
        result = process_image_block(block)
        assert result is block

    def test_jpeg_under_1mp_unchanged(self):
        # 500x500 = 250k pixels — well under 1MP, already JPEG
        block = _make_image_block(500, 500, fmt="JPEG")
        result = process_image_block(block)
        assert result is block  # No processing needed

    def test_corrupt_base64_passes_through(self):
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "not-valid-base64!!!"},
        }
        result = process_image_block(block)
        assert result is block

    def test_empty_content_list(self):
        assert process_image_blocks([]) == []

    def test_mixed_blocks_non_images_unchanged(self):
        text = {"type": "text", "text": "hi"}
        image = _make_image_block(200, 200, fmt="JPEG")
        result = process_image_blocks([text, image])
        assert result[0] is text  # Text block is same object


# ---------------------------------------------------------------------------
# Tests: PNG compression (under 1MP)
# ---------------------------------------------------------------------------


class TestPngCompression:
    def test_png_under_1mp_is_converted_to_jpeg(self):
        block = _make_image_block(500, 500, fmt="PNG")
        assert block["source"]["media_type"] == "image/png"

        result = process_image_block(block)

        assert result["source"]["media_type"] == "image/jpeg"

    def test_png_under_1mp_dimensions_preserved(self):
        block = _make_image_block(400, 300, fmt="PNG")
        result = process_image_block(block)
        img = _decode_block(result)
        assert img.width == 400
        assert img.height == 300

    def test_png_output_is_valid_jpeg(self):
        block = _make_image_block(300, 300, fmt="PNG")
        result = process_image_block(block)
        img = _decode_block(result)
        assert img.format == "JPEG"

    def test_png_data_changes(self):
        block = _make_image_block(300, 300, fmt="PNG")
        original_data = block["source"]["data"]
        result = process_image_block(block)
        assert result["source"]["data"] != original_data


# ---------------------------------------------------------------------------
# Tests: resize for images over 1MP
# ---------------------------------------------------------------------------


class TestResize:
    def test_jpeg_over_1mp_is_resized(self):
        # 1500x1000 = 1.5MP — over the threshold
        block = _make_image_block(1500, 1000, fmt="JPEG")
        result = process_image_block(block)
        assert _pixel_count(result) <= _MAX_PIXELS

    def test_jpeg_over_1mp_output_is_jpeg(self):
        block = _make_image_block(1500, 1000, fmt="JPEG")
        result = process_image_block(block)
        assert result["source"]["media_type"] == "image/jpeg"

    def test_png_over_1mp_is_resized_and_compressed(self):
        # 1200x1000 = 1.2MP, PNG — needs both resize and format conversion
        block = _make_image_block(1200, 1000, fmt="PNG")
        result = process_image_block(block)
        assert result["source"]["media_type"] == "image/jpeg"
        assert _pixel_count(result) <= _MAX_PIXELS

    def test_resize_preserves_aspect_ratio(self):
        # 2000x1000 — 2:1 aspect ratio, 2MP
        block = _make_image_block(2000, 1000, fmt="JPEG")
        result = process_image_block(block)
        img = _decode_block(result)
        # Aspect ratio should be approximately 2:1 (within rounding)
        ratio = img.width / img.height
        assert abs(ratio - 2.0) < 0.05

    def test_resized_pixel_count_at_or_under_1mp(self):
        # Very large image: 4000x3000 = 12MP
        block = _make_image_block(4000, 3000, fmt="JPEG")
        result = process_image_block(block)
        assert _pixel_count(result) <= _MAX_PIXELS

    def test_1mp_exact_jpeg_unchanged(self):
        # Exactly 1000x1000 = 1MP, JPEG — at threshold, not over
        block = _make_image_block(1000, 1000, fmt="JPEG")
        result = process_image_block(block)
        assert result is block


# ---------------------------------------------------------------------------
# Tests: RGBA transparency handling
# ---------------------------------------------------------------------------


class TestTransparency:
    def test_rgba_png_converts_to_rgb_jpeg(self):
        img = Image.new("RGBA", (200, 200), color=(100, 150, 200, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data},
        }
        result = process_image_block(block)
        out_img = _decode_block(result)
        assert out_img.mode == "RGB"
        assert result["source"]["media_type"] == "image/jpeg"


# ---------------------------------------------------------------------------
# Tests: process_image_blocks (list-level)
# ---------------------------------------------------------------------------


class TestProcessImageBlocks:
    def test_all_pngs_processed(self):
        blocks = [_make_image_block(300, 300, fmt="PNG") for _ in range(3)]
        results = process_image_blocks(blocks)
        for r in results:
            assert r["source"]["media_type"] == "image/jpeg"

    def test_returns_same_length(self):
        blocks = [
            {"type": "text", "text": "hi"},
            _make_image_block(300, 300, fmt="PNG"),
            {"type": "text", "text": "there"},
        ]
        results = process_image_blocks(blocks)
        assert len(results) == 3
