# bot.py

import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

def get_main_keyboard(user_id):
    buttons = [
        [KeyboardButton(text="Умови співпраці")],
        [KeyboardButton(text="Питання оператору")],
        [KeyboardButton(text="Новинки")],
        [KeyboardButton(text="Підписатися на розсилку")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="Зробити розсилку")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    print("Start command received")  # Лог для перевірки
    await message.answer("Вітаємо у магазині Заморські подарунки! Оберіть, будь ласка:", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    print("Help command received")  # Лог для перевірки
    await message.answer("/start – Почати спілкування\n/help – Як працює бот")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
