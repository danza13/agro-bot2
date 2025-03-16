# bot.py
import asyncio
import logging
from aiohttp import web
from aiogram import executor

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from keyboards import get_topicality_keyboard

from loader import bot, dp
from config import CHECK_INTERVAL, API_PORT
from db import load_applications, save_applications
from gsheet_utils import (
    get_worksheet1, color_cell_red, color_cell_green, color_cell_yellow,
    parse_price_sheet, calculate_and_set_bot_price, get_worksheet2, rowcol_to_a1, color_entire_row_red
)

# Імпортуємо хендлери (вони тепер імпортують bot/dispatcher з loader.py)
import admin_handlers
import user_handlers

POLLING_PAUSED = False

def pause_polling():
    global POLLING_PAUSED
    POLLING_PAUSED = True

def resume_polling():
    global POLLING_PAUSED
    POLLING_PAUSED = False

async def poll_topicality_notifications():
    """
    Фонове завдання:
    - Щосекунди/хвилини перевіряє всі заявки у файлі.
    - Якщо заявка (зі статусом active/ waiting) була створена більше 24 годин тому
      і для неї ще не відправлено сповіщення (flag 'topicality_notification_sent' не встановлено),
      то надсилається повідомлення користувачу з клавіатурою для уточнення актуальності.
    - Після відправлення в запис заявки додається flag, щоб не надсилати повторно.
    """
    while True:
        apps = load_applications()
        now = datetime.now()
        for uid, app_list in apps.items():
            for i, app in enumerate(app_list):
                status = app.get("proposal_status", "active")
                # Опрацьовуємо лише активні/ waiting заявки
                if status not in ("active", "waiting"):
                    continue
                # Якщо уже відправлено сповіщення – пропускаємо
                if app.get("topicality_notification_sent"):
                    continue
                try:
                    submission_time = datetime.fromisoformat(app["timestamp"])
                except Exception:
                    continue
                if now - submission_time >= timedelta(hours=24):
                    # Формуємо повідомлення. Використовуємо індексацію (i+1) для номера заявки.
                    msg_text = (
                        f"Ваша заявка {i+1}. {app.get('culture', 'Невідомо')} | {app.get('quantity', 'Невідомо')} т "
                        "актуальна, чи потребує змін або видалення?"
                    )
                    try:
                        await bot.send_message(
                            app.get("chat_id"),
                            msg_text,
                            reply_markup=get_topicality_keyboard()
                        )
                        # Позначаємо, що сповіщення для цієї заявки вже відправлено
                        app["topicality_notification_sent"] = True
                    except Exception as e:
                        logging.exception(f"Помилка надсилання topicality сповіщення для uid={uid}: {e}")
        save_applications(apps)
        await asyncio.sleep(60)  # Перевіряти кожну хвилину
        
        
