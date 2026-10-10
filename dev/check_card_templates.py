"""Render previews and verify the frame switch with local Chromium."""

import argparse
import base64
import io
import sys
from pathlib import Path

from jinja2 import Template
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.constant import CARD_TEMPLATES, get_template_path
from dev.mock_data import MockDataGenerator, create_qrcode_sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-previews", action="store_true")
    parser.add_argument("--style", choices=list(CARD_TEMPLATES))
    args = parser.parse_args()
    names = {"template_1": "card_template1", "template_2": "card_classic"}
    avatar = "data:image/png;base64," + base64.b64encode((ROOT / "assets/Astrbot.png").read_bytes()).decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for style in ([args.style] if args.style else CARD_TEMPLATES):
            data = MockDataGenerator.video_dynamic(user_index=0)
            data["pendant"] = ""
            for border in (False, True):
                data["show_border"] = border
                width = (700 if style == "template_1" else 630) if border else (680 if style == "template_1" else 600)
                page = browser.new_page(viewport={"width": width, "height": 1}, device_scale_factor=2)
                page.set_content(Template(Path(get_template_path(style)).read_text(encoding="utf-8")).render(**data))
                page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)", timeout=30000)
                page.evaluate("document.fonts.ready")
                card = page.locator(".card, .dynamic-card").first
                state = card.evaluate("""el => {
                    const s = getComputedStyle(el), b = getComputedStyle(document.body), r = el.getBoundingClientRect();
                    return {x:r.x, y:r.y, width:r.width, height:r.height, border:s.borderTopWidth, outline:s.outlineWidth,
                            padding:b.paddingTop, background:b.backgroundColor};
                }""")
                assert state["width"] > 400 and state["height"] > 150, state
                if border:
                    assert state["x"] > 0 and (state["border"] != "0px" or state["outline"] != "0px"), (style, state)
                else:
                    assert state["x"] == 0 and state["y"] == 0 and state["border"] == "0px", (style, state)
                    assert state["padding"] == "0px" and state["background"] == "rgba(0, 0, 0, 0)", (style, state)
                    assert state["width"] == width, (style, state)
                    screenshot = page.screenshot(full_page=True, omit_background=True)
                    with Image.open(io.BytesIO(screenshot)) as bitmap:
                        assert bitmap.width == width * 2, (style, bitmap.size)
                        assert abs(bitmap.height - state["height"] * 2) <= 2, (style, bitmap.size, state)
                        assert any(low < high for low, high in bitmap.convert("RGB").getextrema()), style
                    if args.update_previews:
                        name = names.get(style, "card_" + style)
                        (ROOT / "assets/preview" / (name + ".png")).write_bytes(screenshot)
                print(f"PASS {style} frame={border}: {state['width']}x{state['height']}", flush=True)
                page.close()
        if args.style:
            browser.close()
            return
        data.update(name="爱丽丝", avatar=avatar, type="LIVE_ON", title="周末直播：游戏与创作",
                    text="订阅的 UP 主开播了！<br>分区：游戏 · 人气：8.6万", show_border=False,
                    url="https://live.bilibili.com/123456", qrcode=create_qrcode_sync("https://live.bilibili.com/123456"))
        page = browser.new_page(viewport={"width":600,"height":1}, device_scale_factor=2)
        page.set_content(Template(Path(get_template_path("bilibili")).read_text(encoding="utf-8")).render(**data))
        page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)", timeout=30000)
        if args.update_previews:
            page.screenshot(full_page=True, omit_background=True, path=str(ROOT / "assets/preview/live_bilibili.png"))
        print("PASS live preview", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
