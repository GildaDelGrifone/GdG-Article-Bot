"""Parla con l'assistente di /scrivi dal terminale: stesso ciclo, stessi strumenti e stesse regole del bot.

Esempi:
    .venv/bin/python scripts/agent_chat.py                      # usa AGENT_PROVIDER / AI_PROVIDER
    .venv/bin/python scripts/agent_chat.py --provider claude    # prova l'altro provider

Comandi nella conversazione: `/foto <file> [<file> ...]` allega immagini al prossimo messaggio, `/fine` azzera la
sessione, `/esci` chiude. Le bozze create sono vere (branch sul sito): non pubblicano nulla, per quello c'è
`scripts/publish_article.py <id>` (o il bot). Usa una sessione a parte (chat 0), separata da quella del bot.

Codici di uscita: 0 ok, 2 impostazioni mancanti.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, db  # noqa: E402
from core.agent import loop  # noqa: E402
from core.agent.llm import get_agent_llm  # noqa: E402
from core.ai.base import AIError  # noqa: E402

CHAT_ID = 0


def build_parser():
    parser = argparse.ArgumentParser(description="Conversazione con l'assistente degli articoli, da terminale.")
    parser.add_argument("--provider", choices=config.AI_PROVIDERS, help="provider dell'agente (default: AGENT_PROVIDER / AI_PROVIDER)")
    parser.add_argument("--model", help="modello dell'agente (default: AGENT_MODEL / quello del provider)")
    return parser


def main(argv=None, read=input, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.provider:
        config.AGENT_PROVIDER = args.provider
    if args.model:
        config.AGENT_MODEL = args.model
    missing = config.missing_settings("agent")
    if missing:
        print("Impostazioni mancanti nel file .env: " + ", ".join(missing), file=err)
        return 2
    db.init_db()
    llm = get_agent_llm()
    if loop.session_status(CHAT_ID) != "active":
        loop.start_session(CHAT_ID, llm)
    print(f"Assistente ({llm.name}, {llm.model}). /foto <file> allega immagini, /fine azzera, /esci chiude.", file=out)

    photos = []
    while True:
        try:
            line = read("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=out)
            return 0
        if not line:
            continue
        if line == "/esci":
            return 0
        if line == "/fine":
            loop.reset_session(CHAT_ID)
            loop.start_session(CHAT_ID, llm)
            photos.clear()
            print("Sessione azzerata.", file=out)
            continue
        if line.startswith("/foto"):
            files = line.split()[1:]
            missing_files = [f for f in files if not os.path.isfile(f)]
            if missing_files:
                print("File non trovati: " + ", ".join(missing_files), file=err)
                continue
            photos += files
            print(f"{len(photos)} immagine/i pronte per il prossimo messaggio.", file=out)
            continue
        try:
            answer, draft_ids, _ = loop.run_turn(CHAT_ID, line, photos, llm=llm)
        except AIError as e:
            print(f"Errore dell'AI: {e}", file=err)
            continue
        photos = []
        print("\n" + answer, file=out)
        for article_id in draft_ids:
            article = db.get_article(article_id)
            print(f"\n[Bozza #{article_id}] {article['title']}\n  bozza: {article['draft_url']}\n  finale: {article['public_url']}"
                  f"\n  pubblica con: .venv/bin/python scripts/publish_article.py {article_id}", file=out)


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
