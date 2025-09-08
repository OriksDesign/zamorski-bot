import os
import re
import csv
import io
import asyncio
import logging
import time
from typing import Optional, List, Tuple, Dict

import pymysql
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ForceReply,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.dispatcher.middlewares.base import BaseMiddleware

# ============================== КОНФІГ =====================================

API_TOKEN = os.getenv("API_TOKEN", "").strip()
ERROR_CHAT_ID_RAW = os.getenv("ERROR_CHAT_ID", "").strip()  # -100..., @channel або пусто

ADMIN_IDS: set[int] = set()
_admin_single = os.getenv("ADMIN_ID", "").strip()
if _admin_single:
    try:
        ADMIN_IDS.add(int(_admin_single))
    except ValueError:
        pass
for p in os.getenv("ADMIN_IDS", "").split(","):
    p = p.strip()
    if p:
        try:
            ADMIN_IDS.add(int(p))
        except ValueError:
            pass

ADMIN_ID_PRIMARY: Optional[int] = None
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

def as_chat_id(raw: str) -> Optional[int | str]:
    if not raw:
        return None
    if raw.startswith("@"):
        return r
