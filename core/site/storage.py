import io
import logging
import mimetypes
import os
import uuid

import requests
from PIL import Image, ImageOps

from core import config
from core.site.client import r2_client

logger = logging.getLogger(__name__)

MAX_IMAGE_WIDTH = 1600
VISION_MAX_SIDE = 1568
HEAD_TIMEOUT = 10
_RESIZABLE = {"image/jpeg": "JPEG", "image/png": "PNG"}


def _prepare_image(filepath):
    """(byte, content_type) pronti per l'upload; JPEG/PNG sono orientati e limitati a MAX_IMAGE_WIDTH."""
    content_type, _ = mimetypes.guess_type(filepath)
    content_type = content_type or "application/octet-stream"
    fmt = _RESIZABLE.get(content_type)
    if not fmt:
        with open(filepath, "rb") as f:
            return f.read(), content_type

    with Image.open(filepath) as img:
        img = ImageOps.exif_transpose(img)
        if img.width > MAX_IMAGE_WIDTH:
            img = img.resize((MAX_IMAGE_WIDTH, round(img.height * MAX_IMAGE_WIDTH / img.width)), Image.LANCZOS)
        buf = io.BytesIO()
        if fmt == "JPEG":
            img.convert("RGB").save(buf, format=fmt, quality=85)
        else:
            img.save(buf, format=fmt)
    return buf.getvalue(), content_type


def vision_copy(filepath):
    """(byte, "image/jpeg") per l'AI: lato lungo al massimo VISION_MAX_SIDE, trasparenza su fondo bianco."""
    with Image.open(filepath) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        longest = max(img.size)
        if longest > VISION_MAX_SIDE:
            scale = VISION_MAX_SIDE / longest
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


def object_name(stem, filepath):
    """Nome del file su R2: '<stem>_<uuid8><estensione>', mai il nome originale dell'utente."""
    extension = os.path.splitext(filepath)[1].lower().replace(".jpeg", ".jpg") or ".bin"
    return f"{stem}_{uuid.uuid4().hex[:8]}{extension}"


def upload_media(filepath, key_prefix, stem):
    """Carica un file su R2 come '<key_prefix>/<stem>_<uuid8>.<ext>'; restituisce {'id': key, 'source_url'} o None."""
    name = object_name(stem, filepath)
    key = f"{key_prefix.strip('/')}/{name}"
    try:
        client = r2_client()
        if not client:
            logger.warning("Credenziali R2 non configurate del tutto: upload impossibile.")
            return None
        logger.info("R2: carico '%s' come '%s'...", os.path.basename(filepath), key)
        body, content_type = _prepare_image(filepath)
        client.put_object(Bucket=config.R2_BUCKET, Key=key, Body=body, ContentType=content_type)
        return {"id": key, "source_url": f"{config.R2_PUBLIC_URL}/{key}"}
    except ImportError:
        logger.error("R2: boto3 non è installato; esegui: pip install -r requirements.txt")
        return None
    except Exception as e:
        logger.error("R2: upload di '%s' fallito: %s", os.path.basename(filepath), e)
        return None


def media_url(key):
    """L'indirizzo pubblico di un oggetto del bucket."""
    return f"{config.R2_PUBLIC_URL}/{key}"


def is_reachable(url):
    """True se una richiesta HEAD su `url` risponde 200 (un errore di rete vale come non raggiungibile)."""
    try:
        return requests.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False
