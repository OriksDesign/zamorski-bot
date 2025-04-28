# bot.py

import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

class SendNews(StatesGroup):
    waiting_for_photo = State()
    waiting_for_caption = State()

class OperatorChat(StatesGroup):
    waiting_for_reply = State()

keyboard_main = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Умови співпраці")],
        [types.KeyboardButton(text="Питання оператору")],
        [types.KeyboardButton(text="Новинки")],
        [types.KeyboardButton(text="Підписатися на розсилку")]
    ],
    resize_keyboard=True,
    is_persistent=True
)

if not os.path.exists("users.txt"):
    with open("users.txt", "w"): pass

def save_user(user_id):
    with open("users.txt", "r+") as f:
        users = f.read().splitlines()
        if str(user_id) not in users:
            f.write(f"{user_id}\n")
            asyncio.create_task(bot.send_message(chat_id=ADMIN_ID, text=f"Новий підписник: {user_id}"))

def is_user_saved(user_id):
    with open("users.txt", "r") as f:
        return str(user_id) in f.read().splitlines()

@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    save_user(message.from_user.id)
    await message.answer("Вітаємо у магазині Заморські подарунки! Оберіть, будь ласка:", reply_markup=keyboard_main)

@dp.message(Command('help'))
async def cmd_help(message: types.Message):
    await message.answer(
        "/start – Почати спілкування\n/help – Як працює бот\n/sendnews – Для адміністратора (розсилка)",
        reply_markup=keyboard_main
    )

@dp.message(F.text == "Умови співпраці")
async def work_conditions(message: types.Message):
    await message.answer("Наші умови співпраці:\n🚚 Доставка по Україні\n💳 Оплата онлайн або при отриманні\n🔄 Обмін/повернення протягом 14 днів.", reply_markup=keyboard_main)

@dp.message(F.text == "Новинки")
async def new_arrivals(message: types.Message):
    await message.answer("Останні новинки нашого магазину можна переглянути тут: https://zamorskiepodarki.com/", reply_markup=keyboard_main)

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    await message.answer("Будь ласка, напишіть ваше питання:")
    await state.set_state(SendNews.waiting_for_caption)

@dp.message(F.text == "Підписатися на розсилку")
async def subscribe_user(message: types.Message):
    if is_user_saved(message.from_user.id):
        await message.answer("Ви вже підписані на розсилку!", reply_markup=keyboard_main)
    else:
        save_user(message.from_user.id)
        await message.answer("Ви успішно підписалися на розсилку!", reply_markup=keyboard_main)

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

    with open("users.txt", "r") as f:
        users = f.read().splitlines()

    if not users:
        await message.answer("Немає зареєстрованих користувачів для розсилки.", reply_markup=keyboard_main)
        await state.clear()
        return

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

    await message.answer(f"Розсилка завершена. Надіслано {count} повідомлень.", reply_markup=keyboard_main)
    await state.clear()

@dp.message(SendNews.waiting_for_caption)
async def operator_question(message: types.Message):
    text = message.text
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Нове питання від користувача {message.from_user.id}:\n{text}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Відповісти", callback_data=f"reply_{message.from_user.id}")]
            ]
        )
    )
    await message.answer("Ваше питання передано оператору. Очікуйте відповідь.")
    await asyncio.sleep(2)
    await message.answer("Оберіть опцію нижче:", reply_markup=keyboard_main)

@dp.callback_query()
async def operator_reply_callback(callback: types.CallbackQuery, state: FSMContext):
    if not callback.data.startswith("reply_"):
        return
    user_id = int(callback.data.split("_")[1])
    await callback.message.answer(f"Напишіть відповідь для користувача {user_id}:")
    await state.update_data(reply_to=user_id)
    await state.set_state(OperatorChat.waiting_for_reply)
    await callback.answer()

@dp.message(OperatorChat.waiting_for_reply)
async def send_operator_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    reply_to = data.get("reply_to")
    if reply_to:
        await bot.send_message(chat_id=reply_to, text=message.text)
        await message.answer("Відповідь надіслано користувачу.", reply_markup=keyboard_main)
    await state.clear()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

# --- Кінець bot.py ---
