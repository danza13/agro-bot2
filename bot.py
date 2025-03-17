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
AUTO_CALC_ENABLED = True  # за замовчуванням вмикаємо

def pause_polling():
    global POLLING_PAUSED
    POLLING_PAUSED = True

def resume_polling():
    global POLLING_PAUSED
    POLLING_PAUSED = False

async def poll_topicality_notifications():
    """
    Фонове завдання:
    - Кожну хвилину перевіряє всі заявки.
    - Для кожного користувача, якщо немає заявки з flag "topicality_in_progress" (тобто користувач вже не в процесі уточнення),
      шукається наступна заявка (за порядком, наприклад, за часом подачі), що старша за 24 години і для якої ще не
      було надіслано сповіщення.
    - Якщо така заявка знайдена, вона помічається як "topicality_in_progress": True і сповіщення надсилається.
    """
    while True:
        apps = load_applications()
        now = datetime.now()
        # Для кожного користувача
        for uid, app_list in apps.items():
            # Якщо у користувача вже є заявка з "topicality_in_progress" = True – пропускаємо
            if any(app.get("topicality_in_progress") for app in app_list):
                continue

            # Знаходимо заявку, що ще не була надіслана (topicality_notification_sent не True) та старша за 24 години
            pending_app = None
            pending_index = None
            for idx, app in enumerate(app_list):
                if app.get("proposal_status", "active") not in ("active", "waiting"):
                    continue  # опрацьовуємо тільки активні/waiting заявки
                try:
                    submission_time = datetime.fromisoformat(app["timestamp"])
                except Exception:
                    continue
                if now - submission_time >= timedelta(hours=24) and not app.get("topicality_notification_sent", False):
                    # Вибираємо найстаршу заявку (або за порядком)
                    pending_app = app
                    pending_index = idx
                    break  # беремо першу таку заявку

            if pending_app is not None:
                # Позначаємо, що для цієї заявки сповіщення зараз в процесі (і flag notification_sent = True)
                apps[uid][pending_index]["topicality_notification_sent"] = True
                apps[uid][pending_index]["topicality_in_progress"] = True
                # Формуємо повідомлення (номер заявки – беремо індекс+1, culture, quantity)
                msg_text = (
                    f"Ваша заявка {pending_index+1}. {pending_app.get('culture', 'Невідомо')} | "
                    f"{pending_app.get('quantity', 'Невідомо')} т актуальна, чи потребує змін або видалення?"
                )
                try:
                    await bot.send_message(
                        pending_app.get("chat_id"),
                        msg_text,
                        reply_markup=get_topicality_keyboard()
                    )
                except Exception as e:
                    logging.exception(f"Помилка надсилання topicality сповіщення для uid={uid}: {e}")
        save_applications(apps)
        await asyncio.sleep(60)  # перевіряти кожну хвилину
        
        
########################################################
# Фонова перевірка manager_price + bot_price
########################################################
# Приклад:
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

                            # Якщо попередньої ціни немає або вона відрізняється від нової, оновлюємо
                            if previous_price is None or previous_price != new_price:
                                app["original_manager_price"] = (
                                    str(previous_price) if previous_price is not None else ""
                                )
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
            if AUTO_CALC_ENABLED:
                updated_apps = load_applications()
                changed = False
                for uid, app_list in updated_apps.items():
                    for idx, app in enumerate(app_list):
                        status = app.get("proposal_status", "active")
                        if status in ("deleted", "confirmed", "Agreed"):
                            continue
                        manager_price_in_sheet = app.get("original_manager_price", "").strip()
                        if manager_price_in_sheet:
                            continue
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
                                f"З'явилася пропозиція для Вашої заявки {idx+1}. "
                                f"{culture} | {quantity} т: {bot_price_value}\n\n"
                                "Для перегляду даної пропозиції натисніть /menu -> Переглянути мої заявки -> "
                                "Оберіть заявку -> Переглянути пропозиції та оберіть потрібну дію"
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

async def schedule_next_topicality(user_id: int):
    # Чекаємо 10 секунд після завершення вибору
    await asyncio.sleep(10)
    # Викликаємо перевірку для цього користувача
    apps = load_applications()
    uid = str(user_id)
    # Якщо вже немає заявки з "topicality_in_progress", спробуємо відправити наступну
    if uid in apps:
        user_apps = apps[uid]
        # Якщо немає жодної заявки, нічого не робимо
        if not user_apps:
            return
        # Якщо жодна заявка не має прапорця "topicality_in_progress" і існує заявка, що потребує сповіщення,
        # надсилаємо сповіщення для першої, що відповідає умовам.
        if not any(app.get("topicality_in_progress") for app in user_apps):
            for idx, app in enumerate(user_apps):
                if app.get("proposal_status", "active") in ("active", "waiting") and not app.get("topicality_notification_sent", False):
                    # Перевіряємо, чи заявка старша 24 години
                    try:
                        submission_time = datetime.fromisoformat(app["timestamp"])
                    except Exception:
                        continue
                    if datetime.now() - submission_time >= timedelta(hours=24):
                        app["topicality_notification_sent"] = True
                        app["topicality_in_progress"] = True
                        msg_text = (
                            f"Ваша заявка {idx+1}. {app.get('culture', 'Невідомо')} | "
                            f"{app.get('quantity', 'Невідомо')} т актуальна, чи потребує змін або видалення?"
                        )
                        try:
                            await bot.send_message(app.get("chat_id"), msg_text, reply_markup=get_topicality_keyboard())
                        except Exception as e:
                            logging.exception(e)
                        break  # надсилаємо лише одне сповіщення
            save_applications(apps)


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
