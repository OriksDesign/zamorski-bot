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

# Підключення до бази даних через pymysql з автоматичним відновленням
class DatabaseConnection:
    def __init__(self):
        self.host = os.getenv('DB_HOST')
        self.user = os.getenv('DB_USER')
        self.password = os.getenv('DB_PASSWORD')
        self.database = os.getenv('DB_NAME')
        self.connection = None
        self.connect()

    def connect(self):
        try:
            self.connection = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True
            )
            print("✅ Database connection successful")
        except pymysql.MySQLError as e:
            print(f"❌ Database connection failed: {e}")
            self.connection = None

    def get_cursor(self):
        try:
            if self.connection is None or not self.connection.open:
                self.connect()
            return self.connection.cursor()
        except Exception as e:
            print(f"❌ Database reconnection failed: {e}")
            return None

    def commit(self):
        try:
            if self.connection is not None and self.connection.open:
                self.connection.commit()
        except Exception as e:
            print(f"❌ Database commit failed: {e}")

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close()
                print("✅ Database connection closed")
        except Exception as e:
            print(f"❌ Database close failed: {e}")

# Ініціалізація з'єднання з базою даних
try:
    db = DatabaseConnection()
    with db.get_cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        ''')
        print("✅ Subscribers table ready")
except Exception as e:
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


def save_user(user_id):
    try:
        with db.get_cursor() as cursor:
            cursor.execute("INSERT INTO subscribers (user_id) VALUES (%s) ON DUPLICATE KEY UPDATE user_id = user_id", (user_id,))
            db.commit()
            print(f"✅ User {user_id} saved")
    except Exception as e:
        print(f"❌ Failed to save user {user_id}: {e}")


def is_user_saved(user_id):
    try:
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM subscribers WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            return result['count'] > 0
    except Exception as e:
        print(f"❌ Failed to check if user {user_id} is saved: {e}")
        return False


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

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
