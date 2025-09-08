import os
import re
import csv
import io
import asyncio
import logging
import time
from typing import Optional, List, Tuple, Dict

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
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.dispatcher.middlewares.base import BaseMiddleware

# ============================== КОНФІГ =====================================

API_TOKEN = os.getenv("API_TOKEN", "").strip()
ERROR_CHAT_ID_RAW = os.getenv("ERROR_CHAT_ID", "").strip()  # -100..., @channel або пусто

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("zamorski-bot")

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def as_chat_id(raw: str) -> Optional[int | str]:
    if not raw:
        return None
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except Exception:
        return raw

ERROR_CHAT_ID = as_chat_id(ERROR_CHAT_ID_RAW)

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

# Таблиці (якщо не існують)
with db.cursor() as cur:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscribers (
            user_id BIGINT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_threads (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            question TEXT NOT NULL,
            admin_message_id BIGINT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS error_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            place VARCHAR(64) NOT NULL,
            detail TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )

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

def get_subscribers_full() -> List[Tuple[int, str]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT user_id, DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') AS created_at "
            "FROM subscribers ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
        return [(int(r["user_id"]), r["created_at"]) for r in rows]

def remove_subscriber(user_id: int) -> None:
    with db.cursor() as cur:
        cur.execute("DELETE FROM subscribers WHERE user_id=%s", (user_id,))

def count_threads(period_days: int | None = None) -> int:
    with db.cursor() as cur:
        if period_days is None:
            cur.execute("SELECT COUNT(*) AS c FROM operator_threads")
        else:
            cur.execute(
                "SELECT COUNT(*) AS c FROM operator_threads "
                "WHERE created_at >= NOW() - INTERVAL %s DAY",
                (int(period_days),),
            )
        return int(cur.fetchone()["c"])

def count_errors(period_days: int | None = None) -> int:
    with db.cursor() as cur:
        if period_days is None:
            cur.execute("SELECT COUNT(*) AS c FROM error_logs")
        else:
            cur.execute(
                "SELECT COUNT(*) AS c FROM error_logs "
                "WHERE created_at >= NOW() - INTERVAL %s DAY",
                (int(period_days),),
            )
        return int(cur.fetchone()["c"])

def save_error(place: str, detail: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO error_logs (place, detail) VALUES (%s, %s)",
            (place[:64], detail[:65535]),
        )

# ============================ СЕРВІСНІ Ф-ЦІЇ ===============================

async def report_error(place: str, detail: str):
    save_error(place, detail)
    logger.error("%s | %s", place, detail)
    if ERROR_CHAT_ID:
        try:
            msg = f"⚠️ <b>Помилка</b>\n<b>Де:</b> {place}\n<b>Деталі:</b> <code>{detail}</code>"
            await bot.send_message(ERROR_CHAT_ID, msg)
        except Exception as e:
            logger.warning(f"Failed to send error to log chat: {e}")

def extract_ttn(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b\d{14}\b", text)
    return m.group(0) if m else None

def tracking_kb(ttn: str) -> InlineKeyboardMarkup:
    url = f"https://tracking.novaposhta.ua/#/uk/parcel/tracking/{ttn}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Відстежити ТТН", url=url)]]
    )

# ============================ АНТИСПАМ =====================================

class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.7, burst_cnt: int = 6, burst_window: float = 10.0):
        self.rate = rate
        self._last: dict[int, float] = {}
        self.burst_cnt = burst_cnt
        self.burst_window = burst_window
        self._hist: dict[int, List[float]] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.id:
            now = time.monotonic()
            # simple rate
            last = self._last.get(user.id, 0.0)
            if now - last < self.rate:
                return
            self._last[user.id] = now
            # burst check
            hist = self._hist.setdefault(user.id, [])
            hist.append(now)
            self._hist[user.id] = [t for t in hist if now - t <= self.burst_window]
            if len(self._hist[user.id]) > self.burst_cnt:
                return
        return await handler(event, data)

# ============================ БОТ/ДИСПЕТЧЕР ================================

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.message.outer_middleware(ThrottleMiddleware(0.7, 6, 10.0))

# ============================== КЛАВІАТУРИ ================================

BACK_BTN = "⬅️ Назад у меню"

