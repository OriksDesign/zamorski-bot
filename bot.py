import os
import re
import asyncio
import logging
from typing import Optional, List
from collections import defaultdict

import pymysql
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ForceReply,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# ============================== КОНФІГ =====================================

API_TOKEN = os.getenv("API_TOKEN", "").strip()
NEW_ARRIVALS_URL = os.getenv(
    "NEW_ARRIVALS_URL", "https://zamorskiepodarki.com/uk/novoe-postuplenie/"
).strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # -100... або @channel

# Адміни (один або кілька)
ADMIN_IDS: set[int] = set()
_admin_single = os.getenv("ADMIN_ID", "").strip()
if _admin_single:
    try:
        ADMIN_IDS.add(int(_admin_single))
    except ValueError:
        pass
for p in os.getenv("ADMIN_IDS", "").split(","):
    p = p.strip()
    if p:
        try:
            ADMIN_IDS.add(int(p))
        except ValueError:
            pass

ADMIN_ID_PRIMARY: Optional[int] = None
if _admin_single:
    try:
        ADMIN_ID_PRIMARY = int(_admin_single)
    except Exception:
        ADMIN_ID_PRIMARY = None
if ADMIN_ID_PRIMARY is None and ADMIN_IDS:
    ADMIN_ID_PRIMARY = min(ADMIN_IDS)

if not API_TOKEN:
    raise RuntimeError("Не задано API_TOKEN у змінних середовища")
if not ADMIN_IDS:
    raise RuntimeError("Не задано ADMIN_ID або ADMIN_IDS у змінних середовища")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("zamorski-bot")

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ====================== MySQL (автоперепідключення) ========================

class MySQL:
    def __init__(self):
        self.host = os.getenv("DB_HOST")
        self.user = os.getenv("DB_USER")
        self.password = os.getenv("DB_PASSWORD")
        self.database = os.getenv("DB_NAME")
        self.conn: Optional[pymysql.connections.Connection] = None
        self.connect()

    def connect(self):
        self.conn = pymysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=20,
            write_timeout=20,
        )
        logger.info("✅ MySQL connected")

    def cursor(self):
        if self.conn is None or not self.conn.open:
            self.connect()
        else:
            try:
                self.conn.ping(reconnect=True)
            except Exception:
                self.connect()
        return self.conn.cursor()

    def close(self):
        try:
            if self.conn:
                self.conn.close()
                logger.info("✅ MySQL connection closed")
        except Exception as e:
            logger.warning(f"Close MySQL failed: {e}")


db = MySQL()

# Таблиці
with db.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
          user_id BIGINT PRIMARY KEY,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS operator_threads (
          id INT AUTO_INCREMENT PRIMARY KEY,
          user_id BIGINT NOT NULL,
          question TEXT NOT NULL,
          admin_message_id BIGINT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)


# =============================== БД-хелпери ================================

def add_subscriber(user_id: int) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO subscribers (user_id) VALUES (%s) "
            "ON DUPLICATE KEY UPDATE user_id=user_id",
            (user_id,),
        )

def get_all_subscribers() -> List[int]:
    with db.cursor() as cur:
        cur.execute("SELECT user_id FROM subscribers ORDER BY created_at DESC")
        return [row["user_id"] for row in cur.fetchall()]

def remove_subscriber(user_id: int) -> None:
    with db.cursor() as cur:
        cur.execute("DELETE FROM subscribers WHERE user_id=%s", (user_id,))


# ================================ СТАНИ ====================================

class OperatorQuestion(StatesGroup):
    waiting_text = State()

class TTNRequest(StatesGroup):
    waiting_name = State()
    waiting_order = State()

class BillRequest(StatesGroup):
    waiting_name = State()
    waiting_order = State()

class SendBroadcast(StatesGroup):
    waiting_content = State()

class NewArrivals(StatesGroup):
    waiting_item = State()
    waiting_order = State()


# ============================ БОТ/ДИСПЕТЧЕР ================================

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

