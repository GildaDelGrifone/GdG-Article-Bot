"""Guarda, correggi o rigenera una bozza già creata (sul branch del sito), senza pubblicarla.

Esempi:
    .venv/bin/python scripts/edit_draft.py show 12                     # articolo e scheda come sono su GitHub
    .venv/bin/python scripts/edit_draft.py pull 12                     # li scrive in drafts/12/ (articolo.md, scheda.md)
    #   ...correggi i file...
    .venv/bin/python scripts/edit_draft.py push 12                     # controlla e rimanda su GitHub
    .venv/bin/python scripts/edit_draft.py revise 12 --feedback "più breve, aggiungi l'indirizzo"   # rigenera con l'AI

`push` applica le stesse regole dell'AI (sezioni, riga ---, chiusura con 🦅, immagini, lunghezze) e, se cambi `date`,
rinomina il file dell'articolo. `revise` riscrive il testo con AI_PROVIDER partendo da quello attuale.
La bozza si indica con il numero o con lo slug.

Codici di uscita: 0 ok, 1 errore, 2 argomenti o impostazioni non validi.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, db  # noqa: E402
from core.ai.base import AIQuotaError  # noqa: E402
from core.pipeline import PipelineError, SettingsError, pull_draft, push_draft, revise_article, show_draft  # noqa: E402

DEFAULT_FOLDER = "drafts"


def build_parser():
    parser = argparse.ArgumentParser(description="Guarda, correggi o rigenera la bozza di un articolo.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("show", "stampa i file della bozza così come sono su GitHub"),
                       ("pull", "scrive i file della bozza in una cartella per correggerli"),
                       ("push", "controlla i file corretti e li scrive sulla bozza"),
                       ("revise", "rigenera il testo con l'AI secondo le tue indicazioni")):
        command = sub.add_parser(name, help=text)
        command.add_argument("reference", help="numero (id) o slug della bozza")
        if name in ("pull", "push"):
            command.add_argument("--dir", help=f"cartella dei file (default {DEFAULT_FOLDER}/<id>)")
        if name == "revise":
            command.add_argument("--feedback", required=True, help="cosa cambiare")
            command.add_argument("--provider", choices=config.AI_PROVIDERS, help="AI da usare (default: AI_PROVIDER)")
    return parser


def render_files(files):
    """File della bozza nel formato dei dry run: ogni file preceduto da `<!-- FILE: percorso sul sito -->`."""
    parts = []
    for path, text in files:
        parts += [f"<!-- FILE: {path} -->", text.rstrip("\n"), ""]
    return "\n".join(parts)


def main(argv=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    args = build_parser().parse_args(argv)
    db.init_db()
    article = db.find_article(args.reference)
    if not article:
        print(f"Bozza '{args.reference}' non trovata", file=err)
        return 1
    folder = getattr(args, "dir", None) or os.path.join(DEFAULT_FOLDER, str(article["id"]))
    if args.command == "revise":
        config.AI_PROVIDER = args.provider or config.AI_PROVIDER
    try:
        if args.command == "show":
            print(render_files(show_draft(article["id"])), file=out, end="")
        elif args.command == "pull":
            for path in pull_draft(article["id"], folder):
                print(f"Scritto {path}", file=out)
        elif args.command == "push":
            print(f"Bozza #{article['id']} aggiornata: {push_draft(article['id'], folder)}", file=out)
        else:
            draft = revise_article(article["id"], args.feedback, provider=None)
            print(f"Bozza #{article['id']} rigenerata: {draft.draft_url}", file=out)
    except SettingsError as e:
        print(str(e), file=err)
        return 2
    except PipelineError as e:
        print(f"Errore: {e}", file=err)
        return 1
    except AIQuotaError as e:
        print(f"Quota o crediti dell'AI esauriti: {e}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
