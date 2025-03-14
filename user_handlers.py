import json
import re
import logging
import asyncio
from datetime import datetime
from urllib.parse import quote

from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text, Regexp

from loader import dp, bot
from config import ADMINS, friendly_names
from states import RegistrationStates, ApplicationStates
from keyboards import remove_keyboard, get_main_menu_keyboard
from db import (
    load_users, save_users,
    load_applications, save_applications,
    add_application, delete_application_soft, update_application_status
)
from gsheet_utils import (
    update_google_sheet, color_cell_red, color_cell_green, color_cell_yellow,
    delete_price_cell_in_table2, get_worksheet1, get_worksheet2, color_entire_row_green,
    color_entire_row_red, mark_edited_cells, update_edit_timestamp
)

############################################
# РЕЄСТРАЦІЯ КОРИСТУВАЧА (/start)
############################################

@dp.message_handler(commands=["start"], state="*")
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.finish()
    users = load_users()
    uid = str(user_id)
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
    users = load_users()
    uid = str(user_id)
    if uid not in users.get("approved_users", {}):
        await message.answer("Немає доступу. Очікуйте схвалення.", reply_markup=remove_keyboard())
        return
    await state.finish()
    await message.answer("Головне меню:", reply_markup=get_main_menu_keyboard())

@dp.message_handler(commands=["support"], state="*")
async def support_command(message: types.Message, state: FSMContext):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Звернутись до підтримки", url="https://t.me/Dealeragro_bot"))
    await message.answer("Якщо вам потрібна допомога, натисніть кнопку нижче:", reply_markup=keyboard)

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
    user_apps = apps.get(uid, [])
    if not user_apps:
        await message.answer("Ви не маєте заявок.", reply_markup=get_main_menu_keyboard())
        return
    buttons = []
    for i, app in enumerate(user_apps, start=1):
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
# Детальний перегляд заявки
############################################

@dp.message_handler(Regexp(r"^(\d+)\.\s(.+)\s\|\s(.+)\sт(?:\s✅)?$"), state="*")
async def view_application_detail(message: types.Message, state: FSMContext):
    if message.text.strip() == "Назад":
        user_id = message.from_user.id
        uid = str(user_id)
        apps = load_applications()
        user_apps = apps.get(uid, [])
        if not user_apps:
            await message.answer("Ви не маєте заявок.", reply_markup=get_main_menu_keyboard())
        else:
            buttons = []
            for i, app in enumerate(user_apps, start=1):
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
        await ApplicationStates.viewing_applications.set()
        return

    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    match = re.match(r"^(\d+)\.\s(.+)\s\|\s(.+)\sт(?:\s✅)?$", message.text.strip())
    if not match:
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return
    idx = int(match.group(1)) - 1
    if idx < 0 or idx >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return
    app = user_apps[idx]
    await state.update_data(selected_app_index=idx)
    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp
    status = app.get("proposal_status", "")
    details = []
    if status == "confirmed":
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
            f"Область: {app.get('region', '')}",
            f"Район: {app.get('district', '')}",
            f"Місто: {app.get('city', '')}",
            f"Група: {app.get('group', '')}",
            f"Культура: {app.get('culture', '')}",
            f"Кількість: {app.get('quantity', '')}",
            f"Форма оплати: {app.get('payment_form', '')}",
            f"Валюта: {app.get('currency', '')}",
            f"Бажана ціна: {app.get('price', '')}",
            f"Пропозиція ціни: {app.get('proposal', '—')}",
            "Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться"
        ]
    else:
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
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
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status == "Agreed":
        once_waited = app.get("onceWaited", False)
        details.append(f"\nПропозиція ціни: {app.get('proposal', '')}")
        if once_waited:
            kb.row("Підтвердити", "Видалити")
        else:
            kb.row("Підтвердити", "Відхилити", "Видалити")
    elif status in ("active", "waiting"):
        kb.add("Переглянути пропозицію")
    elif status == "rejected":
        kb.row("Видалити", "Очікувати")
    elif status == "deleted":
        details.append("\nЦя заявка вже позначена як 'deleted' (видалена).")
        kb.add("Назад")
    # Якщо заявка не підтверджена, додаємо кнопки редагування та видалення
    if status != "confirmed":
        kb.row("Видалити заявку", "Редагувати заявку")
    kb.row("Назад")
    await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
    await ApplicationStates.viewing_application.set()

