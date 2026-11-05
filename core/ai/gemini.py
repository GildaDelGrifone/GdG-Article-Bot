import logging

from google import genai
from google.genai import types

from core import config
from core.ai.base import AIError, AIQuotaError, Provider, parse_json

logger = logging.getLogger(__name__)

_client_instance = None


def _client():
    """Creato al primo uso, così importare il modulo non richiede la chiave API."""
    global _client_instance
    if _client_instance is None:
        _client_instance = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client_instance


def is_quota_error(err):
    text = str(err).lower()
    return ("429" in text or "prepayment credits are depleted" in text or "quota" in text
            or "resourceexhausted" in text or "resource_exhausted" in text)


class GeminiProvider(Provider):
    name = "gemini"

    def generate(self, system, prompt, images, schema):
        contents = []
        for number, (data, mime) in enumerate(images, start=1):
            contents.append(f"Immagine {number}:")
            contents.append(types.Part.from_bytes(data=data, mime_type=mime))
        contents.append(prompt)
        generation = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        try:
            response = _client().models.generate_content(model=config.GEMINI_MODEL, contents=contents, config=generation)
        except Exception as e:
            if is_quota_error(e):
                raise AIQuotaError(f"Gemini: {e}") from e
            raise AIError(f"Gemini: {e}") from e
        return parse_json(response.text)
