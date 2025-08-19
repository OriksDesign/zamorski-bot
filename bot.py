# bot.py
# -*- coding: utf-8 -*-
# Бот-магазину «Заморські Подарунки».
# Користувачі: клавіатура з діями (питання оператору, новинки, умови).
# Адміни: редактор «Нове надходження» з ручним порядком і публікацією.
# Потрібно: python-telegram-bot==21.4

from __future__ import annotations

import logging
import os
from typing import Dict, List, Any, Tuple

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ============================ НАЛАШТУВАННЯ ============================

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

# Якщо треба — можна оперативно публікувати не лише у CHANNEL_ID з Render.
PUBLISH_CHAT_ID = CHANNEL_ID

missing = []
if not TOKEN:
    missing.append("API_TOKEN")
if not ADMIN_IDS:
    missing.append("ADMIN_ID")
if missing:
    raise SystemExit("Не задані " + ", ".join(missing) + " у Render.")

# ============================ ЛОГЕРИ ============================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("zamorski-bot")

# ============================ СТАНИ ТА СХОВИЩА ============================

# Чернетки для адмінів (редактор «Нове надходження»)
# draft = {"items": [{"title": str, "price": str, "note": str}], "cursor": int}
DRAFTS: Dict[int, Dict[str, Any]] = {}

# Користувач натиснув «Питання оператору» і чекаємо наступне повідомлення як питання
WAITING_QUESTION: Dict[int, bool] = {}

# Мапа тікетів: (admin_id, message_id) -> user_id
# Адмін відповідає реплаєм на службове повідомлення — ми знаємо, кому слати відповідь
SUPPORT_THREADS: Dict[Tuple[int, int], int] = {}

# Лічильник тікетів (у пам’яті)
THREAD_NO = 1

# ============================ КОРИСНІ ФУНКЦІЇ ============================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def ensure_draft(user_id: int) -> Dict[str, Any]:
    if user_id not in DRAFTS:
        DRAFTS[user_id] = {"items": [], "cursor": 0}
    return DRAFTS[user_id]

def parse_item_line(text: str) -> Dict[str, str]:
    """Парсимо рядок: 'Назва | Ціна | плюс' або 'Назва - ціна' або просто 'Назва'."""
    for sep in ["—", "–", "  |  ", " | ", " - ", " — ", " – "]:
        text = text.replace(sep, "|")
    text = text.replace(" |", "|").replace("| ", "|").replace(" -", "|").replace("- ", "|")
    parts = [p.strip() for p in text.split("|") if p.strip()]
    title = parts[0] if parts else "Без назви"
    price = parts[1] if len(parts) > 1 else ""
    note  = parts[2] if len(parts) > 2 else ""
    return {"title": title, "price": price, "note": note}

def render_items(items: List[Dict[str, str]]) -> str:
    if not items:
        return "Список порожній. Додайте позиції."
    out = []
    for i, it in enumerate(items, 1):
        tail = []
        if it.get("price"):
            tail.append(it["price"])
        if it.get("note"):
            tail.append(it["note"])
        out.append(f"{i}) {it['title']}" + ((" — " + " — ".join(tail)) if tail else ""))
    return "\n".join(out)

def render_post(items: List[Dict[str, str]]) -> str:
    header = "Нове надходження\n"
    link = f"Дивись всі новинки: {NEW_ARRIVALS_URL}\n"
    return f"{header}\n{link}\n{render_items(items)}"

def kb_main_admin() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("➕ Додати позицію", callback_data="na:add_hint"),
            InlineKeyboardButton("🗑 Очистити список", callback_data="na:clear_confirm"),
        ],
        [InlineKeyboardButton("🧭 Редагувати порядок", callback_data="na:edit")],
        [
            InlineKeyboardButton("👁 Перегляд", callback_data="na:preview"),
            InlineKeyboardButton("📣 Опублікувати", callback_data="na:publish"),
        ],
        [InlineKeyboardButton("🔗 Відкрити розділ новинок", url=NEW_ARRIVALS_URL)],
    ]
    return InlineKeyboardMarkup(kb)

USER_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("Питання оператору")],
        [KeyboardButton("Новинки"), KeyboardButton("Умови співпраці")],
    ],
    resize_keyboard=True
)

