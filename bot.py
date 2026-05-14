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

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
REFERRAL_BONUS = 500
MIN_WITHDRAW = 500
LAUNCH_MODE = True
CHANNEL_USERNAME = "@authentiscan_ru"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)
DB_PATH = "bot.db"


def sheets_append(rid, name, brand, cat, loc, price, photos, status):
    try:
        fn = getattr(__builtins__, 'append_to_sheet', None)
        if callable(fn):
            fn(rid, name, brand, cat, loc, price, photos, status)
    except Exception:
        pass


def sheets_update(rid, status):
    try:
        fn = getattr(__builtins__, 'update_status_in_sheet', None)
        if callable(fn):
            fn(rid, status)
    except Exception:
        pass


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


class BroadcastForm(StatesGroup):
    waiting_text = State()
    waiting_confirm = State()


class PostForm(StatesGroup):
    waiting_text = State()
    waiting_confirm = State()


class OnboardingForm(StatesGroup):
    step1 = State()
    step2 = State()
    step3 = State()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY, username TEXT,
            full_name TEXT, registered_at TEXT,
            referred_by INTEGER, ref_bonus_paid INTEGER DEFAULT 0,
            onboarded INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, brand TEXT, category TEXT,
            location TEXT, price TEXT, signs TEXT,
            photo_count INTEGER, status TEXT DEFAULT 'pending',
            reward INTEGER DEFAULT 0, reject_reason TEXT,
            created_at TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, amount INTEGER, details TEXT,
            status TEXT DEFAULT 'pending', created_at TEXT)""")
        for t, c, ct in [
            ("reports", "reward", "INTEGER DEFAULT 0"),
            ("reports", "reject_reason", "TEXT"),
            ("users", "referred_by", "INTEGER"),
            ("users", "ref_bonus_paid", "INTEGER DEFAULT 0"),
            ("users", "onboarded", "INTEGER DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE {t} ADD COLUMN {c} {ct}")
            except Exception:
                pass
        await db.commit()


async def save_user(uid, uname, fname, ref=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT telegram_id FROM users WHERE telegram_id=?", (uid,)
        ) as cur:
            if await cur.fetchone():
                return False
        if ref == uid:
            ref = None
        await db.execute(
            "INSERT INTO users (telegram_id,username,full_name,"
            "registered_at,referred_by) VALUES(?,?,?,?,?)",
            (uid, uname, fname, datetime.now().isoformat(), ref))
        await db.commit()
        return True


async def is_onboarded(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT onboarded FROM users WHERE telegram_id=?", (uid,)
        ) as cur:
            r = await cur.fetchone()
            return bool(r and r[0])


async def mark_onboarded(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET onboarded=1 WHERE telegram_id=?", (uid,))
        await db.commit()


async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id FROM users") as cur:
            return [r[0] for r in await cur.fetchall()]


async def save_report(uid, data):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO reports (user_id,brand,category,location,"
            "price,signs,photo_count,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, data.get('brand'), data.get('category'),
             data.get('location'), data.get('price'),
             data.get('signs'), len(data.get('photos', [])),
             datetime.now().isoformat()))
        await db.commit()
        return cur.lastrowid


async def get_report(rid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id,user_id,brand,category,location,price,"
            "signs,status,reward FROM reports WHERE id=?", (rid,)
        ) as cur:
            return await cur.fetchone()


async def update_report_status(rid, status, reward=0, reason=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE reports SET status=?,reward=?,reject_reason=? "
            "WHERE id=?", (status, reward, reason, rid))
        await db.commit()


async def count_confirmed(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM reports "
            "WHERE user_id=? AND status='confirmed'", (uid,)
        ) as cur:
            return (await cur.fetchone())[0]


async def get_ref_info(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT referred_by,ref_bonus_paid FROM users "
            "WHERE telegram_id=?", (uid,)
        ) as cur:
            r = await cur.fetchone()
            return {'referred_by': r[0] if r else None,
                    'ref_bonus_paid': r[1] if r else 0}


async def count_referrals(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,)
        ) as cur:
            return (await cur.fetchone())[0]


async def mark_ref_paid(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET ref_bonus_paid=1 WHERE telegram_id=?",
            (uid,))
        await db.commit()


async def add_ref_bonus(referrer_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reports (user_id,brand,category,location,"
            "price,signs,photo_count,status,reward,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (referrer_id, "Реферальный бонус", "Бонус", "—", "0",
             "Друг подал первую подтверждённую заявку",
             0, "confirmed", amount, datetime.now().isoformat()))
        await db.commit()


async def get_user_stats(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*),"
            "SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) "
            "FROM reports WHERE user_id=? "
            "AND brand!='Реферальный бонус'", (uid,)
        ) as cur:
            r = await cur.fetchone()
        async with db.execute(
            "SELECT SUM(CASE WHEN status='confirmed' "
            "THEN reward ELSE 0 END) FROM reports WHERE user_id=?",
            (uid,)
        ) as cur:
            earned = (await cur.fetchone())[0] or 0
        async with db.execute(
            "SELECT SUM(amount) FROM withdrawals "
            "WHERE user_id=? AND status IN('pending','paid')", (uid,)
        ) as cur:
            withdrawn = (await cur.fetchone())[0] or 0
    return {
        'total': r[0] or 0, 'confirmed': r[1] or 0,
        'pending': r[2] or 0, 'rejected': r[3] or 0,
        'earned': earned, 'withdrawn': withdrawn,
        'balance': earned - withdrawn
    }


async def get_user_reports(uid, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id,brand,category,status,reward,created_at "
            "FROM reports WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit)
        ) as cur:
            return await cur.fetchall()


async def save_withdrawal(uid, amount, details):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO withdrawals (user_id,amount,details,created_at)"
            " VALUES(?,?,?,?)",
            (uid, amount, details, datetime.now().isoformat()))
        await db.commit()
        return cur.lastrowid


async def update_withdrawal(wid, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE withdrawals SET status=? WHERE id=?", (status, wid))
        await db.commit()


async def get_withdrawal(wid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id,user_id,amount,details,status "
            "FROM withdrawals WHERE id=?", (wid,)
        ) as cur:
            return await cur.fetchone()


def extract_city(loc):
    return loc.split(",")[0].strip() if loc else "—"


def menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="💰 Мой кабинет", callback_data="my_cabinet")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_reports")],
        [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="referral")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="how_it_works")]])


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    ref = None
    if command.args and command.args.startswith("ref_"):
        try:
            ref = int(command.args.replace("ref_", ""))
        except ValueError:
            pass
    is_new = await save_user(msg.from_user.id, msg.from_user.username,
                             msg.from_user.full_name, ref)
    if is_new and ref:
        try:
            await bot.send_message(ref,
                f"🎉 По твоей ссылке зарегистрировался новый охотник!\n\n"
                f"Когда он получит первую подтверждённую заявку — "
                f"ты получишь бонус {REFERRAL_BONUS} ₽.")
        except Exception:
            pass
    if is_new:
        await state.set_state(OnboardingForm.step1)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Поехали!", callback_data="onb1")]])
        await msg.answer(
            "👋 Добро пожаловать в AuthentiScan!\n\n"
            "Ты присоединился к сообществу охотников за подделками.\n\n"
            "За 1 минуту объясню как зарабатывать 👇", reply_markup=kb)
    else:
        await msg.answer("🏠 С возвращением!\n\nВыбери действие:",
                         reply_markup=menu_kb())


@router.callback_query(F.data == "onb1")
async def onb1(cb: CallbackQuery, state: FSMContext):
    await state.set_state(OnboardingForm.step2)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Сколько платят? →", callback_data="onb2")]])
    await cb.message.edit_text(
        "🔍 Шаг 1 из 3 — Как это работает\n\n"
        "1️⃣ Замечаешь подозрительный товар\n"
        "2️⃣ Делаешь 2-5 фото\n"
        "3️⃣ Заполняешь заявку — 3 минуты\n"
        "4️⃣ Эксперты проверяют до 7 дней\n"
        "5️⃣ Получаешь деньги на карту!\n\n"
        "Полная анонимность.", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "onb2")
async def onb2(cb: CallbackQuery, state: FSMContext):
    await state.set_state(OnboardingForm.step3)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Как фотографировать? →", callback_data="onb3")]])
    await cb.message.edit_text(
        "💰 Шаг 2 из 3 — Сколько платят\n\n"
        "👟 Обувь — 1 500 – 8 000 ₽\n"
        "🎧 Электроника — 2 000 – 15 000 ₽\n"
        "💄 Косметика — 1 000 – 5 000 ₽\n"
        "👜 Сумки — 3 000 – 25 000 ₽\n"
        "⌚ Часы — 5 000 – 50 000 ₽\n\n"
        "Топ-охотники: 30 000 – 50 000 ₽/мес.", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "onb3")
async def onb3(cb: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать первую заявку!", callback_data="onb_report")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="onb_menu")]])
    await cb.message.edit_text(
        "📸 Шаг 3 из 3 — Секрет хорошей заявки\n\n"
        "✅ Общий вид товара\n"
        "✅ Логотип крупным планом\n"
        "✅ Этикетка со штрихкодом\n"
        "✅ Признак подделки\n\n"
        "❌ Размытые фото — отклонят\n\n"
        "Готов? 🔍", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "onb_report")
async def onb_report(cb: CallbackQuery, state: FSMContext):
    await mark_onboarded(cb.from_user.id)
    await state.clear()
    await state.set_state(ReportForm.photos)
    await state.update_data(photos=[])
    await cb.message.answer(
        "📸 Шаг 1 из 6: Фото\n\nОтправь 2-5 фото товара.")
    await cb.answer()


@router.callback_query(F.data == "onb_menu")
async def onb_menu(cb: CallbackQuery, state: FSMContext):
    await mark_onboarded(cb.from_user.id)
    await state.clear()
    await cb.message.edit_text("🏠 Главное меню\n\nВыбери действие:",
                               reply_markup=menu_kb())
    await cb.answer()


@router.callback_query(F.data == "how_it_works")
async def how(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")]])
    await cb.message.edit_text(
        "📖 Как э
