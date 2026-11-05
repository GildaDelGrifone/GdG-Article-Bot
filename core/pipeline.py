import logging
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from typing import Optional

from core import config, db
from core.ai.provider import get_provider
from core.brief import parse_markers
from core.site import content, storage
from core.validate import (DESCRIPTION_LIMIT, EXCERPT_LIMIT, TITLE_LIMIT, ArticleError, check_body, generate_valid,
                           valid_url)

logger = logging.getLogger(__name__)

DEFAULT_ORDER = 100
ARTICLE_FILE = "articolo.md"  # nomi dei file di una bozza scaricata con pull_draft
CARD_FILE = "scheda.md"
DRY_RUN_HOST = "https://media.example"


class PipelineError(Exception):
    """Errore comprensibile all'utente (messaggio in italiano): impostazioni mancanti, upload fallito, bozza non creata..."""


class SettingsError(PipelineError):
    """Mancano impostazioni obbligatorie (variabili del file .env)."""


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
    date_iso: Optional[str] = None


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
        raise SettingsError("Impostazioni mancanti: " + ", ".join(missing))


@contextmanager
def _normalized_images(paths):
    """Copia le immagini in una cartella temporanea come JPEG/PNG (vedi storage.normalize_image) e le dà al chiamante."""
    paths = list(paths or [])
    os.makedirs(config.TMP_DIR, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="img_", dir=config.TMP_DIR)
    try:
        converted = []
        for number, path in enumerate(paths, start=1):
            if not os.path.isfile(path or ""):
                raise PipelineError(f"Immagine {number} non trovata: {path}")
            try:
                converted.append(storage.normalize_image(path, workdir, number))
            except ValueError as e:
                raise PipelineError(f"Immagine {number}: {e}") from e
        yield converted
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


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


