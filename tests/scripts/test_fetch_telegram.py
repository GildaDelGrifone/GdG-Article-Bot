import io
import os
import unittest
from unittest.mock import MagicMock

from scripts import fetch_telegram
from tests.core.test_telegram_post import PAGE, reply
from tests.support.db import TempDBMixin


def run(*argv, get=None):
    out, err = io.StringIO(), io.StringIO()
    code = fetch_telegram.main(list(argv), out=out, err=err, get=get)
    return code, out.getvalue(), err.getvalue()


class TestFetchTelegramCli(TempDBMixin, unittest.TestCase):
    def test_prints_date_photos_links_and_text(self):
        folder = os.path.join(self.temp_dir, "post")
        get = MagicMock(side_effect=[reply(text=PAGE), reply(content=b"A"), reply(content=b"B")])
        code, out, err = run("https://t.me/canale/7", "--out", folder, get=get)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Data (per --date): 25-06-2026", out)
        self.assertIn("Foto: 2", out)
        self.assertIn(os.path.join(folder, "img_1.jpg"), out)
        self.assertIn("https://magicmerchant.it/", out)
        self.assertIn("Sconto del 10%", out)

    def test_default_folder_is_under_data(self):
        get = MagicMock(side_effect=[reply(text=PAGE), reply(content=b"A"), reply(content=b"B")])
        code, out, _ = run("https://t.me/canale/7", get=get)
        self.assertEqual(code, 0)
        self.assertIn(os.path.join(self.temp_dir, "telegram", "canale-7"), out)

    def test_invalid_link_exits_2(self):
        code, _, err = run("https://example.org/x/1")
        self.assertEqual(code, 2)
        self.assertIn("non è il link", err)

    def test_missing_message_exits_1(self):
        code, _, err = run("https://t.me/canale/7", get=MagicMock(return_value=reply(text="<html></html>")))
        self.assertEqual(code, 1)
        self.assertIn("Messaggio non trovato", err)


if __name__ == "__main__":
    unittest.main()
