# bot.py
# -*- coding: utf-8 -*-
# Телеграм-бот для подготовки поста «Новое поступление» и удобной замены позиций местами.
# Требуется python-telegram-bot >= 20.0

from __future__ import annotations

import logging
import os
from typing import Dict, List, Any

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ----------------------- НАЛАШТУВАННЯ -----------------------
def _env(name, *alts, default=None):
    for k in (name, *alts):
        v = os.getenv(k)
        if v:
            return v
    return default

TOKEN = _env("API_TOKEN", "TELEGRAM_TOKEN")
CHANNEL_ID = int(_env("CHANNEL_ID", "TARGET_CHAT_ID", "CHAT_ID", default="0"))
_admin_raw = _env("ADMIN_ID", "ADMIN_IDS", default="")
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x}
NEW_ARRIVALS_URL = _env(
    "NEW_ARRIVALS_URL",
    default="https://zamorskiepodarki.com/uk/novoe-postuplenie/"
)

missing = []
if not TOKEN:
    missing.append("API_TOKEN")
if not ADMIN_IDS:
    missing.append("ADMIN_ID")
if missing:
    raise SystemExit("Не задані " + ", ".join(missing) + " у Render.")
# CHANNEL_ID може бути 0 — тоді /publish просто попередить
# ------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("new_arrivals_bot_ru")

# Черновики по пользователям-админам.
DRAFTS: Dict[int, Dict[str, Any]] = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def ensure_draft(user_id: int) -> Dict[str, Any]:
    if user_id not in DRAFTS:
        DRAFTS[user_id] = {"items": [], "cursor": 0}
    return DRAFTS[user_id]


def parse_item_line(text: str) -> Dict[str, str]:
    for sep in ["—", "–", "  |  ", " | ", " - ", " — ", " – "]:
        text = text.replace(sep, "|")
    text = text.replace(" |", "|").replace("| ", "|").replace(" -", "|").replace("- ", "|")
    parts = [p.strip() for p in text.split("|") if p.strip()]
    title = parts[0] if parts else "Без названия"
    price = parts[1] if len(parts) > 1 else ""
    note = parts[2] if len(parts) > 2 else ""
    return {"title": title, "price": price, "note": note}


def render_items(items: List[Dict[str, str]]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        tail = []
        if it.get("price"):
            tail.append(it["price"])
        if it.get("note"):
            tail.append(it["note"])
        lines.append(f"{i}) {it['title']}" + ((" - " + " - ".join(tail)) if tail else ""))
    return "\n".join(lines) if lines else "Список пуст. Добавьте позиции."


def render_post(items: List[Dict[str, str]]) -> str:
    header = "Новое поступление\n"
    link = f"Смотри все новинки: {NEW_ARRIVALS_URL}\n"
    body = render_items(items)
    return f"{header}\n{link}\n{body}"


def kb_main() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("➕ Добавить позицию", callback_data="na:add_hint"),
            InlineKeyboardButton("🗑 Очистить список", callback_data="na:clear_confirm"),
        ],
        [InlineKeyboardButton("🧭 Редактировать порядок", callback_data="na:edit")],
        [
            InlineKeyboardButton("👁 Предпросмотр", callback_data="na:preview"),
            InlineKeyboardButton("📣 Опубликовать", callback_data="na:publish"),
        ],
        [InlineKeyboardButton("🔗 Открыть раздел новинок", url=NEW_ARRIVALS_URL)],
    ]
    return InlineKeyboardMarkup(kb)


def kb_edit(cursor: int, total: int) -> InlineKeyboardMarkup:
    left_disabled = cursor <= 0
    right_disabled = cursor >= (total - 1)
    btn_prev = InlineKeyboardButton("◀", callback_data="na:nav_prev" if not left_disabled else "na:nop")
    btn_next = InlineKeyboardButton("▶", callback_data="na:nav_next" if not right_disabled else "na:nop")
    kb = [
        [btn_prev, InlineKeyboardButton(f"Позиция {cursor + 1} из {total}", callback_data="na:nop"), btn_next],
        [
            InlineKeyboardButton("⬆️ Выше", callback_data="na:up"),
            InlineKeyboardButton("⬇️ Ниже", callback_data="na:down"),
            InlineKeyboardButton("❌ Удалить", callback_data="na:del"),
        ],
        [InlineKeyboardButton("🔙 Готово", callback_data="na:done")],
    ]
    return InlineKeyboardMarkup(kb)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.effective_message.reply_text("Доступ ограничен.")
        return
    ensure_draft(user.id)
    await update.effective_message.reply_text(
        "Привет. Готов собрать пост «Новое поступление».\n"
        "Отправляй позиции по одной строкой в формате:\n"
        "Название | Цена | плюс (необязательно)\n\n"
        "Пример:\n"
        "Аромалампа Лотос | 399 грн | керамика, 12 см\n\n"
        "Команды:\n"
        "/new - начать новый список\n"
        "/list - показать список\n"
        "/clear - очистить список\n"
        "/preview - предпросмотр\n"
        "/publish - опубликовать\n\n"
        "Или используй кнопки ниже.",
        reply_markup=kb_main(),
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"chat_id: {update.effective_chat.id}")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        return
    DRAFTS[user.id] = {"items": [], "cursor": 0}
    await update.effective_message.reply_text("Создан новый пустой список новинок.", reply_markup=kb_main())


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        return
    draft = ensure_draft(user.id)
    await update.effective_message.reply_text(render_items(draft["items"]), disable_web_page_preview=True)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        return
    DRAFTS[user.id] = {"items": [], "cursor": 0}
    await update.effective_message.reply_text("Список очищен.", reply_markup=kb_main())


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        return
    draft = ensure_draft(user.id)
    await update.effective_message.reply_text(render_post(draft["items"]), disable_web_page_preview=False)


