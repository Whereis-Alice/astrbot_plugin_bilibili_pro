"""Check card geometry and crop fallback with both tight and legacy 720px viewports."""

import argparse
import base64
import io
import sys
import tempfile
from pathlib import Path

from jinja2 import Template
from PIL import Image, ImageColor
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.constant import CARD_TEMPLATES, get_template_path
from core.card_image import trim_card_bottom_padding
from dev.mock_data import MockDataGenerator, create_qrcode_sync


def data_uri(bitmap):
    buffer = io.BytesIO()
    bitmap.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def check_case(browser, output_dir, style, data, border, outline, viewport_height, label):
    data = dict(data, show_border=border, show_outline=outline)
    width = (700 if style == "template_1" else 630) if border else (680 if style == "template_1" else 600)
    scale = 1.8  # The production render service's "ultra" scale.
    page = browser.new_page(viewport={"width": width, "height": viewport_height}, device_scale_factor=scale)
    try:
        page.set_content(Template(Path(get_template_path(style)).read_text(encoding="utf-8")).render(**data))
        page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)", timeout=30000)
        page.evaluate("document.fonts.ready")
        assert "height=1" in page.locator('meta[name="viewport"]').get_attribute("content")
        state = page.locator(".card, .dynamic-card").first.evaluate("""el => {
            const s = getComputedStyle(el), b = getComputedStyle(document.body), r = el.getBoundingClientRect();
            return {x:r.x, y:r.y, width:r.width, height:r.height, border:s.borderTopWidth,
                    padding:b.paddingTop, background:b.backgroundColor};
        }""")
        assert state["width"] > 400 and state["height"] > 150, state
        assert (state["border"] != "0px") is outline, (style, outline, state)
        background = ImageColor.getrgb(data["corner_background"])
        surface = ImageColor.getrgb(CARD_TEMPLATES[style]["surface_color"])
        low_contrast = max(abs(a - b) for a, b in zip(background, surface)) <= 24
        if border:
            assert state["x"] > 0, (style, state)
        else:
            assert state["x"] == 0 and state["y"] == 0, (style, state)
            assert state["padding"] == "0px" and state["width"] == width, (style, state)
            assert state["background"] == f"rgb{background}", (style, state, background)

        name = f"{style}_{label}_{int(border)}_{int(outline)}_{viewport_height}"
        final_png = None
        for extension in ("png", "jpeg"):
            path = output_dir / (name + "." + extension)
            options = {"quality": 95} if extension == "jpeg" else {}
            page.screenshot(full_page=True, type=extension, path=str(path), **options)
            result = str(path) if border else trim_card_bottom_padding(
                str(path), data["corner_background"], CARD_TEMPLATES[style]["surface_color"]
            )
            with Image.open(result) as bitmap, Image.open(path) as raw:
                assert bitmap.width == round(width * scale), (style, bitmap.size)
                if border or low_contrast:
                    assert result == str(path), (style, label, "spacing/low-contrast guard")
                else:
                    assert abs(bitmap.height - state["height"] * scale) <= 8, (style, bitmap.size, state)
                    assert bitmap.tobytes() == raw.crop((0, 0, bitmap.width, bitmap.height)).tobytes()
                    for corner in ((1, 1), (bitmap.width - 2, 1)):
                        assert max(abs(a - b) for a, b in zip(bitmap.getpixel(corner), background)) < 10, (style, extension, corner)
                    if viewport_height == 720 and state["height"] < 710:
                        assert raw.height == round(720 * scale), (style, raw.size)
                        assert bitmap.height < raw.height, (style, label, "legacy height not trimmed")
                assert any(low < high for low, high in bitmap.convert("RGB").getextrema()), style
            if extension == "png":
                final_png = Path(result).read_bytes()
        print(f"PASS {style} {label} viewport={viewport_height} spacing={border} outline={outline}: "
              f"{state['width']}x{state['height']}", flush=True)
        return final_png
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-previews", action="store_true")
    parser.add_argument("--style", choices=list(CARD_TEMPLATES))
    args = parser.parse_args()
    names = {"template_1": "card_template1", "template_2": "card_classic"}
    avatar = "data:image/png;base64," + base64.b64encode((ROOT / "assets/Astrbot.png").read_bytes()).decode()
    with tempfile.TemporaryDirectory(prefix="bilibili_pro_template_qa_") as temp, sync_playwright() as playwright:
        output_dir = Path(temp)
        for style in ([args.style] if args.style else CARD_TEMPLATES):
            # Isolate graphics resources across large screenshot batches.
            browser = playwright.chromium.launch()
            data = MockDataGenerator.video_dynamic(user_index=0)
            # Fully local fixtures: network image failures must not hide layout bugs.
            data.update(avatar=avatar, pendant="", corner_background="#000000",
                        image_urls=[data_uri(Image.new("RGB", (672, 378), "black"))])
            for border, outline in ((False, True), (False, False), (True, True), (True, False)):
                for height in (1, 720):
                    preview = check_case(browser, output_dir, style, data, border, outline, height, "black_video")
                    if args.update_previews and not border and outline and height == 1:
                        name = names.get(style, "card_" + style)
                        (ROOT / "assets/preview" / (name + ".png")).write_bytes(preview)
                short = dict(data, type="LIVE_OFF", title="本场直播结束", text="本场直播时长：1小时", image_urls=[])
                check_case(browser, output_dir, style, short, border, outline, 720, "short_live")
                long = dict(data, type="DYNAMIC_TYPE_WORD", title="长动态", image_urls=[],
                            text="<br>".join(["长动态内容完整保留，不能因为裁剪底部留白而被截断。"] * 45))
                check_case(browser, output_dir, style, long, border, outline, 720, "long_dynamic")
            for color in ("#123456", "#FFFFFF", CARD_TEMPLATES[style]["surface_color"]):
                for outline in (False, True):
                    custom = dict(short, corner_background=color)
                    check_case(browser, output_dir, style, custom, False, outline, 720, color[1:])
            browser.close()
        if not args.style:
            browser = playwright.chromium.launch()
            data.update(name="爱丽丝", avatar=avatar, type="LIVE_ON", title="周末直播：游戏与创作",
                        text="订阅的 UP 主开播了！<br>分区：游戏 · 人气：8.6万", corner_background="#000000",
                        url="https://live.bilibili.com/123456", qrcode=create_qrcode_sync("https://live.bilibili.com/123456"))
            preview = check_case(browser, output_dir, "bilibili", data, False, True, 720, "live_on")
            if args.update_previews:
                (ROOT / "assets/preview/live_bilibili.png").write_bytes(preview)
            browser.close()


if __name__ == "__main__":
    main()
