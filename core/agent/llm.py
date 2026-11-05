"""Interfaccia comune dei modelli con strumenti (tool calling) usati dall'agente di /scrivi.

La conversazione si conserva nel formato **nativo** del provider (dizionari serializzabili in JSON) e si può solo
aggiungere in coda: così i blocchi di ragionamento di Claude e le firme dei pensieri di Gemini tornano al modello
identici a come li ha prodotti. Ogni adattatore sa creare i tre tipi di messaggio e fare un passo.
"""
from dataclasses import dataclass, field

from core import config


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # schema JSON di un oggetto: {"type": "object", "properties": {...}, "required": [...]}


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    call: ToolCall
    content: str
    is_error: bool = False


@dataclass
class Step:
    message: dict  # il messaggio dell'assistente nel formato nativo, da aggiungere alla cronologia
    text: str  # il testo da mostrare all'utente (può essere vuoto se ci sono chiamate)
    calls: list  # le ToolCall richieste, anche più di una nello stesso passo


class AgentLLM:
    """Un modello con strumenti. `images` è una lista di (numero, byte, mime)."""

    name = ""
    model = ""

    def user_message(self, text, images):
        raise NotImplementedError

    def step(self, system, history, tools):
        """Una chiamata al modello con la cronologia `history`; restituisce uno Step. Errori: AIError / AIQuotaError."""
        raise NotImplementedError

    def tool_results_message(self, results):
        """Il messaggio con i risultati (ToolResult) delle chiamate dell'ultimo passo."""
        raise NotImplementedError


def get_agent_llm(name=None):
    """L'adattatore del provider dell'agente (AGENT_PROVIDER o, se manca, AI_PROVIDER)."""
    name = name or config.agent_provider()
    if name == "claude":
        from core.agent.claude_llm import ClaudeAgentLLM
        return ClaudeAgentLLM()
    from core.agent.gemini_llm import GeminiAgentLLM
    return GeminiAgentLLM()
