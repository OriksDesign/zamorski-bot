# 🚀 Zamorski Bot

## Опис
Це чат-бот для Telegram, що дозволяє:
- Привітати користувачів з кнопками меню.
- Запрошувати підписатися на розсилку.
- Приймати запитання від користувачів.
- Зробити розсилку новин з фото та кнопкою.

## Встановлення

1. 👉 Клонуйте репозиторій:
```bash
git clone https://github.com/OriksDesign/zamorski-bot.git
cd zamorski-bot
```

2. 👉 Встановіть залежності:
```bash
pip install -r requirements.txt
```

3. 🔑 Створіть файл `.env` у корені проекту і заповніть так:
```bash
API_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_ID=YOUR_ADMIN_TELEGRAM_USER_ID
```

4. 🔄 Запуск бота:
```bash
python bot.py
```

## Як розгорнути на Render.com

1. Зареєструйтеся на https://render.com
2. Створіть New Web Service.
3. Оберіть GitHub репозиторій.
4. Виставте:
   - Language: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python bot.py`
5. Задайте Environment Variables:
   - `API_TOKEN` = your actual token
   - `ADMIN_ID` = your Telegram user ID
6. Натисніть **Deploy Web Service**

---

Бот буде постійно працювати, навіть якщо ви вимкнете свій комп'ютер!

---

Запитання? 👇 Напишіть мені!
