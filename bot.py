# bot.py

import os
import asyncio
import pymysql
from aiogram import Bot, Dispatcher, types
from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

# Підключення до бази даних через pymysql
connection = pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

# Створення таблиці, якщо не існує
with connection.cursor() as cursor:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)
connection.commit()

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

class SendNews(StatesGroup):
    waiting_for_photo = State()
    waiting_for_caption = State()

class OperatorChat(StatesGroup):
    waiting_for_reply = State()

class OperatorQuestion(StatesGroup):
    waiting_for_choice = State()
    waiting_for_payment = State()

def get_main_keyboard(user_id):
    buttons = [
        [types.KeyboardButton(text="Умови співпраці")],
        [types.KeyboardButton(text="Питання оператору")],
        [types.KeyboardButton(text="Новинки")],
        [types.KeyboardButton(text="Підписатися на розсилку")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([types.KeyboardButton(text="Зробити розсилку")])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True
    )

def save_user(user_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM subscribers WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        if not result:
            cursor.execute("INSERT INTO subscribers (user_id) VALUES (%s)", (user_id,))
            connection.commit()
            asyncio.create_task(bot.send_message(chat_id=ADMIN_ID, text=f"Новий підписник: {user_id}"))

def is_user_saved(user_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM subscribers WHERE user_id = %s", (user_id,))
        return cursor.fetchone() is not None

# Далі всі обробники залишаються без змін — вони вже використовують save_user та is_user_saved
# (їх не потрібно змінювати)

async def get_news_caption(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    photo_id = user_data.get("photo")
    user_text = message.text

    announcement = (
        "Новинки у 'Заморських подарунках'!"

"
        "Ми отримали нове надходження екзотичних сувенірів, ароматів та декору.

"
        "Знижки на обрані товари!

"
        "Переглянути всі новинки можна за посиланням нижче.

"
        "——————

"
    )

    full_caption = announcement + user_text

    with connection.cursor() as cursor:
        cursor.execute("SELECT user_id FROM subscribers")
        users = cursor.fetchall()

    if not users:
        await message.answer("Немає зареєстрованих користувачів для розсилки.", reply_markup=get_main_keyboard(message.from_user.id))
        await state.clear()
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти до магазину", url="https://zamorskiepodarki.com/")]
        ]
    )

    count = 0
    for user in users:
        user_id = user['user_id']
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_id,
                caption=full_caption,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            count += 1
        except Exception as e:
            print(f"Помилка при відправці користувачу {user_id}: {e}")

    await message.answer(f"Розсилка завершена. Надіслано {count} повідомлень.", reply_markup=get_main_keyboard(message.from_user.id))
    await state.clear()

# ... решта твого коду залишається без змін
