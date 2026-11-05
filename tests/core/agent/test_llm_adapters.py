import base64
import unittest
from unittest.mock import MagicMock, patch

import anthropic
from anthropic.types import TextBlock, ToolUseBlock
from google.genai import types

from core.agent import claude_llm, gemini_llm
from core.agent.llm import ToolCall, ToolResult, ToolSpec, get_agent_llm
from core.ai import base
from tests.core.ai.test_ai import api_error
from tests.support.config import patched_config

TOOLS = [ToolSpec("show_draft", "Mostra", {"type": "object", "properties": {"id": {"type": "integer"}}})]


def claude_message(blocks, stop_reason="end_turn"):
    response = MagicMock()
    response.stop_reason = stop_reason
    response.content = blocks
    return response


class TestClaudeAgentLLM(unittest.TestCase):
    def step(self, response=None, error=None, model="claude-sonnet-5-5"):
        client = MagicMock()
        if error:
            client.messages.create.side_effect = error
        else:
            client.messages.create.return_value = response
        with patched_config(AGENT_MODEL=model), patch("core.agent.claude_llm._client", return_value=client):
            llm = claude_llm.ClaudeAgentLLM()
            result = llm.step("SYSTEM", [{"role": "user", "content": "ciao"}], TOOLS)
        return result, client

    def test_request_shape(self):
        _, client = self.step(claude_message([TextBlock(type="text", text="ok")]))
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["system"], "SYSTEM")
        self.assertEqual(kwargs["tools"], [{"name": "show_draft", "description": "Mostra", "input_schema": TOOLS[0].parameters}])
        self.assertEqual(kwargs["cache_control"], {"type": "ephemeral"})
        self.assertEqual(kwargs["output_config"], {"effort": "medium"})
        for forbidden in ("tool_choice", "thinking", "temperature"):
            self.assertNotIn(forbidden, kwargs)

    def test_effort_only_for_new_models(self):
        _, client = self.step(claude_message([TextBlock(type="text", text="ok")]), model="claude-haiku-4-5")
        self.assertNotIn("output_config", client.messages.create.call_args.kwargs)

    def test_text_and_tool_calls_are_parsed_and_blocks_kept_as_they_are(self):
        blocks = [TextBlock(type="text", text="Guardo"), ToolUseBlock(type="tool_use", id="t1", name="show_draft", input={"id": 3})]
        step, _ = self.step(claude_message(blocks, "tool_use"))
        self.assertEqual(step.text, "Guardo")
        self.assertEqual(step.calls, [ToolCall("t1", "show_draft", {"id": 3})])
        self.assertEqual(step.message["role"], "assistant")
        self.assertEqual(step.message["content"][1], {"type": "tool_use", "id": "t1", "name": "show_draft", "input": {"id": 3}})

    def test_user_message_numbers_images_then_the_text(self):
        message = claude_llm.ClaudeAgentLLM().user_message("testo", [(2, b"img", "image/jpeg")])
        content = message["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Immagine 2:"})
        self.assertEqual(content[1]["source"]["data"], base64.standard_b64encode(b"img").decode())
        self.assertEqual(content[2], {"type": "text", "text": "testo"})

    def test_tool_results_message(self):
        message = claude_llm.ClaudeAgentLLM().tool_results_message(
            [ToolResult(ToolCall("a", "x"), "bene"), ToolResult(ToolCall("b", "y"), "male", True)])
        self.assertEqual(message["content"][0], {"type": "tool_result", "tool_use_id": "a", "content": "bene"})
        self.assertEqual(message["content"][1]["is_error"], True)

    def test_errors_are_mapped(self):
        for error in (api_error(anthropic.RateLimitError, 429), api_error(anthropic.OverloadedError, 529),
                      api_error(anthropic.BadRequestError, 400, "Your credit balance is too low")):
            with self.assertRaises(base.AIQuotaError):
                self.step(error=error)
        with self.assertRaises(base.AIError) as ctx:
            self.step(error=api_error(anthropic.BadRequestError, 400, "invalid"))
        self.assertNotIsInstance(ctx.exception, base.AIQuotaError)
        for stop in ("refusal", "max_tokens"):
            with self.assertRaises(base.AIError):
                self.step(claude_message([], stop))

    def test_model_comes_from_agent_model_then_claude_model(self):
        with patched_config(AGENT_MODEL="", CLAUDE_MODEL="m1"):
            self.assertEqual(claude_llm.ClaudeAgentLLM().model, "m1")
        with patched_config(AGENT_MODEL="m2", CLAUDE_MODEL="m1"):
            self.assertEqual(claude_llm.ClaudeAgentLLM().model, "m2")