# ============================ КОМАНДИ / МЕНЮ ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показуємо користувацьке меню всім. Адмін додатково бачить підказку про команди."""
    user = update.effective_user
    ensure_draft(user.id)

    greet = (
        "Вітаємо у магазині «Заморські Подарунки»!\n"
        "Оберіть дію на клавіатурі нижче або напишіть запитання у чат."
    )
    if is_admin(user.id):
        greet += (
            "\n\nРежим адміністратора: для підготовки «Нового надходження» використовуйте команди "
            "/new /list /clear /preview /publish або кнопки в адмін-меню."
        )

    await update.effective_message.reply_text(greet, reply_markup=USER_KB)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = [
        "Доступні команди:",
        "• /start — меню",
        "• /id — показати chat_id",
        "• /whoami — діагностика доступів",
    ]
    if is_admin(update.effective_user.id):
        txt += [
            "",
            "Адмін-команди для «Нового надходження»:",
            "• /new — новий список",
            "• /list — показати список",
            "• /clear — очистити список",
            "• /preview — перегляд",
            "• /publish — опублікувати у канал/чат",
            "• /dest — керувати ціллю публікації (here|env|<id>)",
        ]
    await update.effective_message.reply_text("\n".join(txt), reply_markup=USER_KB)

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"chat_id: {update.effective_chat.id}")

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "user_id: {}\nadmin_ids: {}\nCHANNEL_ID: {}\nPUBLISH_CHAT_ID: {}".format(
            update.effective_user.id, sorted(list(ADMIN_IDS)), CHANNEL_ID, PUBLISH_CHAT_ID
        )
    )

# ---------- Адмін: керування ціллю публікації ----------
async def cmd_dest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    global PUBLISH_CHAT_ID
    args = context.args or []

    if not args:
        await update.effective_message.reply_text(
            "Поточна ціль публікації: {}\n\nВаріанти:\n"
            "/dest here — публікувати у поточний чат/канал\n"
            "/dest env — використовувати CHANNEL_ID з Render\n"
            "/dest <id> — задати конкретний chat_id (числом)".format(
                PUBLISH_CHAT_ID or "не задано"
            )
        )
        return

    arg = args[0].lower()
    if arg == "here":
        PUBLISH_CHAT_ID = update.effective_chat.id
        await update.effective_message.reply_text(f"Готово! Публікуємо сюди: {PUBLISH_CHAT_ID}")
    elif arg == "env":
        PUBLISH_CHAT_ID = CHANNEL_ID
        await update.effective_message.reply_text(f"Готово! Публікуємо на CHANNEL_ID: {PUBLISH_CHAT_ID}")
    else:
        try:
            PUBLISH_CHAT_ID = int(arg)
            await update.effective_message.reply_text(f"Готово! Нова ціль публікації: {PUBLISH_CHAT_ID}")
        except ValueError:
            await update.effective_message.reply_text("Не вдалося розпізнати id. Приклад: /dest -1001234567890")

# ============================ КОРИСТУВАЧ: КНОПКИ ============================

async def handle_user_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Опрацьовуємо тексти з реплай-клавіатури."""
    user = update.effective_user
    text = (update.message.text or "").strip().lower()

    if text == "питання оператору":
        WAITING_QUESTION[user.id] = True
        await update.effective_message.reply_text(
            "Напишіть ваше питання одним повідомленням (можна додати фото/файл з підписом) — і ми відповімо якнайшвидше.",
            reply_markup=USER_KB
        )
        return

    if text == "новинки":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Відкрити сайт", url=NEW_ARRIVALS_URL)]
        ])
        await update.effective_message.reply_text(
            "Слідкуйте за новими надходженнями на нашому сайті:",
            reply_markup=kb
        )
        return

    if text == "умови співпраці":
        await update.effective_message.reply_text(
            "Наші умови співпраці:\n"
            "• Доставка Україною — Нова Пошта\n"
            "• Оплата — на рахунок або при отриманні\n"
            "• У разі браку — надішліть фото; можливий обмін або повернення коштів\n"
            "Питання? Оберіть «Питання оператору» і напишіть нам.",
            reply_markup=USER_KB
        )
        return

# ============================ САПОРТ: ПИТАННЯ/ВІДПОВІДІ ============================

async def _notify_admins_about_question(update: Update, context: ContextTypes.DEFAULT_TYPE, header_text: str):
    """Розсилаємо службове повідомлення всім адмінам і зберігаємо мапінг для відповідей."""
    global SUPPORT_THREADS
    for admin_id in ADMIN_IDS:
        # Службове повідомлення з інструкцією відповідати реплаєм
        header = await context.bot.send_message(
            admin_id,
            header_text + "\n\nВідповісти клієнту: просто зробіть «Reply» на це повідомлення і напишіть відповідь."
        )
        SUPPORT_THREADS[(admin_id, header.message_id)] = update.effective_user.id

        # Копія оригіналу (якщо це не чистий текст)
        if update.message and (update.message.photo or update.message.document or update.message.video or update.message.voice):
            copied = await context.bot.copy_message(
                chat_id=admin_id,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            SUPPORT_THREADS[(admin_id, copied.message_id)] = update.effective_user.id
        elif update.message and update.message.text:
            body = "Текст: " + update.message.text
            body_msg = await context.bot.send_message(admin_id, body)
            SUPPORT_THREADS[(admin_id, body_msg.message_id)] = update.effective_user.id

async def on_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Якщо користувач у режимі питання — відправляємо адмінам."""
    user_id = update.effective_user.id
    if not WAITING_QUESTION.get(user_id):
        return  # інші тексти ловить handle_user_buttons або ігноруємо

    WAITING_QUESTION[user_id] = False
    global THREAD_NO
    thread = THREAD_NO
    THREAD_NO += 1

    header = f"Питання від користувача {user_id}\nThread #{thread}"
    await _notify_admins_about_question(update, context, header)

    await update.effective_message.reply_text(
        "Ваше питання надіслано оператору. Дякуємо за звернення!",
        reply_markup=USER_KB
    )

