#user_handlers.py
import json
import re
import logging
import asyncio
from datetime import datetime
from urllib.parse import quote

from zoneinfo import ZoneInfo

from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text, Regexp
from bot import schedule_next_topicality

from loader import dp, bot
from config import ADMINS, friendly_names
from states import (
    RegistrationStates, ApplicationStates
)
from keyboards import remove_keyboard, get_main_menu_keyboard, get_topicality_keyboard
from db import (
    load_users, save_users,
    load_applications, save_applications,
    add_application, delete_application_soft, update_application_status
)
from gsheet_utils import (
    update_google_sheet, color_cell_red, color_cell_green,
    color_cell_yellow, delete_price_cell_in_table2,
    get_worksheet1, get_worksheet2,
    color_entire_row_green, color_entire_row_red, format_cell_range, 
    update_worksheet1_cells_for_edit, re_run_autocalc_for_app, rowcol_to_a1, update_worksheet2_cells_for_edit_color,
    yellow_format
)

def color_cell_yellow_sheet1(row: int, col: int):
    ws = get_worksheet1()
    cell_range = f"{rowcol_to_a1(row, col)}:{rowcol_to_a1(row, col)}"
    format_cell_range(ws, cell_range, yellow_format)

def color_cell_yellow_sheet2(row: int, col: int):
    ws2 = get_worksheet2()
    cell_range = f"{rowcol_to_a1(row, col)}:{rowcol_to_a1(row, col)}"
    format_cell_range(ws2, cell_range, yellow_format)

# Допоміжна функція для формування деталей заявки при уточненні актуальності
def build_topicality_details(app: dict) -> str:
    # Отримуємо дату створення заявки
    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except Exception:
        formatted_date = timestamp

    # Отримуємо інші поля заявки з дефолтним значенням "Невідомо", якщо їх немає
    group_value = app.get("group", "Невідомо")
    culture = app.get("culture", "Невідомо")
    quantity = app.get("quantity", "Невідомо")
    payment_form = app.get("payment_form", "Невідомо")
    currency = app.get("currency", "Невідомо")
    price = app.get("price", "Невідомо")

    details = (
        f"Дата: {formatted_date}\n"
        f"Група: {group_value}\n"
        f"Культура: {culture}\n"
        f"Кількість: {quantity}\n"
        f"Форма оплати: {payment_form}\n"
        f"Валюта: {currency}\n"
        f"Ціна: {price}"
    )
    return details


############################################
# РЕЄСТРАЦІЯ КОРИСТУВАЧА (/start)
############################################

@dp.message_handler(commands=["start"], state="*")
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    # Якщо є активна заявка з процесом уточнення, відправляємо деталі та потрібну клавіатуру
    if uid in apps:
        for app in apps[uid]:
            if app.get("topicality_in_progress"):
                details = build_topicality_details(app)
                kb = get_topicality_keyboard()  # Клавіатура з кнопками «Актуальна», «Потребує змін», «Видалити»
                await message.answer(
                    "У вас відкрито процес уточнення актуальності заявки.\n\n" +
                    details +
                    "\n\nБудь ласка, завершіть його, обравши одну з опцій:",
                    reply_markup=kb
                )
                await state.set_state(ApplicationStates.viewing_topicality)
                return

    await state.finish()
    users = load_users()
    if uid in users.get("blocked_users", []):
        await message.answer("На жаль, у Вас немає доступу.", reply_markup=remove_keyboard())
        return

    if uid in users.get("approved_users", {}):
        await message.answer("Вітаємо! Оберіть дію:", reply_markup=get_main_menu_keyboard())
        return

    if uid in users.get("pending_users", {}):
        await message.answer("Ваша заявка на модерацію вже відправлена. Очікуйте.", reply_markup=remove_keyboard())
        return

    await message.answer("Введіть, будь ласка, своє ПІБ (повністю).", reply_markup=remove_keyboard())
    await RegistrationStates.waiting_for_fullname.set()



@dp.message_handler(state=RegistrationStates.waiting_for_fullname)
async def process_fullname(message: types.Message, state: FSMContext):
    fullname = message.text.strip()
    if not fullname:
        await message.answer("ПІБ не може бути порожнім. Введіть коректне значення.")
        return

    await state.update_data(fullname=fullname)
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    keyboard.add(types.KeyboardButton("Поділитись контактом", request_contact=True))
    await message.answer("Введіть номер телефону (+380XXXXXXXXX) або поділіться контактом:", reply_markup=keyboard)
    await RegistrationStates.waiting_for_phone.set()


@dp.message_handler(content_types=types.ContentType.CONTACT, state=RegistrationStates.waiting_for_phone)
async def process_phone_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact and message.contact.phone_number else ""
    phone = re.sub(r"[^\d+]", "", phone)
    await state.update_data(phone=phone)
    await show_registration_preview(message, state)


@dp.message_handler(state=RegistrationStates.waiting_for_phone)
async def process_phone_text(message: types.Message, state: FSMContext):
    phone = re.sub(r"[^\d+]", "", message.text.strip())
    if not re.fullmatch(r"\+380\d{9}", phone):
        await message.answer("Невірний формат. Введіть номер у форматі +380XXXXXXXXX")
        return
    await state.update_data(phone=phone)
    await show_registration_preview(message, state)


