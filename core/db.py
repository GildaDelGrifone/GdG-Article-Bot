import json
import logging
import sqlite3
from contextlib import contextmanager

from core import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

CREATE_ARTICLES = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    slug TEXT NOT NULL,
    card_slug TEXT,
    branch TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    date_iso TEXT,
    draft_url TEXT,
    public_url TEXT,
    image_keys TEXT DEFAULT '[]',
    image_paths TEXT DEFAULT '[]',
    cover_override INTEGER,
    logo_override INTEGER,
    cover_index INTEGER,
    logo_index INTEGER,
    logo_key TEXT,
    card_order INTEGER,
    brief TEXT,
    last_body TEXT,
    chat_id INTEGER,
    message_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Colonne aggiunte dopo la prima versione: (nome, definizione). init_db() le aggiunge ai database esistenti.
COLUMN_MIGRATIONS = []

JSON_FIELDS = ("image_keys", "image_paths")
EDITABLE_FIELDS = (
    "slug", "card_slug", "branch", "title", "status", "date_iso", "draft_url", "public_url", "image_keys",
    "image_paths", "cover_override", "logo_override", "cover_index", "logo_index", "logo_key", "card_order",
    "brief", "last_body", "chat_id", "message_id",
)


def get_connection():
    """Connessione sqlite3 a config.DB_PATH (letto a ogni chiamata, così test e script possono cambiarlo)."""
    return sqlite3.connect(config.DB_PATH)


@contextmanager
def transaction():
    """Cursore con righe tipo dizionario; commit se va bene, rollback se c'è un errore, chiude sempre la connessione."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn.cursor()
    finally:
        conn.close()


def init_db():
    with transaction() as cur:
        cur.execute(CREATE_ARTICLES)
        existing = {row["name"] for row in cur.execute("PRAGMA table_info(articles)")}
        for name, definition in COLUMN_MIGRATIONS:
            if name not in existing:
                cur.execute(f"ALTER TABLE articles ADD COLUMN {name} {definition}")


def _decode(row):
    if row is None:
        return None
    article = dict(row)
    for field in JSON_FIELDS:
        try:
            article[field] = json.loads(article.get(field) or "[]")
        except ValueError:
            article[field] = []
    return article


def _encode(fields):
    unknown = set(fields) - set(EDITABLE_FIELDS) - {"kind"}
    if unknown:
        raise ValueError(f"Campi sconosciuti: {', '.join(sorted(unknown))}")
    return {key: json.dumps(value) if key in JSON_FIELDS else value for key, value in fields.items()}


def insert_article(kind, **fields):
    """Inserisce una bozza e restituisce il suo id."""
    values = _encode({"kind": kind, **fields})
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    with transaction() as cur:
        cur.execute(f"INSERT INTO articles ({columns}) VALUES ({marks})", tuple(values.values()))
        return cur.lastrowid


def update_article(article_id, **fields):
    values = _encode(fields)
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    with transaction() as cur:
        cur.execute(f"UPDATE articles SET {assignments} WHERE id = ?", (*values.values(), article_id))


def get_article(article_id):
    with transaction() as cur:
        cur.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        return _decode(cur.fetchone())


def find_article(reference):
    """Trova una bozza dal numero (id) o dallo slug; con più articoli con lo stesso slug vince il più recente."""
    reference = str(reference).strip()
    if reference.isdigit():
        return get_article(int(reference))
    with transaction() as cur:
        cur.execute("SELECT * FROM articles WHERE slug = ? ORDER BY id DESC LIMIT 1", (reference,))
        return _decode(cur.fetchone())


def list_drafts():
    with transaction() as cur:
        cur.execute("SELECT * FROM articles WHERE status = 'draft' ORDER BY id DESC")
        return [_decode(row) for row in cur.fetchall()]
