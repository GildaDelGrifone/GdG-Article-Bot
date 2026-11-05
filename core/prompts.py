import re

from core import config

# Estratto (accorciato) del corpo di "collaborazione-con-aces-games" sul sito, con le immagini come img:N.
# Serve solo come esempio di tono e struttura: l'AI non deve copiarne i contenuti.
COLLAB_STYLE_EXAMPLE = """\
Siamo entusiasti di annunciare una nuova, fantastica collaborazione che andrà ad arricchire le serate di gioco della nostra associazione!

La **Gilda del Grifone** ha infatti stretto una partnership con **Aces Games**, casa editrice italiana nota per i suoi giochi di ruolo dal design unico, meccaniche innovative e ambientazioni fuori dagli schemi.

Grazie a questa collaborazione, i ragazzi di **Aces Games** ([Negozio Ufficiale](https://acesgames.it)) ci hanno inviato una fornitura speciale dei loro titoli di punta. I manuali sono già stati inseriti nella nostra **Ludoteca** e sono pronti per essere sfogliati, studiati e, soprattutto, giocati da tutti i soci durante le nostre serate.

Ecco nel dettaglio i nuovi titoli che potrete intavolare da noi:

### La prima trilogia di VHS (Very Horror Stories)

![Le scatole a forma di videocassetta di VHS](img:2)

Pronti a tornare negli anni ’70, ’80 e ’90? _VHS_ è un gioco di ruolo horror che vi catapulta direttamente nelle atmosfere dei classici film slasher e mostruosi dell’epoca. Preparate i popcorn e cercate di sopravvivere fino ai titoli di coda.

### Heavy Sugar

![Il manuale base di Heavy Sugar](img:3)

Se preferite atmosfere distopiche e cariche di elettricità, _Heavy Sugar_ è il titolo che fa per voi: una metropoli retro-futuristica in stile “teslapunk” in cui impersonate ribelli pronti a sovvertire il controllo della città.

### Come provarli?

Tutti i manuali sono già a vostra disposizione in sede. I giochi della Ludoteca sono a libero accesso per tutti i soci: potete consultarli per preparare le vostre avventure o usarli direttamente per le sessioni in associazione. Prenotate il vostro tavolo tramite il nostro bot Telegram **Grifone Scriba** e venite a scoprire i mondi di Aces Games.

Un ringraziamento speciale da parte di tutto il Direttivo ad Aces Games per il supporto alla nostra realtà ludica. Ci vediamo al tavolo!
"""

SYSTEM_PROMPT = """\
You write articles for the website of "Gilda del Grifone", a tabletop games association \
(board games and role-playing games) based in Turin, Italy. Readers are members and curious \
newcomers. Write in Italian, in a warm, enthusiastic and welcoming tone, addressing the \
readers in the plural ("voi"). Use Markdown only (no HTML, no MDX, no front matter).

Hard rules:
- Use ONLY facts contained in the brief and visible in the images. Never invent dates, \
places, prices, names, quotes, numbers or links. If a useful detail is missing, leave it out.
- The site already shows the title, the date and the cover image above the body: the body must \
NOT start with a heading, NOT repeat the title and NOT include the cover image.
- Use Markdown links [text](url) for every URL, never a bare URL. Only use URLs from the brief \
or from the facts below.
- Images are numbered from 1 in the order they were sent. Insert an image with \
![alt text](img:N) on its own line, where N is its number. Never write real image URLs. \
Use each image at most once. Write a meaningful Italian alt text describing what you actually see.
- Put **Gilda del Grifone** in bold the first time it appears.
- Bold only for key facts (dates, names, places). No emojis except the final 🦅.

Facts about the association (use them in the closing when relevant):
{facts}

Return ONLY a JSON object (no text around it) with the keys described in the task."""

NEWS_TASK = """\
Write a NEWS article from the brief below.

Length: 300-600 words. Layout, in this order:
1. One introduction paragraph, no heading: what happened or what is coming, why it matters.
2. Two to four sections, each starting with "## <short heading>" (or "### <short heading>"), with \
headings and content taken from the brief. Place the images inside the sections where they fit best, \
each followed by one short italic caption line.
3. A line containing only ---
4. A closing paragraph: how to join or find out more (Telegram group, address and opening hours \
if relevant, the contacts for proposals) ending with the 🦅 emoji.

JSON keys:
- "title": the page title, max 80 characters, no trailing period.
- "excerpt": a plain-text summary of at most 160 characters.
- "cover_index": the number of the image that works best as the cover (the cover is NOT shown in the \
body, so do not place it with img:N), or null if there are no images.
- "body": the Markdown body."""