async def on_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Адмін відповів реплаєм на службове повідомлення — перешлемо користувачу."""
    if not is_admin(update.effective_user.id):
        return
    reply = update.message.reply_to_message
    if not reply:
        return

    key = (update.effective_user.id, reply.message_id)
    user_id = SUPPORT_THREADS.get(key)
    if not user_id:
        return

    # Пересилаємо будь-який тип повідомлення як копію
    await context.bot.copy_message(
        chat_id=user_id,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id
    )
    await update.effective_message.reply_text("Надіслано користувачу ✅")

# ============================ АДМІН: НОВЕ НАДХОДЖЕННЯ ============================

async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    DRAFTS[update.effective_user.id] = {"items": [], "cursor": 0}
    await update.effective_message.reply_text("Створено новий порожній список новинок.", reply_markup=kb_main_admin())

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    draft = ensure_draft(update.effective_user.id)
    await update.effective_message.reply_text(render_items(draft["items"]), disable_web_page_preview=True)

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    DRAFTS[update.effective_user.id] = {"items": [], "cursor": 0}
    await update.effective_message.reply_text("Список очищено.", reply_markup=kb_main_admin())

async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    draft = ensure_draft(update.effective_user.id)
    await update.effective_message.reply_text(render_post(draft["items"]), disable_web_page_preview=False)

async def cmd_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    if not PUBLISH_CHAT_ID:
        await update.effective_message.reply_text(
            "Ціль публікації не задана. Використай /dest here або /dest <id>."
        )
        return

    draft = ensure_draft(update.effective_user.id)
    if not draft["items"]:
        await update.effective_message.reply_text("Список порожній. Додайте хоча б одну позицію.")
        return

    await context.bot.send_message(PUBLISH_CHAT_ID, render_post(draft["items"]), disable_web_page_preview=False)
    await update.effective_message.reply_text("Опубліковано ✅", reply_markup=kb_main_admin())

def kb_edit(cursor: int, total: int) -> InlineKeyboardMarkup:
    left_disabled = cursor <= 0
    right_disabled = cursor >= (total - 1)
    btn_prev = InlineKeyboardButton("◀", callback_data="na:nav_prev" if not left_disabled else "na:nop")
    btn_next = InlineKeyboardButton("▶", callback_data="na:nav_next" if not right_disabled else "na:nop")
    kb = [
        [btn_prev, InlineKeyboardButton(f"Позиція {cursor + 1} з {total}", callback_data="na:nop"), btn_next],
        [
            InlineKeyboardButton("⬆️ Вище", callback_data="na:up"),
            InlineKeyboardButton("⬇️ Нижче", callback_data="na:down"),
            InlineKeyboardButton("❌ Видалити", callback_data="na:del"),
        ],
        [InlineKeyboardButton("🔙 Готово", callback_data="na:done")],
    ]
    return InlineKeyboardMarkup(kb)

async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки адмін-редактора."""
    query = update.callback_query
    user = update.effective_user
    if not is_admin(user.id):
        await query.answer("Немає доступу")
        return

    await query.answer()
    draft = ensure_draft(user.id)
    data = query.data or "na:nop"

    if data == "na:nop":
        return

    if data == "na:add_hint":
        await query.message.reply_text(
            "Надішліть позицію одним рядком:\n"
            "Назва | Ціна | плюс\n\n"
            "Можна вставити відразу кілька рядків — кожен стане окремою позицією."
        )
        return

    if data == "na:clear_confirm":
        DRAFTS[user.id] = {"items": [], "cursor": 0}
        await query.message.reply_text("Список очищено.", reply_markup=kb_main_admin())
        return

    if data == "na:preview":
        await query.message.reply_text(render_post(draft["items"]), disable_web_page_preview=False)
        return

    if data == "na:publish":
        if not draft["items"]:
            await query.message.reply_text("Список порожній. Додайте хоча б одну позицію.")
            return
        if not PUBLISH_CHAT_ID:
            await query.message.reply_text("Ціль публікації не задана. /dest here або /dest <id>.")
            return
        await context.bot.send_message(PUBLISH_CHAT_ID, render_post(draft["items"]), disable_web_page_preview=False)
        await query.message.reply_text("Опубліковано ✅", reply_markup=kb_main_admin())
        return

    if data == "na:edit":
        if not draft["items"]:
            await query.message.reply_text("Список порожній. Спочатку додайте позиції.")
            return
        idx = max(0, min(draft["cursor"], len(draft["items"]) - 1))
        draft["cursor"] = idx
        it = draft["items"][idx]
        await query.message.reply_text(
            f"Редагування порядку.\nПоточна позиція {idx + 1}/{len(draft['items'])}:\n"
            f"{it['title']}" + (f" — {it['price']}" if it.get("price") else "") + (f" — {it['note']}" if it.get("note") else ""),
            reply_markup=kb_edit(idx, len(draft["items"])),
        )
        return

    if data in {"na:nav_prev", "na:nav_next", "na:up", "na:down", "na:del", "na:done"}:
        items = draft["items"]
        if not items:
            await query.message.reply_text("Список порожній.")
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
                f"Видалено: {removed['title']}\n\nПоточний список:\n{render_items(items)}",
                disable_web_page_preview=True,
            )
            if not items:
                await query.message.reply_text("Список порожній.", reply_markup=kb_main_admin())
                return
        elif data == "na:done":
            await query.message.reply_text("Готово. Повертаємось у меню.", reply_markup=kb_main_admin())
            return

        if items:
            it = items[draft["cursor"]]
            await query.message.reply_text(
                f"Позиція {draft['cursor'] + 1}/{len(items)}:\n"
                f"{it['title']}" + (f" — {it['price']}" if it.get("price") else "") + (f" — {it['note']}" if it.get("note") else ""),
                reply_markup=kb_edit(draft["cursor"], len(items)),
            )

