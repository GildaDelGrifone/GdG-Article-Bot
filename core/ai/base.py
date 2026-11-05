import json
import re


class AIError(Exception):
    """Errore del provider AI (risposta non valida, rifiuto, errore del servizio)."""


class AIQuotaError(AIError):
    """Quota o crediti esauriti, oppure limite di richieste raggiunto."""


def strip_code_fence(text):
    """Toglie un blocco ```lingua ... ``` che avvolge tutto il testo, qualunque sia la lingua."""
    text = text.strip()
    match = re.match(r"^```[\w-]*[ \t]*\n(.*?)\n?```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def parse_json(text):
    """Il JSON della risposta dell'AI (anche se avvolto da un blocco di codice); AIError se non è un oggetto valido."""
    try:
        data = json.loads(strip_code_fence(text or ""))
    except ValueError as e:
        raise AIError(f"Risposta dell'AI non valida (JSON): {e}") from e
    if not isinstance(data, dict):
        raise AIError("Risposta dell'AI non valida: attesa un oggetto JSON")
    return data


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def article_schema(kind):
    """Schema JSON della risposta (vedi AGENTS.md, "Output contract"); `partner` solo per le collaborazioni."""
    properties = {
        "title": {"type": "string"},
        "excerpt": {"type": "string"},
        "cover_index": _nullable({"type": "integer"}),
        "body": {"type": "string"},
    }
    required = list(properties)
    if kind == "collab":
        properties["partner"] = {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "url": _nullable({"type": "string"}),
                "descrizione": {"type": "string"},
                "logo_index": _nullable({"type": "integer"}),
            },
            "required": ["nome", "url", "descrizione", "logo_index"],
            "additionalProperties": False,
        }
        required.append("partner")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


class Provider:
    """Interfaccia comune: `generate` restituisce il dizionario del contratto di output.

    `images` è una lista di (byte, mime) numerate da 1 nell'ordine ricevuto.
    """

    name = ""

    def generate(self, system, prompt, images, schema):
        raise NotImplementedError