async def show_registration_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fullname = data.get("fullname", "—")
    phone = data.get("phone", "—")
    preview_text = (
        "<b>Перевірте свої дані:</b>\n\n"
        f"ПІБ: {fullname}\n"
        f"Телефон: {phone}\n\n"
        "Якщо все вірно, натисніть <b>Підтвердити</b>."
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Підтвердити", "Редагувати", "Скасувати")
    await message.answer(preview_text, parse_mode="HTML", reply_markup=kb)
    await RegistrationStates.preview.set()


@dp.message_handler(Text(equals="Підтвердити"), state=RegistrationStates.preview)
async def confirm_registration_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fullname = data.get("fullname")
    phone = data.get("phone")
    user_id = message.from_user.id
    uid = str(user_id)

    users = load_users()
    users.setdefault("pending_users", {})[uid] = {
        "fullname": fullname,
        "phone": phone,
        "timestamp": datetime.now().isoformat()
    }
    save_users(users)

    await state.finish()
    await message.answer("Ваша заявка на модерацію відправлена.", reply_markup=remove_keyboard())

    for admin in ADMINS:
        try:
            await bot.send_message(
                admin,
                f"Новий користувач на модерацію:\nПІБ: {fullname}\nНомер: {phone}\nUser ID: {user_id}",
                reply_markup=remove_keyboard()
            )
        except Exception as e:
            logging.exception(f"Не вдалося сповістити адміністратора {admin}: {e}")


@dp.message_handler(Text(equals="Редагувати"), state=RegistrationStates.preview)
async def edit_registration_preview(message: types.Message, state: FSMContext):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Змінити ПІБ", "Змінити номер телефону")
    kb.add("Назад")
    await message.answer("Оберіть, що змінити:", reply_markup=kb)
    await RegistrationStates.editing.set()


@dp.message_handler(Text(equals="Скасувати"), state=RegistrationStates.preview)
async def cancel_registration_preview(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Реєстрацію скасовано. Якщо передумаєте – введіть /start заново.", reply_markup=remove_keyboard())


@dp.message_handler(Text(equals="Змінити ПІБ"), state=RegistrationStates.editing)
async def editing_fullname_button(message: types.Message, state: FSMContext):
    await message.answer("Введіть нове ПІБ:", reply_markup=remove_keyboard())
    await RegistrationStates.editing_fullname.set()


@dp.message_handler(state=RegistrationStates.editing_fullname)
async def process_editing_fullname(message: types.Message, state: FSMContext):
    new_fullname = message.text.strip()
    if not new_fullname:
        await message.answer("ПІБ не може бути порожнім.")
        return
    await state.update_data(fullname=new_fullname)
    await return_to_editing_menu(message, state)


@dp.message_handler(Text(equals="Змінити номер телефону"), state=RegistrationStates.editing)
async def editing_phone_button(message: types.Message, state: FSMContext):
    await message.answer("Введіть новий номер телефону (+380XXXXXXXXX):", reply_markup=remove_keyboard())
    await RegistrationStates.editing_phone.set()


@dp.message_handler(state=RegistrationStates.editing_phone)
async def process_editing_phone(message: types.Message, state: FSMContext):
    phone = re.sub(r"[^\d+]", "", message.text.strip())
    if not re.fullmatch(r"\+380\d{9}", phone):
        await message.answer("Невірний формат. Введіть номер у форматі +380XXXXXXXXX")
        return
    await state.update_data(phone=phone)
    await return_to_editing_menu(message, state)


@dp.message_handler(Text(equals="Назад"), state=RegistrationStates.editing)
async def back_to_preview_from_editing(message: types.Message, state: FSMContext):
    await show_registration_preview(message, state)


async def return_to_editing_menu(message: types.Message, state: FSMContext):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Змінити ПІБ", "Змінити номер телефону")
    kb.add("Назад")
    await RegistrationStates.editing.set()
    await message.answer("Оновлено! Що бажаєте змінити далі?", reply_markup=kb)


############################################
# /menu та /support
############################################
@dp.message_handler(commands=["menu"], state="*")
async def show_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    # Якщо є активна заявка з процесом уточнення, відправляємо деталі заявки та клавіатуру
    if uid in apps:
        for app in apps[uid]:
            if app.get("topicality_in_progress"):
                details = build_topicality_details(app)
                kb = get_topicality_keyboard()
                await message.answer(
                    "У вас відкрито процес уточнення актуальності заявки.\n\n" +
                    details +
                    "\n\nБудь ласка, завершіть його, обравши одну з опцій:",
                    reply_markup=kb
                )
                await state.set_state(ApplicationStates.viewing_topicality)
                return

    await state.finish()
    users = load_users()
    if uid not in users.get("approved_users", {}):
        await message.answer("Немає доступу. Очікуйте схвалення.", reply_markup=remove_keyboard())
        return
    await message.answer("Головне меню:", reply_markup=get_main_menu_keyboard())

@dp.message_handler(commands=["support"], state="*")
async def support_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    # Якщо є активна заявка з процесом уточнення, відправляємо деталі заявки та клавіатуру
    if uid in apps:
        for app in apps[uid]:
            if app.get("topicality_in_progress"):
                details = build_topicality_details(app)
                kb = get_topicality_keyboard()
                await message.answer(
                    "У вас відкрито процес уточнення актуальності заявки.\n\n" +
                    details +
                    "\n\nБудь ласка, завершіть його, обравши одну з опцій:",
                    reply_markup=kb
                )
                await state.set_state(ApplicationStates.viewing_topicality)
                return

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Звернутись до підтримки", url="https://t.me/Dealeragro_bot"))
    await message.answer("Якщо вам потрібна допомога, натисніть кнопку нижче:", reply_markup=keyboard)
    
@dp.message_handler(Text(equals="Актуальна"), state=ApplicationStates.viewing_topicality)
async def topicality_actual(message: types.Message, state: FSMContext):
    logging.info(f"[TOPICALITY] Користувач {message.from_user.id} натиснув 'Актуальна'")
    from gsheet_utils import get_worksheet2, rowcol_to_a1
    uid = str(message.from_user.id)
    apps = load_applications()
    updated = False
    # Знайдемо заявку, що зараз в процесі уточнення
    for app in apps.get(uid, []):
        if app.get("topicality_in_progress"):
            sheet_row = app.get("sheet_row")
            if sheet_row:
                now_str = datetime.now(ZoneInfo("Europe/Kiev")).strftime("%d.%m.%Y\n%H:%M:%S")
                try:
                    ws2 = get_worksheet2()
                    cell_address = rowcol_to_a1(sheet_row, 15)
                    ws2.update_acell(cell_address, now_str)
                    logging.debug(f"[TOPICALITY] Записано дату/час {now_str} у клітинку {cell_address}")
                except Exception as e:
                    logging.exception(f"[TOPICALITY] Помилка при оновленні клітинки {cell_address}: {e}")
            app["topicality_in_progress"] = False
            updated = True
    if updated:
        save_applications(apps)
        logging.info(f"[TOPICALITY] Статус заявки для користувача {uid} оновлено (знято topicality_in_progress)")
    else:
        logging.info(f"[TOPICALITY] Нічого не оновлено для користувача {uid}")
    await state.finish()
    await message.answer("Заявка підтверджена як актуальна.", reply_markup=get_main_menu_keyboard())
    # Запланувати наступну перевірку через 10 секунд
    asyncio.create_task(schedule_next_topicality(message.from_user.id))

@dp.message_handler(Text(equals="Потребує змін"), state=ApplicationStates.viewing_topicality)
async def topicality_edit(message: types.Message, state: FSMContext):
    logging.info(f"[TOPICALITY] Користувач {message.from_user.id} натиснув 'Потребує змін'")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Форма редагування", "Назад")
    await state.update_data(topicality_action="edit")
    await message.answer("Відредагуйте заявку в формі:", reply_markup=kb)
    await ApplicationStates.topicality_editing.set()


@dp.message_handler(Text(equals="Назад"), state=ApplicationStates.topicality_editing)
async def topicality_edit_back(message: types.Message, state: FSMContext):
    # Повертаємо назад до основної клавіатури уточнення
    await state.set_state(ApplicationStates.viewing_topicality)
    await message.answer(
        "Ваша заявка актуальна, чи потребує змін або видалення?",
        reply_markup=get_topicality_keyboard()
    )

@dp.message_handler(Text(equals="Форма редагування"), state=ApplicationStates.topicality_editing)
async def open_edit_form(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)
    apps = load_applications()
    if uid in apps:
        for i, app in enumerate(apps[uid]):
            if app.get("topicality_notification_sent"):
                # Записуємо індекс заявки для редагування в стані
                await state.update_data(editing_app_index=i)
                # Формуємо дані для попереднього заповнення
                import re, json
                from urllib.parse import quote
                quantity_clean = re.sub(r"[^\d.]", "", str(app.get("quantity", "")))
                webapp2_data = {
                    "quantity": quantity_clean,
                    "price": app.get("price", ""),
                    "currency": app.get("currency", ""),
                    "payment_form": app.get("payment_form", "")
                }
                webapp_url2 = "https://danza13.github.io/agro-webapp/webapp2.html"
                prefill = quote(json.dumps(webapp2_data))
                url_with_data = f"{webapp_url2}?data={prefill}"
                kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                kb.add(
                    types.KeyboardButton("Редагувати заявку в WebApp", web_app=types.WebAppInfo(url=url_with_data))
                )
                kb.add("Назад")
                await state.set_state(ApplicationStates.waiting_for_webapp2_data)
                await message.answer("Натисніть, щоб відкрити форму для редагування:", reply_markup=kb)
                return
    await message.answer("Заявку не знайдено для редагування.", reply_markup=get_topicality_keyboard())

@dp.message_handler(Text(equals="Видалити"), state=ApplicationStates.viewing_topicality)
async def topicality_delete(message: types.Message, state: FSMContext):
    logging.info(f"[TOPICALITY] Користувач {message.from_user.id} натиснув 'Видалити'")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Так", "Ні")
    await state.update_data(topicality_action="delete")
    await message.answer("Ви хочете видалити заявку?", reply_markup=kb)
    await ApplicationStates.topicality_deletion_confirmation.set()

@dp.message_handler(Text(equals="Так"), state=ApplicationStates.topicality_deletion_confirmation)
async def topicality_delete_confirm(message: types.Message, state: FSMContext):
    logging.info(f"[TOPICALITY] Користувач {message.from_user.id} підтвердив видалення заявки")
    uid = str(message.from_user.id)
    apps = load_applications()
    if uid in apps:
        for app in apps[uid]:
            if app.get("topicality_in_progress"):
                app["proposal_status"] = "deleted"
                sheet_row = app.get("sheet_row")
                if sheet_row:
                    try:
                        from gsheet_utils import get_worksheet1, get_worksheet2, color_entire_row_red
                        ws1 = get_worksheet1()
                        ws2 = get_worksheet2()
                        color_entire_row_red(ws1, sheet_row)
                        color_entire_row_red(ws2, sheet_row)
                        logging.debug(f"[TOPICALITY] Рядок {sheet_row} зафарбовано у червоний")
                    except Exception as e:
                        logging.exception(f"[TOPICALITY] Помилка фарбування рядка {sheet_row}: {e}")
                app["topicality_in_progress"] = False
    save_applications(apps)
    await state.finish()
    await message.answer("Ваша заявка видалена.", reply_markup=get_main_menu_keyboard())
    asyncio.create_task(schedule_next_topicality(message.from_user.id))

@dp.message_handler(Text(equals="Ні"), state=ApplicationStates.topicality_deletion_confirmation)
async def topicality_delete_cancel(message: types.Message, state: FSMContext):
    logging.info(f"[TOPICALITY] Користувач {message.from_user.id} скасував видалення заявки")
    await state.set_state(ApplicationStates.viewing_topicality)
    await message.answer("Ваша заявка актуальна, чи потребує змін або видалення?", reply_markup=get_topicality_keyboard())

@dp.message_handler(state=ApplicationStates.viewing_topicality)
async def handle_topicality_response(message: types.Message, state: FSMContext):
    allowed = {"Актуальна", "Потребує змін", "Видалити"}
    if message.text not in allowed:
        # Повторно надсилаємо повідомлення з клавіатурою уточнення, не змінюючи стан
        await message.answer("Будь ласка, завершіть уточнення актуальності, обравши одну з опцій:", reply_markup=get_topicality_keyboard())
        return
    # Якщо відповідь правильна, обробляємо її відповідно до логіки
    if message.text == "Актуальна":
        # Обробка відповіді "Актуальна"
        # … (ваша логіка)
        await state.finish()
        await message.answer("Заявка підтверджена як актуальна.", reply_markup=get_main_menu_keyboard())
    elif message.text == "Потребує змін":
        # Перехід до редагування
        await message.answer("Відредагуйте заявку, використовуючи форму.", reply_markup=...)
        await state.set_state(ApplicationStates.topicality_editing)
    elif message.text == "Видалити":
        # Логіка видалення
        await message.answer("Ви хочете видалити заявку? Натисніть 'Так' для підтвердження.", reply_markup=...)
        await state.set_state(ApplicationStates.topicality_deletion_confirmation)


############################################
# "Подати заявку" та "Переглянути мої заявки"
############################################

@dp.message_handler(Text(equals="Подати заявку"), state="*")
async def start_application(message: types.Message, state: FSMContext):
    await state.finish()
    webapp_url = "https://danza13.github.io/agro-webapp/webapp.html"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Відкрити форму для заповнення", web_app=types.WebAppInfo(url=webapp_url)))
    kb.row("Скасувати")
    await message.answer("Заповніть дані заявки у WebApp:", reply_markup=kb)
    await ApplicationStates.waiting_for_webapp_data.set()


@dp.message_handler(Text(equals="Переглянути мої заявки"), state="*")
async def show_user_applications(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    all_apps = apps.get(uid, [])
    
    # Створюємо mapping: filtered_apps – список заявок, які не мають статусу "deleted",
    # а mapping – список їхніх реальних індексів у all_apps.
    filtered_apps = []
    mapping = []
    for idx, app in enumerate(all_apps):
        if app.get("proposal_status", "") != "deleted":
            mapping.append(idx)
            filtered_apps.append(app)
    
    if not filtered_apps:
        await message.answer("Ви не маєте заявок.", reply_markup=get_main_menu_keyboard())
        return

    # Зберігаємо mapping у стані
    await state.update_data(apps_mapping=mapping)
    
    buttons = []
    for i, app in enumerate(filtered_apps, start=1):
        culture = app.get('culture', 'Невідомо')
        quantity = app.get('quantity', 'Невідомо')
        status = app.get("proposal_status", "")
        if status == "confirmed":
            btn_text = f"{i}. {culture} | {quantity} т ✅"
        else:
            btn_text = f"{i}. {culture} | {quantity} т"
        buttons.append(btn_text)
    
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    row = []
    for text in buttons:
        row.append(text)
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    
    kb.row("Назад")
    msg = await message.answer("Ваші заявки:", reply_markup=kb)
    await state.update_data(viewing_msg_id=msg.message_id)
    await ApplicationStates.viewing_applications.set()


@dp.message_handler(Text(equals="Назад"), state=ApplicationStates.viewing_applications)
async def back_from_viewing_applications(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Головне меню:", reply_markup=get_main_menu_keyboard())


############################################
# Детальний перегляд заявки (натискає рядок)
############################################

@dp.message_handler(Regexp(r"^(\d+)\.\s(.+)\s\|\s(.+)\sт(?:\s✅)?$"), state="*")
async def view_application_detail(message: types.Message, state: FSMContext):
    text_str = message.text.strip()
    # Якщо користувач натискає "Назад", відновлюємо список заявок (залишаємо поточну логіку)
    if text_str == "Назад":
        user_id = message.from_user.id
        uid = str(user_id)
        apps = load_applications()
        all_apps = apps.get(uid, [])
        if not all_apps:
            await message.answer("Ви не маєте заявок.", reply_markup=get_main_menu_keyboard())
        else:
            buttons = []
            for i, app in enumerate(all_apps, start=1):
                culture = app.get('culture', 'Невідомо')
                quantity = app.get('quantity', 'Невідомо')
                status = app.get("proposal_status", "")
                if status == "confirmed":
                    btn_text = f"{i}. {culture} | {quantity} т ✅"
                else:
                    btn_text = f"{i}. {culture} | {quantity} т"
                buttons.append(btn_text)
    
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            row = []
            for t in buttons:
                row.append(t)
                if len(row) == 2:
                    kb.row(*row)
                    row = []
            if row:
                kb.row(*row)
            kb.row("Назад")
            await message.answer("Ваші заявки:", reply_markup=kb)
    
        await ApplicationStates.viewing_applications.set()
        return

    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    all_apps = apps.get(uid, [])
    
    # Отримуємо mapping із стану
    state_data = await state.get_data()
    mapping = state_data.get("apps_mapping", [])
    
    match = re.match(r"^(\d+)\.\s(.+)\s\|\s(.+)\sт(?:\s✅)?$", text_str)
    if not match:
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return

    displayed_idx = int(match.group(1)) - 1
    if displayed_idx < 0 or displayed_idx >= len(mapping):
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return

    # Отримуємо реальний індекс із збереженого mapping
    real_idx = mapping[displayed_idx]
    app = all_apps[real_idx]

    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except Exception:
        formatted_date = timestamp

    status = app.get("proposal_status", "")
    details = [
        "<b>Детальна інформація по заявці:</b>",
        f"Дата створення: {formatted_date}",
        f"ФГ: {app.get('fgh_name', '')}",
        f"ЄДРПОУ: {app.get('edrpou', '')}",
        f"Номер ФГ: {app.get('phone', '')}",
        f"Область: {app.get('region', '')}",
        f"Район: {app.get('district', '')}",
        f"Місто: {app.get('city', '')}",
        f"Група: {app.get('group', '')}",
        f"Культура: {app.get('culture', '')}",
        f"Кількість: {app.get('quantity', '')}",
        f"Форма оплати: {app.get('payment_form', '')}",
        f"Валюта: {app.get('currency', '')}",
        f"Бажана ціна: {app.get('price', '')}"
    ]

    extra = app.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")

    if status == "confirmed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '—')}")
        details.append("Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться")
    elif status == "Agreed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '')}")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status in ("active", "waiting", "Agreed"):
        kb.add("Переглянути пропозицію")
    if status != "confirmed":
        kb.add("Редагувати заявку", "Видалити заявку")
    kb.row("Назад")

    # Зберігаємо реальний індекс обраної заявки у стані
    await state.update_data(selected_app_index=real_idx)
    await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
    await ApplicationStates.viewing_application.set()


@dp.message_handler(Text(equals="Назад"), state=ApplicationStates.viewing_application)
async def user_view_application_detail_back(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    all_apps = apps.get(uid, [])

    # Фільтруємо заявки, що НЕ мають статус "deleted", і створюємо mapping
    filtered_apps = []
    mapping = []
    for idx, app in enumerate(all_apps):
        if app.get("proposal_status", "") != "deleted":
            filtered_apps.append(app)
            mapping.append(idx)

    if not filtered_apps:
        await message.answer("Ви не маєте заявок.", reply_markup=get_main_menu_keyboard())
    else:
        buttons = []
        for i, app in enumerate(filtered_apps, start=1):
            culture = app.get('culture', 'Невідомо')
            quantity = app.get('quantity', 'Невідомо')
            status = app.get("proposal_status", "")
            if status == "confirmed":
                btn_text = f"{i}. {culture} | {quantity} т ✅"
            else:
                btn_text = f"{i}. {culture} | {quantity} т"
            buttons.append(btn_text)

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for text in buttons:
            row.append(text)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.row("Назад")
        await message.answer("Ваші заявки:", reply_markup=kb)

    # Зберігаємо mapping у стані для подальшого використання
    await state.update_data(apps_mapping=mapping)
    await ApplicationStates.viewing_applications.set()


############################################
# Переглянути пропозицію
############################################

@dp.message_handler(Text(equals="Переглянути пропозицію"), state=ApplicationStates.viewing_application)
async def view_proposal(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    if index is None:
        await message.answer("Немає даних про заявку.", reply_markup=remove_keyboard())
        return

    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])

    if index < 0 or index >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return

    app = user_apps[index]
    status = app.get("proposal_status", "")
    proposal_text = f"Пропозиція по заявці: {app.get('proposal', 'Немає даних')}"

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)

    if status == "confirmed":
        kb.add("Назад")
        await message.answer("Ви вже підтвердили пропозицію, очікуйте результатів.", reply_markup=kb)
    elif status == "waiting":
        kb.add("Назад")
        await message.answer("Очікування: як тільки менеджер оновить пропозицію, Вам прийде сповіщення.", reply_markup=kb)
    elif status == "Agreed":
        kb.row("Підтвердити", "Відхилити")
        kb.add("Назад")
        await message.answer(proposal_text, reply_markup=kb)
    else:
        kb.add("Назад")
        await message.answer("Немає актуальної пропозиції.", reply_markup=kb)

    await ApplicationStates.viewing_proposal.set()


@dp.message_handler(Text(equals="Назад"), state=ApplicationStates.viewing_proposal)
async def back_from_proposal_to_detail(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("selected_app_index")
    if idx is None:
        await message.answer("Немає даних для перегляду.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    user_apps = apps.get(uid, [])

    if not user_apps or idx >= len(user_apps):
        await message.answer("Заявку не знайдено.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    app = user_apps[idx]
    try:
        dt = datetime.fromisoformat(app.get("timestamp", ""))
        formatted_date = dt.strftime("%d.%m.%Y")
    except Exception:
        formatted_date = app.get("timestamp", "") or "—"

    status = app.get("proposal_status", "")
    details = [
        "<b>Детальна інформація по заявці:</b>",
        f"Дата створення: {formatted_date}",
        f"ФГ: {app.get('fgh_name', '')}",
        f"ЄДРПОУ: {app.get('edrpou', '')}",
        f"Область: {app.get('region', '')}",
        f"Номер ФГ: {app.get('phone', '')}",
        f"Район: {app.get('district', '')}",
        f"Місто: {app.get('city', '')}",
        f"Група: {app.get('group', '')}",
        f"Культура: {app.get('culture', '')}",
        f"Кількість: {app.get('quantity', '')}",
        f"Форма оплати: {app.get('payment_form', '')}",
        f"Валюта: {app.get('currency', '')}",
        f"Бажана ціна: {app.get('price', '')}"
    ]

    extra = app.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")

    if status == "confirmed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '—')}")
        details.append("Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться")
    elif status == "Agreed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '')}")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Переглянути пропозицію")
    kb.row("Редагувати заявку", "Видалити заявку")
    kb.row("Назад")

    await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
    await ApplicationStates.viewing_application.set()


############################################
# Відхилити / Підтвердити (у випадку "Agreed")
############################################

@dp.message_handler(Text(equals="Відхилити"), state=ApplicationStates.viewing_proposal)
async def proposal_rejected(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    if index is None:
        await message.answer("Немає даних про заявку.")
        return

    update_application_status(message.from_user.id, index, "rejected")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Очікувати", "Видалити")
    await message.answer(
        "Пропозицію відхилено. Оберіть: Видалити заявку або Очікувати кращу пропозицію?",
        reply_markup=kb
    )

    await ApplicationStates.proposal_reply.set()


@dp.message_handler(Text(equals="Очікувати"), state="*")
async def wait_after_rejection(message: types.Message, state):
    data = await state.get_data()
    index = data.get("selected_app_index")
    if index is None:
        await message.answer("Немає заявки.")
        await state.finish()
        return

    # Оновлюємо статус заявки на "waiting"
    update_application_status(message.from_user.id, index, "waiting")

    apps = load_applications()
    uid = str(message.from_user.id)
    app = apps[uid][index]
    app["onceWaited"] = True
    save_applications(apps)

    # Фарбування клітинок залежно від типу пропозиції
    sheet_row = app.get("sheet_row")
    if sheet_row:
        if "bot_price" in app:
            # Якщо це ціна бота, фарбування:
            # SHEET1: стовпець O (15), SHEET2: стовпець M (13)
            color_cell_yellow_sheet1(sheet_row, 15)
            color_cell_yellow_sheet2(sheet_row, 13)
        else:
            # Якщо це менеджерська ціна, фарбування:
            # SHEET1: стовпець N (14), SHEET2: стовпець L (12)
            color_cell_yellow_sheet1(sheet_row, 14)
            color_cell_yellow_sheet2(sheet_row, 12)

    await message.answer(
        "Заявка оновлена. Ви будете повідомлені при появі кращої пропозиції.",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("Головне меню")
    )
    await state.finish()

@dp.message_handler(Text(equals="Видалити"), state=ApplicationStates.proposal_reply)
async def delete_after_rejection(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    if index is None:
        await message.answer("Немає заявки для видалення.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if index < 0 or index >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    app = user_apps[index]
    app["proposal_status"] = "deleted"
    save_applications(apps)

    sheet_row = app.get("sheet_row")
    if sheet_row:
        try:
            ws1 = get_worksheet1()
            ws2 = get_worksheet2()
            color_entire_row_red(ws1, sheet_row)
            color_entire_row_red(ws2, sheet_row)
        except Exception as e:
            logging.exception(f"Помилка фарбування рядка {sheet_row} в червоний: {e}")

    await message.answer("Ваша заявка видалена.", reply_markup=get_main_menu_keyboard())
    await state.finish()


@dp.message_handler(Text(equals="Підтвердити"), state=ApplicationStates.viewing_proposal)
async def confirm_proposal(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")

    update_application_status(message.from_user.id, index, "confirmed")

    apps = load_applications()
    uid = str(message.from_user.id)
    app = apps[uid][index]
    sheet_row = app.get("sheet_row")

    if sheet_row:
        try:
            confirmed_price = float(app.get("proposal", ""))
            bot_price = app.get("bot_price", None)
            if bot_price is not None and abs(confirmed_price - bot_price) < 1e-9:
                delete_price_cell_in_table2(sheet_row, col=15)
            else:
                delete_price_cell_in_table2(sheet_row, col=13)
        except Exception:
            delete_price_cell_in_table2(sheet_row, col=13)
            delete_price_cell_in_table2(sheet_row, col=15)

        try:
            ws1 = get_worksheet1()
            ws2 = get_worksheet2()
            color_entire_row_green(ws1, sheet_row)
            color_entire_row_green(ws2, sheet_row)
        except Exception as e:
            logging.exception(f"Помилка фарбування рядка {sheet_row}: {e}")

    save_applications(apps)

    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except Exception:
        formatted_date = timestamp or "—"

    extra_fields = app.get("extra_fields", {})
    extra_list = []
    for key, value in extra_fields.items():
        ukr_name = friendly_names.get(key, key)
        extra_list.append(f"{ukr_name}: {value}")
    extra_part = f"Додаткові параметри:\n<b>{chr(10).join(extra_list)}</b>\n" if extra_list else ""

    user_fullname = app.get("fullname", "")
    phone_from_app = app.get("phone", "")
    if not phone_from_app:
        phone_from_app = load_users().get("approved_users", {}).get(uid, {}).get("phone", "")
    if not user_fullname:
        user_fullname = load_users().get("approved_users", {}).get(uid, {}).get("fullname", "")

    if not phone_from_app:
        phone_from_app = "—"
    if not user_fullname:
        user_fullname = "—"

    user_fullname_line = f"Користувач: {user_fullname}"
    user_phone_line = f"Телефон: {phone_from_app}"

    admin_msg = (
        "<b>ЗАЯВКА ПІДТВЕРДЖЕНА</b>\n\n"
        "Повна інформація по заявці:\n"
        f"Дата створення: <b>{formatted_date}</b>\n\n"
        f"ФГ: <b>{app.get('fgh_name', 'Невідомо')}</b>\n"
        f"ЄДРПОУ: <b>{app.get('edrpou', 'Невідомо')}</b>\n"
        f"Номер ФГ: <b>{app.get('phone', 'Невідомо')}</b>\n"
        f"Область: <b>{app.get('region', 'Невідомо')}</b>\n"
        f"Район: <b>{app.get('district', 'N/A')}</b>\n"
        f"Місто: <b>{app.get('city', 'Невідомо')}</b>\n"
        f"Група: <b>{app.get('group', 'Невідомо')}</b>\n"
        f"Культура: <b>{app.get('culture', 'Nевідомо')}</b>\n"
        f"{extra_part}"
        f"Кількість: <b>{app.get('quantity', 'Невідомо')} т</b>\n"
        f"Бажана ціна: <b>{app.get('price', 'Невідомо')}</b>\n"
        f"Валюта: <b>{app.get('currency', 'N/A')}</b>\n"
        f"Форма оплати: <b>{app.get('payment_form', 'Невідомо')}</b>\n"
        f"Пропозиція ціни: <b>{app.get('proposal', 'Невідомо')}</b>\n\n"
        f"{user_fullname_line}\n"
        f"{user_phone_line}"
    )

    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, admin_msg)
        except Exception as e:
            logging.exception(f"Не вдалося відправити підтвердження адміну {admin_id}: {e}")

    await message.answer(
        "Ви підтвердили пропозицію. Очікуйте на подальші дії від менеджера/адміністратора.",
        reply_markup=get_main_menu_keyboard()
    )
    await state.finish()


############################################
# Редагувати заявку (одразу відкриття WebApp)
############################################

@dp.message_handler(Text(equals="Редагувати заявку"), state=ApplicationStates.viewing_application)
async def edit_application_direct(message: types.Message, state: FSMContext):
    """
    Тепер БЕЗ подвійного «Відкрити форму редагування».
    Просто одразу даємо кнопку з web_app=...
    """
    data = await state.get_data()
    index = data.get("selected_app_index")
    if index is None:
        await message.answer("Немає даних про заявку.", reply_markup=remove_keyboard())
        await state.finish()
        return

    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])

    if index < 0 or index >= len(user_apps):
        await message.answer("Заявку не знайдено.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    app = user_apps[index]

    # Формуємо словник з полями, які хочемо передати до webapp2
    quantity_clean = re.sub(r"[^\d.]", "", str(app.get("quantity", "")))
    webapp2_data = {
        "quantity": quantity_clean,
        "price": app.get("price", ""),
        "currency": app.get("currency", ""),
        "payment_form": app.get("payment_form", "")
    }

    # Додаткове логування даних для редагування
    logging.debug(f"Дані для редагування заявки, що передаються у WebApp2: {webapp2_data}")

    webapp_url2 = "https://danza13.github.io/agro-webapp/webapp2.html"
    prefill = quote(json.dumps(webapp2_data))
    url_with_data = f"{webapp_url2}?data={prefill}"
    logging.debug(f"URL для WebApp2: {url_with_data}")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(
        types.KeyboardButton(
            "Редагувати заявку в WebApp",
            web_app=types.WebAppInfo(url=url_with_data)
        )
    )
    kb.add("Скасувати")

    await message.answer("Натисніть, щоб відкрити форму для редагування:", reply_markup=kb)

    # Тримаємо індекс поточної заявки, чекаємо на дані з WebApp2
    await state.update_data(editing_app_index=index)
    await ApplicationStates.waiting_for_webapp2_data.set()


@dp.message_handler(Text(equals="Скасувати"), state=ApplicationStates.waiting_for_webapp2_data)
async def cancel_webapp2_editing(message: types.Message, state: FSMContext):
    """
    Повертаємось до детального перегляду заявки (як було раніше).
    """
    data = await state.get_data()
    idx = data.get("selected_app_index")
    if idx is None:
        await message.answer("Немає даних про заявку.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])

    if idx < 0 or idx >= len(user_apps):
        await message.answer("Заявку не знайдено.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    app = user_apps[idx]
    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp

    details = [
        "<b>Детальна інформація по заявці:</b>",
        f"Дата створення: {formatted_date}",
        f"ФГ: {app.get('fgh_name', '')}",
        f"ЄДРПОУ: {app.get('edrpou', '')}",
        f"Номер ФГ: {app.get('phone', '')}",
        f"Область: {app.get('region', '')}",
        f"Район: {app.get('district', '')}",
        f"Місто: {app.get('city', '')}",
        f"Група: {app.get('group', '')}",
        f"Культура: {app.get('culture', '')}",
        f"Кількість: {app.get('quantity', '')}",
        f"Форма оплати: {app.get('payment_form', '')}",
        f"Валюта: {app.get('currency', '')}",
        f"Бажана ціна: {app.get('price', '')}"
    ]

    extra = app.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")

    status = app.get("proposal_status", "")
    if status == "confirmed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '—')}")
        details.append("Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться")
    elif status == "Agreed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '')}")
        
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status in ("active", "waiting", "Agreed"):
        kb.add("Переглянути пропозицію")
    kb.add("Редагувати заявку", "Видалити заявку")
    kb.row("Назад")

    await message.answer("\n".join(details), parse_mode="HTML", reply_markup=kb)
    await ApplicationStates.viewing_application.set()


############################################
# Приймаємо дані з webapp2.html
############################################

@dp.message_handler(lambda m: m.text and "/webapp2_data" in m.text, state=ApplicationStates.waiting_for_webapp2_data)
async def webapp2_data_handler_text(message: types.Message, state: FSMContext):
    """
    Якщо веб-форма чомусь надсилає текст /webapp2_data ...
    """
    user_id = message.from_user.id
    try:
        prefix = "/webapp2_data "
        data_str = message.text[len(prefix):].strip() if message.text.startswith(prefix) else message.text.split("/webapp2_data", 1)[-1].strip()
        data_dict = json.loads(data_str)
        await process_webapp2_data(user_id, data_dict, state)
    except Exception as e:
        logging.exception(f"Помилка обробки webapp2 data (text) для user_id={user_id}: {e}")
        await bot.send_message(user_id, "Помилка обробки даних. Спробуйте ще раз.", reply_markup=remove_keyboard())


@dp.message_handler(content_types=types.ContentType.WEB_APP_DATA, state=ApplicationStates.waiting_for_webapp2_data)
async def webapp2_data_handler_web_app(message: types.Message, state: FSMContext):
    """
    Обробка даних, які приходять з webapp2.html (нативне WEB_APP_DATA).
    """
    user_id = message.from_user.id
    try:
        data_str = message.web_app_data.data
        data_dict = json.loads(data_str)
        await process_webapp2_data(user_id, data_dict, state)
    except Exception as e:
        logging.exception(f"Помилка WEB_APP_DATA (webapp2) для user_id={user_id}: {e}")
        await bot.send_message(user_id, "Помилка обробки даних. Спробуйте ще раз.", reply_markup=remove_keyboard())


async def process_webapp2_data(user_id: int, data_dict: dict, state: FSMContext):
    """
    Функція обробки даних із форми уточнення актуальності.
    Якщо користувач надіслав нові дані (навіть якщо зміни відсутні), 
    скидаємо прапорець topicality_in_progress та запускаємо наступну перевірку заявки.
    Якщо дані порожні – відправляємо повідомлення.
    """
    if not data_dict:
        await bot.send_message(user_id, "Дані порожні, спробуйте ще раз.", reply_markup=remove_keyboard())
        return

    fsm_data = await state.get_data()
    index = fsm_data.get("editing_app_index", None)

    apps = load_applications()
    uid = str(user_id)
    user_apps = apps.get(uid, [])

    if index is None or index < 0 or index >= len(user_apps):
        await bot.send_message(user_id, "Немає заявки для редагування.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    app = user_apps[index]
    sheet_row = app.get("sheet_row", None)
    if not sheet_row:
        await bot.send_message(user_id, "Немає рядка в таблиці для цієї заявки. Не можна редагувати.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    # Порівнюємо старі та нові значення
    old_data = {
        "quantity": app.get("quantity", ""),
        "price": app.get("price", ""),
        "currency": app.get("currency", ""),
        "payment_form": app.get("payment_form", "")
    }

    new_quantity = str(data_dict.get("quantity", "")).strip()
    new_price = data_dict.get("price", "").strip()
    new_currency = data_dict.get("currency", "").strip()
    new_payment_form = data_dict.get("payment_form", "").strip()

    changed_fields = {}
    if new_quantity != old_data["quantity"]:
        changed_fields["quantity"] = new_quantity
        app["quantity"] = new_quantity

    if new_price != old_data["price"]:
        changed_fields["price"] = new_price
        app["price"] = new_price

    if new_currency != old_data["currency"]:
        changed_fields["currency"] = new_currency
        app["currency"] = new_currency

    if new_payment_form != old_data["payment_form"]:
        changed_fields["payment_form"] = new_payment_form
        app["payment_form"] = new_payment_form

    save_applications(apps)

    if changed_fields:
        # Оновлюємо форматування у таблицях
        update_worksheet1_cells_for_edit(sheet_row, changed_fields)
        update_worksheet2_cells_for_edit_color(sheet_row, changed_fields)

        # Записуємо дату/час змін у колонку N (14) таблиці2
        now_str = datetime.now(ZoneInfo("Europe/Kiev")).strftime("%d.%m.%Y\n%H:%M:%S")
        ws2 = get_worksheet2()
        cell_address = rowcol_to_a1(sheet_row, 14)
        ws2.update_acell(cell_address, now_str)

        # Запускаємо перерахунок автопрайсу
        await re_run_autocalc_for_app(uid, index)

        await bot.send_message(user_id, "Дані успішно змінені!", reply_markup=remove_keyboard())
    else:
        await bot.send_message(user_id, "Нічого не змінено.", reply_markup=remove_keyboard())

    # ===== НОВИЙ ФРАГМЕНТ =====
    # Якщо користувач успішно надіслав форму, скидаємо прапорець topicality_in_progress
    if app.get("topicality_in_progress"):
        app["topicality_in_progress"] = False
        save_applications(apps)
        # Запускаємо наступну перевірку заявки через 10 секунд
        await asyncio.create_task(schedule_next_topicality(user_id))
    # ===== КІНЕЦЬ НОВОГО ФРАГМЕНТА =====

    # Повертаємось до детальної інформації по заявці
    app_updated = user_apps[index]
    timestamp = app_updated.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except Exception:
        formatted_date = timestamp

    status = app_updated.get("proposal_status", "")
    details = [
        "<b>Детальна інформація по заявці (оновлено):</b>",
        f"Дата створення: {formatted_date}",
        f"ФГ: {app_updated.get('fgh_name', '')}",
        f"ЄДРПОУ: {app_updated.get('edrpou', '')}",
        f"Номер ФГ: {app_updated.get('phone', '')}",
        f"Область: {app_updated.get('region', '')}",
        f"Район: {app_updated.get('district', '')}",
        f"Місто: {app_updated.get('city', '')}",
        f"Група: {app_updated.get('group', '')}",
        f"Культура: {app_updated.get('culture', '')}",
        f"Кількість: {app_updated.get('quantity', '')}",
        f"Форма оплати: {app_updated.get('payment_form', '')}",
        f"Валюта: {app_updated.get('currency', '')}",
        f"Бажана ціна: {app_updated.get('price', '')}"
    ]
    extra = app_updated.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")

    if status == "confirmed":
        details.append(f"Пропозиція ціни: {app_updated.get('proposal', '—')}")
        details.append("Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться")
    elif status == "Agreed":
        details.append(f"Пропозиція ціни: {app_updated.get('proposal', '')}")

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status in ("active", "waiting", "Agreed"):
        kb.add("Переглянути пропозицію")
    kb.add("Редагувати заявку", "Видалити заявку")
    kb.row("Назад")

    await bot.send_message(user_id, "\n".join(details), parse_mode="HTML", reply_markup=kb)
    await ApplicationStates.viewing_application.set()


############################################
# ВИДАЛЕННЯ ЗАЯВКИ
############################################

@dp.message_handler(Text(equals="Видалити заявку"), state=ApplicationStates.viewing_application)
async def ask_deletion_confirmation(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    if index is None:
        await message.answer("Немає вибраної заявки.", reply_markup=remove_keyboard())
        return

    apps = load_applications()
    uid = str(message.from_user.id)
    user_apps = apps.get(uid, [])
    if index < 0 or index >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return

    culture = user_apps[index].get('culture', 'Невідомо')
    quantity = user_apps[index].get('quantity', 'Невідомо')
    question = f"Ви хочете видалити заявку {index+1}. {culture} | {quantity} т?"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Так", "Ні")
    await message.answer(question, reply_markup=kb)
    await ApplicationStates.deletion_confirmation.set()


@dp.message_handler(Text(equals="Так"), state=ApplicationStates.deletion_confirmation)
async def confirm_deletion(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    if index is None:
        await message.answer("Заявку не знайдено.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if index < 0 or index >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    app = user_apps[index]
    app["proposal_status"] = "deleted"
    save_applications(apps)

    sheet_row = app.get("sheet_row")
    if sheet_row:
        try:
            ws1 = get_worksheet1()
            ws2 = get_worksheet2()
            color_entire_row_red(ws1, sheet_row)
            color_entire_row_red(ws2, sheet_row)
        except Exception as e:
            logging.exception(f"Помилка фарбування рядка {sheet_row} в червоний: {e}")

    await message.answer("Заявка видалена.", reply_markup=get_main_menu_keyboard())
    await state.finish()


@dp.message_handler(Text(equals="Ні"), state=ApplicationStates.deletion_confirmation)
async def cancel_deletion(message: types.Message, state: FSMContext):
    """
    Повертаємось до детального перегляду заявки (як було).
    """
    data = await state.get_data()
    idx = data.get("selected_app_index")
    if idx is None:
        await message.answer("Немає даних про заявку.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])

    if idx < 0 or idx >= len(user_apps):
        await message.answer("Заявку не знайдено.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    app = user_apps[idx]
    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp

    details = [
        "<b>Детальна інформація по заявці:</b>",
        f"Дата створення: {formatted_date}",
        f"ФГ: {app.get('fgh_name', '')}",
        f"ЄДРПОУ: {app.get('edrpou', '')}",
        f"Номер ФГ: {app.get('phone', '')}",
        f"Область: {app.get('region', '')}",
        f"Район: {app.get('district', '')}",
        f"Місто: {app.get('city', '')}",
        f"Група: {app.get('group', '')}",
        f"Культура: {app.get('culture', '')}",
        f"Кількість: {app.get('quantity', '')}",
        f"Форма оплати: {app.get('payment_form', '')}",
        f"Валюта: {app.get('currency', '')}",
        f"Бажана ціна: {app.get('price', '')}"
    ]

    extra = app.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")

    status = app.get("proposal_status", "")
    if status == "confirmed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '—')}")
        details.append("Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться")
    elif status == "Agreed":
        details.append(f"Пропозиція ціни: {app.get('proposal', '')}")
        
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status in ("active", "waiting", "Agreed"):
        kb.add("Переглянути пропозицію")
    kb.add("Редагувати заявку", "Видалити заявку")
    kb.row("Назад")

    await message.answer("\n".join(details), parse_mode="HTML", reply_markup=kb)
    await ApplicationStates.viewing_application.set()


############################################
# Робота з WebApp (створення заявки)
############################################

@dp.message_handler(lambda message: message.text and "/webapp_data" in message.text, state=ApplicationStates.waiting_for_webapp_data)
async def webapp_data_handler_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        prefix = "/webapp_data "
        data_str = message.text[len(prefix):].strip() if message.text.startswith(prefix) else message.text.split("/webapp_data", 1)[-1].strip()
        data_dict = json.loads(data_str)
        await state.update_data(webapp_data=data_dict)
        current_data = await state.get_data()
        sheet_row = current_data.get("sheet_row")
        edit_index = current_data.get("edit_index")
        await process_webapp_data_direct(user_id, data_dict, edit_index, sheet_row, state)
    except Exception as e:
        logging.exception(f"Помилка обробки даних для user_id={user_id}: {e}")
        await bot.send_message(user_id, "Помилка обробки даних. Спробуйте ще раз.", reply_markup=remove_keyboard())


@dp.message_handler(content_types=types.ContentType.WEB_APP_DATA, state=ApplicationStates.waiting_for_webapp_data)
async def webapp_data_handler_web_app(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        data_str = message.web_app_data.data
        data_dict = json.loads(data_str)
        await state.update_data(webapp_data=data_dict)
        current_data = await state.get_data()
        sheet_row = current_data.get("sheet_row")
        edit_index = current_data.get("edit_index")
        await process_webapp_data_direct(user_id, data_dict, edit_index, sheet_row, state)
    except Exception as e:
        logging.exception(f"Помилка WEB_APP_DATA для user_id={user_id}: {e}")
        await bot.send_message(user_id, "Помилка обробки даних. Спробуйте ще раз.", reply_markup=remove_keyboard())


async def process_webapp_data_direct(user_id: int, data: dict, edit_index: int = None, sheet_row: int = None, state: FSMContext = None):
    if not data or not any(data.values()):
        logging.warning("Отримано порожні дані, повідомлення не надсилається.")
        return

    message_lines = [
        "<b>Перевірте заявку:</b>",
        f"ФГ: {data.get('fgh_name', '')}",
        f"ЄДРПОУ: {data.get('edrpou', '')}",
        f"Номер ФГ: {data.get('phone', '')}",
        f"Область: {data.get('region', '')}",
        f"Район: {data.get('district', '')}",
        f"Місто: {data.get('city', '')}",
        f"Група: {data.get('group', '')}",
        f"Культура: {data.get('culture', '')}"
    ]
    extra = data.get("extra_fields", {})
    if extra:
        message_lines.append("Додаткові параметри:")
        for key, value in extra.items():
            ukr_name = friendly_names.get(key, key.capitalize())
            message_lines.append(f"{ukr_name}: {value}")

    message_lines.extend([
        f"Кількість: {data.get('quantity', '')} т",
        f"Форма оплати: {data.get('payment_form', '')}",
        f"Валюта: {data.get('currency', '')}",
        f"Ціна: {data.get('price', '')}"
    ])
    preview_text = "\n".join(message_lines)

    reply_kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    reply_kb.add("Підтвердити", "Редагувати", "Скасувати")

    await bot.send_message(user_id, preview_text, parse_mode="HTML", reply_markup=reply_kb)

    curr_state = dp.current_state(chat=user_id, user=user_id)

    # Якщо були якісь правки/редагування – інколи передаємо індекс, але зазвичай це нова заявка
    if edit_index is not None and sheet_row is not None:
        await curr_state.update_data(edit_index=edit_index, sheet_row=sheet_row, webapp_data=data)
        await curr_state.set_state(ApplicationStates.editing_application.state)
    else:
        await curr_state.update_data(webapp_data=data)
        await curr_state.set_state(ApplicationStates.confirm_application.state)


@dp.message_handler(Text(equals="Редагувати"), state=[ApplicationStates.confirm_application, ApplicationStates.editing_application])
async def edit_application_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    webapp_data = data.get("webapp_data")
    if not webapp_data:
        await message.answer("Немає даних для редагування.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return

    webapp_url = "https://danza13.github.io/agro-webapp/webapp.html"
    prefill = quote(json.dumps(webapp_data))
    url_with_data = f"{webapp_url}?data={prefill}"

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Відкрити форму для редагування", web_app=types.WebAppInfo(url=url_with_data)))
    kb.row("Скасувати")

    await message.answer("Редагуйте заявку у WebApp:", reply_markup=kb)
    await state.set_state(ApplicationStates.waiting_for_webapp_data.state)


@dp.message_handler(Text(equals="Скасувати"), state=[ApplicationStates.waiting_for_webapp_data, ApplicationStates.confirm_application, ApplicationStates.editing_application])
async def cancel_process_reply(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Процес скасовано. Головне меню:", reply_markup=get_main_menu_keyboard())


@dp.message_handler(Text(equals="Підтвердити"), state=ApplicationStates.confirm_application)
async def confirm_application_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await message.answer("Очікуйте, зберігаємо заявку...")
    data_state = await state.get_data()
    webapp_data = data_state.get("webapp_data")

    if not webapp_data:
        await message.answer("Немає даних заявки. Спробуйте ще раз.", reply_markup=remove_keyboard())
        await state.finish()
        return

    from db import load_users
    users = load_users()
    if "fullname" not in webapp_data or not webapp_data.get("fullname"):
        approved_user_info = users.get("approved_users", {}).get(str(user_id), {})
        webapp_data["fullname"] = approved_user_info.get("fullname", "")

    webapp_data["chat_id"] = str(message.chat.id)
    webapp_data["original_manager_price"] = webapp_data.get("manager_price", "")

    try:
        sheet_row = update_google_sheet(webapp_data)
        webapp_data["sheet_row"] = sheet_row
        add_application(user_id, message.chat.id, webapp_data)
        await state.finish()
        await message.answer("Ваша заявка прийнята!", reply_markup=get_main_menu_keyboard())
    except Exception as e:
        logging.exception(f"Помилка при збереженні заявки: {e}")
        await message.answer("Сталася помилка при збереженні. Спробуйте пізніше.", reply_markup=remove_keyboard())
        await state.finish()
