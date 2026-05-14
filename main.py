import asyncio
import logging

from bot import dp as hunter_dp, bot as hunter_bot, init_db
from brand_bot import (
    dp as brand_dp,
    brand_bot,
    init_brand_db
)

logging.basicConfig(level=logging.INFO)


async def main():
    # Инициализируем обе базы данных
    await init_db()
    await init_brand_db()
    logging.info("=== Запускаем оба бота ===")

    # Запускаем оба бота параллельно
    await asyncio.gather(
        hunter_dp.start_polling(hunter_bot),
        brand_dp.start_polling(brand_bot)
    )


if __name__ == "__main__":
    asyncio.run(main())
