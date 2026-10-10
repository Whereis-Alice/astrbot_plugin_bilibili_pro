"""Non-destructive fallback for renderers that keep a minimum viewport height."""

import os
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageColor


def trim_card_bottom_padding(
    image_path: str,
    background_color: str,
    card_surface_color: str,
) -> str:
    """Remove only trailing background rows, keeping the full card width.

    JPEG background pixels may deviate slightly from the CSS color. A small
    tolerance handles this without treating the dark skin as black padding.
    If the card and outside background have similar colors, leave the image
    alone: pixel-based trimming cannot reliably locate that card's boundary.

    Save a separate lossless PNG beside the renderer's temporary output. Never
    overwrite the source, re-encode its content as JPEG, or trim inner images.
    File/image errors propagate so the caller can fall back to the source.
    """
    if not all(
        re.fullmatch(r"#[0-9a-fA-F]{6}", color)
        for color in (background_color, card_surface_color)
    ):
        return image_path

    background = ImageColor.getrgb(background_color)
    surface = ImageColor.getrgb(card_surface_color)
    if max(abs(a - b) for a, b in zip(background, surface)) <= 24:
        return image_path

    with Image.open(image_path) as source:
        # Only our non-transparent, single-frame screenshots are supported.
        if source.mode != "RGB" or getattr(source, "n_frames", 1) != 1:
            return image_path
        width, height = source.size
        if width < 64 or height < 64:
            return image_path
        tolerance = 12
        if any(
            max(abs(a - b) for a, b in zip(source.getpixel(point), background)) > tolerance
            for point in ((0, 0), (width - 1, 0))
        ):
            return image_path

        # Scan from the bottom in small strips. Long cards need no full-page
        # difference/mask copies, and scanning stops at the first content row.
        bottom = None
        lookup = [0 if value <= tolerance else 255 for value in range(256)]
        for end in range(height, 0, -128):
            start = max(0, end - 128)
            strip = source.crop((0, start, width, end))
            difference = ImageChops.difference(strip, Image.new("RGB", strip.size, background))
            red, green, blue = difference.split()
            distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            bounds = distance.point(lookup).getbbox()
            if bounds is not None:
                # Preserve two pixels for anti-aliasing at the bottom outline.
                bottom = min(height, start + bounds[3] + 2)
                break
        if bottom is None:
            return image_path
        if bottom < 64 or height - bottom < 8:
            return image_path

        fd, output = tempfile.mkstemp(
            prefix="bilibili_pro_card_", suffix=".png", dir=Path(image_path).resolve().parent
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                source.crop((0, 0, width, bottom)).save(stream, format="PNG")
        except BaseException:
            Path(output).unlink(missing_ok=True)
            raise
        return output
