import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from typing import Optional

from core import config, db
from core.ai.provider import get_provider
from core.brief import parse_markers
from core.site import content, storage
from core.validate import ArticleError, generate_valid

logger = logging.getLogger(__name__)

DEFAULT_ORDER = 100
DRY_RUN_HOST = "https://media.example"


class PipelineError(Exception):
    """Errore comprensibile all'utente (messaggio in italiano): impostazioni mancanti, upload fallito, bozza non creata..."""


@dataclass
class Draft:
    kind: str
    slug: str
    title: str
    excerpt: str
    markdown: str
    card_slug: Optional[str] = None
    card_markdown: Optional[str] = None
    branch: Optional[str] = None
    draft_url: Optional[str] = None
    public_url: Optional[str] = None
    id: Optional[int] = None
    dry_run: bool = False


def _date_iso(date):
    """`date` (DD-MM-YYYY, o None per oggi) in formato AAAA-MM-GG."""
    if not date:
        return date_type.today().isoformat()
    try:
        return datetime.strptime(date.strip(), "%d-%m-%Y").date().isoformat()
    except ValueError as e:
        raise PipelineError(f"Data non valida '{date}': usa GG-MM-AAAA") from e


def _date_label(date_iso):
    return datetime.strptime(date_iso, "%Y-%m-%d").strftime("%d-%m-%Y")


def _check_settings(mode):
    missing = config.missing_settings(mode)
    if missing:
        raise PipelineError("Impostazioni mancanti: " + ", ".join(missing))


def _vision_images(paths):
    images = []
    for number, path in enumerate(paths, start=1):
        if not os.path.isfile(path):
            raise PipelineError(f"Immagine {number} non trovata: {path}")
        try:
            images.append(storage.vision_copy(path))
        except Exception as e:
            raise PipelineError(f"Immagine {number} non leggibile ({os.path.basename(path)}): {e}") from e
    return images


def _check_choice(name, value, n_images):
    if value is not None and not 1 <= value <= n_images:
        raise PipelineError(f"{name}: {value} non valido, le immagini sono {n_images}")


def _replace_images(body, urls):
    return re.sub(r"\]\(img:(\d+)\)", lambda match: f"]({urls[int(match.group(1)) - 1]})", body)


def _render(kind, result, date_iso, image_urls, card_slug, logo_url, order):
    """(estratto, markdown dell'articolo, markdown della scheda o None) per un risultato già validato."""
    cover = result.get("cover_index")
    cover_url = image_urls[cover - 1] if cover else None
    excerpt = result["excerpt"].strip() or content.make_excerpt(result["body"])
    front_matter = content.build_news_front_matter(result["title"].strip(), date_iso, cover_url, excerpt)
    markdown = content.build_news_markdown(front_matter, _replace_images(result["body"], image_urls))
    card = None
    if kind == "collab":
        partner = result["partner"]
        card = content.build_partner_card(partner["nome"].strip(), logo_url, partner.get("url"),
                                          partner["descrizione"].strip(), order)
    return excerpt, markdown, card


def _existing_names(lister, tolerant):
    try:
        return lister()
    except Exception as e:
        if not tolerant:
            raise PipelineError(f"Impossibile leggere le cartelle del sito su GitHub: {e}") from e
        logger.warning("Dry run: unicità degli slug non verificata (%s)", e)
        return []


def _choose_slugs(kind, result, tolerant):
    """(slug dell'articolo, slug della scheda o None), unici rispetto a quelli già presenti sul sito."""
    if kind == "news":
        names = _existing_names(content.existing_news_names, tolerant)
        return content.unique_slug(content.slugify(result["title"]), names), None
    partner_slug = content.slugify(result["partner"]["nome"])
    news_names = _existing_names(content.existing_news_names, tolerant)
    card_names = _existing_names(content.existing_card_names, tolerant)
    return (content.unique_slug(f"collaborazione-con-{partner_slug}", news_names),
            content.unique_slug(partner_slug, card_names, strip=False))


