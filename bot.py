# bot.py
# -*- coding: utf-8 -*-
# «Заморські Подарунки» — клієнтський чат-бот + адмін-редактор «Нове надходження»

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Any, Tuple

import requests
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto,
)
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ===================== НАЛАШТУВАННЯ (Render env) =====================

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
NP_API_KEY = _env("NP_API_KEY", default="")  # опційно
# Оперативна ціль публікації (можна змінити /dest)
PUBLISH_CHAT_ID = CHANNEL_ID

missing = []
if not TOKEN:
    missing.append("API_TOKEN")
if not ADMIN_IDS:
    missing.append("ADMIN_ID")
if missing:
    raise SystemExit("Не задані " + ", ".join(missing) + " у Render.")

# ============================== ЛОГІНГ ===============================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("zamorski-bot")

# ============================ СХОВИЩА/СТАНИ ===========================

# Чернетки для адмінів (редактор новинок)
DRAFTS: Dict[int, Dict[str, Any]] = {}
# Очікування дій від клієнтів
WAITING_QUESTION: Dict[int, bool] = {}
WAITING_TRACK: Dict[int, bool] = {}     # чекаємо ТТН/№замовлення
WAITING_INVOICE: Dict[int, bool] = {}   # чекаємо дані для рахунку
# Мапа «службове повідомлення адміна → user_id» для відповідей
SUPPORT_THREADS: Dict[Tuple[int, int], int] = {}
THREAD_NO = 1

# ============================= УТИЛІТИ ===============================

URL_RE = re.compile(r'(https?://\S+)')

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def ensure_draft(user_id: int) -> Dict[str, Any]:
    if user_id not in DRAFTS:
        DRAFTS[user_id] = {"items": [], "cursor": 0}
    return DRAFTS[user_id]

def parse_item_line(text: str) -> Dict[str, str]:
    """
    Парсимо позицію з підпису/рядка:
    'Назва | Ціна | плюс | URL' або просто з URL у будь-якому місці.
    """
    text = (text or "").strip()
    m = URL_RE.search(text)
    url = m.group(1) if m else ""
    if url:
        text = URL_RE.sub("", text).strip()

    for sep in ["—", "–", "  |  ", " | ", " - ", " — ", " – "]:
        text = text.replace(sep, "|")
    text = text.replace(" |", "|").replace("| ", "|").replace(" -", "|").replace("- ", "|")

    parts = [p.strip() for p in text.split("|") if p.strip()]
    title = parts[0] if parts else "Без назви"
    price = parts[1] if len(parts) > 1 else ""
    note  = parts[2] if len(parts) > 2 else ""

    # photo_id — для стислого Photo, doc_id — для document-image
    return {"title": title, "price": price, "note": note, "url": url, "photo_id": "", "doc_id": ""}

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
        if it.get("url"):
            out.append(it["url"])
    return "\n".join(out)

def render_post(items: List[Dict[str, str]]) -> str:
    header = "Нове надходження\n"
    link = f"Дивись всі новинки: {NEW_ARRIVALS_URL}\n"
    return f"{header}\n{link}\n{render_items(items)}"

def render_caption(item: Dict[str, str], idx: int | None = None) -> str:
    line = f"{idx}) {item['title']}" if idx else item["title"]
    tail = []
    if item.get("price"): tail.append(item["price"])
    if item.get("note"):  tail.append(item["note"])
    if tail: line += " — " + " — ".join(tail)
    if item.get("url"):  line += "\n" + item["url"]
    return line

