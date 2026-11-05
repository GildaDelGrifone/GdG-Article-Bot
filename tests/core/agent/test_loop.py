import os
import unittest

from core import db
from core.agent import loop
from core.agent.llm import AgentLLM, Step, ToolCall, ToolSpec
from core.agent.tools import Tool
from core.ai.base import AIQuotaError
from core.pipeline import PipelineError
from tests.support.config import patched_config
from tests.support.db import TempDBMixin
from tests.support.images import image_file


class FakeLLM(AgentLLM):
    """Un modello che risponde con i passi dati, uno per chiamata, e registra quello che riceve."""
    name = "fake"
    model = "fake-1"

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen = []

    def user_message(self, text, images):
        return {"role": "user", "text": text, "images": [n for n, _, _ in images]}

    def step(self, system, history, tools):
        self.seen.append((system, [dict(m) for m in history], [t.name for t in tools]))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def tool_results_message(self, results):
        return {"role": "tool", "results": [(r.call.name, r.content, r.is_error) for r in results]}


def answer(text):
    return Step({"role": "assistant", "text": text}, text, [])


def asking(*calls, text=""):
    return Step({"role": "assistant", "calls": [c.name for c in calls]}, text, list(calls))


def echo_tool(name="echo", fn=None):
    return Tool(ToolSpec(name, "d", {"type": "object", "properties": {}}), fn or (lambda ctx, **a: f"ok {a}"))


class TestRunTurn(TempDBMixin, unittest.TestCase):
    def test_plain_answer_is_saved_in_the_session(self):
        llm = FakeLLM([answer("Ciao!")])
        text, ids, numbers = loop.run_turn(1, "salve", llm=llm, tools=[echo_tool()])
        self.assertEqual((text, ids, numbers), ("Ciao!", [], []))
        session = db.get_agent_session(1)
        self.assertEqual([m["role"] for m in session["messages"]], ["user", "assistant"])
        self.assertEqual((session["provider"], session["model"]), ("fake", "fake-1"))

    def test_tool_calls_run_then_results_go_back_to_the_model(self):
        llm = FakeLLM([asking(ToolCall("1", "echo", {"x": 1}), ToolCall("2", "echo", {"x": 2})), answer("Fatto")])
        text, _, _ = loop.run_turn(1, "fai", llm=llm, tools=[echo_tool()])
        self.assertEqual(text, "Fatto")
        last_history = llm.seen[1][1]
        self.assertEqual(last_history[-1], {"role": "tool", "results": [("echo", "ok {'x': 1}", False), ("echo", "ok {'x': 2}", False)]})

    def test_tool_errors_go_back_as_errors(self):
        def boom(ctx, **a):
            raise PipelineError("Bozza non trovata")

        def crash(ctx, **a):
            raise RuntimeError("bug")

        llm = FakeLLM([asking(ToolCall("1", "boom"), ToolCall("2", "crash"), ToolCall("3", "nope")), answer("ok")])
        with self.assertLogs("core.agent.loop", "ERROR"):
            loop.run_turn(1, "x", llm=llm, tools=[echo_tool("boom", boom), echo_tool("crash", crash)])
        results = llm.seen[1][1][-1]["results"]
        self.assertEqual(results[0], ("boom", "Bozza non trovata", True))
        self.assertTrue(results[1][2] and "bug" in results[1][1])
        self.assertTrue(results[2][2] and "nope" in results[2][1])

    def test_long_results_are_truncated(self):
        llm = FakeLLM([asking(ToolCall("1", "big")), answer("ok")])
        loop.run_turn(1, "x", llm=llm, tools=[echo_tool("big", lambda ctx, **a: "a" * 20000)])
        content = llm.seen[1][1][-1]["results"][0][1]
        self.assertLess(len(content), loop.MAX_RESULT_CHARS + 100)
        self.assertIn("tagliato", content)

    def test_step_limit_stops_the_loop(self):
        llm = FakeLLM([asking(ToolCall(str(i), "echo")) for i in range(10)])
        with patched_config(AGENT_MAX_STEPS=3):
            text, _, _ = loop.run_turn(1, "x", llm=llm, tools=[echo_tool()])
        self.assertEqual(len(llm.seen), 3)
        self.assertIn(loop.STEP_LIMIT_TEXT, text)

    def test_publication_requests_are_returned(self):
        def request(ctx, **a):
            ctx.publish_ids.append(7)
            return "ok"

        llm = FakeLLM([asking(ToolCall("1", "req")), answer("Guarda")])
        _, ids, _ = loop.run_turn(1, "x", llm=llm, tools=[echo_tool("req", request)])
        self.assertEqual(ids, [7])

    def test_photos_get_continuous_numbers_and_are_shown_to_the_model(self):
        first = image_file(self.temp_dir, "a.jpg", 30, 30)
        second = image_file(self.temp_dir, "b.jpg", 30, 30)
        llm = FakeLLM([answer("1"), answer("2")])
        _, _, numbers = loop.run_turn(1, "foto", [first, second], llm=llm, tools=[echo_tool()])
        self.assertEqual(numbers, [1, 2])
        self.assertEqual(llm.seen[0][1][0]["images"], [1, 2])
        _, _, numbers = loop.run_turn(1, "altra", [first], llm=llm, tools=[echo_tool()])
        self.assertEqual(numbers, [3])
        self.assertEqual([i["number"] for i in db.get_agent_session(1)["images"]], [1, 2, 3])

    def test_system_and_tools_stay_frozen_and_history_only_grows(self):
        llm = FakeLLM([answer("a"), answer("b")])
        loop.run_turn(1, "uno", llm=llm, tools=[echo_tool()])
        db.insert_article("news", slug="nuova", branch="news/nuova", title="Nuova")  # cambia lo stato del database
        loop.run_turn(1, "due", llm=llm, tools=[echo_tool()])
        (system1, history1, tools1), (system2, history2, tools2) = llm.seen
        self.assertEqual((system1, tools1), (system2, tools2))
        self.assertEqual(history2[:len(history1)], history1)
        self.assertGreater(len(history2), len(history1))

    def test_error_mid_turn_rolls_the_turn_back(self):
        llm = FakeLLM([answer("a"), asking(ToolCall("1", "echo")), AIQuotaError("crediti")])
        loop.run_turn(1, "uno", llm=llm, tools=[echo_tool()])
        before = db.get_agent_session(1)["messages"]
        with self.assertRaises(AIQuotaError):
            loop.run_turn(1, "due", llm=llm, tools=[echo_tool()])
        self.assertEqual(db.get_agent_session(1)["messages"], before)

    def test_session_restarts_when_provider_or_model_changes(self):
        loop.run_turn(1, "uno", llm=FakeLLM([answer("a")]), tools=[echo_tool()])
        other = FakeLLM([answer("b")])
        other.model = "fake-2"
        loop.run_turn(1, "due", llm=other, tools=[echo_tool()])
        self.assertEqual(len(other.seen[0][1]), 1)

    def test_long_sessions_restart(self):
        llm = FakeLLM([answer("a"), answer("b")])
        loop.run_turn(1, "uno", llm=llm, tools=[echo_tool()])
        with patched_config(AGENT_MAX_MESSAGES=2):
            self.assertEqual(loop.session_status(1), "expired")
            loop.run_turn(1, "due", llm=llm, tools=[echo_tool()])
        self.assertEqual(len(llm.seen[1][1]), 1)