async def cmd_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        return

    # ✅ Захист: якщо CHANNEL_ID не заданий у Render — не падаємо, а підказуємо що зробити
    if CHANNEL_ID == 0:
        await update.effective_message.reply_text(
            "CHANNEL_ID не заданий у Render. Додай змінну CHANNEL_ID і перезапусти сервіс."
        )
        return

    draft = ensure_draft(user.id)
    if not draft["items"]:
        await update.effective_message.reply_text("Список порожній. Додайте хоча б одну позицію.")
        return

    text = render_post(draft["items"])
    await context.bot.send_message(CHANNEL_ID, text, disable_web_page_preview=False)
    await update.effective_message.reply_text("Опубліковано в канал.", reply_markup=kb_main())



async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    draft = ensure_draft(user.id)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    for ln in lines:
        draft["items"].append(parse_item_line(ln))
    await update.effective_message.reply_text(
        f"Добавлено позиций: {len(lines)}\n\nТекущий список:\n{render_items(draft['items'])}",
        reply_markup=kb_main(),
        disable_web_page_preview=True,
    )


async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not is_admin(user.id):
        await query.answer("Нет доступа")
        return

    await query.answer()
    draft = ensure_draft(user.id)
    data = query.data or "na:nop"

    if data == "na:nop":
        return

    if data == "na:add_hint":
        await query.message.reply_text(
            "Отправьте позицию одной строкой:\n"
            "Название | Цена | плюс\n\n"
            "Можно вставить сразу несколько строк — каждая станет отдельной позицией."
        )
        return

    if data == "na:clear_confirm":
        DRAFTS[user.id] = {"items": [], "cursor": 0}
        await query.message.reply_text("Список очищен.", reply_markup=kb_main())
        return

    if data == "na:preview":
        await query.message.reply_text(render_post(draft["items"]), disable_web_page_preview=False)
        return

    if data == "na:publish":
        if not draft["items"]:
            await query.message.reply_text("Список пуст. Добавьте хотя бы одну позицию.")
            return
        await context.bot.send_message(CHANNEL_ID, render_post(draft["items"]), disable_web_page_preview=False)
        await query.message.reply_text("Опубликовано в канал.", reply_markup=kb_main())
        return

    if data == "na:edit":
        if not draft["items"]:
            await query.message.reply_text("Список пуст. Сначала добавьте позиции.")
            return
        idx = max(0, min(draft["cursor"], len(draft["items"]) - 1))
        draft["cursor"] = idx
        it = draft["items"][idx]
        await query.message.reply_text(
            f"Редактирование порядка.\nТекущая позиция {idx + 1}/{len(draft['items'])}:\n"
            f"{it['title']}" + (f" - {it['price']}" if it.get("price") else "") + (f" - {it['note']}" if it.get("note") else ""),
            reply_markup=kb_edit(idx, len(draft["items"])),
        )
        return

    if data in {"na:nav_prev", "na:nav_next", "na:up", "na:down", "na:del", "na:done"}:
        items = draft["items"]
        if not items:
            await query.message.reply_text("Список пуст.")
            return
        idx = draft["cursor"]

        if data == "na:nav_prev":
            idx = max(0, idx - 1)
            draft["cursor"] = idx
        elif data == "na:nav_next":
            idx = min(len(items) - 1, idx + 1)
            draft["cursor"] = idx
        elif data == "na:up" and idx > 0:
            items[idx - 1], items[idx] = items[idx], items[idx - 1]
            idx -= 1
            draft["cursor"] = idx
        elif data == "na:down" and idx < len(items) - 1:
            items[idx + 1], items[idx] = items[idx], items[idx + 1]
            idx += 1
            draft["cursor"] = idx
        elif data == "na:del":
            removed = items.pop(idx)
            if idx >= len(items):
                idx = max(0, len(items) - 1)
            draft["cursor"] = idx
            await query.message.reply_text(
                f"Удалено: {removed['title']}\n\nТекущий список:\n{render_items(items)}",
                disable_web_page_preview=True,
            )
            if not items:
                await query.message.reply_text("Список пуст.", reply_markup=kb_main())
                return
        elif data == "na:done":
            await query.message.reply_text("Готово. Возврат в меню.", reply_markup=kb_main())
            return

        if items:
            it = items[draft["cursor"]]
            await query.message.reply_text(
                f"Позиция {draft['cursor'] + 1}/{len(items)}:\n"
                f"{it['title']}" + (f" - {it['price']}" if it.get("price") else "") + (f" - {it['note']}" if it.get("note") else ""),
                reply_markup=kb_edit(draft["cursor"], len(items)),
            )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    await update.effective_message.reply_text(
        "Команды:\n"
        "/start — меню\n"
        "/new — новый список\n"
        "/list — показать список\n"
        "/clear — очистить список\n"
        "/preview — предпросмотр\n"
        "/publish — опубликовать\n\n"
        "Отправляйте позиции строками: «Название | Цена | плюс»."
    )


def main() -> None:
    app: Application = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(CommandHandler("publish", cmd_publish))
    app.add_handler(CommandHandler("id", cmd_id))  # <-- було поза main(), тепер ок
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