def main_kb(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Умови співпраці")],
        [KeyboardButton(text="Питання оператору")],
        [KeyboardButton(text="Новинки")],
        [KeyboardButton(text="Запитати рахунок для сплати замовлення")],
        [KeyboardButton(text="Запитати ТТН по замовленню")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton(text="Зробити розсилку")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)

def back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BACK_BTN)]],
        resize_keyboard=True,
        is_persistent=True,
    )

# --------- Швидкі відповіді (інлайн для адміна)

TEMPLATES: dict[str, str] = {
    "thanks": "Дякуємо за оплату! Замовлення готуємо до відправки.",
    "shipped": "Замовлення відправлено. ТТН надамо найближчим часом.",
    "wait": "Замовлення в роботі. Просимо трохи зачекати — ми вже опрацюємо ваше звернення.",
    "hello": "Дякуємо за звернення! Відповімо найближчим часом.",
}

def templates_kb(uid: int) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(text="🙏 Дякуємо за оплату", callback_data=f"tpl|{uid}|thanks")]
    row2 = [
        InlineKeyboardButton(text="📦 Відправлено", callback_data=f"tpl|{uid}|shipped"),
        InlineKeyboardButton(text="⏳ Очікуйте", callback_data=f"tpl|{uid}|wait"),
    ]
    row3 = [InlineKeyboardButton(text="👋 Прийняли", callback_data=f"tpl|{uid}|hello")]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3])

# =========================== КОМАНДИ БОТА ==================================

def user_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Почати / показати меню"),
        BotCommand(command="menu", description="Відкрити меню"),
        BotCommand(command="cancel", description="Скасувати дію"),
    ]

def admin_commands() -> list[BotCommand]:
    return user_commands() + [
        BotCommand(command="reply", description="Відповідь: /reply <id> <текст>"),
        BotCommand(command="broadcast", description="Зробити розсилку"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="export", description="Експорт підписників (CSV)"),
    ]

async def setup_bot_commands(bot: Bot):
    await bot.set_my_commands(user_commands(), scope=BotCommandScopeAllPrivateChats())
    for aid in ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(chat_id=aid))
        except Exception as e:
            logger.warning(f"set_my_commands for admin {aid} failed: {e}")

# ================================ СТАНИ ====================================

class SendBroadcast(StatesGroup):
    waiting_content = State()

class OperatorQuestion(StatesGroup):
    waiting_text = State()

class TTNRequest(StatesGroup):
    waiting_name = State()
    waiting_order = State()

class BillRequest(StatesGroup):
    waiting_name = State()
    waiting_order = State()

# ------------------------- АЛІАСИ ДЛЯ REPLY-ID -----------------------------
# key: admin_message_id (будь-яке службове), value: (user_id, is_ttn_thread)
reply_alias: Dict[int, Tuple[int, bool]] = {}

# ============================== ХЕНДЛЕРИ ===================================

@dp.message(CommandStart())
async def start(message: types.Message):
    add_subscriber(message.from_user.id)
    await message.answer(
        "Вітаємо у магазині Заморські подарунки! Оберіть дію нижче.",
        reply_markup=main_kb(message.from_user.id),
    )

@dp.message(Command("menu"))
async def menu(message: types.Message):
    await message.answer("Головне меню", reply_markup=main_kb(message.from_user.id))

@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Скасовано. Повертаю меню.", reply_markup=main_kb(message.from_user.id))

@dp.message(Command("whoami"))
async def whoami(message: types.Message):
    status = "так" if is_admin(message.from_user.id) else "ні"
    await message.answer(
        f"Ваш user_id: <code>{message.from_user.id}</code>\nАдмін: {status}",
        reply_markup=main_kb(message.from_user.id),
    )

# -------- Інформація

@dp.message(F.text == "Умови співпраці")
async def terms(message: types.Message):
    text = (
        "Наші умови співпраці:\n"
        "• Доставка по Україні службою Нова пошта\n"
        "• Оплата: на рахунок або при отриманні\n"
        "• У разі виявлення браку — надішліть фото; запропонуємо обмін або повернення коштів\n\n"
        "Якщо маєте питання — натисніть «Питання оператору»."
    )
    await message.answer(text, reply_markup=main_kb(message.from_user.id))

