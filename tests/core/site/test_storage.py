import io
import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

from core.site import storage
from core.site.client import r2_client
from tests.support.config import R2_SETTINGS, patched_config, site_config
from tests.support.images import image_file, make_image


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name


class TestUpload(StorageTestCase):
    def upload(self, path, prefix="news/x", stem="img_1", client=None):
        client = client or MagicMock()
        with site_config(), patch("core.site.storage.r2_client", return_value=client):
            return storage.upload_media(path, prefix, stem), client

    def test_key_uses_stem_and_random_suffix_not_the_original_name(self):
        path = image_file(self.dir, "Foto Mia 1.JPG", 800, 600)
        info, client = self.upload(path)
        self.assertRegex(info["id"], r"^news/x/img_1_[0-9a-f]{8}\.jpg$")
        self.assertEqual(info["source_url"], "https://pub.r2.dev/" + info["id"])
        kwargs = client.put_object.call_args.kwargs
        self.assertEqual((kwargs["Bucket"], kwargs["Key"], kwargs["ContentType"]), ("gdg-media", info["id"], "image/jpeg"))

    def test_jpeg_extension_is_normalised(self):
        path = image_file(self.dir, "a.jpeg", 100, 100)
        info, _ = self.upload(path)
        self.assertTrue(info["id"].endswith(".jpg"))

    def test_prefix_slashes_are_stripped(self):
        path = image_file(self.dir, "a.jpg", 100, 100)
        info, _ = self.upload(path, prefix="/news/x/")
        self.assertTrue(info["id"].startswith("news/x/img_1_"))

    def test_wide_image_is_resized_to_1600(self):
        path = image_file(self.dir, "a.jpg", 3200, 2000)
        _, client = self.upload(path)
        body = client.put_object.call_args.kwargs["Body"]
        self.assertEqual(Image.open(io.BytesIO(body)).size, (1600, 1000))

    def test_small_image_is_not_enlarged(self):
        path = image_file(self.dir, "a.jpg", 800, 600)
        _, client = self.upload(path)
        self.assertEqual(Image.open(io.BytesIO(client.put_object.call_args.kwargs["Body"])).size, (800, 600))

    def test_exif_orientation_is_applied(self):
        path = os.path.join(self.dir, "a.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6
        make_image(400, 200).save(path, format="JPEG", exif=exif)
        _, client = self.upload(path)
        self.assertEqual(Image.open(io.BytesIO(client.put_object.call_args.kwargs["Body"])).size, (200, 400))

    def test_png_stays_png(self):
        path = os.path.join(self.dir, "logo.png")
        Image.new("RGBA", (300, 100), (0, 0, 0, 0)).save(path)
        info, client = self.upload(path, stem="logo")
        self.assertEqual(client.put_object.call_args.kwargs["ContentType"], "image/png")
        self.assertEqual(Image.open(io.BytesIO(client.put_object.call_args.kwargs["Body"])).mode, "RGBA")
        self.assertTrue(info["id"].endswith(".png"))

    def test_other_types_are_sent_raw(self):
        path = os.path.join(self.dir, "a.webp")
        make_image(50, 50).save(path, format="WEBP")
        raw = open(path, "rb").read()
        _, client = self.upload(path)
        self.assertEqual(client.put_object.call_args.kwargs["Body"], raw)
        self.assertEqual(client.put_object.call_args.kwargs["ContentType"], "image/webp")

    def test_no_client_returns_none(self):
        path = image_file(self.dir, "a.jpg", 100, 100)
        with site_config(), patch("core.site.storage.r2_client", return_value=None):
            self.assertIsNone(storage.upload_media(path, "news/x", "img_1"))

    def test_upload_error_returns_none(self):
        path = image_file(self.dir, "a.jpg", 100, 100)
        client = MagicMock()
        client.put_object.side_effect = RuntimeError("boom")
        info, _ = self.upload(path, client=client)
        self.assertIsNone(info)

    def test_missing_boto3_is_reported(self):
        path = image_file(self.dir, "a.jpg", 100, 100)
        with site_config(), patch("core.site.storage.r2_client", side_effect=ImportError), \
                self.assertLogs("core.site.storage", "ERROR") as logs:
            self.assertIsNone(storage.upload_media(path, "news/x", "img_1"))
        self.assertIn("boto3", logs.output[0])


class TestVisionCopy(StorageTestCase):
    def test_large_image_is_limited_to_1568_on_the_long_side(self):
        data, mime = storage.vision_copy(image_file(self.dir, "a.jpg", 4000, 3000))
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(Image.open(io.BytesIO(data)).size, (1568, 1176))

    def test_portrait_long_side_is_the_height(self):
        data, _ = storage.vision_copy(image_file(self.dir, "a.jpg", 1000, 3136))
        self.assertEqual(Image.open(io.BytesIO(data)).size, (500, 1568))

    def test_small_image_keeps_its_size(self):
        data, _ = storage.vision_copy(image_file(self.dir, "a.jpg", 300, 200))
        self.assertEqual(Image.open(io.BytesIO(data)).size, (300, 200))

    def test_transparent_png_becomes_opaque_on_white(self):
        path = os.path.join(self.dir, "logo.png")
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(path)
        data, _ = storage.vision_copy(path)
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.mode, "RGB")
        self.assertGreater(min(image.getpixel((5, 5))), 250)


