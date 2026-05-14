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
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except Exception:
                pass
        await db.commit()


async def save_user(user_id, username, full_name, referred_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, есть ли уже пользователь
        async with db.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (user_id,)) as cursor:
            exists = await cursor.fetchone()
        if exists:
            return False  # уже зарегистрирован, реферал не засчитывается
        # Нельзя пригласить сам себя
        if referred_by == user_id:
            referred_by = None
        await db.execute(
            "INSERT INTO users (telegram_id, username, full_name, registered_at, referred_by) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, full_name, datetime.now().isoformat(), referred_by)
        )
        await db.commit()
        return True  # новый пользователь


async def save_report(user_id, data):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reports (user_id, brand, category, location, price, signs, photo_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, data.get('brand'), data.get('category'), data.get('location'),
             data.get('price'), data.get('signs'), len(data.get('photos', [])),
             datetime.now().isoformat())
        )
        await db.commit()
        return cursor.lastrowid


async def get_report(report_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, brand, category, location, price, signs, status, reward FROM reports WHERE id=?",
            (report_id,)
        ) as cursor:
            return await cursor.fetchone()


async def update_report_status(report_id, status, reward=0, reject_reason=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE reports SET status=?, reward=?, reject_reason=? WHERE id=?",
            (status, reward, reject_reason, report_id)
        )
        await db.commit()


async def count_confirmed_reports(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM reports WHERE user_id=? AND status='confirmed'",
            (user_id,)
        ) as cursor:
            return (await cursor.fetchone())[0]


async def get_user_referral_info(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT referred_by, ref_bonus_paid FROM users WHERE telegram_id=?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return {'referred_by': row[0] if row else None,
                    'ref_bonus_paid': row[1] if row else 0}


async def count_referrals(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,)
        ) as cursor:
            total = (await cursor.fetchone())[0]
        return total