############################################
# Хендлер для кнопки "Назад" у стані перегляду заявки
############################################

@dp.message_handler(Text(equals="Назад"), state=ApplicationStates.viewing_application)
async def user_view_application_detail_back(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    uid = str(user_id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if not user_apps:
        await message.answer("Ви не маєте заявок.", reply_markup=get_main_menu_keyboard())
    else:
        buttons = []
        for i, app in enumerate(user_apps, start=1):
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
    await ApplicationStates.viewing_applications.set()

############################################
# "Переглянути пропозицію"
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
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Назад")
    once_waited = app.get("onceWaited", False)
    proposal_text = f"Пропозиція по заявці: {app.get('proposal', 'Немає даних')}"
    if status == "confirmed":
        await message.answer("Ви вже підтвердили пропозицію, очікуйте результатів.", reply_markup=kb)
    elif status == "waiting":
        await message.answer("Очікування: як тільки менеджер оновить пропозицію, Вам прийде сповіщення.", reply_markup=kb)
    elif status == "Agreed":
        if once_waited:
            kb.row("Підтвердити", "Видалити")
        else:
            kb.row("Підтвердити", "Відхилити", "Видалити")
        await message.answer(proposal_text, reply_markup=kb)
    else:
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
        formatted_date = app.get("timestamp", "")
    status = app.get("proposal_status", "")
    details = []
    if status == "confirmed":
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
            f"Область: {app.get('region', '')}",
            f"Район: {app.get('district', '')}",
            f"Місто: {app.get('city', '')}",
            f"Група: {app.get('group', '')}",
            f"Культура: {app.get('culture', '')}",
            f"Кількість: {app.get('quantity', '')}",
            f"Форма оплати: {app.get('payment_form', '')}",
            f"Валюта: {app.get('currency', '')}",
            f"Бажана ціна: {app.get('price', '')}",
            f"Пропозиція ціни: {app.get('proposal', '—')}",
            "Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться"
        ]
    else:
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
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
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status == "Agreed":
        once_waited = app.get("onceWaited", False)
        details.append(f"\nПропозиція ціни: {app.get('proposal', '')}")
        if once_waited:
            kb.row("Підтвердити", "Видалити")
        else:
            kb.row("Підтвердити", "Відхилити", "Видалити")
    elif status in ("active", "waiting"):
        kb.add("Переглянути пропозицію")
    elif status == "rejected":
        kb.row("Видалити", "Очікувати")
    elif status == "deleted":
        details.append("\nЦя заявка вже позначена як 'deleted' (видалена).")
        kb.add("Назад")
    if status != "confirmed":
        kb.row("Видалити заявку", "Редагувати заявку")
    kb.row("Назад")
    await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
    await ApplicationStates.viewing_application.set()

############################################
# Видалення заявки користувачем
############################################

@dp.message_handler(Text(equals="Видалити заявку"), state=ApplicationStates.viewing_application)
async def delete_application_request(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("selected_app_index")
    if idx is None:
        await message.answer("Немає вибраних даних заявки.", reply_markup=remove_keyboard())
        return
    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if idx < 0 or idx >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return
    app = user_apps[idx]
    if app.get("proposal_status") == "confirmed":
        await message.answer("Редагування недоступне для підтверджених заявок.", reply_markup=get_main_menu_keyboard())
        return
    culture = app.get("culture", "Невідомо")
    quantity = app.get("quantity", "Невідомо")
    confirm_text = f"Ви хочете видалити заявку {idx+1}. {culture} | {quantity} т?"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Так", "Ні")
    await message.answer(confirm_text, reply_markup=kb)
    await ApplicationStates.deletion_confirm.set()

@dp.message_handler(lambda message: message.text in ["Так", "Ні"], state=ApplicationStates.deletion_confirm)
async def deletion_confirmation_handler(message: types.Message, state: FSMContext):
    if message.text == "Ні":
        data = await state.get_data()
        idx = data.get("selected_app_index")
        uid = str(message.from_user.id)
        apps = load_applications()
        user_apps = apps.get(uid, [])
        if idx is None or idx < 0 or idx >= len(user_apps):
            await message.answer("Невірна заявка.", reply_markup=get_main_menu_keyboard())
            await state.finish()
            return
        app = user_apps[idx]
        timestamp = app.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(timestamp)
            formatted_date = dt.strftime("%d.%m.%Y")
        except:
            formatted_date = timestamp
        status = app.get("proposal_status", "")
        details = []
        if status == "confirmed":
            details = [
                "<b>Детальна інформація по заявці:</b>",
                f"Дата створення: {formatted_date}",
                f"ФГ: {app.get('fgh_name', '')}",
                f"ЄДРПОУ: {app.get('edrpou', '')}",
                f"Область: {app.get('region', '')}",
                f"Район: {app.get('district', '')}",
                f"Місто: {app.get('city', '')}",
                f"Група: {app.get('group', '')}",
                f"Культура: {app.get('culture', '')}",
                f"Кількість: {app.get('quantity', '')}",
                f"Форма оплати: {app.get('payment_form', '')}",
                f"Валюта: {app.get('currency', '')}",
                f"Бажана ціна: {app.get('price', '')}",
                f"Пропозиція ціни: {app.get('proposal', '—')}",
                "Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться"
            ]
        else:
            details = [
                "<b>Детальна інформація по заявці:</b>",
                f"Дата створення: {formatted_date}",
                f"ФГ: {app.get('fgh_name', '')}",
                f"ЄДРПОУ: {app.get('edrpou', '')}",
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
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        if status == "Agreed":
            once_waited = app.get("onceWaited", False)
            details.append(f"\nПропозиція ціни: {app.get('proposal', '')}")
            if once_waited:
                kb.row("Підтвердити", "Видалити")
            else:
                kb.row("Підтвердити", "Відхилити", "Видалити")
        elif status in ("active", "waiting"):
            kb.add("Переглянути пропозицію")
        elif status == "rejected":
            kb.row("Видалити", "Очікувати")
        elif status == "deleted":
            details.append("\nЦя заявка вже позначена як 'deleted' (видалена).")
            kb.add("Назад")
        if status != "confirmed":
            kb.row("Видалити заявку", "Редагувати заявку")
        kb.row("Назад")
        await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
        await ApplicationStates.viewing_application.set()
    else:
        data = await state.get_data()
        idx = data.get("selected_app_index")
        uid = str(message.from_user.id)
        update_application_status(message.from_user.id, idx, "deleted")
        apps = load_applications()
        app = apps[uid][idx]
        sheet_row = app.get("sheet_row")
        if sheet_row:
            try:
                ws1 = get_worksheet1()
                ws2 = get_worksheet2()
                color_entire_row_red(ws1, sheet_row)
                color_entire_row_red(ws2, sheet_row)
            except Exception as e:
                logging.exception(f"Помилка фарбування рядка {sheet_row}: {e}")
        await message.answer("Ваша заявка видалена.", reply_markup=get_main_menu_keyboard())
        await state.finish()

############################################
# Редагування заявки користувачем
############################################

@dp.message_handler(Text(equals="Редагувати заявку"), state=ApplicationStates.viewing_application)
async def edit_application_request(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("selected_app_index")
    if idx is None:
        await message.answer("Немає вибраних даних заявки.", reply_markup=remove_keyboard())
        return
    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if idx < 0 or idx >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=remove_keyboard())
        return
    app = user_apps[idx]
    if app.get("proposal_status") == "confirmed":
        await message.answer("Редагування недоступне для підтверджених заявок.", reply_markup=get_main_menu_keyboard())
        return
    webapp_url = "https://danza13.github.io/agro-webapp/webapp2.html"
    prefill_data = {
        "quantity": app.get("quantity", ""),
        "price": app.get("price", ""),
        "currency": app.get("currency", ""),
        "payment_form": app.get("payment_form", "")
    }
    prefill = quote(json.dumps(prefill_data))
    url_with_data = f"{webapp_url}?data={prefill}"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Редагувати заявку", web_app=types.WebAppInfo(url=url_with_data)))
    kb.row("Скасувати")
    await message.answer("Відкрийте форму для редагування заявки:", reply_markup=kb)
    await ApplicationStates.editing_waiting_webapp.set()

@dp.message_handler(content_types=types.ContentType.WEB_APP_DATA, state=ApplicationStates.editing_waiting_webapp)
async def webapp_edit_data_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        data_str = message.web_app_data.data
        data_dict = json.loads(data_str)
        await state.update_data(editing_webapp_data=data_dict)
        full_apps = load_applications()
        uid = str(user_id)
        data_state = await state.get_data()
        idx = data_state.get("selected_app_index")
        if idx is None:
            await message.answer("Немає даних про заявку.", reply_markup=remove_keyboard())
            return
        app = full_apps.get(uid, [])[idx]
        edited_app = app.copy()
        edited_app["quantity"] = data_dict.get("quantity", app.get("quantity"))
        edited_app["price"] = data_dict.get("price", app.get("price"))
        edited_app["currency"] = data_dict.get("currency", app.get("currency"))
        edited_app["payment_form"] = data_dict.get("payment_form", app.get("payment_form"))
        timestamp = edited_app.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(timestamp)
            formatted_date = dt.strftime("%d.%m.%Y")
        except:
            formatted_date = timestamp
        status = edited_app.get("proposal_status", "")
        details = [
            "<b>Попередній перегляд зміненої заявки:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {edited_app.get('fgh_name', '')}",
            f"ЄДРПОУ: {edited_app.get('edrpou', '')}",
            f"Область: {edited_app.get('region', '')}",
            f"Район: {edited_app.get('district', '')}",
            f"Місто: {edited_app.get('city', '')}",
            f"Група: {edited_app.get('group', '')}",
            f"Культура: {edited_app.get('culture', '')}",
            f"Кількість: {edited_app.get('quantity', '')}",
            f"Форма оплати: {edited_app.get('payment_form', '')}",
            f"Валюта: {edited_app.get('currency', '')}",
            f"Бажана ціна: {edited_app.get('price', '')}",
            f"Пропозиція ціни: {edited_app.get('proposal', '—')}"
        ]
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("Підтвердити", "Скасувати")
        await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
        await ApplicationStates.editing_preview.set()
    except Exception as e:
        logging.exception(f"Помилка при обробці WEB_APP_DATA для редагування: {e}")
        await bot.send_message(user_id, "Помилка обробки даних. Спробуйте ще раз.", reply_markup=remove_keyboard())

@dp.message_handler(Text(equals="Скасувати"), state=ApplicationStates.editing_preview)
async def cancel_edit_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("selected_app_index")
    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if idx is None or idx < 0 or idx >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return
    app = user_apps[idx]
    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp
    status = app.get("proposal_status", "")
    details = []
    if status == "confirmed":
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
            f"Область: {app.get('region', '')}",
            f"Район: {app.get('district', '')}",
            f"Місто: {app.get('city', '')}",
            f"Група: {app.get('group', '')}",
            f"Культура: {app.get('culture', '')}",
            f"Кількість: {app.get('quantity', '')}",
            f"Форма оплати: {app.get('payment_form', '')}",
            f"Валюта: {app.get('currency', '')}",
            f"Бажана ціна: {app.get('price', '')}",
            f"Пропозиція ціни: {app.get('proposal', '—')}",
            "Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться"
        ]
    else:
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
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
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status == "Agreed":
        once_waited = app.get("onceWaited", False)
        details.append(f"\nПропозиція ціни: {app.get('proposal', '')}")
        if once_waited:
            kb.row("Підтвердити", "Видалити")
        else:
            kb.row("Підтвердити", "Відхилити", "Видалити")
    elif status in ("active", "waiting"):
        kb.add("Переглянути пропозицію")
    elif status == "rejected":
        kb.row("Видалити", "Очікувати")
    elif status == "deleted":
        details.append("\nЦя заявка вже позначена як 'deleted' (видалена).")
        kb.add("Назад")
    if status != "confirmed":
        kb.row("Видалити заявку", "Редагувати заявку")
    kb.row("Назад")
    await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
    await ApplicationStates.viewing_application.set()

@dp.message_handler(Text(equals="Підтвердити"), state=ApplicationStates.editing_preview)
async def confirm_editing_application(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    editing_data = data.get("editing_webapp_data")
    if not editing_data:
        await message.answer("Немає даних для редагування.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return
    uid = str(user_id)
    apps = load_applications()
    idx = data.get("selected_app_index")
    if idx is None or idx < 0 or idx >= len(apps.get(uid, [])):
        await message.answer("Невірна заявка.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return
    app = apps[uid][idx]
    app["quantity"] = editing_data.get("quantity", app.get("quantity"))
    app["price"] = editing_data.get("price", app.get("price"))
    app["currency"] = editing_data.get("currency", app.get("currency"))
    app["payment_form"] = editing_data.get("payment_form", app.get("payment_form"))
    app["edit_timestamp"] = datetime.now().strftime("%d.%m.%Y\n%H:%M:%S")
    sheet_row = app.get("sheet_row")
    if sheet_row:
        try:
            ws1 = get_worksheet1()
            ws2 = get_worksheet2()
            mark_edited_cells(ws1, sheet_row, [8, 11, 12, 13])
            mark_edited_cells(ws2, sheet_row, [8, 11, 12, 13])
            update_edit_timestamp(ws2, sheet_row, col=14)
        except Exception as e:
            logging.exception(f"Помилка оновлення даних в Google Sheets: {e}")
    save_applications(apps)
    await message.answer("Заявка успішно оновлена.", reply_markup=get_main_menu_keyboard())
    await state.finish()

@dp.message_handler(Text(equals="Скасувати"), state=[ApplicationStates.editing_waiting_webapp, ApplicationStates.editing_preview])
async def cancel_editing_process(message: types.Message, state: FSMContext):
    # Повертаємо користувача до перегляду початкової заявки
    data = await state.get_data()
    idx = data.get("selected_app_index")
    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if idx is None or idx < 0 or idx >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return
    app = user_apps[idx]
    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp
    status = app.get("proposal_status", "")
    details = []
    if status == "confirmed":
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
            f"Область: {app.get('region', '')}",
            f"Район: {app.get('district', '')}",
            f"Місто: {app.get('city', '')}",
            f"Група: {app.get('group', '')}",
            f"Культура: {app.get('culture', '')}",
            f"Кількість: {app.get('quantity', '')}",
            f"Форма оплати: {app.get('payment_form', '')}",
            f"Валюта: {app.get('currency', '')}",
            f"Бажана ціна: {app.get('price', '')}",
            f"Пропозиція ціни: {app.get('proposal', '—')}",
            "Ціна була ухвалена, очікуйте, скоро з вами зв'яжуться"
        ]
    else:
        details = [
            "<b>Детальна інформація по заявці:</b>",
            f"Дата створення: {formatted_date}",
            f"ФГ: {app.get('fgh_name', '')}",
            f"ЄДРПОУ: {app.get('edrpou', '')}",
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
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if status == "Agreed":
        once_waited = app.get("onceWaited", False)
        details.append(f"\nПропозиція ціни: {app.get('proposal', '')}")
        if once_waited:
            kb.row("Підтвердити", "Видалити")
        else:
            kb.row("Підтвердити", "Відхилити", "Видалити")
    elif status in ("active", "waiting"):
        kb.add("Переглянути пропозицію")
    elif status == "rejected":
        kb.row("Видалити", "Очікувати")
    elif status == "deleted":
        details.append("\nЦя заявка вже позначена як 'deleted' (видалена).")
        kb.add("Назад")
    if status != "confirmed":
        kb.row("Видалити заявку", "Редагувати заявку")
    kb.row("Назад")
    await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
    await ApplicationStates.viewing_application.set()
