import base64
import logging
import re

import anthropic

from core import config
from core.ai.base import AIError, AIQuotaError, Provider, parse_json

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000
EFFORT = "medium"
# `effort` esiste solo dai modelli 4.6 in poi; con modelli più vecchi non va inviato
_EFFORT_MODELS = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-(5|4-[678])")

_client_instance = None


def _client():
    """Creato al primo uso, così importare il modulo non richiede la chiave API."""
    global _client_instance
    if _client_instance is None:
        _client_instance = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client_instance


def _is_credit_error(err):
    text = str(err).lower()
    return "credit balance" in text or "billing" in text


class ClaudeProvider(Provider):
    name = "claude"

    def generate(self, system, prompt, images, schema):
        content = []
        for number, (data, mime) in enumerate(images, start=1):
            content.append({"type": "text", "text": f"Immagine {number}:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(data).decode("ascii")},
            })
        content.append({"type": "text", "text": prompt})

        # Niente tool_choice forzato (rifiutato dai modelli 5.x): il JSON arriva con output_config.format.
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if _EFFORT_MODELS.match(config.CLAUDE_MODEL):
            output_config["effort"] = EFFORT
        try:
            response = _client().messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config=output_config,
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
        text = next((block.text for block in response.content if block.type == "text"), "")
        return parse_json(text)