na_lists: dict[int, List[str]] = defaultdict(list)  # конструктор новинок


# ============================== КЛАВІАТУРИ ================================

def user_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Умови співпраці")],
            [KeyboardButton(text="Питання оператору")],
            [KeyboardButton(text="Нові надходження")],
            [KeyboardButton(text="Запитати рахунок для сплати замовлення")],
            [KeyboardButton(text="Запитати ТТН Нової пошти")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def admin_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Конструктор новинок")],
            [KeyboardButton(text="Зробити розсилку")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def main_kb(user_id: int) -> ReplyKeyboardMarkup:
    return admin_kb() if is_admin(user_id) else user_kb()

def tracking_kb(ttn: str) -> InlineKeyboardMarkup:
    url = f"https://tracking.novaposhta.ua/#/uk/parcel/tracking/{ttn}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Відстежити ТТН", url=url)]]
    )

def na_render(admin_id: int) -> str:
    items = na_lists[admin_id]
    lines = ["Нове надходження", "", "Дивіться всі новинки:", NEW_ARRIVALS_URL, ""]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}) {item}")
    return "\n".join(lines).strip()

def na_kb(admin_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Додати позицію", callback_data="na:add"),
                InlineKeyboardButton(text="🧹 Очистити список", callback_data="na:clear"),
            ],
            [InlineKeyboardButton(text="✏️ Редагувати порядок", callback_data="na:reorder")],
            [
                InlineKeyboardButton(text="👁 Перегляд", callback_data="na:preview"),
                InlineKeyboardButton(text="🚀 Опублікувати", callback_data="na:publish"),
            ],
            [InlineKeyboardButton(text="🔗 Відкрити розділ новинок", url=NEW_ARRIVALS_URL)],
        ]
    )

def extract_ttn(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b\d{14}\b", text)
    return m.group(0) if m else None


# =========================== МЕНЮ / КОМАНДИ ================================

def user_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Почати / показати меню"),
        BotCommand(command="help", description="Підказки та меню"),
        BotCommand(command="ttn", description="Запитати ТТН Нової пошти"),
        BotCommand(command="bill", description="Запитати рахунок для оплати"),
        BotCommand(command="novinki", description="Нові надходження"),
    ]

def admin_commands() -> list[BotCommand]:
    return user_commands() + [
        BotCommand(command="builder", description="Конструктор новинок"),
        BotCommand(command="broadcast", description="Зробити розсилку"),
        BotCommand(command="reply", description="Відповідь: /reply <id> <текст>"),
    ]

async def setup_bot_commands(bot: Bot):
    await bot.set_my_commands(user_commands(), scope=BotCommandScopeAllPrivateChats())
    for aid in ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(chat_id=aid))
        except Exception as e:
            logger.warning(f"set_my_commands for admin {aid} failed: {e}")


# ============================== ХЕНДЛЕРИ ===================================

@dp.message(CommandStart())
async def start(message: types.Message):
    add_subscriber(message.from_user.id)
    await message.answer(
        "Вітаємо у магазині Заморські подарунки! Оберіть дію нижче.",
        reply_markup=main_kb(message.from_user.id),
    )

@dp.message(Command("menu"), Command("help"))
async def menu(message: types.Message):
    text = (
        "Можливості бота:\n"
        "• /ttn — запитати ТТН Нової пошти\n"
        "• /bill — отримати рахунок для оплати\n"
        "• /novinki — переглянути нові надходження\n\n"
        "Нижче також є кнопки меню."
    )
    await message.answer(text, reply_markup=main_kb(message.from_user.id))

@dp.message(Command("whoami"))
async def whoami(message: types.Message):
    status = "так" if is_admin(message.from_user.id) else "ні"
    await message.answer(
        f"Ваш user_id: <code>{message.from_user.id}</code>\nАдмін: {status}",
        reply_markup=main_kb(message.from_user.id),
    )

# Командні шорткати
@dp.message(Command("ttn"))
async def cmd_ttn(message: types.Message, state: FSMContext):
    await ttn_start(message, state)

