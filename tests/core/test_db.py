import sqlite3
import unittest

from core import config, db
from tests.support.db import TempDBMixin


class TestDb(TempDBMixin, unittest.TestCase):
    def insert(self, **fields):
        base = dict(slug="festa", branch="news/festa", title="Festa")
        return db.insert_article("news", **{**base, **fields})

    def test_insert_and_get_with_defaults(self):
        article_id = self.insert()
        article = db.get_article(article_id)
        self.assertEqual((article["kind"], article["slug"], article["status"]), ("news", "festa", "draft"))
        self.assertEqual(article["image_keys"], [])
        self.assertIsNotNone(article["created_at"])

    def test_json_fields_round_trip(self):
        article_id = self.insert(image_keys=["news/festa/img_1_a.jpg"], image_paths=["/tmp/è.jpg"])
        article = db.get_article(article_id)
        self.assertEqual(article["image_keys"], ["news/festa/img_1_a.jpg"])
        self.assertEqual(article["image_paths"], ["/tmp/è.jpg"])

    def test_update_changes_only_given_fields(self):
        article_id = self.insert(brief="b")
        db.update_article(article_id, status="published", image_keys=["k"])
        article = db.get_article(article_id)
        self.assertEqual((article["status"], article["brief"], article["image_keys"]), ("published", "b", ["k"]))

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            db.update_article(self.insert(), colore="rosso")
        with self.assertRaises(ValueError):
            db.insert_article("news", slug="x", branch="b", sql="drop")

    def test_empty_update_is_a_no_op(self):
        db.update_article(self.insert())

    def test_get_missing(self):
        self.assertIsNone(db.get_article(999))

    def test_find_by_id_or_slug(self):
        first = self.insert()
        second = self.insert()
        self.assertEqual(db.find_article(str(first))["id"], first)
        self.assertEqual(db.find_article("festa")["id"], second)  # lo slug uguale: vince il più recente
        self.assertIsNone(db.find_article("non-esiste"))

    def test_list_drafts_only_returns_drafts_newest_first(self):
        first = self.insert()
        second = self.insert()
        done = self.insert()
        db.update_article(done, status="published")
        self.assertEqual([a["id"] for a in db.list_drafts()], [second, first])

    def test_init_db_is_idempotent_and_migrates(self):
        db.init_db()
        db.COLUMN_MIGRATIONS.append(("extra_note", "TEXT"))
        self.addCleanup(db.COLUMN_MIGRATIONS.pop)
        db.init_db()
        db.init_db()
        with sqlite3.connect(config.DB_PATH) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(articles)")]
        self.assertIn("extra_note", columns)

    def test_transaction_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with db.transaction() as cur:
                cur.execute("INSERT INTO articles (kind, slug, branch) VALUES ('news', 'x', 'b')")
                raise RuntimeError
        self.assertEqual(db.list_drafts(), [])


class TestAgentSessions(TempDBMixin, unittest.TestCase):
    def test_missing_session(self):
        self.assertIsNone(db.get_agent_session(1))

    def test_save_read_replace_and_delete(self):
        db.save_agent_session(1, "gemini", "m", "SYS", [{"role": "user", "text": "è"}], [{"number": 1, "path": "a"}])
        session = db.get_agent_session(1)
        self.assertEqual((session["provider"], session["model"], session["system"]), ("gemini", "m", "SYS"))
        self.assertEqual(session["messages"], [{"role": "user", "text": "è"}])
        self.assertEqual(session["images"], [{"number": 1, "path": "a"}])
        db.save_agent_session(1, "claude", "m2", "SYS2", [], [])
        self.assertEqual(db.get_agent_session(1)["provider"], "claude")
        self.assertEqual(db.get_agent_session(1)["messages"], [])
        db.delete_agent_session(1)
        self.assertIsNone(db.get_agent_session(1))
        db.delete_agent_session(1)  # già assente: nessun errore

    def test_sessions_are_per_chat(self):
        db.save_agent_session(1, "gemini", "m", "A", [], [])
        db.save_agent_session(2, "gemini", "m", "B", [], [])
        db.delete_agent_session(1)
        self.assertEqual(db.get_agent_session(2)["system"], "B")


if __name__ == "__main__":
    unittest.main()
