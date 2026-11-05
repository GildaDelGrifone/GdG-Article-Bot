import io
import os

from PIL import Image


def make_image(width, height, color=(100, 150, 200)):
    return Image.new("RGB", (width, height), color=color)


def image_bytes(width, height, color=(100, 150, 200), fmt="JPEG"):
    buf = io.BytesIO()
    make_image(width, height, color).save(buf, format=fmt)
    return buf.getvalue()


def image_file(directory, filename, width, height, color=(100, 150, 200), quality=95):
    path = os.path.join(directory, filename)
    make_image(width, height, color).save(path, format="JPEG", quality=quality)
    return path