@dp.message(Command("bill"))
async def cmd_bill(message: types.Message, state: FSMContext):
    await bill_start(message, state)

@dp.message(Command("novinki"))
async def cmd_novinki(message: types.Message):
    await new_arrivals(message)

@dp.message(Command("builder"))
async def cmd_builder(message: types.Message):
    if is_admin(message.from_user.id):
        await na_open_cmd(message)

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Надішліть текст або фото з підписом для розсилки.")
    await state.set_state(SendBroadcast.waiting_content)


# -------- Користувач: кнопки

@dp.message(F.text == "Умови співпраці")
async def terms(message: types.Message):
    text = (
        "Наші умови співпраці:\n"
        "- Доставка по Україні службою Нова пошта\n"
        "- Оплата: на рахунок або при отриманні\n"
        "- У разі виявлення браку — надішліть фото; запропонуємо обмін або повернення коштів\n"
        "Якщо маєте питання — натисніть \"Питання оператору\""
    )
    await message.answer(text, reply_markup=main_kb(message.from_user.id))

@dp.message(F.text.in_({"Нові надходження", "Новинки"}))
async def new_arrivals(message: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Відкрити розділ новинок", url=NEW_ARRIVALS_URL)]]
    )
    await message.answer("Слідкуйте за новими надходженнями на нашому сайті.", reply_markup=kb)

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    await message.answer("Напишіть ваше питання. Ми відповімо якнайшвидше.")
    await state.set_state(OperatorQuestion.waiting_text)

@dp.message(OperatorQuestion.waiting_text)
async def got_question(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text or ""

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_threads (user_id, question) VALUES (%s, %s)",
            (user_id, text),
        )
        thread_id = cur.lastrowid

    note = (
        f"Питання від користувача <code>{user_id}</code>\n"
        f"Thread #{thread_id}\n\n{text}"
    )
    sent = await bot.send_message(
        ADMIN_ID_PRIMARY,
        note,
        reply_markup=ForceReply(input_field_placeholder="Напишіть відповідь користувачу…"),
    )

    with db.cursor() as cur:
        cur.execute(
            "UPDATE operator_threads SET admin_message_id=%s WHERE id=%s",
            (sent.message_id, thread_id),
        )

    await message.answer(
        "Ваше питання надіслано оператору. Дякуємо за звернення.",
        reply_markup=user_kb(),
    )
    await state.clear()

# ---- ТТН

@dp.message(F.text == "Запитати ТТН Нової пошти")
async def ttn_start(message: types.Message, state: FSMContext):
    await message.answer("Вкажіть ПІБ отримувача (як у замовленні).")
    await state.set_state(TTNRequest.waiting_name)

@dp.message(TTNRequest.waiting_name)
async def ttn_got_name(message: types.Message, state: FSMContext):
    await state.update_data(ttn_name=message.text.strip())
    await message.answer("Вкажіть номер замовлення.")
    await state.set_state(TTNRequest.waiting_order)

@dp.message(TTNRequest.waiting_order)
async def ttn_got_order(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    name = data.get("ttn_name", "-")
    order_no = message.text.strip()

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_threads (user_id, question) VALUES (%s, %s)",
            (user_id, f"[TTN]\nПІБ: {name}\nЗамовлення: {order_no}"),
        )
        thread_id = cur.lastrowid

    note = (
        f"Запит ТТН від користувача <code>{user_id}</code>\n"
        f"ПІБ: <b>{name}</b>\nЗамовлення: <b>{order_no}</b>\n"
        f"Thread #{thread_id}\n\nВідповідайте номером ТТН (14 цифр) або текстом."
    )
    sent = await bot.send_message(
        ADMIN_ID_PRIMARY,
        note,
        reply_markup=ForceReply(input_field_placeholder="Введіть ТТН або відповідь…"),
    )

    with db.cursor() as cur:
        cur.execute("UPDATE operator_threads SET admin_message_id=%s WHERE id=%s", (sent.message_id, thread_id))

    await message.answer("Дякуємо! Ми перевіримо ТТН і надішлемо вам відповідь.", reply_markup=user_kb())
    await state.clear()