async def mark_ref_bonus_paid(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET ref_bonus_paid=1 WHERE telegram_id=?", (user_id,))
        await db.commit()


async def add_referral_bonus_to_balance(referrer_id, amount):
    # Бонус оформляем как "виртуальную" подтверждённую запись в reports,
    # чтобы он попал в баланс. Но проще — отдельная таблица. Здесь используем reports.
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reports (user_id, brand, category, location, price, signs, photo_count, status, reward, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (referrer_id, "Реферальный бонус", "Бонус", "—", "0", "Друг подал первую подтверждённую заявку",
             0, "confirmed", amount, datetime.now().isoformat())
        )
        await db.commit()


async def get_user_stats(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='confirmed' THEN reward ELSE 0 END) "
            "FROM reports WHERE user_id=? AND brand != 'Реферальный бонус'",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        # Общий заработок включая бонусы
        async with db.execute(
            "SELECT SUM(CASE WHEN status='confirmed' THEN reward ELSE 0 END) FROM reports WHERE user_id=?",
            (user_id,)
        ) as cursor:
            total_earned = (await cursor.fetchone())[0] or 0
        # Уже выведено
        async with db.execute(
            "SELECT SUM(amount) FROM withdrawals WHERE user_id=? AND status IN ('pending','paid')",
            (user_id,)
        ) as cursor:
            withdrawn = (await cursor.fetchone())[0] or 0
        return {
            'total': row[0] or 0,
            'confirmed': row[1] or 0,
            'pending': row[2] or 0,
            'rejected': row[3] or 0,
            'earned': total_earned,
            'withdrawn': withdrawn,
            'balance': total_earned - withdrawn
        }


async def get_user_reports(user_id, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, brand, category, status, reward, created_at FROM reports WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()


async def save_withdrawal(user_id, amount, details):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO withdrawals (user_id, amount, details, created_at) VALUES (?, ?, ?, ?)",
            (user_id, amount, details, datetime.now().isoformat())
        )
        await db.commit()
        return cursor.lastrowid


async def update_withdrawal_status(withdrawal_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (status, withdrawal_id))
        await db.commit()


async def get_withdrawal(withdrawal_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, amount, details, status FROM withdrawals WHERE id=?",
            (withdrawal_id,)
        ) as cursor:
            return await cursor.fetchone()


def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="💰 Мой кабинет", callback_data="my_cabinet")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_reports")],
        [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="referral")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="how_it_works")]
    ])


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    # Разбираем реферальную ссылку: /start ref_12345
    referred_by = None
    if command.args and command.args.startswith("ref_"):
        try:
            referred_by = int(command.args.replace("ref_", ""))
        except ValueError:
            referred_by = None

    is_new = await save_user(message.from_user.id, message.from_user.username,
                             message.from_user.full_name, referred_by)

    # Уведомим реферера, что пришёл новый друг
    if is_new and referred_by:
        try:
            await bot.send_message(
                referred_by,
                f"🎉 По твоей ссылке зарегистрировался новый охотник!\n\n"
                f"Когда он получит первую подтверждённую заявку — ты получишь бонус {REFERRAL_BONUS} ₽."
            )
        except Exception:
            pass

    text = (
        "👋 Привет! Я бот AuthentiScan.\n\n"
        "Помогаю охотникам за подделками находить контрафакт и получать вознаграждение от брендов.\n\n"
        "💰 За каждую подтверждённую заявку — от 1,000 до 50,000 ₽\n"
        "👥 Приглашай друзей — получай бонусы\n"
        "🌍 Работаем в 50+ городах России и СНГ"
    )
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "how_it_works")
async def how_it_works(callback: CallbackQuery):
    text = (
        "📖 Как это работает:\n\n"
        "1️⃣ Находишь подозрительный товар в магазине или на маркетплейсе\n"
        "2️⃣ Фотографируешь его с разных ракурсов (2-5 фото)\n"
        "3️⃣ Заполняешь заявку через этого бота\n"
        "4️⃣ Бренд проверяет твою находку (до 7 дней)\n"
        "5️⃣ За подтверждённую подделку — вознаграждение на карту"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🏠 Главное меню\n\nВыбери действие:", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "referral")
async def referral_menu(callback: CallbackQuery):
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
    refs_count = await count_referrals(callback.from_user.id)
    text = (
        f"👥 Приглашай друзей и зарабатывай\n\n"
        f"За каждого друга, который зарегистрируется по твоей ссылке и получит первую подтверждённую заявку, "
        f"ты получаешь бонус {REFERRAL_BONUS} ₽.\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👥 Приглашено друзей: {refs_count}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"🔗 Твоя личная ссылка:\n"
        f"{ref_link}\n\n"
        f"Просто отправь её друзьям!"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data == "my_cabinet")
async def my_cabinet(callback: CallbackQuery):
    stats = await get_user_stats(callback.from_user.id)
    confirm_rate = 0
    if stats['total'] > 0:
        confirm_rate = round(stats['confirmed'] / stats['total'] * 100)
    text = (
        f"💰 Личный кабинет\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💵 Доступно к выводу: {stats['balance']:,} ₽\n"
        f"💸 Уже выведено: {stats['withdrawn']:,} ₽\n"
        f"⏳ Заявок на проверке: {stats['pending']}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📊 Статистика:\n"
        f"📝 Всего заявок: {stats['total']}\n"
        f"✅ Подтверждено: {stats['confirmed']}\n"
        f"❌ Отклонено: {stats['rejected']}\n"
        f"🎯 Процент подтверждения: {confirm_rate}%"
    ).replace(",", " ")
    if LAUNCH_MODE:
        text += (
            "\n\n⚠️ Платформа в стадии запуска. Накопленный баланс реален, "
            "но реальные выплаты начнутся после подключения первых брендов-партнёров. "
            "Следи за новостями в канале!"
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Вывести средства", callback_data="withdraw")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_reports")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "my_reports")
async def my_reports(callback: CallbackQuery):
    reports = await get_user_reports(callback.from_user.id)
    if not reports:
        text = "📋 У тебя пока нет заявок.\n\nНайди первую подделку и подай заявку!"
    else:
        text = "📋 Твои последние заявки:\n\n"
        status_emoji = {'pending': '⏳', 'confirmed': '✅', 'rejected': '❌'}
        status_name = {'pending': 'На проверке', 'confirmed': 'Подтверждено', 'rejected': 'Отклонено'}
        for r in reports:
            rid, brand, category, status, reward, created = r
            emoji = status_emoji.get(status, '⏳')
            name = status_name.get(status, 'На проверке')
            text += f"{emoji} #{rid} · {brand} ({category})\n"
            text += f"     {name}"
            if status == 'confirmed' and reward:
                text += f" · +{reward:,} ₽".replace(",", " ")
            text += "\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# ============ ВЫВОД СРЕДСТВ ============

