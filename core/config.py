import logging
import os

from dotenv import load_dotenv

# Carica il file .env (dalla cartella corrente, altrimenti la ricerca standard)
if os.path.exists(".env"):
    load_dotenv(dotenv_path=".env")
else:
    load_dotenv()


def _env_number(name, default, cast):
    """Un numero dall'ambiente; un errore di battitura ripiega sul valore di default invece di fermare il bot."""
    try:
        return cast(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _env_int(name, default):
    return _env_number(name, default, int)


def _env_float(name, default):
    return _env_number(name, default, float)


def _env_choice(name, choices, default):
    """Uno tra `choices` (senza distinzione di maiuscole); altro valore: avviso nel log e default."""
    value = os.getenv(name, default).strip().lower()
    if value in choices:
        return value
    logging.getLogger(__name__).warning("Valore non valido per %s=%r, uso %r", name, value, default)
    return default


def _env_list(name, default):
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _env_log_level(name, default="INFO"):
    """Un livello di log valido; un errore di battitura ripiega sul default."""
    value = os.getenv(name, default).strip().upper()
    return value if value in _LOG_LEVELS else default


# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# --- AI ---
AI_PROVIDERS = ("gemini", "claude")
AI_PROVIDER = _env_choice("AI_PROVIDER", AI_PROVIDERS, "gemini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

# --- Sito (repo GdG-Website) ---
ASTRO_GITHUB_REPO = os.getenv("ASTRO_GITHUB_REPO", "GildaDelGrifone/GdG-Website")
ASTRO_GITHUB_BRANCH = os.getenv("ASTRO_GITHUB_BRANCH", "main")
ASTRO_GITHUB_TOKEN = os.getenv("ASTRO_GITHUB_TOKEN")
ASTRO_BASE_URL = os.getenv("ASTRO_BASE_URL", "https://www.gildadelgrifonetorino.it").rstrip("/")
NEWS_CONTENT_DIR = os.getenv("NEWS_CONTENT_DIR", "src/content/news")
COLLAB_CONTENT_DIR = os.getenv("COLLAB_CONTENT_DIR", "src/content/collaborazioni")

# --- Cloudflare R2 ---
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.getenv("R2_BUCKET", "gdg-media")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "").rstrip("/")

# --- Fatti sull'associazione usati nei prompt ---
ASSOCIATION_ADDRESS = os.getenv("ASSOCIATION_ADDRESS", "Gilda del Grifone, Via Ada Negri 8/A, Torino")
OPENING_HOURS = os.getenv("OPENING_HOURS", "lunedì, mercoledì e venerdì dalle 20:45 alle 24:00")
TELEGRAM_GROUP_URL = os.getenv("TELEGRAM_GROUP_URL", "https://t.me/+U5NEyDvN_fMxZTQ0")
ARTICLE_CONTACTS = _env_list("ARTICLE_CONTACTS", "@Destard,@ManueleAbi")
BOOKING_BOT_NAME = os.getenv("BOOKING_BOT_NAME", "").strip()

# --- Varie ---
LOG_LEVEL = _env_log_level("LOG_LEVEL")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.getenv("DATA_DIR", "data")
DB_PATH = os.path.join(DATA_DIR, "articles.db")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
BRIEFS_DIR = os.path.join(DATA_DIR, "briefs")
TMP_DIR = os.path.join(DATA_DIR, "tmp")

for _folder in (DATA_DIR, LOGS_DIR, BRIEFS_DIR, TMP_DIR):
    os.makedirs(_folder, exist_ok=True)


def _ai_key_name(provider=None):
    return "ANTHROPIC_API_KEY" if (provider or AI_PROVIDER) == "claude" else "GEMINI_API_KEY"


def missing_settings(mode):
    """Nomi delle impostazioni mancanti per `mode`: "dry_run", "cli" o "telegram".

    dry_run: solo la chiave dell'AI scelta. cli: in più R2 e GitHub. telegram: in più token e chat admin.
    """
    names = [_ai_key_name()]
    if mode in ("cli", "telegram"):
        names += ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL",
                  "ASTRO_GITHUB_REPO", "ASTRO_GITHUB_TOKEN"]
    if mode == "telegram":
        names += ["TELEGRAM_BOT_TOKEN", "ADMIN_CHAT_ID"]
    return [name for name in names if not globals().get(name)]
