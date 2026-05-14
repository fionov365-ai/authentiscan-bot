import asyncio
import logging
import os
from datetime import datetime
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
)

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_СЮДА_ВАШ_ТОКЕН")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
REFERRAL_BONUS = 500  # бонус за приведённого друга, ₽
MIN_WITHDRAW = 500    # минимальная сумма вывода, ₽
# Стадия запуска: True = показывать пометку, что выплаты ещё не идут
LAUNCH_MODE = True
# ============ КОНЕЦ НАСТРОЕК ============

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

DB_PATH = "bot.db"


class ReportForm(StatesGroup):
    photos = State()
    brand = State()
    category = State()
    location = State()
    price = State()
    signs = State()


class ModerationForm(StatesGroup):
    waiting_reward = State()
    waiting_reject_reason = State()


class WithdrawForm(StatesGroup):
    waiting_amount = State()
    waiting_details = State()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                registered_at TEXT,
                referred_by INTEGER,
                ref_bonus_paid INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                brand TEXT,
                category TEXT,
                location TEXT,
                price TEXT,
                signs TEXT,
                photo_count INTEGER,
                status TEXT DEFAULT 'pending',
                reward INTEGER DEFAULT 0,
                reject_reason TEXT,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                details TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)
        # Безопасное обновление старых баз
        for table, col, coltype in [
            ("reports", "reward", "INTEGER DEFAULT 0"),
            ("reports", "reject_reason", "TEXT"),
            ("users", "referred_by", "INTEGER"),
            ("users", "ref_bonus_paid", "INTEGER DEFAULT 0"),
        ]:
            try:
