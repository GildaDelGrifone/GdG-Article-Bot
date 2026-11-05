import base64
import logging

import anthropic

from core import config
from core.agent.llm import AgentLLM, Step, ToolCall
from core.ai.base import AIError, AIQuotaError
from core.ai.claude import _EFFORT_MODELS, EFFORT, MAX_TOKENS, _client, _is_credit_error

logger = logging.getLogger(__name__)


class ClaudeAgentLLM(AgentLLM):
    """Agente su Claude: ciclo manuale con `messages.create`.

    Il prompt di sistema e gli strumenti non cambiano per tutta la sessione e la cronologia cresce solo in coda, come
    richiede la verifica dei blocchi di ragionamento (preserved thinking). Niente `tool_choice` forzato (i modelli 5.x
    lo rifiutano) né parametri di campionamento.
    """

    name = "claude"

    def __init__(self):
        self.model = config.AGENT_MODEL or config.CLAUDE_MODEL

    def user_message(self, text, images):
        content = []
        for number, data, mime in images:
            content.append({"type": "text", "text": f"Immagine {number}:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                       "data": base64.standard_b64encode(data).decode("ascii")}})
        content.append({"type": "text", "text": text})
        return {"role": "user", "content": content}

    def step(self, system, history, tools):
        kwargs = {}
        if _EFFORT_MODELS.match(self.model):
            kwargs["output_config"] = {"effort": EFFORT}
        tool_params = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        try:
            response = _client().messages.create(
                model=self.model, max_tokens=MAX_TOKENS, system=system, messages=history, tools=tool_params,
                cache_control={"type": "ephemeral"}, **kwargs,
            )
        except (anthropic.RateLimitError, anthropic.OverloadedError) as e:
            raise AIQuotaError(f"Claude: {e}") from e
        except anthropic.BadRequestError as e:
            if _is_credit_error(e):
                raise AIQuotaError(f"Claude: {e}") from e
            raise AIError(f"Claude: {e}") from e
        except anthropic.APIError as e:
            raise AIError(f"Claude: {e}") from e

        if response.stop_reason == "refusal":
            raise AIError("Claude ha rifiutato la richiesta")
        if response.stop_reason == "max_tokens":
            raise AIError("Claude: risposta troncata (max_tokens)")
        blocks = [block.model_dump(exclude_none=True) for block in response.content]
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        calls = [ToolCall(block.id, block.name, dict(block.input or {})) for block in response.content
                 if block.type == "tool_use"]
        return Step({"role": "assistant", "content": blocks}, text, calls)

    def tool_results_message(self, results):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": result.call.id, "content": result.content,
             **({"is_error": True} if result.is_error else {})}
            for result in results
        ]}
