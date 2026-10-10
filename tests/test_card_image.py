import hashlib
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.card_image import trim_card_bottom_padding


def make_screenshot(path, *, background="#000000", surface="#ffe4f2", height=720, card_height=420):
    image = Image.new("RGB", (600, height), background)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, 599, card_height - 1), radius=30, fill=surface,
                           outline="#ffc8e4", width=3)
    # A black image inside the card must not be mistaken for outside padding.
    draw.rectangle((30, 170, 569, card_height - 35), fill="#000000")
    for y in range(60, 150, 7):
        draw.line((40, y, 480, y), fill="#fb7299", width=2)
    image.save(path, quality=95)
    return image


@pytest.mark.parametrize("extension", ["png", "jpg"])
@pytest.mark.parametrize("background", ["#000000", "#123456", "#FFFFFF"])
@pytest.mark.parametrize("surface", ["#ffe4f2", "#1e2029"])
def test_only_bottom_padding_is_removed_and_source_pixels_are_preserved(tmp_path, extension, background, surface):
    source = tmp_path / ("screenshot." + extension)
    make_screenshot(source, background=background, surface=surface)
    before = source.read_bytes()
    result = trim_card_bottom_padding(str(source), background, surface)
    assert result != str(source)
    assert source.read_bytes() == before
    with Image.open(result) as cropped, Image.open(source) as original:
        assert cropped.format == "PNG"
        assert cropped.width == 600
        assert 420 <= cropped.height <= 428  # JPEG ringing + outline safety pixels
        assert cropped.tobytes() == original.crop((0, 0, 600, cropped.height)).tobytes()
        assert cropped.getpixel((300, 220)) == original.getpixel((300, 220))
        assert cropped.getpixel((0, 0)) == original.getpixel((0, 0))


@pytest.mark.parametrize("extension", ["png", "jpg"])
@pytest.mark.parametrize("card_height", [420, 1600])
def test_already_tight_short_and_long_cards_are_unchanged(tmp_path, extension, card_height):
    source = tmp_path / ("tight." + extension)
    make_screenshot(source, height=card_height, card_height=card_height)
    assert trim_card_bottom_padding(str(source), "#000000", "#ffe4f2") == str(source)
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("background,surface", [
    ("#FFFFFF", "#FFFFFF"), ("#202129", "#1e2029"), ("#ffdcee", "#ffdcee"),
    ("#112233", "#1e2029"),
])
def test_similar_surface_and_background_are_not_cropped(tmp_path, background, surface):
    source = tmp_path / "similar.png"
    make_screenshot(source, background=background, surface=surface)
    assert trim_card_bottom_padding(str(source), background, surface) == str(source)


def test_entirely_blank_output_is_not_cropped(tmp_path):
    source = tmp_path / "blank.png"
    Image.new("RGB", (600, 720), "black").save(source)
    assert trim_card_bottom_padding(str(source), "#000000", "#ffffff") == str(source)


def test_short_tail_does_not_create_an_extra_file(tmp_path):
    source = tmp_path / "short_tail.png"
    make_screenshot(source, height=425)
    assert trim_card_bottom_padding(str(source), "#000000", "#ffe4f2") == str(source)


def test_near_background_noise_is_tolerated_without_touching_card_pixels(tmp_path):
    source = tmp_path / "noisy_tail.png"
    image = make_screenshot(source)
    draw = ImageDraw.Draw(image)
    for y in range(420, 720):
        draw.line((0, y, 599, y), fill=(y % 10, y % 7, y % 11))
    image.save(source)
    result = trim_card_bottom_padding(str(source), "#000000", "#ffe4f2")
    with Image.open(result) as cropped:
        assert cropped.size == (600, 422)
        assert cropped.tobytes() == image.crop((0, 0, 600, 422)).tobytes()


def test_non_background_content_in_bottom_row_is_never_removed(tmp_path):
    source = tmp_path / "bottom_content.png"
    image = make_screenshot(source)
    image.putpixel((300, 719), (255, 255, 255))
    image.save(source)
    assert trim_card_bottom_padding(str(source), "#000000", "#ffe4f2") == str(source)


def test_transparent_assets_are_untouched(tmp_path):
    source = tmp_path / "asset.png"
    Image.new("RGBA", (600, 720), (0, 0, 0, 0)).save(source)
    assert trim_card_bottom_padding(str(source), "#000000", "#ffffff") == str(source)


def test_format_is_detected_from_content_not_file_suffix(tmp_path):
    source = tmp_path / "io_temp_img.jpg"
    image = make_screenshot(tmp_path / "source.png")
    image.save(source, format="PNG")
    result = trim_card_bottom_padding(str(source), "#000000", "#ffe4f2")
    with Image.open(result) as cropped:
        assert cropped.size == (600, 422)


def test_invalid_colors_do_not_trigger_processing(tmp_path):
    source = tmp_path / "screenshot.png"
    make_screenshot(source)
    assert trim_card_bottom_padding(str(source), "red", "#ffffff") == str(source)


def test_failed_save_removes_partial_output_and_preserves_source(tmp_path, monkeypatch):
    source = tmp_path / "screenshot.png"
    make_screenshot(source)
    before = hashlib.sha256(source.read_bytes()).digest()

    def failed_save(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Image.Image, "save", failed_save)
    with pytest.raises(OSError, match="disk full"):
        trim_card_bottom_padding(str(source), "#000000", "#ffe4f2")
    assert list(tmp_path.iterdir()) == [source]
    assert hashlib.sha256(source.read_bytes()).digest() == before
