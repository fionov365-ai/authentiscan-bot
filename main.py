import asyncio
import logging
import json
import os
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

_sheets_client = None


def get_sheets_client():
    global _sheets_client
    if _sheets_client:
        return _sheets_client
    if not GOOGLE_CREDENTIALS:
        logging.warning("GOOGLE_CREDENTIALS не заданы")
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(
            creds_dict, scopes=scopes
        )
        _sheets_client = gspread.authorize(creds)
        return _sheets_client
    except Exception as e:
        logging.error(f"Ошибка Google Sheets: {e}")
        return None


def append_to_sheet(report_id, hunter_name, brand, category,
                    location, price, photo_count, status):
    if not GOOGLE_SHEET_ID:
        return
    try:
        client = get_sheets_client()
        if not client:
            return
        sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
        from datetime import datetime
        row = [
            report_id, hunter_name, brand, category,
            location, price, photo_count, status,
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ]
        sheet.append_row(row, value_input_option='RAW')
        logging.info(f"Заявка #{report_id} в Google Sheets")
    except Exception as e:
        logging.error(f"Ошибка записи в Sheets: {e}")


def update_status_in_sheet(report_id, new_status):
    if not GOOGLE_SHEET_ID:
        return
    try:
        client = get_sheets_client()
        if not client:
            return
        sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
        cell = sheet.find(str(report_id))
        if cell:
            sheet.update_cell(cell.row, 8, new_status)
            logging.info(
                f"Статус #{report_id} обновлён в Sheets: {new_status}"
            )
    except Exception as e:
        logging.error(f"Ошибка обновления Sheets: {e}")


# Делаем функции доступными глобально
import builtins
builtins.append_to_sheet = append_to_sheet
builtins.update_status_in_sheet = update_status_in_sheet

from bot import dp as hunter_dp, bot as hunter_bot, init_db
from brand_bot import (
    dp as brand_dp, brand_bot, init_brand_db
)


async def main():
    await init_db()
    await init_brand_db()
    logging.info("=== Запускаем оба бота ===")
    await asyncio.gather(
        hunter_dp.start_polling(hunter_bot),
        brand_dp.start_polling(brand_bot)
    )


if __name__ == "__main__":
    asyncio.run(main())
