# --- bot.py ---

from aiogram import Bot, Dispatcher, types
from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import os

# Токен беремо із змінної середовища
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

class SendNews(StatesGroup):
    waiting_for_photo = State()
    waiting_for_caption = State()

# Збереження користувача
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
    await message.answer("Вітаємо у магазині Заморські подарунки! Оберіть опцію нижче:", reply_markup=keyboard)

@dp.message(F.text == "Умови співпраці")
async def work_conditions(message: types.Message):
    await message.answer("Наші умови співпраці:\n🚚 Доставка по Україні\n💳 Оплата онлайн або при отриманні\n🔄 Обмін/повернення протягом 14 днів.")

@dp.message(F.text == "Новинки")
async def new_arrivals(message: types.Message):
    await message.answer("Останні новинки магазину тут: https://zamorskiepodarki.com/")

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message):
    await message.answer("Будь ласка, напишіть ваше питання, і оператор незабаром відповість.")

@dp.message(F.text == "Підписатися на розсилку")
async def subscribe_newsletter(message: types.Message):
    save_user(message.from_user.id)
    await message.answer("Ви успішно підписалися на розсилку! ✅")

# Розсилка: команда /sendnews
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

# Запуск бота
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
