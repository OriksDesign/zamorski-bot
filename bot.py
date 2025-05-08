# bot.py

import os
import asyncio
import pymysql
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

# Підключення до бази даних через pymysql
try:
    connection = pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    print("✅ Database connection successful")
except pymysql.MySQLError as e:
    print(f"❌ Database connection failed: {e}")

# Створення таблиці, якщо не існує
try:
    with connection.cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        ''')
    connection.commit()
    print("✅ Subscribers table ready")
except pymysql.MySQLError as e:
    print(f"❌ Failed to create subscribers table: {e}")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

class SendNews(StatesGroup):
    waiting_for_photo = State()
    waiting_for_caption = State()

class OperatorQuestion(StatesGroup):
    waiting_for_question = State()

async def log_error(error_message):
    try:
        await bot.send_message(ADMIN_ID, f"🚨 Помилка: {error_message}")
    except Exception as e:
        print(f"Не вдалося надіслати повідомлення про помилку адміну: {e}")


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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    try:
        print(f"✅ Start command received from {message.from_user.id}")
        save_user(message.from_user.id)
        await message.answer("Вітаємо у магазині Заморські подарунки! Оберіть, будь ласка:", reply_markup=get_main_keyboard(message.from_user.id))
    except Exception as e:
        await log_error(f"Помилка при старті: {e}")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    try:
        print(f"✅ Help command received from {message.from_user.id}")
        await message.answer("""\n/start – Почати спілкування\n/help – Як працює бот\n/sendnews – Для адміністратора (розсилка)\n""", reply_markup=get_main_keyboard(message.from_user.id))
    except Exception as e:
        await log_error(f"Помилка при виклику /help: {e}")

@dp.message(F.text == "Підписатися на розсилку")
async def subscribe_user(message: types.Message):
    try:
        print(f"✅ Subscribe command received from {message.from_user.id}")
        if is_user_saved(message.from_user.id):
            await message.answer("Ви вже підписані на розсилку!", reply_markup=get_main_keyboard(message.from_user.id))
        else:
            save_user(message.from_user.id)
            await message.answer("Ви успішно підписалися на розсилку!", reply_markup=get_main_keyboard(message.from_user.id))
    except Exception as e:
        await log_error(f"Помилка при підписці: {e}")

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    try:
        print(f"✅ Operator question initiated by {message.from_user.id}")
        await message.answer("Введіть ваше питання:")
        await state.set_state(OperatorQuestion.waiting_for_question)
    except Exception as e:
        await log_error(f"Помилка при початку питання оператору: {e}")

@dp.message(OperatorQuestion.waiting_for_question)
async def forward_question(message: types.Message, state: FSMContext):
    try:
        print(f"✅ Forwarding question from {message.from_user.id}")
        await bot.send_message(ADMIN_ID, f"Питання від користувача {message.from_user.id}:
{message.text}")
        await message.answer("Ваше питання надіслано оператору!")
        await state.clear()
    except Exception as e:
        await log_error(f"Помилка при надсиланні питання оператору: {e}")


def save_user(user_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM subscribers WHERE user_id = %s", (user_id,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO subscribers (user_id) VALUES (%s)", (user_id,))
                connection.commit()
                asyncio.create_task(bot.send_message(ADMIN_ID, f"Новий підписник: {user_id}"))
    except Exception as e:
        print(f"Помилка збереження користувача: {e}")


def is_user_saved(user_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM subscribers WHERE user_id = %s", (user_id,))
            return cursor.fetchone() is not None
    except Exception as e:
        print(f"Помилка перевірки користувача: {e}")
        return False


async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        await log_error(f"Помилка при запуску бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())
