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
        "📖 Как это работает:\n\n"
        "1️⃣ Находишь подозрительный товар\n"
        "2️⃣ Фотографируешь (2-5 фото)\n"
        "3️⃣ Заполняешь заявку\n"
        "4️⃣ Бренд проверяет (до 7 дней)\n"
        "5️⃣ Получаешь деньги", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "back_menu")
async def back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("🏠 Главное меню\n\nВыбери действие:",
                               reply_markup=menu_kb())
    await cb.answer()


@router.callback_query(F.data == "referral")
async def referral(cb: CallbackQuery):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{cb.from_user.id}"
    cnt = await count_referrals(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")]])
    await cb.message.edit_text(
        f"👥 Приглашай друзей\n\n"
        f"Бонус {REFERRAL_BONUS} ₽ за каждого друга с подтверждённой заявкой.\n\n"
        f"👥 Приглашено: {cnt}\n\n"
        f"🔗 Твоя ссылка:\n{link}",
        reply_markup=kb, disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data == "my_cabinet")
async def cabinet(cb: CallbackQuery):
    s = await get_user_stats(cb.from_user.id)
    cr = round(s['confirmed'] / s['total'] * 100) if s['total'] > 0 else 0
    t = (f"💰 Личный кабинет\n\n"
         f"💵 К выводу: {s['balance']:,} ₽\n"
         f"💸 Выведено: {s['withdrawn']:,} ₽\n"
         f"⏳ На проверке: {s['pending']}\n\n"
         f"📝 Заявок: {s['total']}\n"
         f"✅ Подтверждено: {s['confirmed']}\n"
         f"❌ Отклонено: {s['rejected']}\n"
         f"🎯 Confirm: {cr}%").replace(",", " ")
    if LAUNCH_MODE:
        t += "\n\n⚠️ Платформа в стадии запуска. Выплаты после подключения брендов."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton(text="📋 Заявки", callback_data="my_reports")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")]])
    await cb.message.edit_text(t, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "my_reports")
async def reports(cb: CallbackQuery):
    reps = await get_user_reports(cb.from_user.id)
    if not reps:
        t = "📋 Заявок пока нет. Найди первую подделку!"
    else:
        t = "📋 Последние заявки:\n\n"
        em = {'pending': '⏳', 'confirmed': '✅', 'rejected': '❌'}
        nm = {'pending': 'На проверке', 'confirmed': 'Подтверждено', 'rejected': 'Отклонено'}
        for r in reps:
            t += f"{em.get(r[3],'⏳')} #{r[0]} · {r[1]} ({r[2]})\n     {nm.get(r[3],'?')}"
            if r[3] == 'confirmed' and r[4]:
                t += f" · +{r[4]:,} ₽".replace(",", " ")
            t += "\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_menu")]])
    await cb.message.edit_text(t, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "withdraw")
async def withdraw(cb: CallbackQuery, state: FSMContext):
    s = await get_user_stats(cb.from_user.id)
    if s['balance'] < MIN_WITHDRAW:
        await cb.answer(f"Минимум {MIN_WITHDRAW} ₽. Баланс {s['balance']} ₽.", show_alert=True)
        return
    await state.set_state(WithdrawForm.waiting_amount)
    await state.update_data(max_bal=s['balance'])
    t = f"💳 Доступно: {s['balance']:,} ₽\nМинимум: {MIN_WITHDRAW} ₽\n\nВведи сумму:".replace(",", " ")
    if LAUNCH_MODE:
        t += "\n\n⚠️ Заявка будет обработана после подключения брендов."
    await cb.message.answer(t)
    await cb.answer()


@router.message(WithdrawForm.waiting_amount, F.text)
async def w_amount(msg: Message, state: FSMContext):
    a = ''.join(filter(str.isdigit, msg.text))
    if not a:
        await msg.answer("❌ Цифрами, например: 1000")
        return
    a = int(a)
    d = await state.get_data()
    if a < MIN_WITHDRAW:
        await msg.answer(f"❌ Минимум {MIN_WITHDRAW} ₽.")
        return
    if a > d.get('max_bal', 0):
        await msg.answer(f"❌ На балансе только {d.get('max_bal',0)} ₽.")
        return
    await state.update_data(w_amount=a)
    await state.set_state(WithdrawForm.waiting_details)
    await msg.answer("💳 Реквизиты (карта или телефон СБП):")