def _render(kind, slug, result, date_iso, image_urls, card_slug, logo_url, order):
    """(estratto, markdown dell'articolo, markdown della scheda o None) per un risultato già validato.

    La scheda partner punta all'articolo della collaborazione (`url` = pagina news), non al sito del partner.
    """
    cover = result.get("cover_index")
    cover_url = image_urls[cover - 1] if cover else None
    excerpt = result["excerpt"].strip() or content.make_excerpt(result["body"])
    front_matter = content.build_news_front_matter(result["title"].strip(), date_iso, cover_url, excerpt)
    markdown = content.build_news_markdown(front_matter, _replace_images(result["body"], image_urls))
    card = None
    if kind == "collab":
        partner = result["partner"]
        card = content.build_partner_card(partner["nome"].strip(), logo_url, content.public_url(slug),
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
    Le immagini possono essere in qualunque formato che Pillow apre: vengono convertite in JPEG/PNG prima di tutto il resto.
    Con `dry_run` non carica nulla su R2 e non scrive su GitHub: restituisce solo il testo.
    """
    with _normalized_images(image_paths) as paths:
        return _create_article(kind, brief, paths, cover, logo, order, date, dry_run, provider, chat_id, message_id)


def _create_article(kind, brief, image_paths, cover, logo, order, date, dry_run, provider, chat_id, message_id):
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
        excerpt, markdown, card = _render(kind, slug, result, date_iso, image_urls, card_slug, logo_url, order)
        return Draft(kind, slug, result["title"].strip(), excerpt, markdown, card_slug, card, dry_run=True,
                     date_iso=date_iso)

    keys = _upload_images(image_paths, slug)
    image_urls = [storage.media_url(key) for key in keys]
    logo_key = _upload_logo(image_paths[logo_index - 1], card_slug) if kind == "collab" else None
    logo_url = storage.media_url(logo_key) if logo_key else None
    excerpt, markdown, card = _render(kind, slug, result, date_iso, image_urls, card_slug, logo_url, order)

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
                 content.public_url(slug), article_id, date_iso=date_iso)


def _check_card_fields(nome, logo_path, descrizione):
    if not (nome or "").strip():
        raise PipelineError("Manca il nome del partner")
    if not (descrizione or "").strip():
        raise PipelineError("Manca la descrizione del partner")
    if len(descrizione.strip()) > DESCRIPTION_LIMIT:
        raise PipelineError(f"La descrizione del partner supera {DESCRIPTION_LIMIT} caratteri ({len(descrizione.strip())})")
    if not os.path.isfile(logo_path or ""):
        raise PipelineError(f"Logo non trovato: {logo_path}")


def _check_article_exists(article):
    """Lo slug deve essere quello di un articolo già presente sul sito (la scheda punta a /news/<slug>/)."""
    slugs = {content.strip_date(name) for name in content.existing_news_names()}
    if article not in slugs:
        raise PipelineError(f"Articolo '{article}' non trovato sul sito")


def create_card(nome, logo_path, descrizione, article=None, order=None, dry_run=False):
    """Crea la bozza della sola scheda partner (nessun articolo, nessuna AI); funzione bloccante.

    `article` è lo slug dell'articolo news sulla collaborazione: la scheda ha `url` = /news/<slug>/ (se manca, niente link).

    Il logo va su R2 come `collaborazioni/<scheda>/logo_<uuid8>.<ext>`; la bozza è il branch `scheda/<slug>`.
    Con `dry_run` non carica nulla su R2 e non scrive su GitHub: restituisce solo il testo della scheda.
    """
    _check_settings("card_dry_run" if dry_run else "card")
    _check_card_fields(nome, logo_path, descrizione)
    with _normalized_images([logo_path]) as (logo_file,):
        return _create_card(nome, logo_file, descrizione, article, order, dry_run)


def _create_card(nome, logo_path, descrizione, article, order, dry_run):
    nome, descrizione = nome.strip(), descrizione.strip()
    article = (article or "").strip() or None
    if article:
        try:
            _check_article_exists(article)
        except PipelineError:
            raise
        except Exception as e:
            if not dry_run:
                raise PipelineError(f"Impossibile leggere le cartelle del sito su GitHub: {e}") from e
            logger.warning("Dry run: esistenza dell'articolo non verificata (%s)", e)
    url = content.public_url(article) if article else None
    order = DEFAULT_ORDER if order is None else order

    card_names = _existing_names(content.existing_card_names, tolerant=dry_run)
    card_slug = content.unique_slug(content.slugify(nome), card_names, strip=False)

    if dry_run:
        card = content.build_partner_card(nome, f"{DRY_RUN_HOST}/{card_slug}/logo.png", url, descrizione, order)
        return Draft(content.CARD_KIND, card_slug, nome, descrizione, "", card_slug, card, dry_run=True)

    logo_key = _upload_logo(logo_path, card_slug)
    card = content.build_partner_card(nome, storage.media_url(logo_key), url, descrizione, order)
    draft_url, branch = content.create_card_draft(card_slug, card)
    if not draft_url:
        raise PipelineError("Creazione della bozza su GitHub fallita (il logo è già su R2)")
    public_url = content.collaborations_url()
    article_id = db.insert_article(
        content.CARD_KIND, slug=card_slug, card_slug=card_slug, branch=branch, title=nome,
        date_iso=date_type.today().isoformat(), draft_url=draft_url, public_url=public_url, logo_key=logo_key,
        card_order=order, brief=descrizione,
    )
    return Draft(content.CARD_KIND, card_slug, nome, descrizione, "", card_slug, card, branch, draft_url, public_url,
                 article_id)


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
    if article["kind"] == content.CARD_KIND:
        raise PipelineError("Una scheda partner non si rigenera: scartala e creane una nuova")
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
    excerpt, markdown, card = _render(kind, article["slug"], result, article["date_iso"], image_urls, article["card_slug"], logo_url, order)

    draft_url = content.update_draft(kind, article["slug"], article["date_iso"], article["branch"], markdown, card,
                                     article["card_slug"])
    if not draft_url:
        raise PipelineError("Aggiornamento della bozza su GitHub fallito")
    db.update_article(article_id, title=result["title"].strip(), last_body=result["body"], draft_url=draft_url,
                      cover_index=result.get("cover_index"), logo_index=logo_index, logo_key=logo_key)
    return Draft(kind, article["slug"], result["title"].strip(), excerpt, markdown, article["card_slug"], card,
                 article["branch"], draft_url, article["public_url"], article_id, date_iso=article["date_iso"])


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


# --- correzione delle bozze a mano (skill / riga di comando) ---------------------------------------------------------

def _draft_files(article):
    """[(nome del file locale, percorso sul sito)] dei file di una bozza."""
    files = []
    if article["kind"] != content.CARD_KIND:
        files.append((ARTICLE_FILE, content.news_path(article["date_iso"], article["slug"])))
    if article["kind"] in ("collab", content.CARD_KIND):
        files.append((CARD_FILE, content.card_path(article["card_slug"])))
    return files


def _read_draft(article):
    """[(nome locale, percorso sul sito, testo)] letti dal branch della bozza."""
    files = []
    for local, path in _draft_files(article):
        try:
            text = content.read_draft_file(path, article["branch"])
        except Exception as e:
            raise PipelineError(f"Impossibile leggere la bozza su GitHub: {e}") from e
        if text is None:
            raise PipelineError(f"Il file {path} non esiste sul branch {article['branch']}")
        files.append((local, path, text))
    return files


def show_draft(article_id):
    """I file della bozza come sono sul suo branch: [(percorso sul sito, testo)]."""
    _check_settings("card")
    return [(path, text) for _, path, text in _read_draft(_load_draft(article_id))]


def pull_draft(article_id, folder):
    """Scrive in `folder` i file della bozza (`articolo.md`, `scheda.md`) perché si possano correggere; restituisce i percorsi."""
    _check_settings("card")
    written = []
    os.makedirs(folder, exist_ok=True)
    for local, _, text in _read_draft(_load_draft(article_id)):
        target = os.path.join(folder, local)
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(target)
    return written


def _parse_file(name, text):
    try:
        return content.parse_front_matter(text)
    except ValueError as e:
        raise PipelineError(f"{name}: {e}") from e


def _check_edited_article(article, fields, body):
    """(corpo con i segnaposto img:N, indice della copertina o None) di un articolo corretto; PipelineError se non è valido."""
    problems = []
    title, excerpt, date = (fields.get(key, "").strip() for key in ("title", "excerpt", "date"))
    if not title:
        problems.append("manca il titolo (title)")
    elif len(title) > TITLE_LIMIT:
        problems.append(f"il titolo supera {TITLE_LIMIT} caratteri")
    if not excerpt:
        problems.append("manca l'estratto (excerpt)")
    elif len(excerpt) > EXCERPT_LIMIT:
        problems.append(f"l'estratto supera {EXCERPT_LIMIT} caratteri")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        problems.append(f"la data (date) '{date}' non è valida: usa AAAA-MM-GG")
    cover = fields.get("cover", "").strip()
    if cover and not valid_url(cover):
        problems.append("la copertina (cover) non è un indirizzo web")

    urls = [storage.media_url(key) for key in article["image_keys"]]
    for number, url in enumerate(urls, start=1):
        body = body.replace(f"]({url})", f"](img:{number})")
    problems += check_body(body, article["kind"], len(urls))
    if problems:
        raise PipelineError("L'articolo non è valido: " + "; ".join(problems))
    return body, (urls.index(cover) + 1 if cover in urls else None)


def _check_edited_card(fields):
    problems = []
    if not fields.get("nome", "").strip():
        problems.append("manca il nome (nome)")
    if not valid_url(fields.get("logo", "")):
        problems.append("il logo (logo) non è un indirizzo web")
    if fields.get("url", "").strip() and not valid_url(fields["url"]):
        problems.append("url non è un indirizzo web")
    description = fields.get("descrizione", "").strip()
    if not description:
        problems.append("manca la descrizione (descrizione)")
    elif len(description) > DESCRIPTION_LIMIT:
        problems.append(f"la descrizione supera {DESCRIPTION_LIMIT} caratteri ({len(description)})")
    try:
        order = int(fields.get("ordine", ""))
    except ValueError:
        order = None
        problems.append("ordine deve essere un numero intero")
    if problems:
        raise PipelineError("La scheda non è valida: " + "; ".join(problems))
    return order


def push_draft(article_id, folder):
    """Controlla i file corretti in `folder` e li scrive sul branch della bozza; restituisce il link della bozza.

    Valgono le regole dell'AI (sezioni, riga `---`, chiusura con 🦅, immagini, lunghezze). Il testo salvato nel database
    (`last_body`) è quello corretto, così un «Rivedi» successivo parte da qui. Se cambia `date` l'articolo cambia nome
    (la data è nel nome del file) e il vecchio file viene eliminato.
    """
    _check_settings("card")
    article = _load_draft(article_id)
    texts = {}
    for local, _ in _draft_files(article):
        path = os.path.join(folder, local)
        if not os.path.isfile(path):
            raise PipelineError(f"File non trovato: {path} (scarica prima la bozza con `edit_draft.py pull`)")
        with open(path, encoding="utf-8") as f:
            texts[local] = f.read().strip("\n") + "\n"

    kind, updates = article["kind"], {}
    news_markdown = card_markdown = None
    date_iso = article["date_iso"]
    if ARTICLE_FILE in texts:
        news_markdown = texts[ARTICLE_FILE]
        fields, body = _parse_file(ARTICLE_FILE, news_markdown)
        placeholder_body, cover_index = _check_edited_article(article, fields, body)
        date_iso = fields["date"].strip()
        updates.update(title=fields["title"].strip(), date_iso=date_iso, last_body=placeholder_body,
                       cover_index=cover_index)
    if CARD_FILE in texts:
        card_markdown = texts[CARD_FILE]
        fields, _ = _parse_file(CARD_FILE, card_markdown)
        updates["card_order"] = _check_edited_card(fields)
        if kind == content.CARD_KIND:
            updates["title"] = fields["nome"].strip()

    if kind == content.CARD_KIND:
        draft_url = content.update_card_draft(article["card_slug"], card_markdown, article["branch"])
    else:
        draft_url = content.update_draft(kind, article["slug"], date_iso, article["branch"], news_markdown,
                                         card_markdown, article["card_slug"], old_date_iso=article["date_iso"])
    if not draft_url:
        raise PipelineError("Aggiornamento della bozza su GitHub fallito")
    db.update_article(article_id, draft_url=draft_url, **updates)
    return draft_url