# ---- Рахунок

@dp.message(F.text == "Запитати рахунок для сплати замовлення")
async def bill_start(message: types.Message, state: FSMContext):
    await message.answer("Вкажіть ПІБ платника (як у замовленні).")
    await state.set_state(BillRequest.waiting_name)

@dp.message(BillRequest.waiting_name)
async def bill_got_name(message: types.Message, state: FSMContext):
    await state.update_data(bill_name=message.text.strip())
    await message.answer("Вкажіть номер замовлення.")
    await state.set_state(BillRequest.waiting_order)

@dp.message(BillRequest.waiting_order)
async def bill_got_order(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    name = data.get("bill_name", "-")
    order_no = message.text.strip()

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_threads (user_id, question) VALUES (%s, %s)",
            (user_id, f"[BILL]\nПІБ: {name}\nЗамовлення: {order_no}"),
        )
        thread_id = cur.lastrowid

    note = (
        f"Запит РАХУНКУ від користувача <code>{user_id}</code>\n"
        f"ПІБ: <b>{name}</b>\nЗамовлення: <b>{order_no}</b>\n"
        f"Thread #{thread_id}\n\nВідповідайте реквізитами/рахунком у цьому Reply."
    )
    sent = await bot.send_message(
        ADMIN_ID_PRIMARY,
        note,
        reply_markup=ForceReply(input_field_placeholder="Надішліть реквізити/рахунок…"),
    )

    with db.cursor() as cur:
        cur.execute("UPDATE operator_threads SET admin_message_id=%s WHERE id=%s", (sent.message_id, thread_id))

    await message.answer("Дякуємо! Надішлемо вам реквізити для оплати.", reply_markup=user_kb())
    await state.clear()


# =========================== КОНСТРУКТОР НОВИНОК ===========================

@dp.message(Command("novinki", "builder", "newpost"))
async def na_open_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    uid = message.from_user.id
    await message.answer(
        f"Конструктор новинок.\nУ списку: {len(na_lists[uid])} позицій.",
        reply_markup=na_kb(uid),
    )

@dp.message(F.text == "Конструктор новинок")
async def na_open_btn(message: types.Message):
    await na_open_cmd(message)