@router.callback_query(F.data == "withdraw")
async def withdraw_start(callback: CallbackQuery, state: FSMContext):
    stats = await get_user_stats(callback.from_user.id)
    if stats['balance'] < MIN_WITHDRAW:
        await callback.answer(
            f"Минимальная сумма вывода — {MIN_WITHDRAW} ₽. У тебя на балансе {stats['balance']} ₽.",
            show_alert=True
        )
        return
    await state.set_state(WithdrawForm.waiting_amount)
    await state.update_data(max_balance=stats['balance'])
    text = (
        f"💳 Вывод средств\n\n"
        f"Доступно к выводу: {stats['balance']:,} ₽\n"
        f"Минимальная сумма: {MIN_WITHDRAW} ₽\n\n"
        f"Введи сумму для вывода (только цифры):"
    ).replace(",", " ")
    if LAUNCH_MODE:
        text += (
            "\n\n⚠️ Сейчас платформа в стадии запуска. Заявка на вывод будет принята "
            "и обработана после подключения первых брендов-партнёров."
        )
    await callback.message.answer(text)
    await callback.answer()


@router.message(WithdrawForm.waiting_amount, F.text)
async def withdraw_amount(message: Message, state: FSMContext):
    amount = ''.join(filter(str.isdigit, message.text))
    if not amount:
        await message.answer("❌ Введи сумму цифрами, например: 1000")
        return
    amount = int(amount)
    data = await state.get_data()
    max_balance = data.get('max_balance', 0)
    if amount < MIN_WITHDRAW:
        await message.answer(f"❌ Минимальная сумма вывода — {MIN_WITHDRAW} ₽.")
        return
    if amount > max_balance:
        await message.answer(f"❌ На балансе только {max_balance:,} ₽. Введи меньшую сумму.".replace(",", " "))
        return
    await state.update_data(withdraw_amount=amount)
    await state.set_state(WithdrawForm.waiting_details)
    await message.answer(
        "💳 Введи реквизиты для перевода:\n\n"
        "Номер карты или номер телефона для СБП.\n"
        "Например: 2200 1234 5678 9010 (Тинькофф)\n"
        "или: +7 900 123-45-67 (СБП, Сбербанк)"
    )


@router.message(WithdrawForm.waiting_details, F.text)
async def withdraw_details(message: Message, state: FSMContext):
    details = message.text
    data = await state.get_data()
    amount = data.get('withdraw_amount')
    withdrawal_id = await save_withdrawal(message.from_user.id, amount, details)
    text = (
        f"✅ Заявка на вывод #{withdrawal_id} создана!\n\n"
        f"💵 Сумма: {amount:,} ₽\n"
        f"💳 Реквизиты: {details}\n\n"
    ).replace(",", " ")
    if LAUNCH_MODE:
        text += (
            "⏳ Платформа в стадии запуска. Заявка сохранена и будет обработана "
            "после подключения брендов-партнёров. Мы уведомим тебя!"
        )
    else:
        text += "⏳ Заявка обрабатывается. Деньги поступят в течение 1-3 дней."
    await message.answer(text)

    # Уведомление админу
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"💸 НОВАЯ ЗАЯВКА НА ВЫВОД #{withdrawal_id}\n\n"
                f"👤 От: {message.from_user.full_name}\n"
                f"📱 @{message.from_user.username or 'нет username'}\n"
                f"🆔 ID: {message.from_user.id}\n\n"
                f"💵 Сумма: {amount:,} ₽\n".replace(",", " ") +
                f"💳 Реквизиты: {details}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Выплачено", callback_data=f"paid_{withdrawal_id}")]
                ])
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить админа о выводе: {e}")
    await state.clear()


