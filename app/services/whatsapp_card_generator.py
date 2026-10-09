from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from app.services.text_formatting import format_job_title, format_person_name


BASE_DIR = Path(__file__).resolve().parents[2]
MODEL_PATH = BASE_DIR / "assets" / "whatsapp" / "modelo_whats.jpg"
BACKGROUND_PATH = BASE_DIR / "assets" / "whatsapp" / "fundo_whats.png"
FONT_PATH = BASE_DIR / "assets" / "fonts" / "Poppins-Bold.ttf"

CARD_SIZE = (637, 637)
# Slightly overlap the template's inner white rim so the placeholder portrait
# cannot show through after antialiasing/resizing the uploaded photo.
PHOTO_BOX = (204, 75, 445, 320)
TEXT_AREA = (42, 365, 595, 465)
BACKGROUND_BLEND = (360, 385)
WHITE = (255, 255, 255)
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTO_PIXELS = 24_000_000
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


class InvalidProfilePhoto(ValueError):
    pass


def _font(size):
    return ImageFont.truetype(str(FONT_PATH), size)


def _fit_font(draw, text, max_width, start_size, min_size):
    for size in range(start_size, min_size - 1, -1):
        font = _font(size)
        bounds = draw.textbbox((0, 0), text, font=font)
        if bounds[2] - bounds[0] <= max_width:
            return font
    return _font(min_size)


def _read_profile_photo(uploaded_file):
    raw = uploaded_file.read(MAX_PHOTO_BYTES + 1)
    if not raw:
        raise InvalidProfilePhoto("Selecione uma foto do seu rosto.")
    if len(raw) > MAX_PHOTO_BYTES:
        raise InvalidProfilePhoto("A foto deve ter no máximo 8 MB.")

    try:
        image = Image.open(BytesIO(raw))
        if image.format not in ALLOWED_IMAGE_FORMATS:
            raise InvalidProfilePhoto("Envie uma foto JPG, PNG ou WEBP.")
        if image.width * image.height > MAX_PHOTO_PIXELS:
            raise InvalidProfilePhoto("A resolução da foto é muito alta. Use uma imagem de até 24 megapixels.")
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise InvalidProfilePhoto("O arquivo enviado não é uma foto válida.") from None

    return ImageOps.exif_transpose(image).convert("RGB")


def _cover_photo(image, size, focus_x=0.5, focus_y=0.33, zoom=1.0):
    target_width, target_height = size
    source_ratio = image.width / image.height
    target_ratio = target_width / target_height
    if source_ratio > target_ratio:
        resized_height = target_height
        resized_width = round(resized_height * source_ratio)
    else:
        resized_width = target_width
        resized_height = round(resized_width / source_ratio)
    zoom = min(2.5, max(1.0, float(zoom)))
    resized_width = max(target_width, round(resized_width * zoom))
    resized_height = max(target_height, round(resized_height * zoom))
    image = image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
    focus_x = min(1.0, max(0.0, float(focus_x)))
    focus_y = min(1.0, max(0.0, float(focus_y)))
    left = round((resized_width - target_width) * focus_x)
    top = round((resized_height - target_height) * focus_y)
    return image.crop((left, top, left + target_width, top + target_height))


def generate_whatsapp_card(
    name, role, uploaded_photo, output_path, *, focus_x=0.5, focus_y=0.33, zoom=1.0
):
    """Create the companion WhatsApp card without retaining the uploaded photo."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Modelo do WhatsApp não encontrado: {MODEL_PATH}")
    if not BACKGROUND_PATH.exists():
        raise FileNotFoundError(f"Fundo do WhatsApp não encontrado: {BACKGROUND_PATH}")
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"Fonte Poppins Bold não encontrada: {FONT_PATH}")

    profile = _read_profile_photo(uploaded_photo)
    card = Image.open(MODEL_PATH).convert("RGB").resize(CARD_SIZE, Image.Resampling.LANCZOS)

    clean_background = Image.open(BACKGROUND_PATH).convert("RGB").resize(CARD_SIZE, Image.Resampling.LANCZOS)
    blend_start, blend_end = BACKGROUND_BLEND
    background_mask = Image.new("L", CARD_SIZE, 0)
    mask_pixels = background_mask.load()
    for y in range(blend_start, CARD_SIZE[1]):
        opacity = 255 if y >= blend_end else round(255 * (y - blend_start) / (blend_end - blend_start))
        for x in range(CARD_SIZE[0]):
            mask_pixels[x, y] = opacity
    card.paste(clean_background, (0, 0), background_mask)

    left, top, right, bottom = PHOTO_BOX
    photo_size = (right - left, bottom - top)
    profile = _cover_photo(profile, photo_size, focus_x=focus_x, focus_y=focus_y, zoom=zoom)
    mask = Image.new("L", photo_size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, photo_size[0] - 1, photo_size[1] - 1), fill=255)
    card.paste(profile, (left, top), mask)

    draw = ImageDraw.Draw(card)
    display_name = format_person_name(name or "Nome do Colaborador").upper()
    display_role = format_job_title(role or "").upper()
    name_font = _fit_font(draw, display_name, 545, 43, 25)
    role_font = _fit_font(draw, display_role, 500, 25, 17)

    name_box = draw.textbbox((0, 0), display_name, font=name_font)
    draw.text(((CARD_SIZE[0] - (name_box[2] - name_box[0])) / 2, 371), display_name, font=name_font, fill=WHITE)
    if display_role:
        role_box = draw.textbbox((0, 0), display_role, font=role_font)
        draw.text(((CARD_SIZE[0] - (role_box[2] - role_box[0])) / 2, 418), display_role, font=role_font, fill=WHITE)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    card.save(output, format="PNG", optimize=True)
    return output
