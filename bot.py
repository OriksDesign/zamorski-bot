import os
import asyncio
import logging
from typing import Optional, List


import pymysql
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
KeyboardButton, ReplyKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# ---------------------------------------------------------------------------
# Конфігурація
# ---------------------------------------------------------------------------
API_TOKEN = os.getenv("API_TOKEN")
# Підтримка одного або кількох адміністраторів
ADMIN_IDS = set()
_admin_single = os.getenv("ADMIN_ID", "").strip()
if _admin_single:
try:
ADMIN_IDS.add(int(_admin_single))
except ValueError:
pass
_admin_many = os.getenv("ADMIN_IDS", "").split(",")
for _p in _admin_many:
_p = _p.strip()
if _p:
try:
ADMIN_IDS.add(int(_p))
except ValueError:
pass


# Визначаємо первинного адміна: пріоритет ADMIN_ID, інакше мінімальний із ADMIN_IDS
ADMIN_ID_PRIMARY = None
if _admin_single:
try:
ADMIN_ID_PRIMARY = int(_admin_single)
except Exception:
ADMIN_ID_PRIMARY = None
if ADMIN_ID_PRIMARY is None and ADMIN_IDS:
ADMIN_ID_PRIMARY = min(ADMIN_IDS)


if not API_TOKEN:
raise RuntimeError("Не задано API_TOKEN у змінних середовища")
if not ADMIN_IDS:
raise RuntimeError("Не задано ADMIN_ID або ADMIN_IDS у змінних середовища")


logging.basicConfig(
level=logging.INFO,
format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("zamorski-bot")


def is_admin(uid: int) -> bool:
return uid in ADMIN_IDS


# ---------------------------------------------------------------------------
# Підключення до MySQL з авто‑перепідключенням
# ---------------------------------------------------------------------------
class MySQL:
def __init__(self):
self.host = os.getenv("DB_HOST")
self.user = os.getenv("DB_USER")
self.password = os.getenv("DB_PASSWORD")
self.database = os.getenv("DB_NAME")
self.conn: Optional[pymysql.connections.Connection] = None
self.connect()


def connect(self):
self.conn = pymysql.connect(
host=self.host,
user=self.user,
password=self.password,
database=self.database,
charset="utf8mb4",
autocommit=True,
cursorclass=pymysql.cursors.DictCursor,
asyncio.run(main())