@router.message(WithdrawForm.waiting_details, F.text)
async def w_details(msg: Message, state: FSMContext):
    det = msg.text
    d = await state.get_data()
    a = d.get('w_amount')
    wid = await save_withdrawal(msg.from_user.id, a, det)
    t = f"✅ Заявка #{wid}\n💵 {a:,} ₽\n💳 {det}\n\n".replace(",", " ")
    t += "⏳ Обработается после подключения брендов." if LAUNCH_MODE else "⏳ 1-3 дня."
    await msg.answer(t)
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID,
                f"💸 ВЫВОД #{wid}\n👤 {msg.from_user.full_name}\n"
                f"💵 {a:,} ₽\n💳 {det}".replace(",", " "),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Выплачено",
                                          callback_data=f"paid_{wid}")]]))
        except Exception:
            pass
    await state.clear()


@router.callback_query(F.data.startswith("paid_"))
async def paid(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Только админ", show_alert=True)
        return
    wid = int(cb.data.replace("paid_", ""))
    w = await get_withdrawal(wid)
    if not w:
        await cb.answer("Не найдено", show_alert=True)
        return
    await update_withdrawal(wid, 'paid')
    await cb.message.answer(f"✅ Выплата #{wid} выполнена.")
    try:
        await bot.send_message(w[1],
            f"💰 Выплата #{wid} — {w[2]:,} ₽ выполнена!".replace(",", " "))
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data == "new_report")
async def new_rep(cb: CallbackQuery, state: FSMContext):
    await state.set_state(ReportForm.photos)
    await state.update_data(photos=[])
    await cb.message.answer(
        "📸 Шаг 1 из 6: Фото\n\nОтправь 2-5 фото товара.\n\n"
        "• Общий вид\n• Логотип\n• Этикетка\n• Подозрительные детали")
    await cb.answer()


@router.message(ReportForm.photos, F.photo)
async def photo(msg: Message, state: FSMContext):
    d = await state.get_data()
    p = d.get('photos', [])
    p.append(msg.photo[-1].file_id)
    await state.update_data(photos=p)
    if len(p) < 2:
        await msg.answer(f"📷 {len(p)}/5. Добавь ещё.")
    elif len(p) < 5:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Дальше", callback_data="photos_done")]])
        await msg.answer(f"✅ {len(p)}/5.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data="photos_done")]])
        await msg.answer("✅ Максимум 5.", reply_markup=kb)


@router.callback_query(F.data == "photos_done")
async def ph_done(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if len(d.get('photos', [])) < 2:
        await cb.answer("Минимум 2 фото!", show_alert=True)
        return
    await state.set_state(ReportForm.brand)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Nike", callback_data="brand_Nike"),
         InlineKeyboardButton(text="Adidas", callback_data="brand_Adidas")],
        [InlineKeyboardButton(text="Apple", callback_data="brand_Apple"),
         InlineKeyboardButton(text="Chanel", callback_data="brand_Chanel")],
        [InlineKeyboardButton(text="✍️ Другой", callback_data="brand_other")]])
    await cb.message.answer("🏷 Шаг 2 из 6: Бренд", reply_markup=kb)
    await cb.answer()


@router.callback_query(ReportForm.brand, F.data.startswith("brand_"))
async def sel_brand(cb: CallbackQuery, state: FSMContext):
    if cb.data == "brand_other":
        await cb.message.answer("✍️ Напиши бренд:")
        await cb.answer()
        return
    await state.update_data(brand=cb.data.replace("brand_", ""))
    await ask_cat(cb.message, state)
    await cb.answer()


@router.message(ReportForm.brand, F.text)
async def type_brand(msg: Message, state: FSMContext):
    await state.update_data(brand=msg.text)
    await ask_cat(msg, state)


async def ask_cat(msg, state):
    await state.set_state(ReportForm.category)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👟 Обувь", callback_data="cat_Обувь"),
         InlineKeyboardButton(text="👕 Одежда", callback_data="cat_Одежда")],
        [InlineKeyboardButton(text="🎧 Электроника", callback_data="cat_Электроника"),
         InlineKeyboardButton(text="💄 Косметика", callback_data="cat_Косметика")],
        [InlineKeyboardButton(text="👜 Аксессуары", callback_data="cat_Аксессуары"),
         InlineKeyboardButton(text="📌 Другое", callback_data="cat_Другое")]])
    await msg.answer("📦 Шаг 3 из 6: Категория", reply_markup=kb)


@router.callback_query(ReportForm.category, F.data.startswith("cat_"))
async def sel_cat(cb: CallbackQuery, state: FSMContext):
    await state.update_data(category=cb.data.replace("cat_", ""))
    await state.set_state(ReportForm.location)
    await cb.message.answer("📍 Шаг 4 из 6: Где купил? Адрес или магазин:")
    await cb.answer()


@router.message(ReportForm.location, F.text)
async def loc(msg: Message, state: FSMContext):
    await state.update_data(location=msg.text)
    await state.set_state(ReportForm.price)
    await msg.answer("💵 Шаг 5 из 6: Цена (цифры):")


