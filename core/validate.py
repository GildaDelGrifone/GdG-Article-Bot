import logging
import re
from urllib.parse import urlparse

from core import config, prompts
from core.ai.base import AIError, AIQuotaError, article_schema

logger = logging.getLogger(__name__)

TITLE_LIMIT = 80
EXCERPT_LIMIT = 160
DESCRIPTION_LIMIT = 200
NEWS_SECTIONS = (2, 4)
COLLAB_MIN_SECTIONS = 2
CLOSING_EMOJI = "🦅"
ATTEMPTS = 3

_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
_PLACEHOLDER = re.compile(r"^img:(\d+)$")
_IMAGE_LINE = re.compile(r"^[ \t]*!\[[^\]]*\]\(img:(\d+)\)[ \t]*$")
_CAPTION_LINE = re.compile(r"^[ \t]*[_*][^_*\n].*[_*][ \t]*$")
_LINK = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
_HEADING = re.compile(r"^(#{1,6})[ \t]+\S", re.MULTILINE)
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


class ArticleError(Exception):
    """L'AI non ha prodotto un articolo valido dopo tutti i tentativi."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _drop_images(body, numbers):
    """Toglie dal testo le immagini `numbers` (con l'eventuale didascalia in corsivo che le segue)."""
    lines = body.split("\n")
    kept = []
    skip_caption = False
    for line in lines:
        match = _IMAGE_LINE.match(line)
        if match and int(match.group(1)) in numbers:
            skip_caption = True
            continue
        if skip_caption:
            if not line.strip():
                kept.append(line)
                continue
            skip_caption = False
            if _CAPTION_LINE.match(line):
                continue
        kept.append(line)
    text = "\n".join(kept)
    text = _IMAGE.sub(lambda m: "" if _PLACEHOLDER.match(m.group(1)) and int(m.group(1)[4:]) in numbers else m.group(0), text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize(result, kind, cover=None, logo=None):
    """Copia del risultato con le scelte dell'admin applicate e cover/logo tolti dal testo."""
    result = dict(result)
    if cover:
        result["cover_index"] = cover
    if kind == "collab" and isinstance(result.get("partner"), dict):
        result["partner"] = dict(result["partner"])
        if logo:
            result["partner"]["logo_index"] = logo
    hidden = set()
    if isinstance(result.get("cover_index"), int):
        hidden.add(result["cover_index"])
    if kind == "collab" and isinstance(result.get("partner"), dict) and isinstance(result["partner"].get("logo_index"), int):
        hidden.add(result["partner"]["logo_index"])
    if hidden and isinstance(result.get("body"), str):
        result["body"] = _drop_images(result["body"], hidden)
    return result


def _valid_index(value, n_images):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= n_images


def _valid_url(value):
    parsed = urlparse(value or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _check_body(body, kind, n_images):
    problems = []
    lines = [line.rstrip() for line in body.split("\n")]
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return ["il testo è vuoto"]

    if non_empty[0].lstrip().startswith("#"):
        problems.append("il testo inizia con un titolo: deve iniziare con un paragrafo introduttivo senza intestazione")
    levels = [len(match.group(1)) for match in _HEADING.finditer(body)]
    if 1 in levels:
        problems.append("il testo contiene un titolo di livello 1 (#): il titolo lo mostra già il sito")
    sections = sum(1 for level in levels if level in (2, 3))
    if kind == "news" and not NEWS_SECTIONS[0] <= sections <= NEWS_SECTIONS[1]:
        problems.append(f"servono da {NEWS_SECTIONS[0]} a {NEWS_SECTIONS[1]} sezioni (## o ###), ce ne sono {sections}")
    if kind == "collab" and sections < COLLAB_MIN_SECTIONS:
        problems.append(f"servono almeno {COLLAB_MIN_SECTIONS} sezioni (## o ###), ce ne sono {sections}")

    rules = [index for index, line in enumerate(lines) if line.strip() == "---"]
    if len(rules) != 1:
        problems.append(f"serve esattamente una riga con solo --- prima della chiusura, ce ne sono {len(rules)}")
    elif not any(line.strip() for line in lines[rules[0] + 1:]):
        problems.append("dopo la riga --- manca il paragrafo di chiusura")
    if CLOSING_EMOJI not in non_empty[-1]:
        problems.append(f"l'ultima riga deve contenere {CLOSING_EMOJI}")

    used = []
    for match in _IMAGE.finditer(body):
        target = match.group(1)
        placeholder = _PLACEHOLDER.match(target)
        if not placeholder:
            problems.append(f"immagine con indirizzo reale ({target}): usa solo ![alt](img:N)")
        elif not _valid_index(int(placeholder.group(1)), n_images):
            problems.append(f"immagine img:{placeholder.group(1)} inesistente (le immagini sono {n_images})")
        else:
            used.append(int(placeholder.group(1)))
    for number in sorted({n for n in used if used.count(n) > 1}):
        problems.append(f"l'immagine {number} è usata più di una volta")

    without_links = _LINK.sub(" ", _IMAGE.sub(" ", body))
    if re.search(r"https?://", without_links):
        problems.append("c'è un indirizzo web senza link Markdown: scrivi sempre [testo](url)")
    if _HTML_TAG.search(body):
        problems.append("il testo contiene tag HTML: usa solo Markdown")
    return problems


def check_article(result, kind, n_images):
    """Elenco dei difetti di `result` (già passato da `normalize`); vuoto se è valido."""
    problems = []
    title, excerpt, body = result.get("title"), result.get("excerpt"), result.get("body")
    if not isinstance(title, str) or not title.strip():
        problems.append("manca il titolo")
    elif len(title.strip()) > TITLE_LIMIT:
        problems.append(f"il titolo supera {TITLE_LIMIT} caratteri")
    if not isinstance(excerpt, str) or not excerpt.strip():
        problems.append("manca l'estratto")
    elif len(excerpt.strip()) > EXCERPT_LIMIT:
        problems.append(f"l'estratto supera {EXCERPT_LIMIT} caratteri")

    cover = result.get("cover_index")
    if cover is not None and not _valid_index(cover, n_images):
        problems.append(f"cover_index {cover!r} non valido (le immagini sono {n_images})")

    if not isinstance(body, str) or not body.strip():
        problems.append("manca il testo")
    else:
        problems += _check_body(body, kind, n_images)

    if kind == "collab":
        partner = result.get("partner")
        if not isinstance(partner, dict):
            problems.append("mancano i dati del partner")
        else:
            if not isinstance(partner.get("nome"), str) or not partner["nome"].strip():
                problems.append("manca il nome del partner")
            description = partner.get("descrizione")
            if not isinstance(description, str) or not description.strip():
                problems.append("manca la descrizione del partner")
            elif len(description.strip()) > DESCRIPTION_LIMIT:
                problems.append(f"la descrizione del partner supera {DESCRIPTION_LIMIT} caratteri")
            if partner.get("url") is not None and not _valid_url(partner["url"]):
                problems.append(f"l'indirizzo del partner non è valido: {partner['url']!r}")
            logo = partner.get("logo_index")
            if logo is None:
                problems.append("manca l'immagine del logo del partner (logo_index): la scheda del sito lo richiede")
            elif not _valid_index(logo, n_images):
                problems.append(f"logo_index {logo!r} non valido (le immagini sono {n_images})")
            elif logo == cover:
                problems.append("copertina e logo sono la stessa immagine")
    return problems


def link_warnings(body, brief):
    """Indirizzi del testo che non compaiono nel brief né tra i fatti configurati (solo avviso: l'AI potrebbe inventarli)."""
    known = brief or ""
    allowed = [config.TELEGRAM_GROUP_URL] + [f"https://t.me/{h.lstrip('@')}" for h in config.ARTICLE_CONTACTS]
    warnings = []
    for url in _LINK.findall(body or ""):
        url = url.strip()
        if url and not url.startswith("img:") and url not in known and url not in allowed:
            warnings.append(url)
    return warnings


def generate_valid(provider, kind, brief, images, article_date=None, cover=None, logo=None, revise=None,
                   attempts=ATTEMPTS):
    """Chiede l'articolo all'AI e lo ricontrolla; riprova (fino a `attempts`) passando i difetti trovati.

    Solleva AIQuotaError subito, ArticleError se dopo tutti i tentativi restano difetti.
    """
    system = prompts.build_system_prompt()
    schema = article_schema(kind)
    problems = None
    for attempt in range(1, attempts + 1):
        prompt = prompts.build_prompt(kind, brief, len(images), article_date, cover, logo, revise, problems)
        try:
            result = provider.generate(system, prompt, images, schema)
        except AIQuotaError:
            raise
        except AIError as e:
            problems = [f"risposta non utilizzabile: {e}"]
            logger.warning("Tentativo %d/%d: %s", attempt, attempts, problems[0])
            continue
        result = normalize(result, kind, cover, logo)
        problems = check_article(result, kind, len(images))
        if not problems:
            for url in link_warnings(result["body"], brief):
                logger.warning("Link nel testo non presente nel brief: %s", url)
            return result
        logger.warning("Tentativo %d/%d: %d difetti: %s", attempt, attempts, len(problems), "; ".join(problems))
    raise ArticleError(problems)
