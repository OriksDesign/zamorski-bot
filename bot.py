# bot.py

import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

class SendNews(StatesGroup):
    waiting_for_photo = State()
    waiting_for_caption = State()

class OperatorReply(StatesGroup):
    waiting_for_reply_text = State()
    replying_to_user = State()

def save_user(user_id):
    if not os.path.exists("users.txt"):
        with open("users.txt", "w") as f:
            f.write("")
    with open("users.txt", "r+") as f:
        users = f.read().splitlines()
        if str(user_id) not in users:
            f.write(f"{user_id}\n")

@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    save_user(message.from_user.id)
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Умови співпраці")],
            [types.KeyboardButton(text="Питання оператору")],
            [types.KeyboardButton(text="Новинки")],
            [types.KeyboardButton(text="Підписатися на розсилку")]
        ],
        resize_keyboard=True
    )
    await message.answer("Вітаємо у магазині Заморські подарунки! Оберіть, будь ласка:", reply_markup=keyboard)

@dp.message(F.text == "Умови співпраці")
async def work_conditions(message: types.Message):
    await message.answer("Наші умови співпраці:\n🚚 Доставка по Україні\n💳 Оплата онлайн або при отриманні\n🔄 Обмін/повернення протягом 14 днів.")

@dp.message(F.text == "Новинки")
async def new_arrivals(message: types.Message):
    await message.answer("Останні новинки нашого магазину можна переглянути тут: https://zamorskiepodarki.com/")

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    await message.answer("Будь ласка, напишіть ваше питання:")
    await state.set_state(OperatorReply.waiting_for_reply_text)

@dp.message(F.text == "Підписатися на розсилку")
async def subscribe_user(message: types.Message):
    save_user(message.from_user.id)
    await message.answer("Ви успішно підписалися на розсилку!")

@dp.message(OperatorReply.waiting_for_reply_text)
async def receive_question(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Відповісти", callback_data=f"reply_{user_id}")]
        ]
    )

    await bot.send_message(chat_id=ADMIN_ID, text=f"Нове питання від користувача {user_id}:\n\n{text}", reply_markup=keyboard)
    await message.answer("Ваше питання передано оператору. Очікуйте відповіді.")
    await state.clear()

@dp.callback_query(F.data.startswith("reply_"))
async def reply_to_user(call: CallbackQuery, state: FSMContext):
    user_id = int(call.data.split("_")[1])
    await call.message.answer(f"Напишіть відповідь для користувача {user_id}:")
    await state.update_data(reply_user_id=user_id)
    await state.set_state(OperatorReply.replying_to_user)
    await call.answer()

@dp.message(OperatorReply.replying_to_user)
async def send_reply_to_user(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("reply_user_id")
    try:
        await bot.send_message(chat_id=user_id, text=message.text)
        await message.answer("Відповідь надіслано користувачу.")
    except Exception as e:
        await message.answer(f"Помилка при надсиланні відповіді: {e}")
    await state.clear()

@dp.message(Command('sendnews'))
async def cmd_sendnews(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас немає прав для цієї команди.")
        return
    await message.answer("Будь ласка, надішліть фото для розсилки.")
    await state.set_state(SendNews.waiting_for_photo)

@dp.message(SendNews.waiting_for_photo, F.photo)
async def get_news_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("Тепер надішліть текст для підпису під фото.")
    await state.set_state(SendNews.waiting_for_caption)

@dp.message(SendNews.waiting_for_caption)
async def get_news_caption(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    photo_id = user_data.get("photo")
    user_text = message.text

    announcement = (
        "*Новинки у \"Заморських подарунках\"!*\n\n"
        "Ми отримали нове надходження екзотичних сувенірів, ароматів та декору.\n\n"
        "Знижки на обрані товари!\n\n"
        "Переглянути всі новинки можна за посиланням нижче.\n\n"
        "——————\n\n"
    )

    full_caption = announcement + user_text

    if not os.path.exists("users.txt"):
        await message.answer("Немає зареєстрованих користувачів для розсилки.")
        await state.clear()
        return

    with open("users.txt", "r") as f:
        users = f.read().splitlines()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти до магазину", url="https://zamorskiepodarki.com/")]
        ]
    )

    count = 0
    for user_id in users:
        try:
            await bot.send_photo(
                chat_id=int(user_id),
                photo=photo_id,
                caption=full_caption,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            count += 1
        except Exception as e:
            print(f"Помилка при відправці користувачу {user_id}: {e}")

    await message.answer(f"Розсилка завершена. Надіслано {count} повідомлень.")
    await state.clear()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

# --- Кінець bot.py ---
