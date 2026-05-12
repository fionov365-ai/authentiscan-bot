import asyncio
import logging
import os
from datetime import datetime
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
)

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_СЮДА_ВАШ_ТОКЕН")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
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


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                registered_at TEXT
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
                created_at TEXT
            )
        """)
        await db.commit()


async def save_user(user_id, username, full_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, full_name, registered_at) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, datetime.now().isoformat())
        )
        await db.commit()


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


async def get_user_stats(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END), SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) FROM reports WHERE user_id=?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return {'total': row[0] or 0, 'confirmed': row[1] or 0, 'pending': row[2] or 0}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    text = (
        "👋 Привет! Я бот AuthentiScan.\n\n"
        "Помогаю охотникам за подделками находить контрафакт и получать вознаграждение от брендов.\n\n"
        "💰 За каждую подтверждённую заявку — от 1,000 до 50,000 ₽\n"
        "🌍 Работаем в 50+ городах России и СНГ\n"
        "⚡ Проверка занимает до 7 дней"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_reports")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="how_it_works")]
    ])
    await message.answer(text, reply_markup=keyboard)


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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_reports")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="how_it_works")]
    ])
    await callback.message.edit_text("🏠 Главное меню\n\nВыбери действие:", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "my_reports")
async def my_reports(callback: CallbackQuery):
    stats = await get_user_stats(callback.from_user.id)
    text = (
        f"📋 Твоя статистика:\n\n"
        f"📝 Всего заявок: {stats['total']}\n"
        f"✅ Подтверждено: {stats['confirmed']}\n"
        f"⏳ На проверке: {stats['pending']}\n\n"
    )
    if stats['total'] == 0:
        text += "Ты ещё не подавал заявок. Найди первую подделку!"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подать заявку", callback_data="new_report")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


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
        f"⏱ Срок проверки: до 7 дней"
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
            await bot.send_message(ADMIN_ID, admin_text)
            for photo_id in data['photos']:
                await bot.send_photo(ADMIN_ID, photo_id)
        except Exception as e:
            logging.error(f"Не удалось отправить админу: {e}")
    await state.clear()


@router.message(Command("stats"))
async def admin_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM reports") as cursor:
            reports_count = (await cursor.fetchone())[0]
    await message.answer(f"📊 Статистика:\n👥 Пользователей: {users_count}\n📋 Заявок: {reports_count}")


async def main():
    await init_db()
    logging.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