def np_track(ttn: str, phone: str | None = None) -> tuple[str | None, str]:
    """
    Повертає (повідомлення, причина). Якщо повідомлення None:
      reason: 'no_api' (немає ключа), 'bad' (помилка API), 'empty' (немає даних).
    """
    if not NP_API_KEY:
        return None, "no_api"
    payload = {
        "apiKey": NP_API_KEY,
        "modelName": "TrackingDocument",
        "calledMethod": "getStatusDocuments",
        "methodProperties": {"Documents": [{"DocumentNumber": ttn, "Phone": phone or ""}]}
    }
    try:
        r = requests.post("https://api.novaposhta.ua/v2.0/json/", json=payload, timeout=15)
        data = r.json()
    except Exception:
        return None, "bad"
    if not data.get("success"):
        return None, "bad"
    arr = data.get("data", [])
    if not arr:
        return None, "empty"
    d = arr[0]
    lines = [f"Статус: {d.get('Status','—')}", f"Номер ТТН: {d.get('Number','—')}"]
    if d.get("CitySender") or d.get("WarehouseSender"):
        lines.append(f"Відправлено з: {d.get('CitySender','')}, {d.get('WarehouseSender','')}".strip(", "))
    if d.get("CityRecipient") or d.get("WarehouseRecipient"):
        lines.append(f"Отримання: {d.get('CityRecipient','')}, {d.get('WarehouseRecipient','')}".strip(", "))
    if d.get("RecipientFullNameEW"):
        lines.append(f"Одержувач: {d.get('RecipientFullNameEW')}")
    if d.get("ScheduledDeliveryDate"):
        lines.append(f"Планова дата: {d.get('ScheduledDeliveryDate')}")
    if d.get("ActualDeliveryDate"):
        lines.append(f"Фактична дата: {d.get('ActualDeliveryDate')}")
    if d.get("CostOnSite"):
        lines.append(f"Оціночна вартість: {d.get('CostOnSite')} грн")
    return "\n".join(lines), ""

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
        [KeyboardButton("Стан замовлення / ТТН")],
        [KeyboardButton("Рахунок на оплату")],
        [KeyboardButton("Поділитись телефоном", request_contact=True)],
    ],
    resize_keyboard=True
)

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