class TestObjectName(unittest.TestCase):
    def test_names_are_unique(self):
        names = {storage.object_name("img_1", "a.jpg") for _ in range(20)}
        self.assertEqual(len(names), 20)
        for name in names:
            self.assertTrue(re.fullmatch(r"img_1_[0-9a-f]{8}\.jpg", name))


class TestPublicUrls(unittest.TestCase):
    def test_media_url(self):
        with patched_config(R2_PUBLIC_URL="https://pub.r2.dev"):
            self.assertEqual(storage.media_url("news/x/a.jpg"), "https://pub.r2.dev/news/x/a.jpg")

    def test_reachable_only_on_200(self):
        with patch("core.site.storage.requests.head") as head:
            head.return_value.status_code = 200
            self.assertTrue(storage.is_reachable("https://x/a.jpg"))
            self.assertEqual(head.call_args.kwargs["timeout"], 10)
            head.return_value.status_code = 404
            self.assertFalse(storage.is_reachable("https://x/a.jpg"))

    def test_network_error_is_not_reachable(self):
        with patch("core.site.storage.requests.head", side_effect=requests.ConnectionError):
            self.assertFalse(storage.is_reachable("https://x/a.jpg"))


class TestRealR2Client(unittest.TestCase):
    def test_client_is_built_for_the_account_endpoint(self):
        with patched_config(**R2_SETTINGS), patch("boto3.client") as boto_client:
            r2_client()
        args, kwargs = boto_client.call_args
        self.assertEqual(args, ("s3",))
        self.assertEqual(kwargs["endpoint_url"], "https://acc.r2.cloudflarestorage.com")
        self.assertEqual(kwargs["region_name"], "auto")

    def test_none_when_a_setting_is_missing(self):
        with patched_config(**{**R2_SETTINGS, "R2_SECRET_ACCESS_KEY": None}):
            self.assertIsNone(r2_client())


class TestNormalizeImage(StorageTestCase):
    def save(self, name, fmt, mode="RGB", size=(120, 80), color=(10, 120, 200)):
        path = os.path.join(self.dir, name)
        Image.new(mode, size, color).save(path, format=fmt)
        return path

    def test_jpeg_is_copied_whatever_its_extension(self):
        path = self.save("Foto.JFIF", "JPEG")
        target = storage.normalize_image(path, self.dir, 3)
        self.assertEqual(os.path.basename(target), "03.jpg")
        with open(path, "rb") as original, open(target, "rb") as copy:
            self.assertEqual(original.read(), copy.read())

    def test_png_stays_png(self):
        target = storage.normalize_image(self.save("logo.png", "PNG"), self.dir, 1)
        self.assertEqual(os.path.basename(target), "01.png")

    def test_content_wins_over_the_extension(self):
        target = storage.normalize_image(self.save("sembra.png", "JPEG"), self.dir, 2)
        self.assertEqual(os.path.basename(target), "02.jpg")

    def test_opaque_webp_becomes_jpeg(self):
        target = storage.normalize_image(self.save("a.webp", "WEBP"), self.dir)
        self.assertTrue(target.endswith(".jpg"))
        with Image.open(target) as img:
            self.assertEqual((img.format, img.size), ("JPEG", (120, 80)))

    def test_transparent_webp_becomes_png(self):
        target = storage.normalize_image(self.save("a.webp", "WEBP", "RGBA", color=(10, 120, 200, 0)), self.dir)
        with Image.open(target) as img:
            self.assertEqual((img.format, img.mode), ("PNG", "RGBA"))

    def test_gif_keeps_the_first_frame(self):
        frames = [Image.new("P", (40, 30), color=value) for value in (1, 2)]
        path = os.path.join(self.dir, "anim.gif")
        frames[0].save(path, save_all=True, append_images=frames[1:])
        target = storage.normalize_image(path, self.dir, 4)
        self.assertEqual(os.path.basename(target), "04.jpg")

    def test_avif_is_readable_when_pillow_supports_it(self):
        from PIL import features
        if not features.check("avif"):
            self.skipTest("Pillow senza AVIF")
        target = storage.normalize_image(self.save("a.avif", "AVIF"), self.dir)
        self.assertTrue(target.endswith(".jpg"))

    def test_unreadable_file_is_a_value_error(self):
        path = os.path.join(self.dir, "rotta.jpg")
        with open(path, "wb") as f:
            f.write(b"non sono un'immagine")
        with self.assertRaisesRegex(ValueError, "formato non supportato"):
            storage.normalize_image(path, self.dir)


if __name__ == "__main__":
    unittest.main()
