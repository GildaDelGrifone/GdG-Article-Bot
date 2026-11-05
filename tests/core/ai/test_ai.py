import base64
import json
import unittest
from unittest.mock import MagicMock, patch

import anthropic

from core.ai import base, claude, gemini
from core.ai.provider import get_provider
from tests.support.config import patched_config

IMAGES = [(b"one", "image/jpeg"), (b"two", "image/jpeg")]
RESULT = {"title": "T", "excerpt": "E", "cover_index": 1, "body": "Corpo"}


def api_error(cls, status, message="boom"):
    response = MagicMock(status_code=status, headers={}, text=message)
    return cls(message, response=response, body=None)


class TestBase(unittest.TestCase):
    def test_parse_plain_and_fenced_json(self):
        self.assertEqual(base.parse_json('{"a": 1}'), {"a": 1})
        self.assertEqual(base.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(base.parse_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_invalid_json_raises(self):
        for bad in ("non json", "[1, 2]", "", None):
            with self.assertRaises(base.AIError):
                base.parse_json(bad)

    def test_news_schema_has_no_partner(self):
        schema = base.article_schema("news")
        self.assertEqual(schema["required"], ["title", "excerpt", "cover_index", "body"])
        self.assertFalse(schema["additionalProperties"])

    def test_collab_schema_requires_partner_fields(self):
        schema = base.article_schema("collab")
        self.assertIn("partner", schema["required"])
        partner = schema["properties"]["partner"]
        self.assertEqual(partner["required"], ["nome", "url", "descrizione", "logo_index"])
        self.assertFalse(partner["additionalProperties"])

    def test_quota_error_is_an_ai_error(self):
        self.assertTrue(issubclass(base.AIQuotaError, base.AIError))


class TestGemini(unittest.TestCase):
    def run_generate(self, text=None, error=None, schema=None):
        client = MagicMock()
        if error:
            client.models.generate_content.side_effect = error
        else:
            client.models.generate_content.return_value.text = text
        with patched_config(GEMINI_MODEL="gem-model"), patch("core.ai.gemini._client", return_value=client):
            result = gemini.GeminiProvider().generate("SYSTEM", "PROMPT", IMAGES, schema or {"type": "object"})
        return result, client

    def test_sends_system_images_in_order_then_prompt(self):
        result, client = self.run_generate(json.dumps(RESULT))
        self.assertEqual(result, RESULT)
        kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["model"], "gem-model")
        contents = kwargs["contents"]
        self.assertEqual(contents[0], "Immagine 1:")
        self.assertEqual(contents[1].inline_data.data, b"one")
        self.assertEqual(contents[2], "Immagine 2:")
        self.assertEqual(contents[3].inline_data.data, b"two")
        self.assertEqual(contents[4], "PROMPT")
        config = kwargs["config"]
        self.assertEqual(config.system_instruction, "SYSTEM")
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertEqual(config.response_json_schema, {"type": "object"})

    def test_fenced_json_is_accepted(self):
        result, _ = self.run_generate("```json\n" + json.dumps(RESULT) + "\n```")
        self.assertEqual(result, RESULT)

    def test_invalid_json_raises_ai_error(self):
        with self.assertRaises(base.AIError):
            self.run_generate("niente json")

    def test_quota_errors_are_mapped(self):
        for message in ("429 RESOURCE_EXHAUSTED", "You exceeded your current quota", "Your prepayment credits are depleted"):
            with self.assertRaises(base.AIQuotaError):
                self.run_generate(error=RuntimeError(message))

    def test_other_errors_are_ai_errors_not_quota(self):
        with self.assertRaises(base.AIError) as ctx:
            self.run_generate(error=RuntimeError("500 internal"))
        self.assertNotIsInstance(ctx.exception, base.AIQuotaError)

    def test_client_is_created_lazily_with_the_key(self):
        with patched_config(GEMINI_API_KEY="k"), patch("core.ai.gemini._client_instance", None), \
                patch("core.ai.gemini.genai.Client") as ctor:
            gemini._client()
            gemini._client()
        ctor.assert_called_once_with(api_key="k")


def claude_response(text=None, stop_reason="end_turn"):
    response = MagicMock()
    response.stop_reason = stop_reason
    block = MagicMock()
    block.type = "text"
    block.text = text
    response.content = [block]
    return response


class TestClaude(unittest.TestCase):
    def run_generate(self, response=None, error=None, model="claude-sonnet-5-5"):
        client = MagicMock()
        if error:
            client.messages.create.side_effect = error
        else:
            client.messages.create.return_value = response
        with patched_config(CLAUDE_MODEL=model), patch("core.ai.claude._client", return_value=client):
            result = claude.ClaudeProvider().generate("SYSTEM", "PROMPT", IMAGES, {"type": "object"})
        return result, client

    def test_request_shape(self):
        result, client = self.run_generate(claude_response(json.dumps(RESULT)))
        self.assertEqual(result, RESULT)
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-5-5")
        self.assertEqual(kwargs["system"], "SYSTEM")
        self.assertNotIn("tool_choice", kwargs)
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["output_config"]["format"], {"type": "json_schema", "schema": {"type": "object"}})
        self.assertEqual(kwargs["output_config"]["effort"], "medium")

    def test_images_are_numbered_base64_blocks_then_the_prompt(self):
        _, client = self.run_generate(claude_response(json.dumps(RESULT)))
        content = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Immagine 1:"})
        self.assertEqual(content[1]["source"]["data"], base64.standard_b64encode(b"one").decode())
        self.assertEqual(content[1]["source"]["media_type"], "image/jpeg")
        self.assertEqual(content[2], {"type": "text", "text": "Immagine 2:"})
        self.assertEqual(content[4], {"type": "text", "text": "PROMPT"})

    def test_effort_is_not_sent_to_old_models(self):
        _, client = self.run_generate(claude_response(json.dumps(RESULT)), model="claude-haiku-4-5")
        self.assertNotIn("effort", client.messages.create.call_args.kwargs["output_config"])

    def test_rate_limit_and_overload_are_quota_errors(self):
        for error in (api_error(anthropic.RateLimitError, 429), api_error(anthropic.OverloadedError, 529)):
            with self.assertRaises(base.AIQuotaError):
                self.run_generate(error=error)

    def test_low_credit_balance_is_a_quota_error(self):
        error = api_error(anthropic.BadRequestError, 400, "Your credit balance is too low")
        with self.assertRaises(base.AIQuotaError):
            self.run_generate(error=error)

    def test_other_bad_requests_are_plain_ai_errors(self):
        with self.assertRaises(base.AIError) as ctx:
            self.run_generate(error=api_error(anthropic.BadRequestError, 400, "invalid"))
        self.assertNotIsInstance(ctx.exception, base.AIQuotaError)

    def test_refusal_and_truncation_raise(self):
        for stop in ("refusal", "max_tokens"):
            with self.assertRaises(base.AIError):
                self.run_generate(claude_response("{}", stop_reason=stop))

    def test_invalid_json_raises(self):
        with self.assertRaises(base.AIError):
            self.run_generate(claude_response("non json"))

    def test_client_is_created_lazily_with_the_key(self):
        with patched_config(ANTHROPIC_API_KEY="k"), patch("core.ai.claude._client_instance", None), \
                patch("core.ai.claude.anthropic.Anthropic") as ctor:
            claude._client()
            claude._client()
        ctor.assert_called_once_with(api_key="k")


class TestGetProvider(unittest.TestCase):
    def test_follows_the_setting(self):
        with patched_config(AI_PROVIDER="claude"):
            self.assertIsInstance(get_provider(), claude.ClaudeProvider)
        with patched_config(AI_PROVIDER="gemini"):
            self.assertIsInstance(get_provider(), gemini.GeminiProvider)

    def test_explicit_name_wins_and_unknown_falls_back(self):
        with patched_config(AI_PROVIDER="gemini"):
            self.assertIsInstance(get_provider("claude"), claude.ClaudeProvider)
            self.assertIsInstance(get_provider("sconosciuto"), gemini.GeminiProvider)


if __name__ == "__main__":
    unittest.main()
