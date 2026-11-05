"""Crea la bozza della sola scheda partner (pagina /collaborazioni), senza articolo e senza AI.

Esempio:
    .venv/bin/python scripts/create_card.py --name "Aces Games" --logo logo.png \\
        --article collaborazione-con-aces-games --description "Casa editrice italiana di giochi di ruolo." --dry-run

Poi si pubblica o si scarta come ogni bozza: scripts/publish_article.py <id> [--discard].
Codici di uscita: 0 ok, 1 errore della pipeline, 2 argomenti o impostazioni non validi.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, db  # noqa: E402
from core.pipeline import PipelineError, SettingsError, create_card  # noqa: E402
from core.site import content  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="Crea la bozza della scheda di un partner (solo la scheda, senza articolo).")
    parser.add_argument("--name", required=True, help="nome del partner")
    parser.add_argument("--logo", required=True, help="file immagine del logo")
    parser.add_argument("--description", required=True, help="descrizione, al massimo 200 caratteri")
    parser.add_argument("--article", metavar="SLUG",
                        help="slug dell'articolo news sulla collaborazione (la scheda punta a /news/<slug>/)")
    parser.add_argument("--order", type=int, help="posizione della scheda (default 100)")
    parser.add_argument("--dry-run", action="store_true", help="non carica su R2 e non scrive su GitHub")
    parser.add_argument("--output", help="con --dry-run: scrive il risultato in questo file invece che a schermo")
    return parser


def render_dry_run(draft):
    return f"<!-- FILE: {content.card_path(draft.card_slug)} -->\n{draft.card_markdown.rstrip()}\n"


def main(argv=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.output and not args.dry_run:
        print("--output si usa solo con --dry-run", file=err)
        return 2
    try:
        if not args.dry_run:
            db.init_db()
        draft = create_card(args.name, args.logo, args.description, article=args.article, order=args.order,
                            dry_run=args.dry_run)
    except SettingsError as e:
        print(str(e), file=err)
        return 2
    except PipelineError as e:
        print(f"Errore: {e}", file=err)
        return 1

    if args.dry_run:
        text = render_dry_run(draft)
        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"Dry run: scritto {args.output} (scheda: {draft.title})", file=out)
        else:
            print(text, file=out, end="")
        return 0

    print(f"Bozza #{draft.id} creata: scheda {draft.title}", file=out)
    print(f"Bozza:         {draft.draft_url}", file=out)
    print(f"Pagina finale: {draft.public_url}", file=out)
    print(f"Per pubblicare: .venv/bin/python scripts/publish_article.py {draft.id}", file=out)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
