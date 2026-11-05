import os
import tempfile
import unittest
from unittest.mock import MagicMock

import requests

from core.pipeline import PipelineError
from core.telegram_post import fetch_post, parse_post, parse_url

PAGE = """
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message_user"><i class="tgme_widget_message_user_photo bgcolor6" data-content="G"></i></div>
 <a class="tgme_widget_message_photo_wrap 5857153 1363724703_4562" href="https://t.me/canale/7"
    style="width:800px;background-image:url('https://cdn4.telesco.pe/file/one.jpg')"></a>
 <a class="tgme_widget_message_photo_wrap 5857154 1363724703_4563" href="https://t.me/canale/7"
    style="width:800px;background-image:url('https://cdn4.telesco.pe/file/two.jpg?a=1&amp;b=2')"></a>
 <div class="tgme_widget_message_text js-message_text" dir="auto">Siamo felici!<br/><br/>Sconto del 10% su
<a href="https://magicmerchant.it/" target="_blank">https://magicmerchant.it</a> e su
<a href="https://www.instagram.com/entra/">il loro profilo</a> <i class="emoji" style="background-image:url('//telegram.org/img/emoji/40/E29DA4.png')"><b>❤</b></i><br>Fine &amp; grazie</div>
 <span class="tgme_widget_message_meta"><a class="tgme_widget_message_date" href="https://t.me/canale/7"><time datetime="2026-06-24T22:30:00+00:00" class="time">22:30</time></a></span>
</div>
"""


class TestParseUrl(unittest.TestCase):
    def test_valid_links(self):
        self.assertEqual(parse_url("https://t.me/Gildadelgrifonetorino/2347"), ("Gildadelgrifonetorino", 2347))
        self.assertEqual(parse_url("t.me/canale/7?single"), ("canale", 7))
        self.assertEqual(parse_url(" https://t.me/s/canale/7 "), ("canale", 7))

    def test_invalid_links(self):
        for url in ("https://t.me/c/123/4", "https://example.org/canale/7", "https://t.me/canale", "", None):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_url(url)


class TestParsePost(unittest.TestCase):
    def test_fields(self):
        post = parse_post(PAGE)
        self.assertEqual(post["photos"], ["https://cdn4.telesco.pe/file/one.jpg", "https://cdn4.telesco.pe/file/two.jpg?a=1&b=2"])
        self.assertEqual(post["links"], ["https://magicmerchant.it/", "https://www.instagram.com/entra/"])
        self.assertFalse(post["has_video"])

    def test_text_keeps_line_breaks_links_and_entities(self):
        text = parse_post(PAGE)["text"]
        self.assertTrue(text.startswith("Siamo felici!\n\nSconto del 10% su"))
        self.assertIn("https://magicmerchant.it/ e su", text)
        self.assertIn("il loro profilo (https://www.instagram.com/entra/) ❤\nFine & grazie", text)

    def test_date_is_the_one_in_the_site_timezone(self):
        post = parse_post(PAGE)
        self.assertEqual(post["datetime"], "2026-06-24T22:30:00+00:00")
        self.assertEqual(post["date_iso"], "2026-06-25")  # 00:30 a Roma

    def test_photo_only_message(self):
        page = PAGE.replace('<div class="tgme_widget_message_text js-message_text" dir="auto">', "<div>")
        post = parse_post(page)
        self.assertEqual((post["text"], len(post["photos"])), ("", 2))

    def test_video_is_reported(self):
        self.assertTrue(parse_post(PAGE + '<a class="tgme_widget_message_video_player"></a>')["has_video"])

    def test_page_without_a_message(self):
        with self.assertRaisesRegex(PipelineError, "Messaggio non trovato"):
            parse_post("<html><body>Channel not found</body></html>")


def reply(status=200, text="", content=b""):
    response = MagicMock()
    response.status_code, response.text, response.content = status, text, content
    return response


class TestFetchPost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "post")

    def test_saves_text_and_photos(self):
        get = MagicMock(side_effect=[reply(text=PAGE), reply(content=b"IMG1"), reply(content=b"IMG2")])
        post = fetch_post("https://t.me/canale/7", self.folder, get=get)
        self.assertEqual(get.call_args_list[0].args[0], "https://t.me/canale/7")
        self.assertEqual(get.call_args_list[0].kwargs["params"], {"embed": "1", "mode": "tme"})
        self.assertEqual([os.path.basename(p) for p in post["photo_files"]], ["img_1.jpg", "img_2.jpg"])
        with open(post["photo_files"][1], "rb") as f:
            self.assertEqual(f.read(), b"IMG2")
        with open(os.path.join(self.folder, "post.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), post["text"])
        self.assertEqual((post["channel"], post["number"]), ("canale", 7))

    def test_bad_link(self):
        with self.assertRaisesRegex(PipelineError, "non è il link"):
            fetch_post("https://example.org/x", self.folder, get=MagicMock())

    def test_http_error(self):
        with self.assertRaisesRegex(PipelineError, "risposta 404"):
            fetch_post("https://t.me/canale/7", self.folder, get=MagicMock(return_value=reply(404)))

    def test_network_error(self):
        with self.assertRaisesRegex(PipelineError, "Impossibile leggere"):
            fetch_post("https://t.me/canale/7", self.folder, get=MagicMock(side_effect=requests.ConnectionError("giù")))

    def test_photo_download_error(self):
        get = MagicMock(side_effect=[reply(text=PAGE), reply(500)])
        with self.assertRaisesRegex(PipelineError, "foto 1"):
            fetch_post("https://t.me/canale/7", self.folder, get=get)


if __name__ == "__main__":
    unittest.main()
