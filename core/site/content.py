import json
import logging
import re
import unicodedata

from core import config
from core.site import github
from core.site.client import github_credentials

logger = logging.getLogger(__name__)

EXCERPT_LIMIT = 160
SLUG_MAX_LENGTH = 60
KINDS = ("news", "collab")

# Stessa regola di articleSlug in GdG-Website/src/lib/urls.ts
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}(-\d{2})?-")


def slugify(text, max_length=SLUG_MAX_LENGTH):
    """'Festival dell'Oriente 2026!' -> 'festival-dell-oriente-2026' (ASCII, trattini, al massimo `max_length`)."""
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(slug) > max_length:
        cut = slug[:max_length]
        # taglia a fine parola, a meno che il limite cada proprio su un trattino
        if slug[max_length] != "-" and "-" in cut:
            cut = cut.rsplit("-", 1)[0]
        slug = cut.strip("-")
    return slug or "articolo"


def strip_date(name):
    """'2026-10-02-collaborazione-con-x.md' -> 'collaborazione-con-x': lo slug come lo vede il sito."""
    stem = name[:-3] if name.endswith(".md") else name
    return _DATE_PREFIX.sub("", stem)


def unique_slug(slug, existing_names, strip=True):
    """`slug`, o `slug-2`, `slug-3`... se uno slug uguale esiste già (un duplicato blocca la build del sito).

    Con `strip` i nomi esistenti vengono confrontati senza la data iniziale (articoli); senza, così come sono (schede).
    """
    taken = {strip_date(name) if strip else (name[:-3] if name.endswith(".md") else name) for name in existing_names}
    if slug not in taken:
        return slug
    number = 2
    while f"{slug}-{number}" in taken:
        number += 1
    return f"{slug}-{number}"


def make_excerpt(markdown, limit=EXCERPT_LIMIT):
    """Inizio dell'articolo in testo semplice, tagliato a fine parola con '…' se supera `limit`."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", markdown)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*_`>]", "", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
    return f"{cut.rstrip(' ,.;:-')}…"


def _quote(value):
    """Una stringa YAML tra virgolette (le stringhe JSON sono YAML valido)."""
    return json.dumps(str(value), ensure_ascii=False)


def build_news_front_matter(title, date_iso, cover_url, excerpt):
    """Front matter della collezione `news` (schema in GdG-Website/src/content.config.ts)."""
    lines = ["---", f"title: {_quote(title)}", f"date: {date_iso}"]
    if cover_url:
        lines.append(f"cover: {_quote(cover_url)}")
    if excerpt:
        lines.append(f"excerpt: {_quote(excerpt)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def build_news_markdown(front_matter, body):
    return f"{front_matter}\n{body.strip()}\n"


def build_partner_card(nome, logo_url, url, descrizione, ordine=100):
    """Scheda partner (collezione `collaborazioni`): solo front matter, il corpo non viene mostrato dal sito."""
    lines = ["---", f"nome: {_quote(nome)}", f"logo: {_quote(logo_url)}"]
    if url:
        lines.append(f"url: {_quote(url)}")
    lines.append(f"descrizione: {_quote(descrizione)}")
    lines.append(f"ordine: {int(ordine)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def news_path(date_iso, slug):
    return f"{config.NEWS_CONTENT_DIR.strip('/')}/{date_iso}-{slug}.md"


def card_path(card_slug):
    return f"{config.COLLAB_CONTENT_DIR.strip('/')}/{card_slug}.md"


def branch_name(kind, slug):
    return f"{'collaborazione' if kind == 'collab' else 'news'}/{slug}"


def public_url(slug):
    """La pagina sul sito: l'URL non ha la data, solo /news/<slug>/."""
    return f"{config.ASTRO_BASE_URL}/news/{slug}/"


def existing_news_names():
    return github.list_dir(config.NEWS_CONTENT_DIR.strip("/"), config.ASTRO_GITHUB_BRANCH)


def existing_card_names():
    return github.list_dir(config.COLLAB_CONTENT_DIR.strip("/"), config.ASTRO_GITHUB_BRANCH)


def _file_url(branch, path):
    repo, _ = github_credentials()
    return f"https://github.com/{repo}/blob/{branch}/{path}"


def _commit_files(kind, slug, date_iso, branch, news_markdown, card_markdown, card_slug):
    message_kind = "collaborazione" if kind == "collab" else "news"
    path = news_path(date_iso, slug)
    if not github.put_file(path, news_markdown, f"Bozza {message_kind} {slug}", branch):
        return None
    if card_markdown:
        if not github.put_file(card_path(card_slug), card_markdown, f"Bozza scheda {card_slug}", branch):
            return None
    return _file_url(branch, path)


def create_draft(kind, slug, date_iso, news_markdown, card_markdown=None, card_slug=None):
    """Crea il branch di bozza e vi committa articolo (ed eventuale scheda). Restituisce (link_bozza, branch) o (None, None)."""
    if not github_credentials():
        return None, None
    branch = branch_name(kind, slug)
    try:
        existed = github.branch_exists(branch)
        if not github.create_branch(branch, config.ASTRO_GITHUB_BRANCH):
            return None, None
        draft_url = _commit_files(kind, slug, date_iso, branch, news_markdown, card_markdown, card_slug)
        if not draft_url:
            if not existed:
                github.delete_branch(branch)
            return None, None
        return draft_url, branch
    except Exception as e:
        logger.error("Creazione della bozza '%s' fallita: %s", branch, e)
        return None, None


def update_draft(kind, slug, date_iso, branch, news_markdown, card_markdown=None, card_slug=None):
    """Riscrive i file di una bozza sullo stesso branch (Rivedi). Restituisce il link della bozza o None."""
    if not github_credentials():
        return None
    try:
        if not github.branch_exists(branch):
            return None
        return _commit_files(kind, slug, date_iso, branch, news_markdown, card_markdown, card_slug)
    except Exception as e:
        logger.error("Aggiornamento della bozza '%s' fallito: %s", branch, e)
        return None


def publish_draft(branch):
    """Unisce il branch in ASTRO_GITHUB_BRANCH (il sito va online) e lo elimina; un'eliminazione fallita non conta."""
    if not github_credentials():
        return False
    try:
        if not github.merge_branch(branch, config.ASTRO_GITHUB_BRANCH, f"Pubblica {branch}"):
            return False
        github.delete_branch(branch)
        return True
    except Exception as e:
        logger.error("Pubblicazione di '%s' fallita: %s", branch, e)
        return False


def discard_draft(branch):
    """True se il branch è stato eliminato, "published" se non esiste più (già pubblicato), False in caso di errore."""
    if not github_credentials():
        return False
    try:
        if not github.branch_exists(branch):
            return "published"
        return github.delete_branch(branch)
    except Exception as e:
        logger.error("Eliminazione della bozza '%s' fallita: %s", branch, e)
        return False