class TestSessionLifecycle(TempDBMixin, unittest.TestCase):
    def test_status_and_reset(self):
        llm = FakeLLM([])
        self.assertEqual(loop.session_status(1), "none")
        loop.start_session(1, llm)
        self.assertEqual(loop.session_status(1), "active")
        folder = loop.session_dir(1)
        os.makedirs(folder)
        open(os.path.join(folder, "01.jpg"), "w").close()
        self.assertTrue(loop.reset_session(1))
        self.assertEqual(loop.session_status(1), "none")
        self.assertFalse(os.path.exists(folder))
        self.assertFalse(loop.reset_session(1))

    def test_idle_sessions_expire(self):
        loop.start_session(1, FakeLLM([]))
        self.assertEqual(loop.session_status(1), "active")
        with patched_config(AGENT_IDLE_HOURS=-1):  # qualunque età supera il limite
            self.assertEqual(loop.session_status(1), "expired")

    def test_reset_keeps_the_drafts(self):
        article_id = db.insert_article("news", slug="x", branch="news/x", title="T")
        loop.start_session(1, FakeLLM([]))
        loop.reset_session(1)
        self.assertEqual(db.get_article(article_id)["status"], "draft")

    def test_system_prompt_lists_pending_drafts_and_today(self):
        db.insert_article("collab", slug="collab-x", branch="collaborazione/x", title="Collab X")
        system = loop.start_session(1, FakeLLM([]))
        self.assertIn("Collab X", system)
        self.assertIn("Today is", system)
        self.assertIn("NEVER written by you", system)


if __name__ == "__main__":
    unittest.main()