def _upload_images(image_paths, slug):
    keys = []
    for number, path in enumerate(image_paths, start=1):
        info = storage.upload_media(path, f"news/{slug}", f"img_{number}")
        if not info:
            raise PipelineError(f"Upload su R2 fallito per l'immagine {number}: nessun articolo creato")
        keys.append(info["id"])
    return keys


def _upload_logo(path, card_slug):
    info = storage.upload_media(path, f"collaborazioni/{card_slug}", "logo")
    if not info:
        raise PipelineError("Upload su R2 fallito per il logo: nessun articolo creato")
    return info["id"]


def _keep_originals(article_id, image_paths):
    """Copia le immagini originali in TMP_DIR/<id>/ (servono a «Rivedi»); restituisce i nuovi percorsi."""
    folder = os.path.join(config.TMP_DIR, str(article_id))
    os.makedirs(folder, exist_ok=True)
    kept = []
    for number, path in enumerate(image_paths, start=1):
        target = os.path.join(folder, f"{number:02d}{os.path.splitext(path)[1].lower()}")
        shutil.copy2(path, target)
        kept.append(target)
    return kept


def _forget_originals(article_id):
    shutil.rmtree(os.path.join(config.TMP_DIR, str(article_id)), ignore_errors=True)


def create_article(kind, brief, image_paths, cover=None, logo=None, order=None, date=None, dry_run=False,
                   provider=None, chat_id=None, message_id=None):
    """Crea la bozza di un articolo `kind` ("news" o "collab") dal brief e dalle immagini; funzione bloccante.

    `brief` può contenere le righe `copertina: N`, `logo: N`, `ordine: N` (gli argomenti espliciti hanno la precedenza).
    Con `dry_run` non carica nulla su R2 e non scrive su GitHub: restituisce solo il testo.
    """
    if kind not in content.KINDS:
        raise PipelineError(f"Tipo di articolo sconosciuto: {kind}")
    _check_settings("dry_run" if dry_run else "cli")
    parsed = parse_markers(brief)
    if not parsed.text:
        raise PipelineError("Il brief è vuoto")
    cover = cover or parsed.cover
    logo = (logo or parsed.logo) if kind == "collab" else None
    order = order if order is not None else (parsed.order if parsed.order is not None else DEFAULT_ORDER)
    image_paths = list(image_paths or [])
    _check_choice("Copertina", cover, len(image_paths))
    _check_choice("Logo", logo, len(image_paths))
    if kind == "collab" and not image_paths:
        raise PipelineError("Una collaborazione ha bisogno almeno dell'immagine del logo")
    date_iso = _date_iso(date)

    images = _vision_images(image_paths)
    try:
        result = generate_valid(provider or get_provider(), kind, parsed.text, images,
                                article_date=_date_label(date_iso), cover=cover, logo=logo)
    except ArticleError as e:
        raise PipelineError("L'AI non ha prodotto un articolo valido dopo 3 tentativi: " + "; ".join(e.problems)) from e

    slug, card_slug = _choose_slugs(kind, result, tolerant=dry_run)
    logo_index = result["partner"]["logo_index"] if kind == "collab" else None

    if dry_run:
        image_urls = [f"{DRY_RUN_HOST}/{slug}/img_{n}.jpg" for n in range(1, len(image_paths) + 1)]
        logo_url = f"{DRY_RUN_HOST}/{card_slug}/logo.png" if card_slug else None
        excerpt, markdown, card = _render(kind, result, date_iso, image_urls, card_slug, logo_url, order)
        return Draft(kind, slug, result["title"].strip(), excerpt, markdown, card_slug, card, dry_run=True)

    keys = _upload_images(image_paths, slug)
    image_urls = [storage.media_url(key) for key in keys]
    logo_key = _upload_logo(image_paths[logo_index - 1], card_slug) if kind == "collab" else None
    logo_url = storage.media_url(logo_key) if logo_key else None
    excerpt, markdown, card = _render(kind, result, date_iso, image_urls, card_slug, logo_url, order)

    draft_url, branch = content.create_draft(kind, slug, date_iso, markdown, card, card_slug)
    if not draft_url:
        raise PipelineError("Creazione della bozza su GitHub fallita (le immagini sono già su R2)")

    article_id = db.insert_article(
        kind, slug=slug, card_slug=card_slug, branch=branch, title=result["title"].strip(), date_iso=date_iso,
        draft_url=draft_url, public_url=content.public_url(slug), image_keys=keys,
        cover_override=cover, logo_override=logo, cover_index=result.get("cover_index"), logo_index=logo_index,
        logo_key=logo_key, card_order=order, brief=parsed.text, last_body=result["body"],
        chat_id=chat_id, message_id=message_id,
    )
    db.update_article(article_id, image_paths=_keep_originals(article_id, image_paths))
    return Draft(kind, slug, result["title"].strip(), excerpt, markdown, card_slug, card, branch, draft_url,
                 content.public_url(slug), article_id)


