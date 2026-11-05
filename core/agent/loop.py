"""Il ciclo dell'agente: un messaggio dell'amministratore -> chiamate al modello e agli strumenti -> risposta.

La cronologia è nel formato nativo del provider e si conserva nella tabella `agent_sessions`. Non si accorcia mai a
metà: oltre `AGENT_MAX_MESSAGES` messaggi (o dopo `AGENT_IDLE_HOURS` ore ferma) la sessione riparte da zero, e si può
azzerare a mano con `reset_session`.
"""
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone

from core import config, db
from core.agent import tools as agent_tools
from core.agent.llm import ToolResult, get_agent_llm
from core.agent.prompts import build_system
from core.pipeline import PipelineError
from core.site import storage

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 12000
STEP_LIMIT_TEXT = "Mi sono fermato: troppi passaggi per una sola richiesta. Dimmi come proseguire."


def session_dir(chat_id):
    return os.path.join(config.TMP_DIR, "agent", str(chat_id))


def reset_session(chat_id):
    """Azzera la sessione dell'agente (cronologia e immagini); le bozze create restano. True se ce n'era una."""
    existed = db.get_agent_session(chat_id) is not None
    db.delete_agent_session(chat_id)
    shutil.rmtree(session_dir(chat_id), ignore_errors=True)
    return existed


def session_status(chat_id):
    """"none" (nessuna sessione), "expired" (troppo lunga o ferma da troppo) o "active"."""
    session = db.get_agent_session(chat_id)
    if session is None:
        return "none"
    return "expired" if is_expired(session) else "active"


def is_expired(session):
    """True se la sessione è troppo lunga, ferma da troppo tempo o di un altro provider/modello."""
    if len(session["messages"]) >= config.AGENT_MAX_MESSAGES:
        return True
    try:
        updated = datetime.strptime(session["updated_at"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc).replace(tzinfo=None) - updated > timedelta(hours=config.AGENT_IDLE_HOURS)


def start_session(chat_id, llm=None):
    """Apre una sessione vuota (azzerando la precedente); restituisce il prompt di sistema."""
    llm = llm or get_agent_llm()
    reset_session(chat_id)
    system = build_system()
    db.save_agent_session(chat_id, llm.name, llm.model, system, [], [])
    return system


def _truncate(text):
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"\n[…tagliato: {len(text) - MAX_RESULT_CHARS} caratteri in più]"


def _execute(tools, context, call):
    try:
        return ToolResult(call, _truncate(agent_tools.run_tool(tools, context, call.name, call.args)))
    except PipelineError as e:
        return ToolResult(call, str(e), is_error=True)
    except ValueError as e:
        return ToolResult(call, str(e), is_error=True)
    except Exception as e:  # uno strumento non deve mai far cadere il ciclo: l'errore torna al modello
        logger.exception("Strumento %s fallito", call.name)
        return ToolResult(call, f"Errore imprevisto: {e}", is_error=True)


def run_turn(chat_id, text, photo_paths=(), llm=None, tools=None):
    """Un turno: il messaggio `text` (con le foto `photo_paths` appena arrivate) e la risposta dell'agente.

    Restituisce `(testo, id_bozze_da_mostrare, numeri_delle_nuove_immagini)`. Blocca: da eseguire in un thread.
    Solleva AIError / AIQuotaError se il modello non risponde.
    """
    llm = llm or get_agent_llm()
    tools = tools or agent_tools.build_tools()
    session = db.get_agent_session(chat_id)
    if session is None or is_expired(session) or (session["provider"], session["model"]) != (llm.name, llm.model):
        start_session(chat_id, llm)
        session = db.get_agent_session(chat_id)
    system, history = session["system"], session["messages"]

    context = agent_tools.AgentContext(chat_id, session_dir(chat_id), session["images"])
    new_numbers = [context.add_image(path) for path in photo_paths]
    pictures = []
    for number in new_numbers:
        path = context.image_paths([number])[0]
        data, mime = storage.vision_copy(path)
        pictures.append((number, data, mime))

    start = len(history)
    history.append(llm.user_message(text, pictures))
    specs = [tool.spec for tool in tools]
    answer = ""
    try:
        for _ in range(config.AGENT_MAX_STEPS):
            step = llm.step(system, history, specs)
            history.append(step.message)
            if step.text:
                answer = step.text
            if not step.calls:
                break
            history.append(llm.tool_results_message([_execute(tools, context, call) for call in step.calls]))
        else:
            answer = (answer + "\n\n" if answer else "") + STEP_LIMIT_TEXT
            # l'ultimo messaggio sono i risultati degli strumenti: il modello deve poter riprendere dal turno dopo
    except Exception:
        # turno interrotto (modello non raggiungibile, quota...): si toglie il turno a metà, così la cronologia resta
        # valida (nessuna chiamata di strumento senza risposta); le immagini arrivate restano nella sessione
        del history[start:]
        db.save_agent_session(chat_id, llm.name, llm.model, system, history, context.images)
        raise
    db.save_agent_session(chat_id, llm.name, llm.model, system, history, context.images)
    return answer, list(context.publish_ids), new_numbers
