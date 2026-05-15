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
# Токен бота для брендов (от @BotFather)
BRAND_BOT_TOKEN = os.getenv("BRAND_BOT_TOKEN", "ВСТАВЬТЕ_ТОКЕН_БРЕНД_БОТА")
# Токен основного бота (чтобы уведомлять охотников)
MAIN_BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_ТОКЕН_ОСНОВНОГО_БОТА")
# Ваш Telegram ID (для уведомлений о новых регистрациях брендов)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
# ============ КОНЕЦ НАСТРОЕК ============

logging.basicConfig(level=logging.INFO)
brand_bot = Bot(token=BRAND_BOT_TOKEN)
main_bot = Bot(token=MAIN_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

DB_PATH = "bot.db"


# ============ ИНТЕГРАЦИЯ С GOOGLE SHEETS ============
# По той же схеме, что в bot.py: функции append_to_sheet /
# update_status_in_sheet регистрируются в __builtins__ из main.py.
# Здесь — безопасные обёртки, которые ничего не ломают, если
# Google Sheets не подключён.

def sheets_update(rid, status):
    try:
        fn = getattr(__builtins__, 'update_status_in_sheet', None)
        if callable(fn):
            fn(rid, status)
    except Exception:
        pass


class BrandRegForm(StatesGroup):
    waiting_company = State()
    waiting_brand_name = State()
    waiting_contact = State()


class BrandRejectForm(StatesGroup):
    waiting_reason = State()


class BrandRewardForm(StatesGroup):
    waiting_reward = State()


async def init_brand_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS brand_users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                company TEXT,
                brand_name TEXT,
                contact TEXT,
                status TEXT DEFAULT 'pending',
                registered_at TEXT
            )
        """)
        await db.commit()


async def save_brand_user(user_id, username, full_name):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT telegram_id FROM brand_users WHERE telegram_id=?",
            (user_id,)
        ) as cursor:
            exists = await cursor.fetchone()
        if exists:
            return False
        await db.execute(
            "INSERT INTO brand_users "
            "(telegram_id, username, full_name, registered_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, datetime.now().isoformat())
        )
        await db.commit()
        return True


async def update_brand_profile(user_id, company, brand_name, contact):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE brand_users SET company=?, brand_name=?, "
            "contact=?, status='active' WHERE telegram_id=?",
            (company, brand_name, contact, user_id)
        )
        await db.commit()


async def get_brand_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT telegram_id, company, brand_name, contact, status "
            "FROM brand_users WHERE telegram_id=?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()


async def get_brand_reports(brand_name, status=None, limit=20):
    async with aiosqlite.connect(DB_PATH) as db:
        if status:
            async with db.execute(
                "SELECT id, user_id, brand, category, location, "
                "price, signs, photo_count, status, reward, created_at "
                "FROM reports WHERE brand=? AND status=? "
                "ORDER BY id DESC LIMIT ?",
                (brand_name, status, limit)
            ) as cursor:
                return await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT id, user_id, brand, category, location, "
                "price, signs, photo_count, status, reward, created_at "
                "FROM reports WHERE brand=? "
                "ORDER BY id DESC LIMIT ?",
                (brand_name, limit)
            ) as cursor:
                return await cursor.fetchall()


async def get_report_by_id(report_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, brand, category, location, "
            "price, signs, photo_count, status, reward "
            "FROM reports WHERE id=?",
            (report_id,)
        ) as cursor:
            return await cursor.fetchone()


async def update_report_status(report_id, status,
                                reward=0, reject_reason=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE reports SET status=?, reward=?, "
            "reject_reason=? WHERE id=?",
            (status, reward, reject_reason, report_id)
        )
        await db.commit()


async def get_brand_stats(brand_name):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='confirmed' THEN reward ELSE 0 END) "
            "FROM reports WHERE brand=?",
            (brand_name,)
        ) as cursor:
            row = await cursor.fetchone()
            return {
                'total': row[0] or 0,
                'confirmed': row[1] or 0,
                'pending': row[2] or 0,
                'rejected': row[3] or 0,
                'paid': row[4] or 0
            }


def brand_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Новые заявки",
            callback_data="brand_reports_pending"
        )],
        [InlineKeyboardButton(
            text="✅ Подтверждённые",
            callback_data="brand_reports_confirmed"
        )],
        [InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="brand_stats"
        )],
        [InlineKeyboardButton(
            text="ℹ️ Мой профиль",
            callback_data="brand_profile"
        )]
    ])


# ============ РЕГИСТРАЦИЯ БРЕНДА ============

@router.message(CommandStart())
async def brand_start(message: Message, state: FSMContext):
    await state.clear()
    brand = await get_brand_user(message.from_user.id)

    if brand and brand[4] == 'active':
        # Уже зарегистрирован
        await message.answer(
            f"🏢 Добро пожаловать, {brand[1]}!\n\n"
            f"Бренд: {brand[2]}\n\n"
            f"Выберите действие:",
            reply_markup=brand_main_menu()
        )
    else:
        # Новый — начинаем регистрацию
        await save_brand_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name
        )
        await state.set_state(BrandRegForm.waiting_company)
        await message.answer(
            "👋 Добро пожаловать в AuthentiScan для брендов!\n\n"
            "Здесь вы будете получать заявки о контрафакте "
            "вашей продукции от нашей сети охотников.\n\n"
            "Для начала нужно зарегистрироваться. "
            "Это займёт 1 минуту.\n\n"
            "📝 Введите название вашей компании:"
        )


@router.message(BrandRegForm.waiting_company, F.text)
async def brand_reg_company(message: Message, state: FSMContext):
    await state.update_data(company=message.text)
    await state.set_state(BrandRegForm.waiting_brand_name)
    await message.answer(
        "✅ Компания сохранена.\n\n"
        "🏷 Введите название бренда который нужно защитить\n"
        "(например: Nike, Apple, Chanel):"
    )


@router.message(BrandRegForm.waiting_brand_name, F.text)
async def brand_reg_name(message: Message, state: FSMContext):
    await state.update_data(brand_name=message.text)
    await state.set_state(BrandRegForm.waiting_contact)
    await message.answer(
        "✅ Бренд сохранён.\n\n"
        "📞 Введите контактный email или телефон "
        "для связи с вами:"
    )


@router.message(BrandRegForm.waiting_contact, F.text)
async def brand_reg_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    await update_brand_profile(
        message.from_user.id,
        data['company'],
        data['brand_name'],
        message.text
    )
    await state.clear()

    # Уведомляем платформу о новом бренде
    if ADMIN_ID:
        try:
            await main_bot.send_message(
                ADMIN_ID,
                f"🏢 НОВЫЙ БРЕНД ЗАРЕГИСТРИРОВАН\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"📱 @{message.from_user.username or 'нет'}\n"
                f"🆔 ID: {message.from_user.id}\n\n"
                f"🏢 Компания: {data['company']}\n"
                f"🏷 Бренд: {data['brand_name']}\n"
                f"📞 Контакт: {message.text}"
            )
        except Exception as e:
            logging.error(f"Ошибка уведомления админа: {e}")

    await message.answer(
        f"✅ Регистрация завершена!\n\n"
        f"🏢 Компания: {data['company']}\n"
        f"🏷 Бренд: {data['brand_name']}\n"
        f"📞 Контакт: {message.text}\n\n"
        f"Теперь вы будете получать заявки о контрафакте "
        f"вашего бренда от охотников.\n\n"
        f"Выберите действие:",
        reply_markup=brand_main_menu()
    )


# ============ ПРОСМОТР ЗАЯВОК ============

@router.callback_query(F.data == "brand_reports_pending")
async def brand_reports_pending(callback: CallbackQuery):
    brand = await get_brand_user(callback.from_user.id)
    if not brand or brand[4] != 'active':
        await callback.answer("Сначала завершите регистрацию.", show_alert=True)
        return

    reports = await get_brand_reports(brand[2], status='pending')

    if not reports:
        await callback.message.edit_text(
            "📋 Новых заявок нет.\n\n"
            "Охотники активно ищут контрафакт — "
            "заявки появятся здесь как только будут найдены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="⬅️ В меню",
                    callback_data="brand_menu"
                )]
            ])
        )
    else:
        text = f"📋 Новые заявки ({len(reports)}):\n\n"
        buttons = []
        for r in reports[:10]:
            rid, uid, brand_n, cat, loc, price, signs, photos, status, reward, created = r
            city = loc.split(",")[0].strip() if loc else "—"
            text += (
                f"#{rid} · {cat} · {city} · "
                f"{price} ₽ · {photos} фото\n"
            )
            buttons.append([InlineKeyboardButton(
                text=f"👁 Заявка #{rid} — {cat}",
                callback_data=f"brand_view_{rid}"
            )])
        buttons.append([InlineKeyboardButton(
            text="⬅️ В меню", callback_data="brand_menu"
        )])
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    await callback.answer()


@router.callback_query(F.data == "brand_reports_confirmed")
async def brand_reports_confirmed(callback: CallbackQuery):
    brand = await get_brand_user(callback.from_user.id)
    if not brand or brand[4] != 'active':
        await callback.answer("Сначала завершите регистрацию.", show_alert=True)
        return

    reports = await get_brand_reports(brand[2], status='confirmed')

    if not reports:
        await callback.message.edit_text(
            "✅ Подтверждённых заявок пока нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="⬅️ В меню", callback_data="brand_menu"
                )]
            ])
        )
    else:
        text = f"✅ Подтверждённые заявки ({len(reports)}):\n\n"
        status_emoji = {'confirmed': '✅', 'pending': '⏳', 'rejected': '❌'}
        for r in reports[:10]:
            rid, uid, brand_n, cat, loc, price, signs, photos, status, reward, created = r
            city = loc.split(",")[0].strip() if loc else "—"
            text += (
                f"✅ #{rid} · {cat} · {city} · "
                f"Награда: {reward:,} ₽\n".replace(",", " ")
            )
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="⬅️ В меню", callback_data="brand_menu"
                )]
            ])
        )
    await callback.answer()


@router.callback_query(F.data.startswith("brand_view_"))
async def brand_view_report(callback: CallbackQuery):
    report_id = int(callback.data.replace("brand_view_", ""))
    report = await get_report_by_id(report_id)

    if not report:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    rid, uid, brand_n, cat, loc, price, signs, photos, status, reward = report

    text = (
        f"📋 Заявка #{rid}\n\n"
        f"🏷 Бренд: {brand_n}\n"
        f"📦 Категория: {cat}\n"
        f"📍 Место: {loc}\n"
        f"💵 Цена: {price} ₽\n"
        f"📸 Фото: {photos} шт.\n\n"
        f"🔍 Признаки подделки:\n{signs}\n\n"
        f"📊 Статус: {status}"
    )

    if status == 'pending':
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=f"brand_approve_{rid}"
            ),
             InlineKeyboardButton(
                 text="❌ Отклонить",
                 callback_data=f"brand_reject_{rid}"
             )],
            [InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="brand_reports_pending"
            )]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="brand_reports_pending"
            )]
        ])

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# ============ МОДЕРАЦИЯ БРЕНДОМ ============

@router.callback_query(F.data.startswith("brand_approve_"))
async def brand_approve(callback: CallbackQuery, state: FSMContext):
    report_id = int(callback.data.replace("brand_approve_", ""))
    await state.set_state(BrandRewardForm.waiting_reward)
    await state.update_data(report_id=report_id)
    await callback.message.answer(
        f"✅ Подтверждение заявки #{report_id}\n\n"
        f"Введите сумму вознаграждения охотнику в рублях:"
    )
    await callback.answer()


@router.message(BrandRewardForm.waiting_reward, F.text)
async def brand_set_reward(message: Message, state: FSMContext):
    reward = ''.join(filter(str.isdigit, message.text))
    if not reward:
        await message.answer("❌ Введите сумму цифрами, например: 4000")
        return
    reward = int(reward)
    data = await state.get_data()
    report_id = data.get('report_id')
    report = await get_report_by_id(report_id)
    if not report:
        await message.answer("Заявка не найдена.")
        await state.clear()
        return

    await update_report_status(report_id, 'confirmed', reward=reward)
    # Google Sheets — обновляем статус заявки
    sheets_update(report_id, f"confirmed by brand ({reward} ₽)")

    await message.answer(
        f"✅ Заявка #{report_id} подтверждена!\n"
        f"Награда охотнику: {reward:,} ₽".replace(",", " "),
        reply_markup=brand_main_menu()
    )

    # Уведомляем охотника через основной бот
    hunter_id = report[1]
    try:
        await main_bot.send_message(
            hunter_id,
            f"🎉 Отличные новости!\n\n"
            f"Твоя заявка #{report_id} ({report[2]}) "
            f"подтверждена брендом!\n\n"
            f"💰 Вознаграждение: {reward:,} ₽\n\n".replace(",", " ") +
            f"Сумма зачислена на твой баланс. Спасибо!"
        )
    except Exception as e:
        logging.error(f"Ошибка уведомления охотника: {e}")
        await message.answer(
            "⚠️ Не удалось уведомить охотника — "
            "возможно, он заблокировал основной бот."
        )

    # Уведомляем платформу (вас)
    if ADMIN_ID:
        try:
            await main_bot.send_message(
                ADMIN_ID,
                f"🏢 Бренд подтвердил заявку #{report_id}\n"
                f"Бренд: {report[2]}\n"
                f"Награда: {reward:,} ₽".replace(",", " ")
            )
        except Exception:
            pass
    await state.clear()


@router.callback_query(F.data.startswith("brand_reject_"))
async def brand_reject(callback: CallbackQuery, state: FSMContext):
    report_id = int(callback.data.replace("brand_reject_", ""))
    await state.set_state(BrandRejectForm.waiting_reason)
    await state.update_data(report_id=report_id)
    await callback.message.answer(
        f"❌ Отклонение заявки #{report_id}\n\n"
        f"Укажите причину (охотник её увидит):"
    )
    await callback.answer()


@router.message(BrandRejectForm.waiting_reason, F.text)
async def brand_set_reject_reason(message: Message, state: FSMContext):
    reason = message.text
    data = await state.get_data()
    report_id = data.get('report_id')
    report = await get_report_by_id(report_id)
    if not report:
        await message.answer("Заявка не найдена.")
        await state.clear()
        return

    await update_report_status(
        report_id, 'rejected', reject_reason=reason
    )
    # Google Sheets — обновляем статус заявки
    sheets_update(report_id, "rejected by brand")

    await message.answer(
        f"❌ Заявка #{report_id} отклонена.",
        reply_markup=brand_main_menu()
    )

    # Уведомляем охотника
    hunter_id = report[1]
    try:
        await main_bot.send_message(
            hunter_id,
            f"📋 Заявка #{report_id} ({report[2]}) отклонена.\n\n"
            f"Причина: {reason}\n\n"
            f"Не расстраивайся — подавай новые заявки!"
        )
    except Exception as e:
        logging.error(f"Ошибка уведомления охотника: {e}")

    # Уведомляем платформу
    if ADMIN_ID:
        try:
            await main_bot.send_message(
                ADMIN_ID,
                f"🏢 Бренд отклонил заявку #{report_id}\n"
                f"Причина: {reason}"
            )
        except Exception:
            pass
    await state.clear()


# ============ СТАТИСТИКА И ПРОФИЛЬ ============

@router.callback_query(F.data == "brand_stats")
async def brand_stats(callback: CallbackQuery):
    brand = await get_brand_user(callback.from_user.id)
    if not brand or brand[4] != 'active':
        await callback.answer("Сначала завершите регистрацию.", show_alert=True)
        return

    stats = await get_brand_stats(brand[2])
    confirm_rate = 0
    if stats['total'] > 0:
        confirm_rate = round(stats['confirmed'] / stats['total'] * 100)

    await callback.message.edit_text(
        f"📊 Статистика бренда {brand[2]}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📋 Заявок всего: {stats['total']}\n"
        f"⏳ На проверке: {stats['pending']}\n"
        f"✅ Подтверждено: {stats['confirmed']}\n"
        f"❌ Отклонено: {stats['rejected']}\n"
        f"🎯 Процент подтверждения: {confirm_rate}%\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Выплачено охотникам: "
        f"{stats['paid']:,} ₽".replace(",", " "),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="⬅️ В меню", callback_data="brand_menu"
            )]
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "brand_profile")
async def brand_profile(callback: CallbackQuery):
    brand = await get_brand_user(callback.from_user.id)
    if not brand:
        await callback.answer("Профиль не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        f"ℹ️ Мой профиль\n\n"
        f"🏢 Компания: {brand[1] or '—'}\n"
        f"🏷 Бренд: {brand[2] or '—'}\n"
        f"📞 Контакт: {brand[3] or '—'}\n"
        f"✅ Статус: Активен",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="⬅️ В меню", callback_data="brand_menu"
            )]
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "brand_menu")
async def brand_menu(callback: CallbackQuery):
    brand = await get_brand_user(callback.from_user.id)
    name = brand[1] if brand else "Бренд"
    await callback.message.edit_text(
        f"🏠 Главное меню\n\nВыберите действие:",
        reply_markup=brand_main_menu()
    )
    await callback.answer()


@router.message(Command("help"))
async def brand_help(message: Message):
    await message.answer(
        "ℹ️ Помощь для брендов\n\n"
        "/start — главное меню\n\n"
        "📋 Новые заявки — заявки ожидающие вашей проверки\n"
        "✅ Подтверждённые — история принятых заявок\n"
        "📊 Статистика — сводка по вашему бренду\n\n"
        "По вопросам сотрудничества: @authentiscan_support"
    )


async def run():
    await init_brand_db()
    logging.info("Бот для брендов запущен!")
    await dp.start_polling(brand_bot)


if __name__ == "__main__":
    asyncio.run(run())