def _load_draft(article_id):
    article = db.get_article(article_id)
    if not article:
        raise PipelineError(f"Bozza {article_id} non trovata")
    if article["status"] != "draft":
        raise PipelineError(f"La bozza {article_id} è già {'pubblicata' if article['status'] == 'published' else 'scartata'}")
    return article


def revise_article(article_id, feedback, provider=None):
    """Rigenera il testo di una bozza secondo le indicazioni, sullo stesso branch e con le stesse immagini."""
    _check_settings("cli")
    article = _load_draft(article_id)
    if not (feedback or "").strip():
        raise PipelineError("Servono le indicazioni su cosa cambiare")
    kind, paths = article["kind"], article["image_paths"]
    if len(paths) != len(article["image_keys"]) or not all(os.path.isfile(p) for p in paths):
        raise PipelineError("Le immagini originali della bozza non sono più disponibili")

    images = _vision_images(paths)
    try:
        result = generate_valid(provider or get_provider(), kind, article["brief"], images,
                                article_date=_date_label(article["date_iso"]), cover=article["cover_override"],
                                logo=article["logo_override"], revise=(article["last_body"], feedback.strip()))
    except ArticleError as e:
        raise PipelineError("L'AI non ha prodotto un articolo valido dopo 3 tentativi: " + "; ".join(e.problems)) from e

    image_urls = [storage.media_url(key) for key in article["image_keys"]]
    logo_index = result["partner"]["logo_index"] if kind == "collab" else None
    logo_key = article["logo_key"]
    if kind == "collab" and (logo_index != article["logo_index"] or not logo_key):
        logo_key = _upload_logo(paths[logo_index - 1], article["card_slug"])
    logo_url = storage.media_url(logo_key) if logo_key else None
    order = article["card_order"] if article["card_order"] is not None else DEFAULT_ORDER
    excerpt, markdown, card = _render(kind, result, article["date_iso"], image_urls, article["card_slug"], logo_url, order)

    draft_url = content.update_draft(kind, article["slug"], article["date_iso"], article["branch"], markdown, card,
                                     article["card_slug"])
    if not draft_url:
        raise PipelineError("Aggiornamento della bozza su GitHub fallito")
    db.update_article(article_id, title=result["title"].strip(), last_body=result["body"], draft_url=draft_url,
                      cover_index=result.get("cover_index"), logo_index=logo_index, logo_key=logo_key)
    return Draft(kind, article["slug"], result["title"].strip(), excerpt, markdown, article["card_slug"], card,
                 article["branch"], draft_url, article["public_url"], article_id)


def publish(article_id):
    """Pubblica la bozza (merge sul branch del sito); restituisce l'indirizzo pubblico."""
    _check_settings("cli")
    article = _load_draft(article_id)
    if not content.publish_draft(article["branch"]):
        raise PipelineError("Pubblicazione fallita (merge su GitHub non riuscito)")
    db.update_article(article_id, status="published")
    _forget_originals(article_id)
    return article["public_url"]


def discard(article_id):
    """Scarta la bozza (elimina il branch). Restituisce "discarded", oppure "published" se era già online."""
    _check_settings("cli")
    article = _load_draft(article_id)
    outcome = content.discard_draft(article["branch"])
    if outcome is False:
        raise PipelineError("Eliminazione della bozza fallita")
    status = "published" if outcome == "published" else "discarded"
    db.update_article(article_id, status=status)
    _forget_originals(article_id)
    return status
