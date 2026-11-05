from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from core import config

R2_SETTINGS = dict(
    R2_ACCOUNT_ID="acc",
    R2_ACCESS_KEY_ID="k",
    R2_SECRET_ACCESS_KEY="s",
    R2_BUCKET="gdg-media",
    R2_PUBLIC_URL="https://pub.r2.dev",
)
GITHUB_SETTINGS = dict(
    ASTRO_GITHUB_REPO="o/r",
    ASTRO_GITHUB_BRANCH="main",
    ASTRO_GITHUB_TOKEN="tok",
    ASTRO_BASE_URL="https://example.org",
    NEWS_CONTENT_DIR="src/content/news",
    COLLAB_CONTENT_DIR="src/content/collaborazioni",
)


@contextmanager
def patched_config(**values):
    """Sostituisce temporaneamente attributi di core.config, es. patched_config(ADMIN_CHAT_ID="999")."""
    with ExitStack() as stack:
        for name, value in values.items():
            if not hasattr(config, name):
                raise AttributeError(f"core.config non ha l'impostazione {name}")
            stack.enter_context(patch.object(config, name, value))
        yield


@contextmanager
def site_config(**extra):
    """R2 e GitHub configurati con valori finti."""
    with patched_config(**{**R2_SETTINGS, **GITHUB_SETTINGS, **extra}):
        yield