@router.callback_query(F.data.startswith("paid_"))
async def mark_paid(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только для администратора", show_alert=True)
        return
    withdrawal_id = int(callback.data.replace("paid_", ""))
    withdrawal = await get_withdrawal(withdrawal_id)
    if not withdrawal:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await update_withdrawal_status(withdrawal_id, 'paid')
    await callback.message.answer(f"✅ Выплата #{withdrawal_id} отмечена как выполненная.")
    # Уведомляем охотника
    try:
        await bot.send_message(
            withdrawal[1],
            f"💰 Выплата по заявке #{withdrawal_id} на сумму {withdrawal[2]:,} ₽ выполнена!\n\n".replace(",", " ") +
            f"Проверь поступление по реквизитам: {withdrawal[3]}\n\n"
            f"Спасибо, что с нами! Продолжай находить подделки 🔍"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить о выплате: {e}")
    await callback.answer("Готово")


# ============ ПОДАЧА ЗАЯВКИ ============

@router.callback_query(F.data == "new_report")
async def start_report(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReportForm.photos)
    await state.update_data(photos=[])
    text = (
        "📸 Шаг 1 из 6: Фото\n\n"
        "Отправь от 2 до 5 фотографий товара с разных ракурсов.\n\n"
        "Лучше всего:\n"
        "• Общий вид товара\n"
        "• Логотип крупным планом\n"
        "• Этикетка / штрихкод\n"
        "• Подозрительные детали"
    )
    await callback.message.answer(text)
    await callback.answer()


@router.message(ReportForm.photos, F.photo)
async def process_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get('photos', [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    if len(photos) < 2:
        await message.answer(f"📷 Фото {len(photos)}/5 получено. Добавь ещё минимум одно.")
    elif len(photos) < 5:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Достаточно, дальше", callback_data="photos_done")]
        ])
        await message.answer(f"✅ Фото {len(photos)}/5 получено.", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data="photos_done")]
        ])
        await message.answer("✅ Получено максимум — 5 фото.", reply_markup=keyboard)


@router.callback_query(F.data == "photos_done")
async def photos_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if len(data.get('photos', [])) < 2:
        await callback.answer("Нужно минимум 2 фото!", show_alert=True)
        return
    await state.set_state(ReportForm.brand)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Nike", callback_data="brand_Nike"),
         InlineKeyboardButton(text="Adidas", callback_data="brand_Adidas")],
        [InlineKeyboardButton(text="Apple", callback_data="brand_Apple"),
         InlineKeyboardButton(text="Chanel", callback_data="brand_Chanel")],
        [InlineKeyboardButton(text="✍️ Другой (напишу)", callback_data="brand_other")]
    ])
    await callback.message.answer("🏷 Шаг 2 из 6: Бренд", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(ReportForm.brand, F.data.startswith("brand_"))
async def select_brand(callback: CallbackQuery, state: FSMContext):
    if callback.data == "brand_other":
        await callback.message.answer("✍️ Напиши название бренда:")
        await callback.answer()
        return
    brand = callback.data.replace("brand_", "")
    await state.update_data(brand=brand)
    await ask_category(callback.message, state)
    await callback.answer()


@router.message(ReportForm.brand, F.text)
async def type_brand(message: Message, state: FSMContext):
    await state.update_data(brand=message.text)
    await ask_category(message, state)


async def ask_category(message, state):
    await state.set_state(ReportForm.category)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👟 Обувь", callback_data="cat_Обувь"),
         InlineKeyboardButton(text="👕 Одежда", callback_data="cat_Одежда")],
        [InlineKeyboardButton(text="🎧 Электроника", callback_data="cat_Электроника"),
         InlineKeyboardButton(text="💄 Косметика", callback_data="cat_Косметика")],
        [InlineKeyboardButton(text="👜 Аксессуары", callback_data="cat_Аксессуары"),
         InlineKeyboardButton(text="📌 Другое", callback_data="cat_Другое")]
    ])
    await message.answer("📦 Шаг 3 из 6: Категория", reply_markup=keyboard)


@router.callback_query(ReportForm.category, F.data.startswith("cat_"))
async def select_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.replace("cat_", "")
    await state.update_data(category=category)
    await state.set_state(ReportForm.location)
    await callback.message.answer("📍 Шаг 4 из 6: Место\n\nГде купил товар? Напиши адрес или название магазина:")
    await callback.answer()