@dp.message(F.text == "Новинки")
async def news(message: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Відкрити сайт", url="https://zamorskiepodarki.com/uk")]
        ]
    )
    await message.answer("Слідкуйте за новинками на нашому сайті.", reply_markup=kb)

# ----------------------- Питання оператору ---------------------------------

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    await message.answer("Напишіть ваше питання. Ми відповімо якнайшвидше.", reply_markup=back_kb())
    await state.set_state(OperatorQuestion.waiting_text)

@dp.message(OperatorQuestion.waiting_text)
async def got_question(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text or ""
    try:
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
        # Швидкі шаблони під рукою
        tpl_msg = await bot.send_message(ADMIN_ID_PRIMARY, "Швидкі відповіді:", reply_markup=templates_kb(user_id))
        reply_alias[tpl_msg.message_id] = (user_id, False)

        await message.answer(
            "Ваше питання надіслано оператору. Дякуємо за звернення.",
            reply_markup=main_kb(message.from_user.id),
        )
    except Exception as e:
        await report_error("got_question", str(e))
        await message.answer("Сталася помилка. Спробуйте пізніше.")
    finally:
        await state.clear()

# ----------------------- Запит ТТН -----------------------------------------

@dp.message(F.text == "Запитати ТТН по замовленню")
async def ttn_start(message: types.Message, state: FSMContext):
    await message.answer("Вкажіть ПІБ отримувача (як у замовленні).", reply_markup=back_kb())
    await state.set_state(TTNRequest.waiting_name)

@dp.message(TTNRequest.waiting_name)
async def ttn_name(message: types.Message, state: FSMContext):
    await state.update_data(ttn_name=(message.text or "").strip())
    await message.answer("Вкажіть номер замовлення.")
    await state.set_state(TTNRequest.waiting_order)

@dp.message(TTNRequest.waiting_order)
async def ttn_order(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    name = data.get("ttn_name", "-")
    order_no = (message.text or "").strip()

    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_threads (user_id, question) VALUES (%s, %s)",
                (user_id, f"[TTN]\nПІБ: {name}\nЗамовлення: {order_no}"),
            )
            thread_id = cur.lastrowid

        note = (
            f"Запит ТТН від користувача <code>{user_id}</code>\n"
            f"ПІБ: <b>{name}</b>\nЗамовлення: <b>{order_no}</b>\n"
            f"Thread #{thread_id}\n\n"
            f"Відповідайте реплаєм: вкажіть номер ТТН (14 цифр)."
        )
        sent = await bot.send_message(
            ADMIN_ID_PRIMARY,
            note,
            reply_markup=ForceReply(input_field_placeholder="Введіть ТТН або відповідь…"),
        )
        with db.cursor() as cur:
            cur.execute("UPDATE operator_threads SET admin_message_id=%s WHERE id=%s", (sent.message_id, thread_id))

        tpl_msg = await bot.send_message(ADMIN_ID_PRIMARY, "Швидкі відповіді:", reply_markup=templates_kb(user_id))
        reply_alias[tpl_msg.message_id] = (user_id, True)

        await message.answer("Дякуємо! Ми перевіримо ТТН і надішлемо вам відповідь.", reply_markup=main_kb(user_id))
    except Exception as e:
        await report_error("ttn_order", str(e))
        await message.answer("Сталася помилка. Спробуйте пізніше.", reply_markup=main_kb(user_id))
    finally:
        await state.clear()

# ----------------------- Запит Рахунку -------------------------------------

@dp.message(F.text == "Запитати рахунок для сплати замовлення")
async def bill_start(message: types.Message, state: FSMContext):
    await message.answer("Вкажіть ПІБ платника (як у замовленні).", reply_markup=back_kb())
    await state.set_state(BillRequest.waiting_name)

@dp.message(BillRequest.waiting_name)
async def bill_name(message: types.Message, state: FSMContext):
    await state.update_data(bill_name=(message.text or "").strip())
    await message.answer("Вкажіть номер замовлення.")
    await state.set_state(BillRequest.waiting_order)

@dp.message(BillRequest.waiting_order)
async def bill_order(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    name = data.get("bill_name", "-")
    order_no = (message.text or "").strip()

    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_threads (user_id, question) VALUES (%s, %s)",
                (user_id, f"[BILL]\nПІБ: {name}\nЗамовлення: {order_no}"),
            )
            thread_id = cur.lastrowid

        note = (
            f"Запит РАХУНКУ від користувача <code>{user_id}</code>\n"
            f"ПІБ: <b>{name}</b>\nЗамовлення: <b>{order_no}</b>\n"
            f"Thread #{thread_id}\n\n"
            f"Надішліть реквізити/рахунок у цьому reply."
        )
        sent = await bot.send_message(
            ADMIN_ID_PRIMARY,
            note,
            reply_markup=ForceReply(input_field_placeholder="Надішліть реквізити/рахунок…"),
        )
        with db.cursor() as cur:
            cur.execute("UPDATE operator_threads SET admin_message_id=%s WHERE id=%s", (sent.message_id, thread_id))

        tpl_msg = await bot.send_message(ADMIN_ID_PRIMARY, "Швидкі відповіді:", reply_markup=templates_kb(user_id))
        reply_alias[tpl_msg.message_id] = (user_id, False)

        await message.answer("Дякуємо! Надішлемо вам реквізити для оплати.", reply_markup=main_kb(user_id))
    except Exception as e:
        await report_error("bill_order", str(e))
        await message.answer("Сталася помилка. Спробуйте пізніше.", reply_markup=main_kb(user_id))
    finally:
        await state.clear()

# ============================ ВІДПОВІДІ АДМІНА ============================

@dp.callback_query(F.data.startswith("tpl|"))
async def template_send(cb: types.CallbackQuery):
    try:
        _, uid_str, key = cb.data.split("|", 2)
        uid = int(uid_str)
        text = TEMPLATES.get(key)
        if not text:
            await cb.answer("Невідомий шаблон", show_alert=True); return
        await bot.send_message(uid, text, reply_markup=main_kb(uid))
        await cb.answer("Надіслано")
    except Exception as e:
        await report_error("template_send", str(e))
        await cb.answer("Помилка", show_alert=True)

# 1) ВІДПОВІДЬ РЕПЛАЄМ НА СЛУЖБОВЕ ПОВІДОМЛЕННЯ
@dp.message(F.reply_to_message)
async def admin_reply_to_service(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    admin_msg_id = message.reply_to_message.message_id
    try:
        uid: Optional[int] = None
        is_ttn_thread = False
        qtext = ""

        # Спершу шукаємо у БД основний service message
        with db.cursor() as cur:
            cur.execute(
                "SELECT user_id, question FROM operator_threads "
                "WHERE admin_message_id=%s ORDER BY id DESC LIMIT 1",
                (admin_msg_id,),
            )
            row = cur.fetchone()
        if row:
            uid = int(row["user_id"])
            qtext = row.get("question") or ""
            is_ttn_thread = "[TTN]" in qtext

        # Якщо не знайшли — дивимось у alias map (для ForceReply/кнопок)
        if uid is None and admin_msg_id in reply_alias:
            uid, is_ttn_thread = reply_alias[admin_msg_id]

        if uid is None:
            await message.reply("Не вдалося визначити одержувача (не reply на службове).")
            return

        ttn = extract_ttn(message.text or message.caption or "")

        if is_ttn_thread and not ttn:
            warn = await message.reply(
                "Це запит ТТН: номер має містити 14 цифр. Будь ласка, введіть правильний ТТН.",
                reply_markup=ForceReply(input_field_placeholder="Вкажіть номер ТТН (14 цифр)"),
            )
            # Прив’язуємо і це повідомлення
            reply_alias[warn.message_id] = (uid, True)
            return

        if ttn:
            rest = (message.text or message.caption or "")
            rest = re.sub(re.escape(ttn), "", rest).strip()
            text_out = f"Ваша ТТН Нової пошти: <code>{ttn}</code>"
            if rest:
                text_out += f"\n{rest}"
            await bot.send_message(uid, text_out, reply_markup=tracking_kb(ttn))
            await bot.send_message(
                uid,
                "Якщо маєте ще питання — натисніть «Питання оператору».",
                reply_markup=main_kb(uid),
            )
        else:
            if message.photo:
                await bot.send_photo(
                    uid, message.photo[-1].file_id,
                    caption=message.caption or "", reply_markup=main_kb(uid)
                )
            else:
                await bot.send_message(uid, message.text or "", reply_markup=main_kb(uid))

        await message.reply("Надіслано користувачу")
    except TelegramForbiddenError:
        await message.reply("Користувач заблокував бота або недоступний")
    except Exception as e:
        await report_error("admin_reply_to_service", str(e))
        await message.reply(f"Помилка відправки: {e}")

# 2) АЛЬТЕРНАТИВА: /reply <user_id> <текст>
@dp.message(Command("reply"))
async def reply_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.reply("Формат: /reply <user_id> <текст>")
        return

    uid = int(parts[1])
    txt = parts[2]
    try:
        ttn = extract_ttn(txt)
        if ttn:
            rest = re.sub(re.escape(ttn), "", txt).strip()
            text_out = f"Ваша ТТН Нової пошти: <code>{ttn}</code>"
            if rest:
                text_out += f"\n{rest}"
            await bot.send_message(uid, text_out, reply_markup=tracking_kb(ttn))
            await bot.send_message(uid, "Якщо маєте ще питання — натисніть «Питання оператору».", reply_markup=main_kb(uid))
        else:
            await bot.send_message(uid, txt, reply_markup=main_kb(uid))
        await message.reply("Надіслано користувачу")
    except Exception as e:
        await report_error("reply_cmd", str(e))
        await message.reply(f"Помилка відправки: {e}")

# 3) РОЗСИЛКА (кнопка)
@dp.message(F.text == "Зробити розсилку")
async def start_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Надішліть текст або фото з підписом для розсилки.", reply_markup=back_kb())
    await state.set_state(SendBroadcast.waiting_content)

# =========================== РОЗСИЛКА (АДМІН) =============================

@dp.message(SendBroadcast.waiting_content, F.photo)
async def broadcast_photo(message: types.Message, state: FSMContext):
    await do_broadcast(photo_id=message.photo[-1].file_id, caption=message.caption or "")
    await state.clear()
    await message.answer("Розсилку завершено ✅", reply_markup=main_kb(message.from_user.id))

@dp.message(SendBroadcast.waiting_content)
async def broadcast_text(message: types.Message, state: FSMContext):
    await do_broadcast(text=message.text or "")
    await state.clear()
    await message.answer("Розсилку завершено ✅", reply_markup=main_kb(message.from_user.id))

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
            await asyncio.sleep(e.retry_after + 1); continue
        except (TelegramForbiddenError, TelegramBadRequest):
            blocked += 1; remove_subscriber(uid)
        except Exception as e:
            await report_error("broadcast_loop", f"{uid}: {e}")
        await asyncio.sleep(0.05)
    try:
        await bot.send_message(ADMIN_ID_PRIMARY, f"Розсилка завершена. Успішно: {ok}, видалено зі списку: {blocked}.")
    except Exception:
        pass

# =========================== АДМІН: СТАТИСТИКА/ЕКСПОРТ ====================

@dp.message(Command("stats"))
async def stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        total_subs = len(get_all_subscribers())
        threads7 = count_threads(7)
        threads_total = count_threads(None)
        err7 = count_errors(7)
        err_total = count_errors(None)
        text = (
            "<b>Статистика</b>\n"
            f"👥 Підписників: <b>{total_subs}</b>\n"
            f"💬 Тредів за 7 днів: <b>{threads7}</b>\n"
            f"💬 Тредів всього: <b>{threads_total}</b>\n"
            f"⚠️ Помилки за 7 днів: <b>{err7}</b>\n"
            f"⚠️ Помилки всього: <b>{err_total}</b>"
        )
        await message.answer(text)
    except Exception as e:
        await report_error("stats", str(e))
        await message.answer("Не вдалося отримати статистику.")

@dp.message(Command("export"))
async def export_csv(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        rows = get_subscribers_full()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["user_id", "created_at"])
        for uid, created in rows:
            writer.writerow([uid, created])
        data = buf.getvalue().encode("utf-8")
        file = BufferedInputFile(data, filename="subscribers.csv")
        await message.answer_document(file, caption=f"Експортовано: {len(rows)} записів")
    except Exception as e:
        await report_error("export_csv", str(e))
        await message.answer("Не вдалося сформувати CSV.")

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
