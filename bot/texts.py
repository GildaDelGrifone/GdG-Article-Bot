"""Tutti i testi che il bot mostra su Telegram (messaggi in HTML, pulsanti, descrizioni dei comandi)."""
import html

UNAUTHORIZED = "Non sei autorizzato."

KIND_LABELS = {"news": "News", "collab": "Collaborazione", "card": "Scheda partner"}

BUTTON_PUBLISH = "Pubblica sul sito"
BUTTON_REVISE = "✏️ Rivedi"
BUTTON_DISCARD = "🗑️ Scarta"

COMMAND_DESCRIPTIONS = [
    ("news", "Scrivi una news per il sito"),
    ("collab", "Scrivi un articolo di collaborazione"),
    ("scrivi", "Scrivi articoli chattando con l'assistente"),
    ("fine", "Chiudi l'assistente e azzera la cronologia"),
    ("genera", "Genera la bozza con foto e testo ricevuti"),
    ("annulla", "Annulla la bozza in corso"),
    ("bozze", "Mostra le bozze in attesa"),
    ("help", "Come funziona il bot"),
]

HELP = (
    "👋 <b>GdG Article Bot</b>: scrive gli articoli del sito della Gilda con l'AI.\n\n"
    "• /news: una news\n"
    "• /collab: una collaborazione (articolo + scheda partner)\n\n"
    "Dopo il comando mandami le <b>immagini</b> (come file per non perdere qualità) e il <b>testo</b> con le idee, "
    "anche in più messaggi. Poi /genera.\n\n"
    "Righe speciali nel testo:\n"
    "<code>copertina: 2</code> immagine di copertina\n"
    "<code>logo: 3</code> logo del partner (collaborazioni)\n"
    "<code>ordine: 10</code> posizione della scheda partner\n\n"
    "/annulla ferma tutto, /bozze mostra le bozze in attesa.\n\n"
    "💬 <b>/scrivi</b>: in alternativa parla con l'assistente in chat. Mandagli note, link di post del canale e foto: "
    "ti fa le domande che mancano, crea le bozze, le controlla e te le mostra con i pulsanti. "
    "Pubblica solo il pulsante. <b>/fine</b> (o /reset) azzera la conversazione: usalo quando hai finito, "
    "così non si consumano token inutilmente."
)

START_NEWS = (
    "📰 <b>Nuova news</b>\nMandami le immagini e il testo con le idee dell'articolo, poi scrivi /genera. "
    "/annulla per fermarti."
)
START_COLLAB = (
    "🤝 <b>Nuova collaborazione</b>\nMandami il <b>logo del partner</b>, le altre immagini e il testo "
    "(chi è il partner, cosa ottengono i soci, link), poi scrivi /genera.\n"
    "Indica quale immagine è il logo con una riga <code>logo: N</code> (altrimenti lo cerca l'AI). /annulla per fermarti."
)
NO_ACTIVE_DRAFT = "Nessuna bozza in corso: usa /news o /collab."


def format_image_received(number):
    return f"📷 Immagine {number} ricevuta"


NOT_AN_IMAGE = "Mandami solo immagini (JPG, PNG, WEBP) o testo."
IMAGE_DOWNLOAD_ERROR = "❌ Non sono riuscito a scaricare l'immagine, riprova."
NEED_TEXT = "Mi serve almeno un messaggio di testo con le idee dell'articolo."
NEED_LOGO_IMAGE = "Per una collaborazione mandami almeno l'immagine del logo del partner."
GENERATING = "⏳ Sto scrivendo l'articolo (ci vuole circa un minuto)..."
GENERIC_ERROR = "❌ Errore imprevisto, controlla i log del bot."
CANCELLED = "Bozza in corso annullata."
NO_DRAFTS = "Nessuna bozza in attesa."


def format_error(message):
    return f"❌ {html.escape(str(message))}"


def format_quota_alert(provider, detail):
    return f"🚨 Errore {html.escape(provider)} AI (quota o crediti esauriti):\n{html.escape(str(detail))}"


def format_draft_card(kind, title, draft_url=None, public_url=None, excerpt=None, card_slug=None, draft_id=None):
    lines = [f"📝 <b>{html.escape(title)}</b>"]
    lines.append(f"{KIND_LABELS.get(kind, kind)}" + (f" · bozza #{draft_id}" if draft_id else ""))
    if excerpt:
        lines += ["", html.escape(excerpt)]
    lines.append("")
    if draft_url:
        lines.append(f'🔗 <a href="{html.escape(draft_url, quote=True)}">Vedi bozza</a>')
    if public_url:
        lines.append(f'🌐 <a href="{html.escape(public_url, quote=True)}">Pagina finale</a> (online dopo la pubblicazione)')
    if card_slug:
        lines.append(f"🃏 Scheda partner: <code>{html.escape(card_slug)}</code>")
    return "\n".join(lines)


def format_published_suffix(public_url):
    return (f'\n\n✅ <b>PUBBLICATO</b>\n{html.escape(public_url)}\n'
            "Sarà online dopo il deploy di Cloudflare (circa 1-2 minuti).")


DISCARDED_SUFFIX = "\n\n🗑️ <b>BOZZA SCARTATA</b>"
ALREADY_PUBLISHED_SUFFIX = "\n\n⚠️ L'articolo era già stato pubblicato: non si può scartare."


def format_action_error(message):
    return f"\n\n❌ {html.escape(str(message))}"


def format_revise_prompt(draft_id):
    return f"✏️ Cosa vuoi cambiare nella bozza #{draft_id}? Rispondi a questo messaggio con le indicazioni."


REVISE_MARKER = "Cosa vuoi cambiare nella bozza #"
REVISING = "⏳ Aggiorno la bozza..."
REVISED = "✅ Bozza aggiornata."
POPUP_WORKING = "⏳ Un attimo..."


# --- assistente (/scrivi) ---------------------------------------------------------------------------------------------
START_AGENT = (
    "💬 <b>Assistente articoli</b>\nScrivimi cosa vuoi pubblicare: puoi mandare note, link di post del canale "
    "(<code>https://t.me/canale/123</code>) e foto, anche in più messaggi. Ti faccio le domande che mancano, creo le "
    "bozze e te le mostro qui con i pulsanti: pubblica solo il pulsante.\n"
    "Quando hai finito scrivi /fine: azzera la conversazione (le bozze restano)."
)
AGENT_ALREADY_OPEN = "💬 La sessione è già aperta, continua pure. /fine per azzerarla e ripartire da zero."
AGENT_ENDED = "🧹 Sessione azzerata: cronologia e foto cancellate (le bozze create restano). Per ricominciare: /scrivi."
AGENT_NO_SESSION = "Nessuna sessione aperta: scrivi /scrivi per iniziare."
AGENT_SESSION_RESTARTED = (
    "ℹ️ La conversazione precedente era troppo lunga o ferma da molto: riparto da zero (le bozze in attesa restano)."
)


def format_missing_settings(names):
    return "❌ L'assistente non è configurato, mancano nel file .env: " + html.escape(", ".join(names))


def format_live_ok(url):
    return f"✅ Online: {html.escape(url)}"


def format_live_missing(url):
    return f"⚠️ La pagina non è ancora raggiungibile: {html.escape(url)}\nIl deploy può richiedere qualche minuto in più, riprova a controllare tra poco."
