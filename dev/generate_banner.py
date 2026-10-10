"""Regenerate the original geometric card banner with Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw


def generate_banner() -> None:
    width, height = 2400, 554
    image = Image.new("RGB", (width, height), "#fff2f7")
    draw = ImageDraw.Draw(image)
    draw.polygon([(0, 0), (1480, 0), (910, height), (0, height)], fill="#dbf3fc")
    draw.polygon([(0, 0), (680, 0), (1120, height), (0, height)], fill="#b9e8f8")
    draw.polygon([(1530, 0), (width, 0), (width, height), (2080, height)], fill="#ffd5e5")
    draw.polygon([(1930, 0), (width, 0), (width, 390)], fill="#ffafcc")
    draw.line([(0, 440), (800, 440), (1230, 80), (1760, 80)], fill="#ffffff", width=5)
    draw.line([(1700, 510), (2090, 190), (width, 190)], fill="#ffffff", width=5)
    for x, y in [(400, 120), (850, 220), (1860, 335), (2190, 100)]:
        draw.line([(x - 15, y), (x + 15, y)], fill="#ffffff", width=4)
        draw.line([(x, y - 15), (x, y + 15)], fill="#ffffff", width=4)
    draw.regular_polygon((1290, 270, 100), n_sides=3, rotation=270, fill="#ffffff")
    output = Path(__file__).resolve().parent.parent / "assets" / "banner.png"
    image.save(output, optimize=True)
    print(f"Generated {output.name}: {width}x{height}")


if __name__ == "__main__":
    generate_banner()