@dp.callback_query(F.data.startswith("na:"))
async def na_callbacks(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Лише для адмінів", show_alert=True); return

    uid = cb.from_user.id
    action = cb.data.split(":")[1]

    if action == "add":
        await cb.message.answer(
            "Надішліть текст позиції (одним повідомленням).",
            reply_markup=ForceReply(input_field_placeholder="Текст позиції…"),
        )
        await state.set_state(NewArrivals.waiting_item)
        await cb.answer(); return

    if action == "clear":
        na_lists[uid].clear()
        await cb.message.answer("Список очищено.")
        await cb.message.answer(f"У списку: {len(na_lists[uid])} позицій.", reply_markup=na_kb(uid))
        await cb.answer(); return

    if action == "reorder":
        if not na_lists[uid]:
            await cb.answer("Список порожній", show_alert=True); return
        await cb.message.answer(
            "Надішліть новий порядок індексів, напр.: 2 1 3 4",
            reply_markup=ForceReply(input_field_placeholder="Порядок (напр. 2 1 3 4)"),
        )
        await state.set_state(NewArrivals.waiting_order)
        await cb.answer(); return

    if action == "preview":
        if not na_lists[uid]:
            await cb.answer("Список порожній", show_alert=True); return
        await cb.message.answer(na_render(uid))
        await cb.answer("Попередній перегляд"); return

    if action == "publish":
        if not na_lists[uid]:
            await cb.answer("Немає що публікувати", show_alert=True); return
        text = na_render(uid)
        try:
            if CHANNEL_ID:
                await bot.send_message(CHANNEL_ID, text)
            else:
                await cb.message.answer(text)
            await cb.answer("Опубліковано")
            na_lists[uid].clear()
            await cb.message.answer("Готово. Список очищено.")
        except Exception as e:
            await cb.answer("Помилка публікації", show_alert=True)
            await cb.message.answer(f"Помилка публікації: {e}")
        return


# ============================ ВІДПОВІДІ АДМІНА ============================

@dp.message()
async def admin_router(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    # reply на службове повідомлення бота
    if message.reply_to_message and message.reply_to_message.message_id:
        admin_msg_id = message.reply_to_message.message_id
        with db.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM operator_threads "
                "WHERE admin_message_id=%s ORDER BY id DESC LIMIT 1",
                (admin_msg_id,),
            )
            row = cur.fetchone()
        if row:
            uid = int(row["user_id"])
            try:
                ttn = extract_ttn(message.text or message.caption or "")
                if ttn:
                    await bot.send_message(uid, f"Ваша ТТН Нової пошти: <code>{ttn}</code>", reply_markup=tracking_kb(ttn))
                    await bot.send_message(uid, "Якщо потрібна додаткова інформація — напишіть нам 😊", reply_markup=user_kb())
                else:
                    if message.photo:
                        await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption or "", reply_markup=user_kb())
                    else:
                        await bot.send_message(uid, message.text or "", reply_markup=user_kb())
                await message.reply("Надіслано користувачу")
                return
            except TelegramForbiddenError:
                await message.reply("Користувач заблокував бота або недоступний")
                return
            except Exception as e:
                await message.reply(f"Помилка відправки: {e}")
                return

    # Альтернатива: /reply <user_id> <текст>
    if message.text and message.text.startswith("/reply"):
        parts = message.text.split(maxsplit=2)
        if len(parts) >= 3 and parts[1].isdigit():
            uid = int(parts[1]); txt = parts[2]
            try:
                ttn = extract_ttn(txt)
                if ttn:
                    await bot.send_message(uid, f"Ваша ТТН Нової пошти: <code>{ttn}</code>", reply_markup=tracking_kb(ttn))
                    await bot.send_message(uid, "Якщо потрібна додаткова інформація — напишіть нам 😊", reply_markup=user_kb())
                else:
                    await bot.send_message(uid, txt, reply_markup=user_kb())
                await message.reply("Надіслано користувачу")
            except Exception as e:
                await message.reply(f"Помилка відправки: {e}")
            return

    if message.text == "Зробити розсилку":
        await message.answer("Надішліть текст або фото з підписом для розсилки.")
        await state.set_state(SendBroadcast.waiting_content)


# =========================== РОЗСИЛКА (АДМІН) =============================

@dp.message(SendBroadcast.waiting_content, F.photo)
async def broadcast_photo(message: types.Message, state: FSMContext):
    await do_broadcast(photo_id=message.photo[-1].file_id, caption=message.caption or "")
    await state.clear()

@dp.message(SendBroadcast.waiting_content)
async def broadcast_text(message: types.Message, state: FSMContext):
    await do_broadcast(text=message.text or "")
    await state.clear()

async def do_broadcast(text: str = "", photo_id: Optional[str] = None, caption: str = ""):
    users = get_all_subscribers()
    ok = 0
    blocked = 0
    for uid in users:
        try:
            if photo_id:
                await bot.send_photo(uid, photo_id, caption=caption)
            else:
                await bot.send_message(uid, text)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            continue
        except (TelegramForbiddenError, TelegramBadRequest):
            blocked += 1
            remove_subscriber(uid)
        except Exception as e:
            logger.warning(f"Broadcast to {uid} failed: {e}")
        await asyncio.sleep(0.05)
    await bot.send_message(ADMIN_ID_PRIMARY, f"Розсилка завершена. Успішно: {ok}, видалено зі списку: {blocked}.")


# =============================== MAIN ======================================

async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await setup_bot_commands(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