@router.message(ReportForm.location, F.text)
async def set_location(message: Message, state: FSMContext):
    await state.update_data(location=message.text)
    await state.set_state(ReportForm.price)
    await message.answer("💵 Шаг 5 из 6: Цена\n\nЦена в рублях (только цифры):")


@router.message(ReportForm.price, F.text)
async def set_price(message: Message, state: FSMContext):
    price = ''.join(filter(str.isdigit, message.text))
    if not price:
        await message.answer("❌ Напиши цену цифрами, например: 5000")
        return
    await state.update_data(price=price)
    await state.set_state(ReportForm.signs)
    await message.answer(
        "🔍 Шаг 6 из 6: Признаки подделки\n\n"
        "Опиши признаки:\n"
        "• Неровный шов\n"
        "• Размытый логотип\n"
        "• Дешёвый материал\n"
        "• Отсутствует голограмма"
    )


@router.message(ReportForm.signs, F.text)
async def set_signs(message: Message, state: FSMContext):
    await state.update_data(signs=message.text)
    data = await state.get_data()
    report_id = await save_report(message.from_user.id, data)
    text = (
        f"✅ Заявка #{report_id} отправлена!\n\n"
        f"🏷 Бренд: {data['brand']}\n"
        f"📦 Категория: {data['category']}\n"
        f"📍 Место: {data['location']}\n"
        f"💵 Цена: {data['price']} ₽\n"
        f"📸 Фото: {len(data['photos'])} шт.\n\n"
        f"⏱ Срок проверки: до 7 дней\n"
        f"Уведомлю о результате здесь же!"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать ещё", callback_data="new_report")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="back_to_menu")]
    ])
    await message.answer(text, reply_markup=keyboard)

    if ADMIN_ID:
        try:
            admin_text = (
                f"🆕 НОВАЯ ЗАЯВКА #{report_id}\n\n"
                f"👤 От: {message.from_user.full_name}\n"
                f"📱 @{message.from_user.username or 'нет username'}\n"
                f"🆔 ID: {message.from_user.id}\n\n"
                f"🏷 Бренд: {data['brand']}\n"
                f"📦 Категория: {data['category']}\n"
                f"📍 Место: {data['location']}\n"
                f"💵 Цена: {data['price']} ₽\n"
                f"🔍 Признаки:\n{data['signs']}\n\n"
                f"📸 Фото ниже ⬇️"
            )
            mod_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{report_id}"),
                 InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{report_id}")]
            ])
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=mod_keyboard)
            for photo_id in data['photos']:
                await bot.send_photo(ADMIN_ID, photo_id)
        except Exception as e:
            logging.error(f"Не удалось отправить админу: {e}")
    await state.clear()


# ============ МОДЕРАЦИЯ ============