def gemini_response(parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


class TestGeminiAgentLLM(unittest.TestCase):
    def step(self, response=None, error=None, history=None):
        client = MagicMock()
        if error:
            client.models.generate_content.side_effect = error
        else:
            client.models.generate_content.return_value = response
        with patch("core.agent.gemini_llm._client", return_value=client):
            llm = gemini_llm.GeminiAgentLLM()
            result = llm.step("SYSTEM", history or [llm.user_message("ciao", [])], TOOLS)
        return result, client

    def test_request_disables_automatic_function_calling_and_sends_declarations(self):
        _, client = self.step(gemini_response([types.Part(text="ok")]))
        generation = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(generation.system_instruction, "SYSTEM")
        self.assertTrue(generation.automatic_function_calling.disable)
        declaration = generation.tools[0].function_declarations[0]
        self.assertEqual((declaration.name, declaration.parameters_json_schema), ("show_draft", TOOLS[0].parameters))

    def test_text_and_calls_are_parsed_and_ids_generated_when_missing(self):
        parts = [types.Part(text="Guardo"), types.Part(function_call=types.FunctionCall(name="show_draft", args={"id": 3})),
                 types.Part(function_call=types.FunctionCall(id="x", name="show_draft", args={"id": 4}))]
        step, _ = self.step(gemini_response(parts))
        self.assertEqual(step.text, "Guardo")
        self.assertEqual([(c.name, c.args) for c in step.calls], [("show_draft", {"id": 3}), ("show_draft", {"id": 4})])
        self.assertEqual(step.calls[1].id, "x")
        self.assertTrue(step.calls[0].id)

    def test_thought_parts_are_not_shown_but_signatures_survive_the_round_trip(self):
        parts = [types.Part(text="penso", thought=True),
                 types.Part(function_call=types.FunctionCall(name="show_draft", args={"id": 1}), thought_signature=b"sig"),
                 types.Part(text="Risposta")]
        step, client = self.step(gemini_response(parts))
        self.assertEqual(step.text, "Risposta")
        self.assertEqual(types.Content.model_validate(step.message).parts[1].thought_signature, b"sig")
        # la cronologia salvata si rimanda così com'è
        history = [step.message]
        self.step(gemini_response([types.Part(text="ok")]), history=history)

    def test_tool_results_message(self):
        message = gemini_llm.GeminiAgentLLM().tool_results_message(
            [ToolResult(ToolCall("a", "x"), "bene"), ToolResult(ToolCall("b", "y"), "male", True)])
        content = types.Content.model_validate(message)
        self.assertEqual(content.parts[0].function_response.response, {"result": "bene"})
        self.assertEqual(content.parts[1].function_response.response, {"error": "male"})

    def test_user_message_numbers_images(self):
        message = gemini_llm.GeminiAgentLLM().user_message("testo", [(3, b"img", "image/jpeg")])
        parts = types.Content.model_validate(message).parts
        self.assertEqual(parts[0].text, "Immagine 3:")
        self.assertEqual(parts[1].inline_data.data, b"img")
        self.assertEqual(parts[2].text, "testo")

    def test_errors(self):
        with self.assertRaises(base.AIQuotaError):
            self.step(error=RuntimeError("429 RESOURCE_EXHAUSTED"))
        with self.assertRaises(base.AIError) as ctx:
            self.step(error=RuntimeError("500 internal"))
        self.assertNotIsInstance(ctx.exception, base.AIQuotaError)
        with self.assertRaises(base.AIError):
            self.step(types.GenerateContentResponse(candidates=[]))


class TestGetAgentLlm(unittest.TestCase):
    def test_follows_agent_provider_then_ai_provider(self):
        with patched_config(AGENT_PROVIDER="claude", AI_PROVIDER="gemini"):
            self.assertEqual(get_agent_llm().name, "claude")
        with patched_config(AGENT_PROVIDER="", AI_PROVIDER="claude"):
            self.assertEqual(get_agent_llm().name, "claude")
        with patched_config(AGENT_PROVIDER="boh", AI_PROVIDER="gemini"):
            self.assertEqual(get_agent_llm().name, "gemini")
        self.assertEqual(get_agent_llm("gemini").name, "gemini")


if __name__ == "__main__":
    unittest.main()
