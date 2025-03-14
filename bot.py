# bot.py
import asyncio
from aiogram import Bot, Dispatcher, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

import logging
from aiohttp import web

from config import TELEGRAM_TOKEN, CHECK_INTERVAL, API_PORT
from db import load_applications, save_applications
from gsheet_utils import (
    get_worksheet1, color_cell_red, color_cell_green, color_cell_yellow,
    parse_price_sheet, calculate_and_set_bot_price
)
from admin_handlers import *  # щоб зареєструвати хендлери
from user_handlers import *    # щоб зареєструвати хендлери


bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

POLLING_PAUSED = False

def pause_polling():
    global POLLING_PAUSED
    POLLING_PAUSED = True

def resume_polling():
    global POLLING_PAUSED
    POLLING_PAUSED = False


########################################################
# Фонова перевірка manager_price + bot_price
########################################################

async def poll_manager_proposals():
    """
    Кожні CHECK_INTERVAL секунд:
      1) Оновлюємо заявки, якщо manager_price змінився (старий код).
      2) Скануємо заявки без manager_price та без bot_price.
         Якщо можливо, розраховуємо bot_price та записуємо у таблицю2 (col M=13) і в JSON.
         Якщо розрахувати не вдалося (немає тарифу або даних) — пропускаємо.
    """
    from aiogram.utils.exceptions import BotBlocked

    # Завантажуємо конфіг для цін (лист "Ціни")
    price_config = parse_price_sheet()

    while True:
        if POLLING_PAUSED:
            await asyncio.sleep(3)
            continue

        try:
            # 1) Старий алгоритм обробки manager_price
            ws = get_worksheet1()
            rows = ws.get_all_values()
            apps = load_applications()

            for i, row in enumerate(rows[1:], start=2):
                if len(row) < 15:
                    continue
                current_manager_price_str = row[13].strip()
                if not current_manager_price_str:
                    continue

                try:
                    cur_price = float(current_manager_price_str)
                except ValueError:
                    continue

                for uid, app_list in apps.items():
                    for idx, app in enumerate(app_list):
                        if app.get("sheet_row") == i:
                            status = app.get("proposal_status", "active")
                            if status in ("deleted", "confirmed"):
                                continue

                            original_manager_price_str = app.get("original_manager_price", "").strip()
                            try:
                                orig_price = float(original_manager_price_str) if original_manager_price_str else None
                            except:
                                orig_price = None

                            if orig_price is None:
                                # нова пропозиція
                                culture = app.get("culture", "Невідомо")
                                quantity = app.get("quantity", "Невідомо")
                                app["original_manager_price"] = current_manager_price_str
                                app["proposal"] = current_manager_price_str
                                app["proposal_status"] = "Agreed"
                                try:
                                    await bot.send_message(
                                        app.get("chat_id"),
                                        f"Нова пропозиція по Вашій заявці {idx+1}. {culture} | {quantity} т. Ціна: {current_manager_price_str}"
                                    )
                                except BotBlocked:
                                    pass
                            else:
                                previous_proposal = app.get("proposal")
                                if previous_proposal != current_manager_price_str:
                                    app["original_manager_price"] = previous_proposal
                                    app["proposal"] = current_manager_price_str
                                    app["proposal_status"] = "Agreed"

                                    if status == "waiting":
                                        culture = app.get("culture", "Невідомо")
                                        quantity = app.get("quantity", "Невідомо")
                                        msg = f"Ціна по заявці {idx+1}. {culture} | {quantity} т змінилась з {previous_proposal} на {current_manager_price_str}"
                                    else:
                                        msg = f"Для Вашої заявки оновлено пропозицію: {current_manager_price_str}"

                                    try:
                                        await bot.send_message(
                                            app.get("chat_id"),
                                            msg
                                        )
                                    except BotBlocked:
                                        pass
            save_applications(apps)

            # 2) Тепер перевіряємо заявки, чи не треба їм bot_price
            # Шукаємо заявки, у яких manager_price ще не з'явився (тобто col 13 порожня),
            # і при цьому у JSON немає "bot_price".
            # Скануємо active / waiting / rejected.
            updated_apps = load_applications()  # перезавантажили
            changed = False

            for uid, app_list in updated_apps.items():
                for idx, app in enumerate(app_list):
                    status = app.get("proposal_status", "active")
                    if status in ("deleted", "confirmed", "Agreed"):
                        continue
                    # Перевіримо, чи немає manager_price
                    # (це у sheets col 13: row[13] == "")
                    # але оскільки ми вже це вище ловимо, то просто дивимося, чи proposal ~?
                    manager_price_in_sheet = app.get("original_manager_price", "").strip()
                    if manager_price_in_sheet:
                        # Якщо вже менеджер вніс — пропускаємо
                        continue

                    # Перевіряємо, чи вже bot_price був
                    if "bot_price" in app:
                        # Вже є ціна бота
                        continue

                    # Спробуємо розрахувати
                    row_idx = app.get("sheet_row")
                    if not row_idx:
                        continue

                    new_price = calculate_and_set_bot_price(app, row_idx, price_config)
                    if new_price is not None:
                        # Записуємо "proposal", "bot_price", "proposal_status=Agreed"
                        app["bot_price"] = float(new_price)
                        app["proposal"] = str(new_price)
                        app["proposal_status"] = "Agreed"

                        # Надсилаємо користувачу повідомлення
                        culture = app.get("culture", "Невідомо")
                        quantity = app.get("quantity", "Невідомо")
                        msg = f"Автоматична пропозиція для Вашої заявки {idx+1}. {culture} | {quantity} т: {new_price}"
                        try:
                            await bot.send_message(
                                app.get("chat_id"),
                                msg
                            )
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
    asyncio.create_task(start_webserver())


########################################################
# Головний старт
########################################################

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