@router.callback_query(F.data.startswith("approve_"))
async def approve_report(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только для администратора", show_alert=True)
        return
    report_id = int(callback.data.replace("approve_", ""))
    await state.set_state(ModerationForm.waiting_reward)
    await state.update_data(moderating_report=report_id)
    await callback.message.answer(
        f"✅ Подтверждение заявки #{report_id}\n\n"
        f"Введи сумму вознаграждения охотнику в рублях (только цифры):"
    )
    await callback.answer()


@router.message(ModerationForm.waiting_reward, F.text)
async def set_reward(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    reward = ''.join(filter(str.isdigit, message.text))
    if not reward:
        await message.answer("❌ Введи сумму цифрами, например: 4000")
        return
    reward = int(reward)
    data = await state.get_data()
    report_id = data.get('moderating_report')
    report = await get_report(report_id)
    if not report:
        await message.answer("Заявка не найдена.")
        await state.clear()
        return
    await update_report_status(report_id, 'confirmed', reward=reward)
    await message.answer(f"✅ Заявка #{report_id} подтверждена. Награда: {reward:,} ₽".replace(",", " "))
    hunter_id = report[1]
    try:
        await bot.send_message(
            hunter_id,
            f"🎉 Отличные новости!\n\n"
            f"Твоя заявка #{report_id} ({report[2]}) подтверждена!\n\n"
            f"💰 Вознаграждение: {reward:,} ₽\n\n".replace(",", " ") +
            f"Сумма зачислена на твой баланс. Спасибо за помощь в борьбе с контрафактом!"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить охотника: {e}")
        await message.answer("⚠️ Не удалось отправить уведомление охотнику.")

    # Проверка реферального бонуса: если это первая подтверждённая заявка охотника
    confirmed_count = await count_confirmed_reports(hunter_id)
    if confirmed_count == 1:
        ref_info = await get_user_referral_info(hunter_id)
        referrer = ref_info['referred_by']
        if referrer and not ref_info['ref_bonus_paid']:
            await add_referral_bonus_to_balance(referrer, REFERRAL_BONUS)
            await mark_ref_bonus_paid(hunter_id)
            try:
                await bot.send_message(
                    referrer,
                    f"🎁 Реферальный бонус!\n\n"
                    f"Приглашённый тобой охотник получил первую подтверждённую заявку.\n"
                    f"Тебе начислен бонус {REFERRAL_BONUS} ₽ на баланс!"
                )
            except Exception:
                pass
            await message.answer(f"ℹ️ Рефереру охотника начислен бонус {REFERRAL_BONUS} ₽.")
    await state.clear()


@router.callback_query(F.data.startswith("reject_"))
async def reject_report(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только для администратора", show_alert=True)
        return
    report_id = int(callback.data.replace("reject_", ""))
    await state.set_state(ModerationForm.waiting_reject_reason)
    await state.update_data(moderating_report=report_id)
    await callback.message.answer(
        f"❌ Отклонение заявки #{report_id}\n\n"
        f"Напиши причину отклонения (охотник её увидит):"
    )
    await callback.answer()


@router.message(ModerationForm.waiting_reject_reason, F.text)
async def set_reject_reason(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    reason = message.text
    data = await state.get_data()
    report_id = data.get('moderating_report')
    report = await get_report(report_id)
    if not report:
        await message.answer("Заявка не найдена.")
        await state.clear()
        return
    await update_report_status(report_id, 'rejected', reject_reason=reason)
    await message.answer(f"❌ Заявка #{report_id} отклонена.")
    hunter_id = report[1]
    try:
        await bot.send_message(
            hunter_id,
            f"📋 Обновление по заявке #{report_id} ({report[2]})\n\n"
            f"К сожалению, заявка отклонена.\n\n"
            f"Причина: {reason}\n\n"
            f"Не расстраивайся — подавай новые заявки, учитывая этот опыт!"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить охотника: {e}")
        await message.answer("⚠️ Не удалось отправить уведомление охотнику.")
    await state.clear()


# ============ КОМАНДЫ АДМИНА ============

@router.message(Command("stats"))
async def admin_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM reports WHERE brand != 'Реферальный бонус'") as cursor:
            reports_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM reports WHERE status='pending'") as cursor:
            pending_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM reports WHERE status='confirmed' AND brand != 'Реферальный бонус'") as cursor:
            confirmed_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT SUM(reward) FROM reports WHERE status='confirmed'") as cursor:
            total_rewards = (await cursor.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'") as cursor:
            pending_withdrawals = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL") as cursor:
            referred_users = (await cursor.fetchone())[0]
    await message.answer(
        f"📊 Статистика бота:\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"   из них по рефералам: {referred_users}\n"
        f"📋 Заявок всего: {reports_count}\n"
        f"⏳ На проверке: {pending_count}\n"
        f"✅ Подтверждено: {confirmed_count}\n"
        f"💰 Начислено наград: {total_rewards:,} ₽\n".replace(",", " ") +
        f"💸 Заявок на вывод (ожидают): {pending_withdrawals}"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "ℹ️ Помощь\n\n"
        "/start — главное меню\n"
        "/help — эта справка\n\n"
        "Как подать заявку: нажми «🚀 Подать заявку».\n"
        "Баланс и история: «💰 Мой кабинет».\n"
        "Приглашай друзей: «👥 Пригласить друзей».\n\n"
        "Вопросы: @authentiscan_support"
    )


async def main():
    await init_db()
    logging.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