COLLAB_TASK = """\
Write an article announcing a COLLABORATION between Gilda del Grifone and a partner, and the \
data for the partner card shown on the site's "Collaborazioni" page.

Length: 300-600 words. Required elements, in this order (headings are NOT fixed):
1. One introduction paragraph, no heading, announcing the collaboration with the **partner name** in bold.
2. At least two sections ("## <heading>" or "### <heading>") chosen from the brief: for example one per \
product or per benefit for members. Place photos (not the logo) inside the sections where they fit.
3. A section on how to take advantage of it, with a link [Visita <Partner>](url) when the brief gives a URL.
4. A thank-you to the partner.
5. A line containing only ---
6. A closing paragraph ending with the 🦅 emoji.

Match the tone and structure of this real article, NOT its content. Do not reuse its sentences or stock phrases \
(for example its opening, its thank-you or its closing line): write fresh wording based only on the brief:
-----
{style_example}-----

JSON keys:
- "title": max 80 characters (e.g. "La Gilda incontra <Partner>").
- "excerpt": plain-text summary, at most 160 characters.
- "cover_index": best image for the cover (not placed in the body), or null.
- "body": the Markdown body.
- "partner": an object with the keys "nome" (the partner's name), "url" (its website from the brief, or null), \
"descrizione" (one or two sentences, max 200 characters, plain text, describing the partner for a card, \
with no marketing claims that are not in the brief) and "logo_index" (the number of the image that is the \
partner's logo, or null if none). The logo image is never placed in the body."""

REVISE_ADDENDUM = """\
You already wrote the article below. Apply the admin's feedback and return the full JSON again.
Change only what the feedback asks for; keep everything else (structure, image placement, facts).

PREVIOUS BODY:
{last_body}

FEEDBACK:
{feedback}"""

FIX_ADDENDUM = """\
Your previous answer had these problems. Return the full JSON again with all of them fixed:
{problems}"""


def _fill(template, **values):
    """Sostituisce i segnaposto {nome} senza interpretare le altre graffe (il testo può contenerne)."""
    return re.sub(r"\{(\w+)\}", lambda match: str(values.get(match.group(1), match.group(0))), template)


def _contacts_as_links():
    return ", ".join(f"[{handle}](https://t.me/{handle.lstrip('@')})" for handle in config.ARTICLE_CONTACTS)


def build_facts():
    facts = []
    if config.ASSOCIATION_ADDRESS:
        facts.append(f"- Address: {config.ASSOCIATION_ADDRESS}")
    if config.OPENING_HOURS:
        facts.append(f"- Opening: {config.OPENING_HOURS}")
    if config.TELEGRAM_GROUP_URL:
        facts.append(f"- Telegram group: {config.TELEGRAM_GROUP_URL}")
    if config.ARTICLE_CONTACTS:
        facts.append(f"- Contacts to propose initiatives: {_contacts_as_links()}")
    if config.BOOKING_BOT_NAME:
        facts.append(f'- Tables are booked through the Telegram bot "{config.BOOKING_BOT_NAME}" '
                     "(mention it only when relevant)")
    return "\n".join(facts) or "- (none)"


def build_system_prompt():
    return _fill(SYSTEM_PROMPT, facts=build_facts())


def build_prompt(kind, brief, n_images, article_date=None, cover=None, logo=None, revise=None, problems=None):
    """Il prompt utente per `kind` ("news" o "collab").

    `cover` / `logo`: immagini scelte dall'admin (l'AI non le inserisce nel testo). `revise`: (testo precedente, indicazioni).
    `problems`: elenco di difetti della risposta precedente da correggere.
    """
    task = _fill(COLLAB_TASK, style_example=COLLAB_STYLE_EXAMPLE) if kind == "collab" else NEWS_TASK
    parts = [task, f"Number of images: {n_images}."]
    if article_date:
        parts.append(f"Article date: {article_date}.")
    if cover:
        parts.append(f"The admin chose image {cover} as the cover: use {cover} as cover_index and do not place it in the body.")
    if logo and kind == "collab":
        parts.append(f"The admin chose image {logo} as the partner's logo: use {logo} as logo_index and do not place it in the body.")
    parts.append("BRIEF:\n" + (brief or "").strip())
    if revise:
        last_body, feedback = revise
        parts.append(_fill(REVISE_ADDENDUM, last_body=last_body, feedback=feedback))
    if problems:
        parts.append(_fill(FIX_ADDENDUM, problems="\n".join(f"- {problem}" for problem in problems)))
    return "\n\n".join(parts)
