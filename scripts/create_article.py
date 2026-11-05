"""Crea la bozza di un articolo (news o collaborazione) dal brief e dalle immagini.

Esempi:
    .venv/bin/python scripts/create_article.py --kind news --brief data/briefs/festa.md \\
        --images foto1.jpg foto2.jpg --dry-run --output drafts/festa.md
    .venv/bin/python scripts/create_article.py --kind collab --brief data/briefs/partner.md \\
        --images logo.png foto.jpg --logo 1

Codici di uscita: 0 ok, 1 errore della pipeline, 2 argomenti o impostazioni non validi.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, db  # noqa: E402
from core.ai.base import AIQuotaError  # noqa: E402
from core.pipeline import PipelineError, SettingsError, create_article  # noqa: E402
from core.site import content  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="Crea la bozza di un articolo per il sito della Gilda del Grifone.")
    parser.add_argument("--kind", choices=content.KINDS, required=True, help="news o collab (collaborazione)")
    parser.add_argument("--brief", required=True, help="file di testo con il brief (UTF-8), oppure - per stdin")
    parser.add_argument("--images", nargs="*", default=[], metavar="IMG", help="immagini, numerate da 1 nell'ordine dato")
    parser.add_argument("--cover", type=int, help="numero dell'immagine di copertina (altrimenti sceglie l'AI)")
    parser.add_argument("--logo", type=int, help="numero dell'immagine del logo del partner (solo collab)")
    parser.add_argument("--order", type=int, help="ordine della scheda partner (default 100)")
    parser.add_argument("--date", help="data dell'articolo GG-MM-AAAA (default oggi)")
    parser.add_argument("--provider", choices=config.AI_PROVIDERS, help="AI da usare (default: AI_PROVIDER del .env)")
    parser.add_argument("--dry-run", action="store_true", help="non carica su R2 e non scrive su GitHub")
    parser.add_argument("--output", help="con --dry-run: scrive il risultato in questo file invece che a schermo")
    return parser


def read_brief(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def render_dry_run(draft):
    """Il testo da mostrare/salvare per un dry run: articolo ed eventuale scheda, ciascuno con il suo percorso nel sito."""
    parts = [f"<!-- FILE: {content.news_path(draft.date_iso, draft.slug)} -->", draft.markdown.rstrip("\n")]
    if draft.card_markdown:
        parts += ["", f"<!-- FILE: {content.card_path(draft.card_slug)} -->", draft.card_markdown.rstrip("\n")]
    return "\n".join(parts) + "\n"


def main(argv=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.output and not args.dry_run:
        print("--output si usa solo con --dry-run", file=err)
        return 2
    try:
        brief = read_brief(args.brief)
    except OSError as e:
        print(f"Impossibile leggere il brief: {e}", file=err)
        return 2

    if args.provider:
        config.AI_PROVIDER = args.provider  # vale per questo processo: anche il controllo delle chiavi lo usa
    try:
        if not args.dry_run:
            db.init_db()
        draft = create_article(
            args.kind, brief, args.images, cover=args.cover, logo=args.logo, order=args.order, date=args.date,
            dry_run=args.dry_run,
        )
    except SettingsError as e:
        print(str(e), file=err)
        return 2
    except PipelineError as e:
        print(f"Errore: {e}", file=err)
        return 1
    except AIQuotaError as e:
        print(f"Quota o crediti dell'AI esauriti: {e}", file=err)
        return 1

    if args.dry_run:
        text = render_dry_run(draft)
        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"Dry run: scritto {args.output} (titolo: {draft.title})", file=out)
        else:
            print(text, file=out, end="")
        return 0

    print(f"Bozza #{draft.id} creata: {draft.title}", file=out)
    print(f"Bozza:         {draft.draft_url}", file=out)
    print(f"Pagina finale: {draft.public_url}", file=out)
    print(f"Per pubblicare: .venv/bin/python scripts/publish_article.py {draft.id}", file=out)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