@router.message(ReportForm.price, F.text)
async def price(msg: Message, state: FSMContext):
    p = ''.join(filter(str.isdigit, msg.text))
    if not p:
        await msg.answer("❌ Цифрами: 5000")
        return
    await state.update_data(price=p)
    await state.set_state(ReportForm.signs)
    await msg.answer("🔍 Шаг 6 из 6: Признаки подделки:")


@router.message(ReportForm.signs, F.text)
async def signs(msg: Message, state: FSMContext):
    await state.update_data(signs=msg.text)
    d = await state.get_data()
    rid = await save_report(msg.from_user.id, d)

    # Google Sheets
    sheets_append(rid, msg.from_user.full_name, d['brand'],
                  d['category'], d['location'], d['price'],
                  len(d['photos']), 'pending')

    t = (f"✅ Заявка #{rid}!\n\n"
         f"🏷 {d['brand']}\n📦 {d['category']}\n"
         f"📍 {d['location']}\n💵 {d['price']} ₽\n"
         f"📸 {len(d['photos'])} фото\n\n⏱ До 7 дней")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Ещё", callback_data="new_report")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_menu")]])
    await msg.answer(t, reply_markup=kb)
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID,
                f"🆕 ЗАЯВКА #{rid}\n👤 {msg.from_user.full_name}\n"
                f"📱 @{msg.from_user.username or '—'}\n"
                f"🏷 {d['brand']}\n📦 {d['category']}\n"
                f"📍 {d['location']}\n💵 {d['price']} ₽\n"
                f"🔍 {d['signs']}\n📸 Фото ⬇️",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅", callback_data=f"approve_{rid}"),
                     InlineKeyboardButton(text="❌", callback_data=f"reject_{rid}")]]))
            for ph in d['photos']:
                await bot.send_photo(ADMIN_ID, ph)
        except Exception as e:
            logging.error(f"Ошибка: {e}")
    await state.clear()


