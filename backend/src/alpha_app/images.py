"""images.py — Image processing middleware.

Resizes and compresses images in content blocks before they reach Claude.
Any image exceeding 1 megapixel is scaled down proportionally. PNG images
(and other lossless formats) are always JPEG-compressed at quality 85 for
bandwidth savings — same token cost per the Mind's Eye experiment.

Only base64-sourced image blocks are touched; URL-based images pass through.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

_MAX_PIXELS = 1_000_000  # 1 megapixel threshold
_JPEG_QUALITY = 85

# Media types that should be JPEG-compressed even when under 1MP
_LOSSLESS_MEDIA_TYPES = frozenset(
    {"image/png", "image/gif", "image/webp", "image/bmp", "image/tiff", "image/heic", "image/heif"}
)


def process_image_block(block: dict) -> dict:
    """Process a single image content block.

    Rules:
      - Images over 1MP: scale down proportionally, then JPEG-compress.
      - PNG/lossless images under 1MP: JPEG-compress (no resize).
      - JPEG images already under 1MP: pass through unchanged.
      - Non-base64 sources (URL): pass through unchanged.

    Returns a new block dict with the processed image, or the original
    block unchanged if no processing was needed or possible.
    """
    if block.get("type") != "image":
        return block

    source = block.get("source", {})
    if source.get("type") != "base64":
        return block  # URL-based images — leave untouched

    media_type = source.get("media_type", "").lower()
    raw_data = source.get("data", "")

    try:
        image_bytes = base64.standard_b64decode(raw_data)
    except Exception:
        return block  # Corrupt base64 — pass through unchanged

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()  # Ensure the image data is fully read
        # Apply EXIF orientation — phone cameras store rotation as metadata,
        # not as actual pixel orientation. Without this, portrait photos
        # from phones render sideways.
        img = ImageOps.exif_transpose(img)
    except Exception:
        return block  # Unreadable image — pass through unchanged

    width, height = img.size
    pixels = width * height
    is_lossless = media_type in _LOSSLESS_MEDIA_TYPES

    # Nothing to do: already JPEG and already under 1MP
    if pixels <= _MAX_PIXELS and not is_lossless:
        return block

    # Resize if over 1MP
    if pixels > _MAX_PIXELS:
        scale = (_MAX_PIXELS / pixels) ** 0.5
        new_width = max(1, int(width * scale))
        new_height = max(1, int(height * scale))
        img = img.resize((new_width, new_height), Image.LANCZOS)

    # Convert to RGB for JPEG output (flatten any transparency onto white)
    if img.mode != "RGB":
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    compressed = base64.standard_b64encode(buf.getvalue()).decode("ascii")

    return {
        **block,
        "source": {
            **source,
            "media_type": "image/jpeg",
            "data": compressed,
        },
    }


def process_image_blocks(content: list[dict]) -> list[dict]:
    """Process all image blocks in a content list.

    Applies resize and compression to each image block in-place.
    Text blocks and other block types are returned unchanged.
    """
    return [process_image_block(block) for block in content]
