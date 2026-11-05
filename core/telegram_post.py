"""Legge un messaggio di un canale Telegram pubblico (testo, data, foto) dalla sua pagina di anteprima.

Serve a preparare un articolo partendo dall'annuncio già pubblicato sul canale: la skill lo usa per il brief,
per `--date` e per le immagini. Nessun login: funziona solo per i canali pubblici.
"""
import html
import os
import re
from datetime import datetime, timezone

import requests

from core.pipeline import PipelineError

TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (GdG-Article-Bot)"}
SITE_TIMEZONE = "Europe/Rome"

_URL = re.compile(r"^(?:https?://)?(?:www\.)?t\.me/(?:s/)?([A-Za-z0-9_]{4,})/(\d+)(?:[/?#].*)?$")
_TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_DATE = re.compile(r'<time[^>]*datetime="([^"]+)"')
_PHOTO = re.compile(r'tgme_widget_message_photo_wrap[^>]*background-image:url\(\'([^\']+)\'\)')
_ANCHOR = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_BREAK = re.compile(r"<br\s*/?>")
_TAG = re.compile(r"<[^>]+>")


def parse_url(url):
    """`(canale, numero)` da un link come https://t.me/canale/123; ValueError se non è il link di un messaggio pubblico."""
    match = _URL.match((url or "").strip())
    if not match or match.group(1) == "c":
        raise ValueError(f"'{url}' non è il link di un messaggio di un canale pubblico (atteso https://t.me/<canale>/<numero>)")
    return match.group(1), int(match.group(2))


def _to_text(fragment):
    """HTML del messaggio in testo semplice: a capo veri e link nella forma «testo (indirizzo)»."""
    fragment = _BREAK.sub("\n", fragment)

    def link(match):
        target, label = html.unescape(match.group(1)), html.unescape(_TAG.sub("", match.group(2))).strip()
        same = not label or label.lower().rstrip("/") in target.lower()
        return target if same else f"{label} ({target})"

    fragment = _ANCHOR.sub(link, fragment)
    text = html.unescape(_TAG.sub("", fragment))
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _site_date(stamp):
    """Data (AAAA-MM-GG) nel fuso del sito di un orario ISO 8601 con fuso, come quello di Telegram."""
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        moment = moment.astimezone(ZoneInfo(SITE_TIMEZONE))
    except Exception:  # database dei fusi orari non disponibile: si usa quello dell'orario ricevuto
        pass
    return moment.date().isoformat()


def parse_post(page):
    """Dalla pagina di anteprima: {text, datetime, date_iso, photos, links, has_video}; PipelineError se non c'è il messaggio."""
    text_match = _TEXT.search(page)
    photos = _PHOTO.findall(page)
    if not text_match and not photos:
        raise PipelineError("Messaggio non trovato: il canale non è pubblico o il numero è sbagliato")
    fragment = text_match.group(1) if text_match else ""
    stamp = (_DATE.search(page) or [None, None])[1]
    return {
        "text": _to_text(fragment),
        "datetime": stamp,
        "date_iso": _site_date(stamp) if stamp else None,
        "photos": [html.unescape(url) for url in photos],
        "links": [html.unescape(target) for target, _ in _ANCHOR.findall(fragment)],
        "has_video": "tgme_widget_message_video" in page,
    }


def fetch_post(url, folder, get=requests.get):
    """Scarica il messaggio `url` in `folder` (`post.md` e `img_1.jpg`...) e restituisce il risultato di parse_post
    con in più `photo_files` (i percorsi salvati), `channel` e `number`."""
    try:
        channel, number = parse_url(url)
    except ValueError as e:
        raise PipelineError(str(e)) from e
    try:
        response = get(f"https://t.me/{channel}/{number}", params={"embed": "1", "mode": "tme"}, headers=HEADERS,
                       timeout=TIMEOUT)
    except requests.RequestException as e:
        raise PipelineError(f"Impossibile leggere il messaggio Telegram: {e}") from e
    if response.status_code != 200:
        raise PipelineError(f"Impossibile leggere il messaggio Telegram: risposta {response.status_code}")
    post = parse_post(response.text)

    os.makedirs(folder, exist_ok=True)
    files = []
    for index, photo_url in enumerate(post["photos"], start=1):
        try:
            image = get(photo_url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise PipelineError(f"Impossibile scaricare la foto {index}: {e}") from e
        if image.status_code != 200:
            raise PipelineError(f"Impossibile scaricare la foto {index}: risposta {image.status_code}")
        path = os.path.join(folder, f"img_{index}.jpg")
        with open(path, "wb") as f:
            f.write(image.content)
        files.append(path)
    with open(os.path.join(folder, "post.md"), "w", encoding="utf-8") as f:
        f.write(post["text"] + "\n")
    return {**post, "photo_files": files, "channel": channel, "number": number}