@router.callback_query(F.data.startswith("approve_"))
async def approve(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Только админ", show_alert=True)
        return
    rid = int(cb.data.replace("approve_", ""))
    await state.set_state(ModerationForm.waiting_reward)
    await state.update_data(mod_rid=rid)
    await cb.message.answer(f"✅ Заявка #{rid}\nСумма награды (цифры):")
    await cb.answer()


@router.message(ModerationForm.waiting_reward, F.text)
async def reward(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    rw = ''.join(filter(str.isdigit, msg.text))
    if not rw:
        await msg.answer("❌ Цифрами: 4000")
        return
    rw = int(rw)
    d = await state.get_data()
    rid = d.get('mod_rid')
    rep = await get_report(rid)
    if not rep:
        await msg.answer("Не найдена.")
        await state.clear()
        return
    await update_report_status(rid, 'confirmed', reward=rw)
    sheets_update(rid, f"confirmed ({rw} ₽)")
    await msg.answer(f"✅ #{rid} подтверждена. {rw:,} ₽".replace(",", " "))
    hid = rep[1]
    try:
        await bot.send_message(hid,
            f"🎉 Заявка #{rid} ({rep[2]}) подтверждена!\n"
            f"💰 {rw:,} ₽ на балансе!".replace(",", " "))
    except Exception:
        pass
    if CHANNEL_USERNAME:
        try:
            await bot.send_message(CHANNEL_USERNAME,
                f"🎯 Находка!\n\n🏷 {rep[2]}\n📦 {rep[3]}\n"
                f"📍 {extract_city(rep[4])}\n\nОткрой бота!")
        except Exception:
            pass
    cc = await count_confirmed(hid)
    if cc == 1:
        ri = await get_ref_info(hid)
        if ri['referred_by'] and not ri['ref_bonus_paid']:
            await add_ref_bonus(ri['referred_by'], REFERRAL_BONUS)
            await mark_ref_paid(hid)
            try:
                await bot.send_message(ri['referred_by'],
                    f"🎁 Бонус {REFERRAL_BONUS} ₽ за реферала!")
            except Exception:
                pass
    await state.clear()


@router.callback_query(F.data.startswith("reject_"))
async def reject(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Только админ", show_alert=True)
        return
    rid = int(cb.data.replace("reject_", ""))
    await state.set_state(ModerationForm.waiting_reject_reason)
    await state.update_data(mod_rid=rid)
    await cb.message.answer(f"❌ Заявка #{rid}\nПричина:")
    await cb.answer()


@router.message(ModerationForm.waiting_reject_reason, F.text)
async def rej_reason(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    d = await state.get_data()
    rid = d.get('mod_rid')
    rep = await get_report(rid)
    if not rep:
        await msg.answer("Не найдена.")
        await state.clear()
        return
    await update_report_status(rid, 'rejected', reject_reason=msg.text)
    sheets_update(rid, f"rejected")
    await msg.answer(f"❌ #{rid} отклонена.")
    try:
        await bot.send_message(rep[1],
            f"📋 Заявка #{rid} ({rep[2]}) отклонена.\n"
            f"Причина: {msg.text}")
    except Exception:
        pass
    await state.clear()


@router.message(Command("post"))
async def post_start(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not CHANNEL_USERNAME:
        await msg.answer("❌ Канал не настроен.")
        return
    await state.set_state(PostForm.waiting_text)
    await msg.answer(f"📢 Пост в {CHANNEL_USERNAME}\nНапиши текст (/cancel для отмены):")


@router.message(PostForm.waiting_text, F.text == "/cancel")
async def post_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменено.")


@router.message(PostForm.waiting_text, F.text)
async def post_preview(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await state.update_data(post_text=msg.text)
    await state.set_state(PostForm.waiting_confirm)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="post_ok"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="post_no")]])
    await msg.answer(f"👁 Превью:\n━━━\n{msg.text}\n━━━\nПубликуем?", reply_markup=kb)


@router.callback_query(F.data == "post_no")
async def post_no(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Отменено.")
    await cb.answer()


@router.callback_query(F.data == "post_ok")
async def post_ok(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID:
        return
    d = await state.get_data()
    await state.clear()
    try:
        await bot.send_message(CHANNEL_USERNAME, d['post_text'], parse_mode="Markdown")
        await cb.message.edit_text(f"✅ Опубликовано в {CHANNEL_USERNAME}!")
    except Exception as e:
        await cb.message.edit_text(f"❌ Ошибка: {e}")
    await cb.answer()


@router.message(Command("broadcast"))
async def bc_start(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await state.set_state(BroadcastForm.waiting_text)
    await msg.answer("📢 Рассылка всем.\nНапиши текст (/cancel для отмены):")


@router.message(BroadcastForm.waiting_text, F.text == "/cancel")
async def bc_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменено.")


@router.message(BroadcastForm.waiting_text, F.text)
async def bc_preview(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await state.update_data(bc_text=msg.text)
    await state.set_state(BroadcastForm.waiting_confirm)
    ids = await get_all_user_ids()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Отправить ({len(ids)})", callback_data="bc_ok"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="bc_no")]])
    await msg.answer(f"📢 Превью:\n━━━\n{msg.text}\n━━━\n{len(ids)} чел.", reply_markup=kb)


@router.callback_query(F.data == "bc_no")
async def bc_no(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Отменено.")
    await cb.answer()


@router.callback_query(F.data == "bc_ok")
async def bc_ok(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID:
        return
    d = await state.get_data()
    await state.clear()
    await cb.message.edit_text("📤 Рассылка...")
    ids = await get_all_user_ids()
    ok = fail = 0
    for uid in ids:
        try:
            await bot.send_message(uid, d['bc_text'])
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await cb.message.answer(f"✅ Готово!\n📬 {ok}\n❌ {fail}")
    await cb.answer()


@router.message(Command("stats"))
async def stats(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        u = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        ob = (await (await db.execute("SELECT COUNT(*) FROM users WHERE onboarded=1")).fetchone())[0]
        r = (await (await db.execute("SELECT COUNT(*) FROM reports WHERE brand!='Реферальный бонус'")).fetchone())[0]
        p = (await (await db.execute("SELECT COUNT(*) FROM reports WHERE status='pending'")).fetchone())[0]
        c = (await (await db.execute("SELECT COUNT(*) FROM reports WHERE status='confirmed' AND brand!='Реферальный бонус'")).fetchone())[0]
        rw = (await (await db.execute("SELECT SUM(reward) FROM reports WHERE status='confirmed'")).fetchone())[0] or 0
        pw = (await (await db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")).fetchone())[0]
        rf = (await (await db.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")).fetchone())[0]
    await msg.answer(
        f"📊 Статистика\n\n👥 {u} (онб: {ob}, реф: {rf})\n"
        f"📋 {r} заявок (⏳{p} ✅{c})\n"
        f"💰 {rw:,} ₽\n💸 Ожидают: {pw}".replace(",", " "))


@router.message(Command("help"))
async def help_cmd(msg: Message):
    await msg.answer(
        "ℹ️ /start — меню\n/help — справка\n\n"
        "🚀 Подать заявку\n💰 Кабинет\n👥 Рефералы\n\n"
        "Поддержка: @authentiscan_support")


async def main():
    await init_db()
    logging.info("Бот охотников запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