# ============================ ХЕНДЛЕРИ ДОДАВАННЯ ПОЗИЦІЙ ============================

async def on_any_text_for_admin_lists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Адмін надіслав рядки для списку новинок (можна пачкою)."""
    if not is_admin(update.effective_user.id):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    draft = ensure_draft(update.effective_user.id)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    for ln in lines:
        draft["items"].append(parse_item_line(ln))
    await update.effective_message.reply_text(
        f"Додано позицій: {len(lines)}\n\nПоточний список:\n{render_items(draft['items'])}",
        reply_markup=kb_main_admin(),
        disable_web_page_preview=True,
    )

# ============================ MAIN ============================

def main() -> None:
    app: Application = ApplicationBuilder().token(TOKEN).build()

    # Базові команди
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("whoami", whoami))

    # Адмін: керування місцем публікації
    app.add_handler(CommandHandler("dest", cmd_dest))

    # Адмін: редактор «Нове надходження»
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(CommandHandler("publish", cmd_publish))
    app.add_handler(CallbackQueryHandler(on_cb))

    # Користувацькі кнопки (реплай-клавіатура)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_buttons))

    # Сапорт: очікуємо питання користувача (будь-який контент)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.DOCUMENT | filters.VIDEO | filters.VOICE) & ~filters.COMMAND,
        on_user_message
    ))

    # Адмін відповідає реплаєм на службове повідомлення — перешлемо клієнту
    app.add_handler(MessageHandler(filters.REPLY & filters.User(list(ADMIN_IDS)), on_admin_reply))

    # Текст для списку новинок (адмін), якщо це не кнопка/команда і не сапорт
    app.add_handler(MessageHandler(filters.TEXT & filters.User(list(ADMIN_IDS)) & ~filters.COMMAND, on_any_text_for_admin_lists))

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