########################################################
# Фонова перевірка manager_price + bot_price
########################################################
async def poll_manager_proposals():
    """
    Фонове завдання:
      1) Перевіряє зміни у manager_price та розсилку нових пропозицій.
      2) Розраховує автоматичну (ботову) ціну для заявок.
      Дані прайс-листа (SHEET2_NAME_2) оновлюються кожні 60 секунд.
    """
    from aiogram.utils.exceptions import BotBlocked
    while True:
        if POLLING_PAUSED:
            await asyncio.sleep(3)
            continue
        try:
            # Оновлюємо конфігурацію прайс-листа з SHEET2_NAME_2 кожного циклу
            price_config = parse_price_sheet()

            # 1) Обробка змін manager_price
            ws = get_worksheet1()
            rows = ws.get_all_values()
            apps = load_applications()
            for i, row in enumerate(rows[1:], start=2):
                if len(row) < 15:
                    continue
                current_manager_price_str = row[13].strip()
                if not current_manager_price_str:
                    continue

                # Перетворюємо менеджерську ціну на число
                try:
                    new_price = float(current_manager_price_str)
                except ValueError:
                    continue

                for uid, app_list in apps.items():
                    for idx, app in enumerate(app_list):
                        if app.get("sheet_row") == i:
                            status = app.get("proposal_status", "active")
                            if status in ("deleted", "confirmed"):
                                continue

                            previous_proposal = app.get("proposal")
                            try:
                                previous_price = float(previous_proposal) if previous_proposal else None
                            except ValueError:
                                previous_price = None

                            # Якщо попередньої ціни немає або вона відрізняється від нової, оновлюємо дані
                            if previous_price is None or previous_price != new_price:
                                app["original_manager_price"] = str(previous_price) if previous_price is not None else ""
                                app["proposal"] = current_manager_price_str
                                app["proposal_status"] = "Agreed"
                                if status == "waiting":
                                    culture = app.get("culture", "Невідомо")
                                    quantity = app.get("quantity", "Невідомо")
                                    msg = (
                                        f"Ціна по заявці {idx+1}. {culture} | {quantity} т змінилась з "
                                        f"{previous_proposal} на {current_manager_price_str}\n\n"
                                        "Для перегляду даної пропозиції натисніть /menu -> Переглянути мої заявки -> Оберіть заявку -> Переглянути пропозиції та оберіть потрібну дію"
                                    )
                                else:
                                    msg = (
                                        f"Для Вашої заявки оновлено пропозицію: {current_manager_price_str}\n\n"
                                        "Для перегляду даної пропозиції натисніть /menu -> Переглянути мої заявки -> Оберіть заявку -> Переглянути пропозиції та оберіть потрібну дію"
                                    )
                                try:
                                    await bot.send_message(app.get("chat_id"), msg)
                                except BotBlocked:
                                    pass
            save_applications(apps)

            # 2) Розрахунок автоматичної (ботової) ціни
            updated_apps = load_applications()
            changed = False
            for uid, app_list in updated_apps.items():
                for idx, app in enumerate(app_list):
                    status = app.get("proposal_status", "active")
                    if status in ("deleted", "confirmed", "Agreed"):
                        continue
                    # Якщо в таблиці вже є manager_price – пропускаємо
                    manager_price_in_sheet = app.get("original_manager_price", "").strip()
                    if manager_price_in_sheet:
                        continue
                    # Якщо вже є ціна від бота – пропускаємо
                    if "bot_price" in app:
                        continue
                    row_idx = app.get("sheet_row")
                    if not row_idx:
                        continue
                    bot_price_value = calculate_and_set_bot_price(app, row_idx, price_config)
                    if bot_price_value is not None:
                        app["bot_price"] = float(bot_price_value)
                        app["proposal"] = str(bot_price_value)
                        app["proposal_status"] = "Agreed"
                        culture = app.get("culture", "Невідомо")
                        quantity = app.get("quantity", "Невідомо")
                        msg = (
                            f"З'явилася пропозиція для Вашої заявки {idx+1}. {culture} | {quantity} т: {bot_price_value}\n\n"
                            "Для перегляду даної пропозиції натисніть /menu -> Переглянути мої заявки -> Оберіть заявку -> Переглянути пропозиції та оберіть потрібну дію"
                        )
                        try:
                            await bot.send_message(app.get("chat_id"), msg)
                        except BotBlocked:
                            pass
                        changed = True
            if changed:
                save_applications(updated_apps)
        except Exception as e:
            logging.exception(f"Помилка у фоні: {e}")
        await asyncio.sleep(CHECK_INTERVAL)



########################################################
# HTTP-сервер (опційно)
########################################################
async def handle_webapp_data(request: web.Request):
    try:
        data = await request.json()
        user_id = data.get("user_id")
        if not user_id:
            return web.json_response({"status": "error", "error": "user_id missing"})
        if not data or not any(data.values()):
            return web.json_response({"status": "error", "error": "empty data"})
        logging.info(f"API отримав дані для user_id={user_id}: {data}")
        return web.json_response({"status": "preview"})
    except Exception as e:
        logging.exception(f"API: Помилка: {e}")
        return web.json_response({"status": "error", "error": str(e)})

async def start_webserver():
    app_web = web.Application()
    app_web.add_routes([web.post('/api/webapp_data', handle_webapp_data)])
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', API_PORT)
    await site.start()
    logging.info(f"HTTP-сервер запущено на порті {API_PORT}.")

########################################################
# on_startup
########################################################
async def on_startup(dp):
    logging.info("Бот запущено. Старт фонових задач...")
    asyncio.create_task(poll_manager_proposals())
    asyncio.create_task(poll_topicality_notifications())
    asyncio.create_task(start_webserver())

########################################################
# Головний старт
########################################################
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
