import os
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.text_formatting import format_job_title, format_person_name


BASE_DIR = Path(__file__).resolve().parents[2]
ART_DIR = BASE_DIR / "assets" / "signature"
FONT_PATH = BASE_DIR / "assets" / "fonts" / "Poppins-Bold.ttf"

SIGNATURE_SIZE = (532, 173)
BRAND_BLUE = (27, 20, 100)
ICON_BLUE = (46, 49, 146)
SECONDARY_TEXT = (116, 115, 115)
WHITE = (255, 255, 255)


def _font(size: int):
    """Load the exact Poppins Bold font embedded in the approved PowerPoint."""
    return ImageFont.truetype(str(FONT_PATH), size)


def _fit_font(draw, text, max_width, start_size, min_size):
    for size in range(start_size, min_size - 1, -1):
        font = _font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
    return _font(min_size)


def _asset(name):
    return Image.open(ART_DIR / name).convert("RGBA")


def _paste(image, asset_name, box, crop_transparent=False):
    x, y, width, height = box
    asset = _asset(asset_name).resize((width, height), Image.Resampling.LANCZOS)
    if crop_transparent:
        source = _asset(asset_name)
        alpha_box = source.getchannel("A").getbbox()
        if alpha_box:
            asset = source.crop(alpha_box).resize((width, height), Image.Resampling.LANCZOS)
    image.alpha_composite(asset, (x, y))


def _draw_powerpoint_art(show_phone=False):
    """Render the static art and icons at the coordinates of the approved PPTX."""
    background = _asset("image1.png").resize((532, 174), Image.Resampling.LANCZOS)
    image = background.crop((0, 0, 532, 173))
    draw = ImageDraw.Draw(image)

    # Contact icons from the approved PowerPoint.
    contact_icons = [(83, "image12.png"), (109, "image13.png")]
    if show_phone:
        contact_icons.append((137, "image15.png"))
    for y, asset in contact_icons:
        draw.rounded_rectangle((243, y, 261, y + 18), radius=2, fill=ICON_BLUE + (255,))
        _paste(image, asset, (247, y + 4, 10, 10))

    return image


def generate_signature_image(name, role, email, output_path, phone=""):
    """Render employee data over the approved signature artwork."""
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"Fonte Poppins Bold não encontrada: {FONT_PATH}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    name = format_person_name(name or "Nome do Colaborador")
    role = format_job_title(role)
    email = (email or "colaborador@empresa.com").strip().lower()
    phone = (phone or "").strip()

    image = _draw_powerpoint_art(show_phone=bool(phone))
    draw = ImageDraw.Draw(image)

    name_font = _fit_font(draw, name, 280, 18, 13)
    role_font = _fit_font(draw, role, 280, 12, 9)
    email_font = _fit_font(draw, email, 260, 8, 6)
    detail_font = _font(8)
    phone_font = _fit_font(draw, phone, 235, 10, 8) if phone else None

    # Positions, colors and relative sizes converted from the PPTX EMU coordinates.
    draw.text((246, 22), name, font=name_font, fill=BRAND_BLUE)
    if role:
        draw.text((245, 45), role, font=role_font, fill=SECONDARY_TEXT)
    draw.line((245, 69, 506, 69), fill=ICON_BLUE, width=2)
    draw.text((270, 91), email, font=email_font, fill=SECONDARY_TEXT)
    draw.text((270, 117), "www.dinhodistribuidora.com.br", font=detail_font, fill=SECONDARY_TEXT)
    if phone:
        draw.text((270, 141), phone, font=phone_font, fill=SECONDARY_TEXT)

    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output


def generate_temp_signature(name, role, email, phone=""):
    suffix = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "assinatura").strip())
    temp_dir = Path(tempfile.gettempdir())
    output_path = temp_dir / f"assinatura_{suffix or 'colaborador'}_{os.getpid()}.png"
    return generate_signature_image(name, role, email, output_path, phone=phone)
