from bot import texts
from bot.keyboards import draft_keyboard


def card_text_from_draft(draft):
    """Il testo della scheda bozza per il risultato di pipeline.create_article / revise_article."""
    return texts.format_draft_card(draft.kind, draft.title, draft.draft_url, draft.public_url, draft.excerpt,
                                   draft.card_slug, draft.id)


def card_text_from_row(article):
    """Lo stesso, per una bozza letta dal database (non abbiamo l'estratto)."""
    return texts.format_draft_card(article["kind"], article["title"] or article["slug"], article["draft_url"],
                                   article["public_url"], None, article["card_slug"], article["id"])


async def send_card(message, text, article_id):
    """Invia la scheda con i pulsanti Pubblica / Rivedi / Scarta e restituisce il messaggio inviato."""
    return await message.reply_text(text, parse_mode="HTML", reply_markup=draft_keyboard(article_id),
                                    disable_web_page_preview=True)


async def send_card_to_chat(bot, chat_id, text, article_id):
    """Come send_card, ma senza un messaggio a cui rispondere (usato fuori dagli handler, es. dall'agente)."""
    return await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=draft_keyboard(article_id),
                                  disable_web_page_preview=True)
