from core import config
from core.ai.base import Provider
from core.ai.claude import ClaudeProvider
from core.ai.gemini import GeminiProvider

_PROVIDERS = {"gemini": GeminiProvider, "claude": ClaudeProvider}


def get_provider(name=None) -> Provider:
    """Il provider scelto da AI_PROVIDER (o da `name`); un valore sconosciuto dà Gemini."""
    return _PROVIDERS.get(name or config.AI_PROVIDER, GeminiProvider)()
