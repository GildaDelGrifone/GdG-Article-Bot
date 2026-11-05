"""Scarica testo, data e foto di un messaggio di un canale Telegram pubblico, per usarli in un brief.

Esempio:
    .venv/bin/python scripts/fetch_telegram.py https://t.me/Gildadelgrifonetorino/2347
    .venv/bin/python scripts/fetch_telegram.py https://t.me/Gildadelgrifonetorino/2347 --out data/telegram/mm

Salva `post.md` e le foto (`img_1.jpg`...) nella cartella (default `data/telegram/<canale>-<numero>/`) e stampa la data
in formato GG-MM-AAAA, pronta per `create_article.py --date`.

Codici di uscita: 0 ok, 1 errore, 2 argomenti non validi.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config  # noqa: E402
from core.pipeline import PipelineError  # noqa: E402
from core.telegram_post import fetch_post, parse_url  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="Scarica un messaggio di un canale Telegram pubblico.")
    parser.add_argument("url", help="link del messaggio, per esempio https://t.me/canale/123")
    parser.add_argument("--out", help="cartella di destinazione (default data/telegram/<canale>-<numero>)")
    return parser


def main(argv=None, out=None, err=None, get=None):
    out, err = out or sys.stdout, err or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        channel, number = parse_url(args.url)
    except ValueError as e:
        print(str(e), file=err)
        return 2
    folder = args.out or os.path.join(config.DATA_DIR, "telegram", f"{channel}-{number}")
    kwargs = {"get": get} if get else {}
    try:
        post = fetch_post(args.url, folder, **kwargs)
    except PipelineError as e:
        print(f"Errore: {e}", file=err)
        return 1

    date = "-".join(reversed(post["date_iso"].split("-"))) if post["date_iso"] else "sconosciuta"
    print(f"Cartella: {folder}", file=out)
    print(f"Data (per --date): {date}", file=out)
    print(f"Foto: {len(post['photo_files'])}" + (" (il messaggio ha anche un video, non scaricato)" if post["has_video"] else ""),
          file=out)
    for path in post["photo_files"]:
        print(f"  {path}", file=out)
    if post["links"]:
        print("Link nel testo:", file=out)
        for link in post["links"]:
            print(f"  {link}", file=out)
    print("--- testo ---", file=out)
    print(post["text"], file=out)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
