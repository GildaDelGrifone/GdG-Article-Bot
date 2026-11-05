from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot import texts

PUBLISH_PREFIX = "art_publish_"
REVISE_PREFIX = "art_revise_"
DISCARD_PREFIX = "art_discard_"


def draft_keyboard(article_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BUTTON_PUBLISH, callback_data=f"{PUBLISH_PREFIX}{article_id}")],
        [
            InlineKeyboardButton(texts.BUTTON_REVISE, callback_data=f"{REVISE_PREFIX}{article_id}"),
            InlineKeyboardButton(texts.BUTTON_DISCARD, callback_data=f"{DISCARD_PREFIX}{article_id}"),
        ],
    ])
