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

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    buttons = [
        [types.KeyboardButton(text="📦 Надіслати ТТН по замовленню")],
        [types.KeyboardButton(text="📞 Зв’язок з оператором")],
        [types.KeyboardButton(text="💳 Я надіслав оплату")],
        [types.KeyboardButton(text="📦 Коли очікувати доставку")],
        [types.KeyboardButton(text="❌ Скасувати замовлення")],
        [types.KeyboardButton(text="🏠 Змінити адресу доставки")],
        [types.KeyboardButton(text="⬅️ Повернутися до меню")]
    ]
    await message.answer("Будь ласка, оберіть ваше питання:", reply_markup=types.ReplyKeyboardMarkup(
        keyboard=buttons, resize_keyboard=True, is_persistent=True
    ))
    await state.set_state(OperatorQuestion.waiting_for_choice)

@dp.message(OperatorQuestion.waiting_for_choice)
async def handle_operator_choice(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    choice = message.text.strip()

    if choice == "⬅️ Повернутися до меню":
        await message.answer("Повертаємось до головного меню:", reply_markup=get_main_keyboard(user_id))
        await state.clear()
        return

    elif choice == "💳 Я надіслав оплату":
        await message.answer("Будь ласка, надішліть фото для підтвердження оплати:")
        await state.set_state(OperatorQuestion.waiting_for_payment)
        return

    await bot.send_message(chat_id=ADMIN_ID, text=f"Клієнт {user_id} просить: {choice}")
    await message.answer("Ваш запит відправлений оператору.")
    await state.clear()

@dp.message(OperatorQuestion.waiting_for_payment, F.photo)
async def handle_payment_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    photo_id = message.photo[-1].file_id
    caption = f"Клієнт {user_id} надіслав підтвердження оплати."
    await bot.send_photo(chat_id=ADMIN_ID, photo=photo_id, caption=caption)
    await message.answer("Дякуємо! Ваше підтвердження надіслано оператору.")
    await state.clear()

# ... решта твого коду залишається без змін (додавання новин, відповіді оператора, /start і т.д.)
