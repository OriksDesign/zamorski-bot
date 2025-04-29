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

class OperatorQuestion(StatesGroup):
    waiting_for_choice = State()
    waiting_for_text = State()
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
        if not cursor.fetchone():
            cursor.execute("INSERT INTO subscribers (user_id) VALUES (%s)", (user_id,))
            connection.commit()
            asyncio.create_task(bot.send_message(ADMIN_ID, f"Новий підписник: {user_id}"))

def is_user_saved(user_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM subscribers WHERE user_id = %s", (user_id,))
        return cursor.fetchone() is not None

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    save_user(message.from_user.id)
    await message.answer("Вітаємо у магазині Заморські подарунки! Оберіть, будь ласка:", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("""
/start – Почати спілкування
/help – Як працює бот
/sendnews – Для адміністратора (розсилка)
""", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "Умови співпраці")
async def work_conditions(message: types.Message):
    await message.answer("Наші умови співпраці:\n🚚 Доставка по Україні\n💳 Оплата онлайн або при отриманні\n🔄 Обмін/повернення протягом 14 днів.", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "Новинки")
async def new_arrivals(message: types.Message):
    await message.answer("Останні новинки нашого магазину можна переглянути тут: https://zamorskiepodarki.com/", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "Підписатися на розсилку")
async def subscribe_user(message: types.Message):
    if is_user_saved(message.from_user.id):
        await message.answer("Ви вже підписані на розсилку!", reply_markup=get_main_keyboard(message.from_user.id))
    else:
        save_user(message.from_user.id)
        await message.answer("Ви успішно підписалися на розсилку!", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "Питання оператору")
async def ask_operator(message: types.Message, state: FSMContext):
    buttons = [
        [types.KeyboardButton(text="📦 Надіслати ТТН по замовленню")],
        [types.KeyboardButton(text="👩‍💼 Зв'язок із оператором")],
        [types.KeyboardButton(text="💳 Я надіслав оплату")],
        [types.KeyboardButton(text="⏰ Коли очікувати доставку")],
        [types.KeyboardButton(text="🚫 Скасувати замовлення")],
        [types.KeyboardButton(text="📝 Змінити адресу доставки")],
        [types.KeyboardButton(text="⬅️ Повернутись до головного меню")]
    ]
    await message.answer("Оберіть питання:", reply_markup=types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state(OperatorQuestion.waiting_for_choice)

@dp.message(OperatorQuestion.waiting_for_choice)
async def handle_operator_selection(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()

    if text == "⬅️ Повернутись до головного меню":
        await message.answer("Повертаємось до головного меню:", reply_markup=get_main_keyboard(user_id))
        await state.clear()
        return

    elif text == "💳 Я надіслав оплату":
        await message.answer("Будь ласка, надішліть файл (скріншот) підтвердження оплати:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(OperatorQuestion.waiting_for_payment)
        return

    elif text in ["📦 Надіслати ТТН по замовленню", "👩‍💼 Зв'язок із оператором", "⏰ Коли очікувати доставку", "🚫 Скасувати замовлення", "📝 Змінити адресу доставки"]:
        await bot.send_message(ADMIN_ID, f"Клієнт {user_id} просить: {text}")
        await message.answer("Ваш запит відправлений оператору.", reply_markup=get_main_keyboard(user_id))
        await state.clear()

@dp.message(OperatorQuestion.waiting_for_payment, F.photo | F.document)
async def receive_operator_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.photo:
        file_id = message.photo[-1].file_id
        await bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=f"Файл підтвердження оплати від {user_id}")
    elif message.document:
        file_id = message.document.file_id
        await bot.send_document(chat_id=ADMIN_ID, document=file_id, caption=f"Файл підтвердження оплати від {user_id}")
    await message.answer("Файл успішно надіслано оператору.", reply_markup=get_main_keyboard(user_id))
    await state.clear()

@dp.message(Command("sendnews"))
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
    data = await state.get_data()
    photo_id = data.get("photo")
    text = message.text

    caption = (
        "*Новинки у \"Заморських подарунках\"!*\n\n"
        "Ми отримали нове надходження екзотичних сувенірів, ароматів та декору.\n\n"
        "Знижки на обрані товари!\n\n"
        "Переглянути всі новинки можна за посиланням нижче.\n\n"
        "——————\n\n"
        + text
    )

    with connection.cursor() as cursor:
        cursor.execute("SELECT user_id FROM subscribers")
        users = cursor.fetchall()

    if not users:
        await message.answer("Немає зареєстрованих користувачів для розсилки.", reply_markup=get_main_keyboard(message.from_user.id))
        await state.clear()
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Перейти до магазину", url="https://zamorskiepodarki.com/")]
    ])

    count = 0
    for row in users:
        try:
            await bot.send_photo(chat_id=row['user_id'], photo=photo_id, caption=caption, parse_mode="Markdown", reply_markup=keyboard)
            count += 1
        except Exception as e:
            print(f"Помилка при надсиланні {row['user_id']}: {e}")

    await message.answer(f"Розсилка завершена. Надіслано {count} повідомлень.", reply_markup=get_main_keyboard(message.from_user.id))
    await state.clear()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
