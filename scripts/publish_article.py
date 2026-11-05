"""Pubblica o scarta una bozza creata da create_article.py o dal bot.

Esempi:
    .venv/bin/python scripts/publish_article.py --list
    .venv/bin/python scripts/publish_article.py 12            # pubblica la bozza 12 (o per slug)
    .venv/bin/python scripts/publish_article.py 12 --discard  # la scarta

Codici di uscita: 0 ok, 1 errore, 2 argomenti o impostazioni non validi.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, db  # noqa: E402
from core.pipeline import PipelineError, SettingsError, discard, publish  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="Pubblica o scarta la bozza di un articolo.")
    parser.add_argument("reference", nargs="?", help="numero (id) o slug della bozza")
    parser.add_argument("--discard", action="store_true", help="scarta la bozza invece di pubblicarla")
    parser.add_argument("--list", action="store_true", help="elenca le bozze in attesa")
    return parser


def main(argv=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    db.init_db()

    if args.list:
        drafts = db.list_drafts()
        if not drafts:
            print("Nessuna bozza in attesa.", file=out)
        for draft in drafts:
            print(f"#{draft['id']}  {draft['kind']:<6} {draft['slug']}  {draft['title']}  {draft['draft_url']}", file=out)
        return 0
    if not args.reference:
        parser.print_usage(err)
        print("Indica la bozza (id o slug) oppure usa --list", file=err)
        return 2

    article = db.find_article(args.reference)
    if not article:
        print(f"Bozza '{args.reference}' non trovata", file=err)
        return 1
    try:
        if args.discard:
            outcome = discard(article["id"])
            print("Era già stata pubblicata: non si può scartare." if outcome == "published"
                  else f"Bozza #{article['id']} scartata.", file=out)
        else:
            url = publish(article["id"])
            print(f"Bozza #{article['id']} pubblicata: {url}", file=out)
            print("Il sito sarà online dopo il deploy di Cloudflare (circa 1-2 minuti).", file=out)
    except SettingsError as e:
        print(str(e), file=err)
        return 2
    except PipelineError as e:
        print(f"Errore: {e}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
