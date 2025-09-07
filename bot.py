import os
import re
import asyncio
import logging
import time
from typing import Optional, List

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
from aiogram.dispatcher.middlewares.base import BaseMiddleware


# ---------------------------------------------------------------------------
# Конфігурація
# ---------------------------------------------------------------------------
API_TOKEN = os.getenv("API_TOKEN", "").strip()

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


# ---------------------------------------------------------------------------
# Підключення до MySQL з авто-перепідключенням
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Хелпери БД
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# FSM стани
# ---------------------------------------------------------------------------
class SendBroadcast(StatesGroup):
    waiting_content = State()

class OperatorQuestion(StatesGroup):
    waiting_text = State()


# ---------------------------------------------------------------------------
# Анти-спам (тротлінг) middleware: 1 повідомлення / 0.7s
# ---------------------------------------------------------------------------
class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.7):
        self.rate = rate
        self._last: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.id:
            now = time.monotonic()
            last = self._last.get(user.id, 0.0)
            if now - last < self.rate:
                return  # тихо ігноруємо
            self._last[user.id] = now
        return await handler(event, data)


# ---------------------------------------------------------------------------
# Бот / Диспетчер
# ---------------------------------------------------------------------------
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.message.outer_middleware(ThrottleMiddleware(0.7))  # вмикаємо тротлінг


# ---------------------------------------------------------------------------
# Клавіатури
# ---------------------------------------------------------------------------
BACK_BTN = "⬅️ Назад у меню"

def main_kb(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Умови співпраці")],
        [KeyboardButton(text="Питання оператору")],
        [KeyboardButton(text="Новинки")],
        [KeyboardButton(text="Підписатися на розсилку")],
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


# ---------------------------------------------------------------------------
# Команди бота (без /help)
# ---------------------------------------------------------------------------
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
    ]

async def setup_bot_commands(bot: Bot):
    await bot.set_my_commands(user_commands(), scope=BotCommandScopeAllPrivateChats())
    for aid in ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(chat_id=aid))
        except Exception as e:
            logger.warning(f"set_my_commands for admin {aid} failed: {e}")


# ---------------------------------------------------------------------------
# Утиліти
# ---------------------------------------------------------------------------
async def typing(chat_id: int):
    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Хендлери
# ---------------------------------------------------------------------------
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

@dp.message(F.text == BACK_BTN)
async def go_back(message: types.Message, state: FSMContext):
    await state.clear()
    await menu(message)


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

@dp.message(F.text == "Підписатися на розсилку")
async def subscribe(message: types.Message):
    add_subscriber(message.from_user.id)
    await message.answer("Готово. Ви у списку розсилки.", reply_markup=main_kb(message.from_user.id))


# ----------------------- Питання оператору -------------------------------
@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    await typing(message.chat.id)
    await message.answer(
        "Напишіть ваше питання. Ми відповімо якнайшвидше.",
        reply_markup=back_kb(),
    )
    await state.set_state(OperatorQuestion.waiting_text)

@dp.message(OperatorQuestion.waiting_text)
async def got_question(message: types.Message, state: FSMContext):
    await typing(message.chat.id)
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
        reply_markup=main_kb(message.from_user.id),
    )
    await state.clear()


# ----------------------- Адмін-роутер ------------------------------------
@dp.message()
async def admin_router(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    # 1) Відповідь реплаєм на службове повідомлення
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
                if message.photo:
                    await bot.send_photo(
                        uid, message.photo[-1].file_id, caption=message.caption or "",
                        reply_markup=main_kb(uid),
                    )
                else:
                    await bot.send_message(uid, message.text or "", reply_markup=main_kb(uid))
                await message.reply("Надіслано користувачу")
                return
            except TelegramForbiddenError:
                await message.reply("Користувач заблокував бота або недоступний")
                return
            except Exception as e:
                await message.reply(f"Помилка відправки: {e}")
                return

    # 2) Альтернатива без reply: /reply <user_id> <текст>
    if message.text and message.text.startswith("/reply"):
        parts = message.text.split(maxsplit=2)
        if len(parts) >= 3 and parts[1].isdigit():
            uid = int(parts[1]); txt = parts[2]
            try:
                await bot.send_message(uid, txt, reply_markup=main_kb(uid))
                await message.reply("Надіслано користувачу")
            except Exception as e:
                await message.reply(f"Помилка відправки: {e}")
            return

    # 3) Розсилка
    if message.text == "Зробити розсилку":
        await message.answer("Надішліть текст або фото з підписом для розсилки.", reply_markup=back_kb())
        await state.set_state(SendBroadcast.waiting_content)


# ----------------------- Розсилка ----------------------------------------
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
            await asyncio.sleep(e.retry_after + 1)
            continue
        except (TelegramForbiddenError, TelegramBadRequest):
            blocked += 1
            remove_subscriber(uid)
        except Exception as e:
            logger.warning(f"Broadcast to {uid} failed: {e}")
        await asyncio.sleep(0.05)  # анти-флуд

    try:
        await bot.send_message(
            ADMIN_ID_PRIMARY,
            f"Розсилка завершена. Успішно: {ok}, видалено зі списку: {blocked}.",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Точка входу
# ---------------------------------------------------------------------------
async def main():
    try:
        # важливо, щоб не було конфлікту webhook/getUpdates
        await bot.delete_webhook(drop_pending_updates=True)
        await setup_bot_commands(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
