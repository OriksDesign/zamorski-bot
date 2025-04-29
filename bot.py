# bot.py

import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
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
    waiting_for_selection = State()
    waiting_for_text = State()
    waiting_for_file = State()

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

keyboard_operator_menu = types.ReplyKeyboardMarkup(
    keyboard=[
        [
            types.KeyboardButton(text="📦 Надіслати ТТН по замовленню"),
            types.KeyboardButton(text="👩‍💼 Зв'язок із оператором")
        ],
        [
            types.KeyboardButton(text="💳 Я надіслав оплату"),
            types.KeyboardButton(text="⏰ Коли очікувати доставку")
        ],
        [
            types.KeyboardButton(text="🚫 Скасувати замовлення"),
            types.KeyboardButton(text="📝 Змінити адресу доставки")
        ],
        [types.KeyboardButton(text="⬅️ Повернутись до головного меню")]
    ],
    resize_keyboard=True
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

@dp.message(F.text == "Підписатися на розсилку")
async def subscribe_user(message: types.Message):
    if is_user_saved(message.from_user.id):
        await message.answer("Ви вже підписані на розсилку!", reply_markup=keyboard_main)
    else:
        save_user(message.from_user.id)
        await message.answer("Ви успішно підписалися на розсилку!", reply_markup=keyboard_main)

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    await message.answer("Оберіть питання:", reply_markup=keyboard_operator_menu)
    await state.set_state(OperatorChat.waiting_for_selection)

@dp.message(OperatorChat.waiting_for_selection)
async def handle_operator_selection(message: types.Message, state: FSMContext):
    text = message.text

    if text == "⬅️ Повернутись до головного меню":
        await message.answer("Повертаємось до головного меню:", reply_markup=keyboard_main)
        await state.clear()
        return

    if text == "📦 Надіслати ТТН по замовленню":
        await bot.send_message(ADMIN_ID, f"Клієнт {message.from_user.id} просить надіслати ТТН.")
        await message.answer("Ваш запит відправлений оператору.", reply_markup=keyboard_main)
        await state.clear()
    elif text == "👩‍💼 Зв'язок із оператором":
        await message.answer("Будь ласка, введіть ваше питання:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(OperatorChat.waiting_for_text)
    elif text == "💳 Я надіслав оплату":
        await message.answer("Будь ласка, надішліть файл (скріншот) підтвердження оплати:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(OperatorChat.waiting_for_file)
    elif text == "⏰ Коли очікувати доставку":
        await bot.send_message(ADMIN_ID, f"Клієнт {message.from_user.id} запитує, коли буде доставка.")
        await message.answer("Ваш запит відправлений оператору.", reply_markup=keyboard_main)
        await state.clear()
    elif text == "🚫 Скасувати замовлення":
        await message.answer("Будь ласка, введіть причину скасування або номер замовлення:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(OperatorChat.waiting_for_text)
    elif text == "📝 Змінити адресу доставки":
        await message.answer("Будь ласка, введіть нову адресу доставки:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(OperatorChat.waiting_for_text)

@dp.message(OperatorChat.waiting_for_text)
async def receive_operator_text(message: types.Message, state: FSMContext):
    await bot.send_message(ADMIN_ID, f"Нове повідомлення від {message.from_user.id}:\n{message.text}")
    await message.answer("Ваше повідомлення надіслано оператору.", reply_markup=keyboard_main)
    await state.clear()

@dp.message(OperatorChat.waiting_for_file, F.photo | F.document)
async def receive_operator_file(message: types.Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        await bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=f"Файл підтвердження оплати від {message.from_user.id}")
    elif message.document:
        file_id = message.document.file_id
        await bot.send_document(chat_id=ADMIN_ID, document=file_id, caption=f"Файл підтвердження оплати від {message.from_user.id}")
    await message.answer("Файл успішно надіслано оператору.", reply_markup=keyboard_main)
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

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

# --- Кінець bot.py ---
