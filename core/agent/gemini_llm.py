import logging

from google.genai import types

from core import config
from core.agent.llm import AgentLLM, Step, ToolCall
from core.ai.base import AIError, AIQuotaError
from core.ai.gemini import _client, is_quota_error

logger = logging.getLogger(__name__)


class GeminiAgentLLM(AgentLLM):
    """Agente su Gemini: function calling con il ciclo fatto da noi (`automatic_function_calling` spento).

    Le `Content` del modello si salvano e si rimandano intere, così le firme dei pensieri (`thought_signature`) restano.
    """

    name = "gemini"

    def __init__(self):
        self.model = config.AGENT_MODEL or config.GEMINI_MODEL

    @staticmethod
    def _dump(content):
        return content.model_dump(mode="json", exclude_none=True)

    def user_message(self, text, images):
        parts = []
        for number, data, mime in images:
            parts.append(types.Part(text=f"Immagine {number}:"))
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        parts.append(types.Part(text=text))
        return self._dump(types.Content(role="user", parts=parts))

    def step(self, system, history, tools):
        declarations = [types.FunctionDeclaration(name=t.name, description=t.description,
                                                  parameters_json_schema=t.parameters) for t in tools]
        generation = types.GenerateContentConfig(
            system_instruction=system, tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = [types.Content.model_validate(message) for message in history]
        try:
            response = _client().models.generate_content(model=self.model, contents=contents, config=generation)
        except Exception as e:
            if is_quota_error(e):
                raise AIQuotaError(f"Gemini: {e}") from e
            raise AIError(f"Gemini: {e}") from e

        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.content is None or not candidate.content.parts:
            reason = getattr(candidate, "finish_reason", None) if candidate else None
            raise AIError(f"Gemini: risposta vuota ({reason or 'nessun candidato'})")
        parts = candidate.content.parts
        text = "".join(part.text for part in parts if part.text and not part.thought).strip()
        calls = [ToolCall(part.function_call.id or f"call_{index}", part.function_call.name,
                          dict(part.function_call.args or {}))
                 for index, part in enumerate(p for p in parts if p.function_call)]
        return Step(self._dump(candidate.content), text, calls)

    def tool_results_message(self, results):
        parts = [types.Part.from_function_response(
            name=result.call.name,
            response={"error": result.content} if result.is_error else {"result": result.content},
        ) for result in results]
        return self._dump(types.Content(role="user", parts=parts))
