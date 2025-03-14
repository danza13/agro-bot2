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
from gsheet_utils import update_google_sheet, color_cell_red, color_cell_green, color_cell_yellow

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
    # Додаємо кнопку "Назад", яка повертає до головного меню
    kb.row("Назад")
    await message.answer("Ваші заявки:", reply_markup=kb)
    # Встановлюємо стан списку заявок
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
    # Якщо користувач натискає "Назад" – повертаємо до списку заявок
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

    # Інакше – інтерпретуємо повідомлення як вибір заявки для детального перегляду
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
    extra = app.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")
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
    # Додаємо кнопку "Назад", щоб повернутися до списку заявок
    kb.row("Назад")
    await state.update_data(selected_app_index=idx)
    await message.answer("\n".join(details), reply_markup=kb, parse_mode="HTML")
    await ApplicationStates.viewing_application.set()


############################################
# Хендлер для кнопки "Назад" у стані перегляду заявки
# (Повертає користувача до списку заявок)
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
    # НЕ завершувати стан, щоб кнопка "Назад" (хендлер вище) могла повернути користувача до списку заявок

############################################
# "Відхилити" пропозицію
############################################

@dp.message_handler(Text(equals="Відхилити"), state=ApplicationStates.viewing_application)
async def proposal_rejected(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    update_application_status(message.from_user.id, index, "rejected")
    apps = load_applications()
    uid = str(message.from_user.id)
    app = apps[uid][index]
    manager_price = app.get("original_manager_price", "").strip()
    bot_price = app.get("bot_price", None)
    sheet_row = app.get("sheet_row")
    if sheet_row:
        prop = app.get("proposal", "")
        try:
            float_prop = float(prop)
            if bot_price is not None and abs(float_prop - bot_price) < 1e-9:
                color_cell_red(sheet_row, col=13)
            else:
                color_cell_red(sheet_row, col=12)
        except:
            color_cell_red(sheet_row, col=12)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Видалити", "Очікувати")
    await message.answer("Пропозицію відхилено. Оберіть: Видалити заявку або Очікувати кращу пропозицію?", reply_markup=kb)
    await ApplicationStates.proposal_reply.set()


@dp.message_handler(Text(equals="Очікувати"), state=ApplicationStates.proposal_reply)
async def wait_after_rejection(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    update_application_status(message.from_user.id, index, "waiting")
    apps = load_applications()
    uid = str(message.from_user.id)
    app = apps[uid][index]
    app["onceWaited"] = True
    sheet_row = app.get("sheet_row")
    if sheet_row:
        prop = app.get("proposal", "")
        bot_price = app.get("bot_price", None)
        try:
            float_prop = float(prop)
            if bot_price is not None and abs(float_prop - bot_price) < 1e-9:
                color_cell_yellow(sheet_row, col=13)
            else:
                color_cell_yellow(sheet_row, col=12)
        except:
            color_cell_yellow(sheet_row, col=12)
    save_applications(apps)
    await message.answer("Заявка оновлена. Ви будете повідомлені при появі кращої пропозиції.", reply_markup=get_main_menu_keyboard())
    await state.finish()


@dp.message_handler(Text(equals="Видалити"), state=ApplicationStates.proposal_reply)
async def delete_after_rejection(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    uid = str(message.from_user.id)
    apps = load_applications()
    user_apps = apps.get(uid, [])
    if index is None or index < 0 or index >= len(user_apps):
        await message.answer("Невірна заявка.", reply_markup=get_main_menu_keyboard())
        await state.finish()
        return
    app = user_apps[index]
    sheet_row = app.get("sheet_row")
    if sheet_row:
        prop = app.get("proposal", "")
        bot_price = app.get("bot_price", None)
        try:
            float_prop = float(prop)
            if bot_price is not None and abs(float_prop - bot_price) < 1e-9:
                color_cell_red(sheet_row, col=13)
            else:
                color_cell_red(sheet_row, col=12)
        except:
            color_cell_red(sheet_row, col=12)
    delete_application_soft(message.from_user.id, index)
    await message.answer("Ваша заявка видалена (позначена як 'deleted').", reply_markup=get_main_menu_keyboard())
    await state.finish()


############################################
# "Підтвердити" заявку
############################################

@dp.message_handler(Text(equals="Підтвердити"), state=ApplicationStates.viewing_application)
async def confirm_proposal(message: types.Message, state: FSMContext):
    data = await state.get_data()
    index = data.get("selected_app_index")
    update_application_status(message.from_user.id, index, "confirmed")
    apps = load_applications()
    uid = str(message.from_user.id)
    app = apps[uid][index]
    sheet_row = app.get("sheet_row")
    if sheet_row:
        prop = app.get("proposal", "")
        bot_price = app.get("bot_price", None)
        try:
            float_prop = float(prop)
            if bot_price is not None and abs(float_prop - bot_price) < 1e-9:
                color_cell_green(sheet_row, col=13)
            else:
                color_cell_green(sheet_row, col=12)
        except:
            color_cell_green(sheet_row, col=12)
    save_applications(apps)
    timestamp = app.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp or "—"
    extra_fields = app.get("extra_fields", {})
    extra_list = []
    for key, value in extra_fields.items():
        ukr_name = friendly_names.get(key, key)
        extra_list.append(f"{ukr_name}: {value}")
    extra_part = ""
    if extra_list:
        extra_part = f"Додаткові параметри:\n<b>{chr(10).join(extra_list)}</b>\n"
    user_fullname = app.get("fullname", "")
    phone_from_app = app.get("phone", "")
    if not phone_from_app:
        users = load_users()
        phone_from_app = users.get("approved_users", {}).get(uid, {}).get("phone", "")
    if not phone_from_app:
        phone_from_app = "—"
    if not user_fullname:
        users = load_users()
        user_fullname = users.get("approved_users", {}).get(uid, {}).get("fullname", "—")
    user_fullname_line = f"Користувач: {user_fullname}"
    user_phone_line = f"Телефон: {phone_from_app}"
    admin_msg = (
        "<b>ЗАЯВКА ПІДТВЕРДЖЕНА</b>\n\n"
        "Повна інформація по заявці:\n"
        f"Дата створення: <b>{formatted_date}</b>\n\n"
        f"ФГ: <b>{app.get('fgh_name', 'Невідомо')}</b>\n"
        f"ЄДРПОУ: <b>{app.get('edrpou', 'Невідомо')}</b>\n"
        f"Область: <b>{app.get('region', 'Невідомо')}</b>\n"
        f"Район: <b>{app.get('district', 'Невідомо')}</b>\n"
        f"Місто: <b>{app.get('city', 'Невідомо')}</b>\n"
        f"Група: <b>{app.get('group', 'Невідомо')}</b>\n"
        f"Культура: <b>{app.get('culture', 'Невідомо')}</b>\n"
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
    await message.answer("Ви підтвердили пропозицію. Очікуйте на подальші дії від менеджера/адміністратора.",
                         reply_markup=get_main_menu_keyboard())
    await state.finish()


############################################
# Робота з WebApp
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
        from db import add_application
        add_application(user_id, message.chat.id, webapp_data)
        await state.finish()
        await message.answer("Ваша заявка прийнята!", reply_markup=get_main_menu_keyboard())
    except Exception as e:
        logging.exception(f"Помилка при збереженні заявки: {e}")
        await message.answer("Сталася помилка при збереженні. Спробуйте пізніше.", reply_markup=remove_keyboard())
        await state.finish()