# ============================= КОМАНДИ ===============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    ensure_draft(user.id)
    greet = (
        "Вітаємо у магазині «Заморські Подарунки»!\n"
        "Оберіть дію на клавіатурі нижче або напишіть запитання у чат."
    )
    if is_admin(user.id):
        greet += (
            "\n\nРежим адміністратора: для «Нового надходження» використовуйте "
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
            "Адмін-команди:",
            "• /new /list /clear /preview /publish — робота з новинками",
            "• /dest — керування ціллю публікації (here|env|<id>)",
            "• Щоб додати позицію з фото — надішліть фото/файл-зображення з підписом: 'Назва | Ціна | плюс | URL'",
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

async def cmd_dest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    global PUBLISH_CHAT_ID
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Поточна ціль публікації: {}\n\n"
            "Варіанти:\n"
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

# ======================== КОРИСТУВАЧ: МЕНЮ/КНОПКИ =======================

async def handle_user_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip().lower()

    if text == "питання оператору":
        WAITING_QUESTION[user.id] = True
        WAITING_TRACK.pop(user.id, None)
        WAITING_INVOICE.pop(user.id, None)
        await update.effective_message.reply_text(
            "Напишіть ваше питання одним повідомленням (можна додати фото/файл з підписом) — і ми відповімо якнайшвидше.",
            reply_markup=USER_KB
        )
        return

    if text == "новинки":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Відкрити сайт", url=NEW_ARRIVALS_URL)]])
        await update.effective_message.reply_text("Слідкуйте за новими надходженнями на нашому сайті:", reply_markup=kb)
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

    if text == "стан замовлення / ттн":
        WAITING_TRACK[user.id] = True
        WAITING_QUESTION.pop(user.id, None)
        WAITING_INVOICE.pop(user.id, None)
        await update.effective_message.reply_text(
            "Введіть *ТТН Нової пошти* (14 цифр) або номер вашого замовлення в магазині.\n"
            "_Порада:_ якщо у вас лише №замовлення — ми передамо запит оператору.",
            reply_markup=USER_KB,
            parse_mode="Markdown"
        )
        return

    if text == "рахунок на оплату":
        WAITING_INVOICE[user.id] = True
        WAITING_QUESTION.pop(user.id, None)
        WAITING_TRACK.pop(user.id, None)
        await update.effective_message.reply_text(
            "Вкажіть, будь ласка, *суму* та *email для рахунку*.\n"
            "Наприклад: `1250 грн, test@example.com`",
            reply_markup=USER_KB,
            parse_mode="Markdown"
        )
        return

# ======================== САПОРТ: ПИТАННЯ/ВІДПОВІДІ =====================

async def _notify_admins_about_question(update: Update, context: ContextTypes.DEFAULT_TYPE, header_text: str):
    for admin_id in ADMIN_IDS:
        header = await context.bot.send_message(
            admin_id,
            header_text + "\n\nВідповісти клієнту: зробіть «Reply» на це повідомлення і напишіть відповідь."
        )
        SUPPORT_THREADS[(admin_id, header.message_id)] = update.effective_user.id

        # докладаємо зміст
        if update.message and (update.message.photo or update.message.document or update.message.video or update.message.voice):
            copied = await context.bot.copy_message(
                chat_id=admin_id,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            SUPPORT_THREADS[(admin_id, copied.message_id)] = update.effective_user.id
        elif update.message and update.message.text:
            body_msg = await context.bot.send_message(admin_id, "Текст: " + update.message.text)
            SUPPORT_THREADS[(admin_id, body_msg.message_id)] = update.effective_user.id

async def on_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ловимо повідомлення користувача для сценаріїв: ТТН, рахунок, питання."""
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    # --- Стан замовлення / ТТН ---
    if WAITING_TRACK.get(user_id):
        WAITING_TRACK[user_id] = False
        ttn = "".join(ch for ch in text if ch.isdigit())
        if len(ttn) >= 10:
            msg, reason = np_track(ttn)
            if msg:
                await update.effective_message.reply_text(msg, reply_markup=USER_KB)
                return
            # немає API або помилка — передамо оператору
            header = f"Запит відстеження (ТТН/замовлення): {text}\nThread #{context.bot.id}"
            await _notify_admins_about_question(update, context, header)
            await update.effective_message.reply_text(
                "Передали запит оператору. Відповімо найближчим часом.",
                reply_markup=USER_KB
            )
            return
        # не схоже на ТТН — напряму оператору
        header = f"Запит стану замовлення: {text}\nThread #{context.bot.id}"
        await _notify_admins_about_question(update, context, header)
        await update.effective_message.reply_text(
            "Передали запит оператору. Відповімо найближчим часом.",
            reply_markup=USER_KB
        )
        return

    # --- Рахунок на оплату ---
    if WAITING_INVOICE.get(user_id):
        WAITING_INVOICE[user_id] = False
        header = f"Запит рахунку на оплату: {text}\nThread #{context.bot.id}"
        await _notify_admins_about_question(update, context, header)
        await update.effective_message.reply_text(
            "Запит на рахунок отримано. Оператор надішле рахунок у відповідь у цьому чаті.",
            reply_markup=USER_KB
        )
        return

    # --- Питання оператору ---
    if WAITING_QUESTION.get(user_id):
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

async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Користувач поділився контактом — передаємо адмінам."""
    c = update.message.contact
    human = f"{c.first_name or ''} {c.last_name or ''}".strip()
    txt = f"Контакт від користувача {update.effective_user.id}:\nТелефон: {c.phone_number}\nІм'я: {human}"
    for admin_id in ADMIN_IDS:
        msg = await context.bot.send_message(admin_id, txt)
        SUPPORT_THREADS[(admin_id, msg.message_id)] = update.effective_user.id
    await update.effective_message.reply_text("Дякуємо! Ми зв'яжемося з вами найближчим часом.", reply_markup=USER_KB)

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
    await context.bot.copy_message(
        chat_id=user_id,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id
    )
    await update.effective_message.reply_text("Надіслано користувачу ✅")

# ===================== АДМІН: «НОВЕ НАДХОДЖЕННЯ» =====================

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

    # фотопрев'ю перших до 10 фото
    media = []
    for i, it in enumerate(draft["items"], 1):
        if it.get("photo_id"):
            media.append(InputMediaPhoto(media=it["photo_id"], caption=render_caption(it, i)))
        if len(media) == 10:
            break
    if media:
        await update.effective_message.reply_media_group(media=media)

async def cmd_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if not PUBLISH_CHAT_ID:
        await update.effective_message.reply_text("Ціль публікації не задана. Використай /dest here або /dest <id>.")
        return
    draft = ensure_draft(update.effective_user.id)
    if not draft["items"]:
        await update.effective_message.reply_text("Список порожній. Додайте хоча б одну позицію.")
        return

    # Заголовок + лінк
    await context.bot.send_message(PUBLISH_CHAT_ID, f"Нове надходження\n\nДивись всі новинки: {NEW_ARRIVALS_URL}")

    # Відправляємо позиції: фото — альбомами (по 10), document-image — окремо, без фото — текстом
    batch: List[InputMediaPhoto] = []
    idx = 1
    for it in draft["items"]:
        if it.get("photo_id"):
            batch.append(InputMediaPhoto(media=it["photo_id"], caption=render_caption(it, idx)))
            if len(batch) == 10:
                await context.bot.send_media_group(PUBLISH_CHAT_ID, media=batch)
                batch.clear()
        elif it.get("doc_id"):
            if batch:
                await context.bot.send_media_group(PUBLISH_CHAT_ID, media=batch)
                batch.clear()
            await context.bot.send_document(PUBLISH_CHAT_ID, it["doc_id"], caption=render_caption(it, idx))
        else:
            if batch:
                await context.bot.send_media_group(PUBLISH_CHAT_ID, media=batch)
                batch.clear()
            await context.bot.send_message(PUBLISH_CHAT_ID, render_caption(it, idx), disable_web_page_preview=False)
        idx += 1
    if batch:
        await context.bot.send_media_group(PUBLISH_CHAT_ID, media=batch)

    await update.effective_message.reply_text("Опубліковано ✅", reply_markup=kb_main_admin())

async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            "Назва | Ціна | плюс | URL (за бажанням)\n\n"
            "Можна вставити одразу кілька рядків — кожен стане окремою позицією.\n"
            "Позицію з зображенням додайте як *фото* або *файл-зображення* з підписом у такому ж форматі.",
            parse_mode="Markdown"
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
        await cmd_publish(update, context)
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

    # Навігація/зміни
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
            draft["cursor"] = idx - 1
        elif data == "na:down" and idx < len(items) - 1:
            items[idx + 1], items[idx] = items[idx], items[idx + 1]
            draft["cursor"] = idx + 1
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

# ---- Адмін: додавання позиції з МЕДІА (photo або document-image) ----
async def on_admin_media_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    caption = (update.message.caption or "").strip()
    if not caption:
        await update.effective_message.reply_text("Додайте підпис у форматі: «Назва | Ціна | плюс | URL».")
        return

    item = parse_item_line(caption)

    if update.message.photo:
        item["photo_id"] = update.message.photo[-1].file_id
    elif update.message.document:
        mt = update.message.document.mime_type or ""
        if mt.startswith("image/"):
            item["doc_id"] = update.message.document.file_id
        else:
            await update.effective_message.reply_text("Це не схоже на зображення. Надішліть фото або файл-картинку.")
            return
    else:
        await update.effective_message.reply_text("Підтримуються фото або файли-зображення з підписом.")
        return

    draft = ensure_draft(update.effective_user.id)
    draft["items"].append(item)
    await update.effective_message.reply_text(
        "Додано позицію з зображенням.\n\nПоточний список:\n" + render_items(draft["items"]),
        reply_markup=kb_main_admin(),
        disable_web_page_preview=True,
    )

# ---- Адмін: додавання позицій ТЕКСТОМ (можна пачкою) ----
async def on_admin_list_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

# ================================ MAIN ================================

def main() -> None:
    app: Application = ApplicationBuilder().token(TOKEN).build()

    # Команди
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("dest", cmd_dest))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(CommandHandler("publish", cmd_publish))

    # Адмін: callback-кнопки редактора
    app.add_handler(CallbackQueryHandler(on_cb))

    # Адмін: відповіді реплаєм на службові повідомлення клієнтів
    app.add_handler(MessageHandler(filters.REPLY & filters.User(list(ADMIN_IDS)), on_admin_reply))

    # Адмін: додавання позицій із зображеннями (фото або документ-image) — ПЕРЕД текстовим хендлером
    app.add_handler(MessageHandler(filters.ATTACHMENT & filters.User(list(ADMIN_IDS)), on_admin_media_item))

    # Адмін: додавання позицій текстом (ставимо ПЕРЕД загальними текстами)
    app.add_handler(MessageHandler(filters.TEXT & filters.User(list(ADMIN_IDS)) & ~filters.COMMAND, on_admin_list_text))

    # Користувач: поділитись телефоном
    app.add_handler(MessageHandler(filters.CONTACT, on_contact))

    # Користувач: кнопки (звичайний текст без команд)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_buttons))

    # Користувач: повідомлення для сценаріїв (ТТН/рахунок/питання) — текст або вкладення
    app.add_handler(MessageHandler((filters.TEXT | filters.ATTACHMENT) & ~filters.COMMAND, on_user_message))

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
