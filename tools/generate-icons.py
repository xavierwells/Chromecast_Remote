"""Generate the raster PWA icons from CastDeck's brand geometry."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"


def create_icon(size: int) -> None:
    scale = size / 512
    image = Image.new("RGB", (size, size), "#090b0f")
    draw = ImageDraw.Draw(image)

    def box(values):
        return tuple(round(value * scale) for value in values)

    lime = "#d5ff52"
    width = max(4, round(28 * scale))
    draw.rounded_rectangle(box((86, 82, 426, 422)), radius=round(52 * scale), fill="#11151b",
                           outline=lime, width=width)
    draw.arc(box((-12, 368, 140, 520)), 270, 360, fill=lime, width=width)
    draw.arc(box((-52, 286, 222, 560)), 270, 360, fill=lime, width=width)
    draw.arc(box((-94, 204, 304, 602)), 270, 360, fill=lime, width=width)
    draw.ellipse(box((70, 402, 106, 438)), fill=lime)
    image.save(PUBLIC / f"icon-{size}.png", optimize=True)


for icon_size in (192, 512):
    create_icon(icon_size)
