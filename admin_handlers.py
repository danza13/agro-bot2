# admin_handlers.py
import re
import logging
import asyncio
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text, Regexp

from auto_calc import save_auto_calc_setting, load_auto_calc_setting

from loader import dp, bot, pause_polling, resume_polling
from config import ADMINS, friendly_names
from states import AdminMenuStates, AdminReview
from keyboards import (
    remove_keyboard, get_admin_root_menu, get_admin_moderation_menu,
    get_admin_requests_menu, get_main_menu_keyboard
)
from db import (
    load_users, save_users, load_applications, save_applications,
    approve_user, block_user,
    update_application_status, delete_application_from_file_entirely
)
from gsheet_utils import (
    export_database, admin_remove_app_permanently,
    get_worksheet1, get_worksheet2, delete_price_cell_in_table2
)

############################################
# Функція видалення заявки з повною логікою
############################################

async def admin_remove_app_permanently(user_id: int, app_index: int):
    """
    Видаляє заявку адміністратора з файлу та з обох таблиць (worksheet1 та worksheet2).
    Призупиняє polling, видаляє заявку, видаляє рядки у таблицях з затримкою між ними,
    оновлює індекси рядків, після затримки відновлює polling.
    """
    logging.info(f"Адміністратор видаляє заявку: user_id={user_id}, app_index={app_index}")
    
    # Призупиняємо polling
    pause_polling()
    logging.info("Polling призупинено перед видаленням заявки.")
    
    apps = load_applications()
    uid = str(user_id)
    if uid not in apps or app_index < 0 or app_index >= len(apps[uid]):
        logging.error("Не знайдено заявку для видалення.")
        resume_polling()
        return False

    app = apps[uid][app_index]
    sheet_row = app.get("sheet_row")
    logging.debug(f"Заявка знаходиться у рядку: {sheet_row}")

    # Видаляємо заявку з локального файлу
    delete_application_from_file_entirely(user_id, app_index)
    logging.debug("Заявка видалена з локального файлу.")

    # Якщо визначено рядок у Google Sheets, видаляємо дані
    if sheet_row:
        try:
            # Видаляємо рядок у таблиці2 (ws2)
            ws2 = get_worksheet2()
            ws2.delete_rows(sheet_row)
            logging.debug(f"Видалено рядок {sheet_row} у таблиці2.")
            
            # Затримка 3 секунди перед видаленням у таблиці1
            await asyncio.sleep(3)
            
            # Видаляємо рядок у таблиці1 (ws1)
            ws1 = get_worksheet1()
            ws1.delete_rows(sheet_row)
            logging.debug(f"Видалено рядок {sheet_row} у таблиці1.")

            # Оновлюємо sheet_row для решти заявок (якщо рядки зміщуються)
            updated_apps = load_applications()
            for u_str, user_apps in updated_apps.items():
                for a in user_apps:
                    old_row = a.get("sheet_row", 0)
                    if old_row and old_row > sheet_row:
                        a["sheet_row"] = old_row - 1
            save_applications(updated_apps)
            logging.debug("Оновлено номери рядків для заявок після видалення.")

        except Exception as e:
            logging.exception(f"Помилка видалення рядка в Google Sheets: {e}")

    # Чекаємо 20 секунд перед відновленням polling'у
    logging.info("Чекаємо 20 секунд перед відновленням polling'у.")
    await asyncio.sleep(20)
    resume_polling()
    logging.info("Polling відновлено після видалення заявки.")
    return True


############################################
# Вхід в адмін-меню
############################################

@dp.message_handler(commands=["admin"], state="*")
async def admin_entry_point(message: types.Message, state: FSMContext):
    if str(message.from_user.id) not in ADMINS:
        await message.answer("Немає доступу.", reply_markup=remove_keyboard())
        return

    await state.finish()
    await message.answer("Ви в адмін-меню. Оберіть розділ:", reply_markup=get_admin_root_menu())
    await AdminMenuStates.choosing_section.set()


@dp.message_handler(state=AdminMenuStates.choosing_section)
async def admin_menu_choosing_section(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "Модерація":
        await message.answer("Розділ 'Модерація'. Оберіть дію:", reply_markup=get_admin_moderation_menu())
        await AdminMenuStates.moderation_section.set()
    elif text == "Заявки":
        await message.answer("Розділ 'Заявки'. Оберіть дію:", reply_markup=get_admin_requests_menu())
        await AdminMenuStates.requests_section.set()
    elif text == "Вийти з адмін-меню":
        await state.finish()
        await message.answer("Вихід з адмін-меню. Повертаємось у звичайне меню:", reply_markup=get_main_menu_keyboard())
    else:
        await message.answer("Будь ласка, оберіть із меню: «Модерація», «Заявки» або «Вийти з адмін-меню».")


############################################
# МОДЕРАЦІЯ КОРИСТУВАЧІВ
############################################

@dp.message_handler(state=AdminMenuStates.moderation_section)
async def admin_moderation_section_handler(message: types.Message, state: FSMContext):
    text = message.text.strip()

    if text == "Користувачі на модерацію":
        users_data = load_users()
        pending = users_data.get("pending_users", {})
        if not pending:
            await message.answer("Немає заявок на модерацію.", reply_markup=get_admin_moderation_menu())
            return
        
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        for uid, info in pending.items():
            kb.add(info.get("fullname", "Невідомо"))
        kb.add("Назад")
        
        await message.answer("Оберіть заявку для перегляду:", reply_markup=kb)
        await AdminReview.waiting_for_application_selection.set()
        await state.update_data(pending_dict=pending, from_moderation_menu=True)

    elif text == "База користувачів":
        users_data = load_users()
        approved = users_data.get("approved_users", {})
        if not approved:
            await message.answer("Немає схвалених користувачів.", reply_markup=get_admin_moderation_menu())
            return
        
        approved_dict = {}
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for u_id, info in approved.items():
            name = info.get("fullname", f"ID:{u_id}")
            approved_dict[name] = u_id
            row.append(name)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.row("Вивантажити базу", "Розсилка")
        kb.add("Назад")

        await state.update_data(approved_dict=approved_dict, from_moderation_menu=True)
        await message.answer("Список схвалених користувачів:", reply_markup=kb)
        await AdminReview.viewing_approved_list.set()

    elif text == "Очистити заблокованих":
        users_data = load_users()
        # Очищаємо список заблокованих
        users_data["blocked_users"] = []
        save_users(users_data)

        await message.answer(
            "Список заблокованих користувачів успішно очищено!",
            reply_markup=get_admin_moderation_menu()
        )

    elif text == "Назад":
        await message.answer("Головне меню адміна:", reply_markup=get_admin_root_menu())
        await AdminMenuStates.choosing_section.set()

    else:
        await message.answer(
            "Оберіть зі списку: «Користувачі на модерацію», «База користувачів», "
            "«Очистити заблокованих» або «Назад»."
        )

@dp.message_handler(state=AdminReview.waiting_for_application_selection)
async def admin_select_pending_application(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await message.answer("Повертаємось до розділу 'Модерація':", reply_markup=get_admin_moderation_menu())
        await AdminMenuStates.moderation_section.set()
        return

    data = await state.get_data()
    pending = data.get("pending_dict", {})
    selected_fullname = message.text.strip()
    uid = None
    for pending_uid, info in pending.items():
        if info.get("fullname", "").strip() == selected_fullname:
            uid = pending_uid
            break

    if not uid:
        await message.answer(
            "Заявку не знайдено. Спробуйте ще раз або натисніть 'Назад'.",
            reply_markup=remove_keyboard()
        )
        return

    info = pending[uid]
    from datetime import datetime
    from zoneinfo import ZoneInfo
    timestamp_str = info.get("timestamp", "")
    if timestamp_str:
        dt = datetime.fromisoformat(timestamp_str)
        dt_kyiv = dt.astimezone(ZoneInfo("Europe/Kiev"))
        formatted_timestamp = dt_kyiv.strftime("%d.%m.%Y | %H:%M:%S")
    else:
        formatted_timestamp = "Невідомо"

    text_answer = (
        f"Користувач на модерацію:\n"
        f"User ID: {uid}\n"
        f"ПІБ: {info.get('fullname', 'Невідомо')}\n"
        f"Номер: {info.get('phone', '')}\n"
        f"Дата та час: {formatted_timestamp}"
    )

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Дозволити", "Заблокувати", "Видалити")
    kb.add("Назад")

    await state.update_data(selected_uid=uid)
    await message.answer(text_answer, reply_markup=kb)
    await AdminReview.waiting_for_decision.set()


@dp.message_handler(lambda msg: msg.text in ["Дозволити", "Заблокувати", "Видалити"], state=AdminReview.waiting_for_decision)
async def admin_decision_pending_user(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("selected_uid", None)
    if not uid:
        await message.answer("Не знайдено користувача.", reply_markup=remove_keyboard())
        return

    users_data = load_users()

    # Дії залежно від натисненої кнопки
    if message.text == "Дозволити":
        approve_user(uid)
        response_text = "Користувача дозволено."

        # Надсилаємо повідомлення користувачу
        try:
            await bot.send_message(
                int(uid),
                "Вітаємо! Ви пройшли модерацію і тепер можете користуватися ботом. "
                "Для початку роботи натисніть /start у меню.",
                reply_markup=remove_keyboard()
            )
        except Exception as e:
            logging.exception(f"Не вдалося надіслати повідомлення користувачу {uid}: {e}")

    elif message.text == "Заблокувати":
        block_user(uid)
        response_text = "Користувача заблоковано."

        # Надсилаємо повідомлення користувачу
        try:
            await bot.send_message(
                int(uid),
                "На жаль, Ви не пройшли модерацію і Вас заблоковано.",
                reply_markup=remove_keyboard()
            )
        except Exception as e:
            logging.exception(f"Не вдалося надіслати повідомлення користувачу {uid}: {e}")

    elif message.text == "Видалити":
        # Лише видаляємо користувача з pending, щоб він міг знову подати заявку
        if uid in users_data.get("pending_users", {}):
            users_data["pending_users"].pop(uid)
            save_users(users_data)

        response_text = (
            "Користувача вилучено зі списку pending_users. "
            "Тепер він зможе знову подати заявку."
        )

    # Повернення в меню
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Назад")
    await message.answer(
        f"{response_text}\nНатисніть «Назад» для повернення в меню.",
        reply_markup=kb
    )
    await AdminMenuStates.moderation_section.set()

@dp.message_handler(Text(equals="Назад"), state=AdminReview.waiting_for_decision)
async def back_to_pending_list(message: types.Message, state: FSMContext):
    # 1) Повідомлення, що повертаємося
    await message.answer("Повертаємось до списку користувачів на модерацію:", 
                         reply_markup=remove_keyboard())  # або якась ваша клавіатура

    # 2) Повертаємось у стан waiting_for_application_selection
    #   (де ви показували список pending-користувачів)
    pending = (await state.get_data()).get("pending_dict", {})

    if not pending:
        # Якщо вже немає пендінг-користувачів – просто повертаємось у меню «Модерація»:
        await message.answer("Заявок на модерацію немає.", reply_markup=get_admin_moderation_menu())
        await AdminMenuStates.moderation_section.set()
    else:
        # Показуємо перелік користувачів знову
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        for uid, info in pending.items():
            kb.add(info.get("fullname", "Невідомо"))
        kb.add("Назад")
        await message.answer("Оберіть заявку для перегляду:", reply_markup=kb)
        await AdminReview.waiting_for_application_selection.set()


############################################
# База користувачів (схвалені)
############################################

@dp.message_handler(Text(equals="Вивантажити базу"), state=AdminReview.viewing_approved_list)
async def handle_export_database(message: types.Message, state: FSMContext):
    try:
        export_database()
        data = await state.get_data()
        approved_dict = data.get("approved_dict", {})
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for name in approved_dict.keys():
            row.append(name)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.add("Вивантажити базу", "Назад")
        await message.answer("База успішно вивантажена до Google Sheets.", reply_markup=kb)
    except Exception as e:
        logging.exception(f"Помилка вивантаження бази: {e}")
        data = await state.get_data()
        approved_dict = data.get("approved_dict", {})
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for name in approved_dict.keys():
            row.append(name)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.add("Вивантажити базу", "Назад")
        await message.answer("Помилка вивантаження бази.", reply_markup=kb)


@dp.message_handler(state=AdminReview.viewing_approved_list)
async def admin_view_approved_list(message: types.Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    from_moderation_menu = data.get("from_moderation_menu", False)
    approved_dict = data.get("approved_dict", {})
    # Обробка кнопки "Розсилка"
    if text == "Розсилка":
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add("Скасувати")
        await message.answer("Відправте текст для розсилки", reply_markup=kb)
        await AdminReview.sending_mass_message.set()
        return

    if text == "Назад":
        if from_moderation_menu:
            await message.answer("Повертаємось до розділу 'Модерація':", reply_markup=get_admin_moderation_menu())
            await AdminMenuStates.moderation_section.set()
        else:
            await state.finish()
            await message.answer("Головне меню адміна:", reply_markup=get_admin_root_menu())
        return
    if text not in approved_dict:
        await message.answer("Оберіть користувача зі списку або натисніть «Назад».")
        return
    user_id = approved_dict[text]
    users_data = load_users()
    approved_users = users_data.get("approved_users", {})
    if str(user_id) not in approved_users:
        await message.answer("Користувача не знайдено серед схвалених.")
        return
    info = approved_users[str(user_id)]
    fullname = info.get("fullname", "—")
    phone = info.get("phone", "—")
    details = (
        f"ПІБ: {fullname}\n"
        f"Номер телефону: {phone}\n"
        f"Телеграм ID: {user_id}"
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Редагувати", "Видалити", "Відправити повідомлення")
    kb.add("Назад")
    await state.update_data(selected_approved_user_id=str(user_id), selected_fullname=fullname)
    await AdminReview.viewing_approved_user.set()
    await message.answer(details, reply_markup=kb)


@dp.message_handler(Text(equals="Розсилка"), state=AdminReview.viewing_approved_list)
async def handle_mass_mailing_prompt(message: types.Message, state: FSMContext):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Скасувати")
    await message.answer("Відправте текст для розсилки", reply_markup=kb)
    await AdminReview.sending_mass_message.set()


@dp.message_handler(state=AdminReview.sending_mass_message)
async def process_mass_mailing(message: types.Message, state: FSMContext):
    if message.text == "Скасувати":
        data = await state.get_data()
        approved_dict = data.get("approved_dict", {})
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for name in approved_dict.keys():
            row.append(name)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.row("Вивантажити базу", "Розсилка")
        kb.add("Назад")
        await AdminReview.viewing_approved_list.set()
        await message.answer("База користувачів:", reply_markup=kb)
        return

    users_data = load_users()
    approved_users = users_data.get("approved_users", {})
    failed = []
    for uid in approved_users.keys():
        try:
            await bot.send_message(int(uid), message.text, reply_markup=remove_keyboard())
        except Exception as e:
            logging.exception(f"Не вдалося надіслати повідомлення користувачу {uid}: {e}")
            failed.append(uid)
    response = "Розсилка виконана." if not failed else f"Повідомлення не надіслано наступним користувачам: {', '.join(failed)}"
    data = await state.get_data()
    approved_dict = data.get("approved_dict", {})
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    row = []
    for name in approved_dict.keys():
        row.append(name)
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    kb.row("Вивантажити базу", "Розсилка")
    kb.add("Назад")
    await AdminReview.viewing_approved_list.set()
    await message.answer(response, reply_markup=kb)


@dp.message_handler(Text(equals="Відправити повідомлення"), state=AdminReview.viewing_approved_user)
async def handle_send_private_message_prompt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fullname = data.get("selected_fullname", "користувачу")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Скасувати")
    await message.answer(f"Введіть текст для відправки повідомлення користувачу {fullname}", reply_markup=kb)
    await AdminReview.sending_private_message.set()


@dp.message_handler(state=AdminReview.sending_private_message)
async def process_send_private_message(message: types.Message, state: FSMContext):
    if message.text == "Скасувати":
        data = await state.get_data()
        user_id_str = data.get("selected_approved_user_id")
        users_data = load_users()
        info = users_data.get("approved_users", {}).get(user_id_str, {})
        fullname = info.get("fullname", "—")
        phone = info.get("phone", "—")
        details = (
            f"ПІБ: {fullname}\n"
            f"Номер телефону: {phone}\n"
            f"Телеграм ID: {user_id_str}"
        )
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("Редагувати", "Видалити", "Відправити повідомлення")
        kb.add("Назад")
        await AdminReview.viewing_approved_user.set()
        await message.answer(details, reply_markup=kb)
        return

    user_id_str = (await state.get_data()).get("selected_approved_user_id")
    try:
        await bot.send_message(int(user_id_str), message.text, reply_markup=remove_keyboard())
        response = "Повідомлення відправлено."
    except Exception as e:
        logging.exception(f"Не вдалося надіслати повідомлення користувачу {user_id_str}: {e}")
        response = "Помилка відправлення повідомлення."
    users_data = load_users()
    info = users_data.get("approved_users", {}).get(user_id_str, {})
    fullname = info.get("fullname", "—")
    phone = info.get("phone", "—")
    details = (
        f"ПІБ: {fullname}\n"
        f"Номер телефону: {phone}\n"
        f"Телеграм ID: {user_id_str}"
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Редагувати", "Видалити", "Відправити повідомлення")
    kb.add("Назад")
    await AdminReview.viewing_approved_user.set()
    await message.answer(response + "\n" + details, reply_markup=kb)


@dp.message_handler(state=AdminReview.viewing_approved_user)
async def admin_view_approved_single_user(message: types.Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    user_id_str = data.get("selected_approved_user_id", None)
    if not user_id_str:
        await message.answer("Немає вибраного користувача.", reply_markup=get_admin_moderation_menu())
        return

    # Кнопка "Назад"
    if text == "Назад":
        users_data = load_users()
        approved = users_data.get("approved_users", {})
        if not approved:
            await message.answer("Наразі немає схвалених користувачів.", reply_markup=get_admin_moderation_menu())
            await AdminMenuStates.moderation_section.set()
            return
        # Повертаємо список знову
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for uid, info in approved.items():
            fname = info.get("fullname", f"ID:{uid}")
            row.append(fname)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.row("Вивантажити базу", "Розсилка")
        kb.add("Назад")

        await message.answer("Список схвалених користувачів:", reply_markup=kb)
        await AdminReview.viewing_approved_list.set()
        return

    # Кнопка "Редагувати"
    elif text == "Редагувати":
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("Змінити ПІБ", "Змінити номер телефону")
        kb.add("Назад")
        await message.answer("Оберіть, що бажаєте змінити:", reply_markup=kb)
        await AdminReview.editing_approved_user.set()

    # Кнопка "Видалити" (повністю видаляє користувача з users.json)
    elif text == "Видалити":
        users_data = load_users()
        uid = str(user_id_str)
        # Прибираємо з усіх списків: approved_users, pending_users, blocked_users
        if uid in users_data.get("approved_users", {}):
            users_data["approved_users"].pop(uid)
        if uid in users_data.get("pending_users", {}):
            users_data["pending_users"].pop(uid)
        if uid in users_data.get("blocked_users", []):
            users_data["blocked_users"].remove(uid)

        save_users(users_data)
        await message.answer(
            "Користувача повністю видалено з бази (усіх списків).",
            reply_markup=get_admin_moderation_menu()
        )
        await AdminMenuStates.moderation_section.set()

    # Кнопка "Заблокувати"
    elif text == "Заблокувати":
        block_user(user_id_str)
        await message.answer(
            "Користувача перенесено у заблоковані.",
            reply_markup=get_admin_moderation_menu()
        )
        await AdminMenuStates.moderation_section.set()

    # Кнопка "Відправити повідомлення"
    elif text == "Відправити повідомлення":
        fullname = data.get("selected_fullname", "користувачу")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add("Скасувати")
        await message.answer(f"Введіть текст для відправки повідомлення користувачу {fullname}", reply_markup=kb)
        await AdminReview.sending_private_message.set()

    else:
        await message.answer("Оберіть: «Редагувати», «Видалити», «Заблокувати», «Відправити повідомлення» або «Назад».")


@dp.message_handler(state=AdminReview.editing_approved_user)
async def admin_edit_approved_user_menu(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "Назад":
        data = await state.get_data()
        user_id_str = data.get("selected_approved_user_id", None)
        if user_id_str is None:
            await message.answer("Немає користувача. Повернення.", reply_markup=get_admin_moderation_menu())
            await AdminMenuStates.moderation_section.set()
            return
        users_data = load_users()
        user_info = users_data.get("approved_users", {}).get(user_id_str, {})
        fullname = user_info.get("fullname", "—")
        phone = user_info.get("phone", "—")
        details = (
            f"ПІБ: {fullname}\n"
            f"Номер телефону: {phone}\n"
            f"Телеграм ID: {user_id_str}"
        )
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("Редагувати", "Видалити")
        kb.add("Назад")
        await AdminReview.viewing_approved_user.set()
        await message.answer(details, reply_markup=kb)
        return
    elif text == "Змінити ПІБ":
        await AdminReview.editing_approved_user_fullname.set()
        await message.answer("Введіть новий ПІБ:", reply_markup=remove_keyboard())
    elif text == "Змінити номер телефону":
        await AdminReview.editing_approved_user_phone.set()
        await message.answer("Введіть новий номер телефону у форматі +380XXXXXXXXX:", reply_markup=remove_keyboard())
    else:
        await message.answer("Оберіть 'Змінити ПІБ', 'Змінити номер телефону' або 'Назад'.")


@dp.message_handler(state=AdminReview.editing_approved_user_fullname)
async def admin_edit_approved_user_fullname(message: types.Message, state: FSMContext):
    new_fullname = message.text.strip()
    if not new_fullname:
        await message.answer("ПІБ не може бути порожнім. Спробуйте ще раз.")
        return
    data = await state.get_data()
    user_id_str = data.get("selected_approved_user_id", None)
    if user_id_str is None:
        await message.answer("Немає користувача для редагування.", reply_markup=get_admin_moderation_menu())
        await AdminMenuStates.moderation_section.set()
        return
    users_data = load_users()
    if user_id_str not in users_data.get("approved_users", {}):
        await message.answer("Користувача не знайдено в approved_users.", reply_markup=get_admin_moderation_menu())
        await AdminMenuStates.moderation_section.set()
        return
    users_data["approved_users"][user_id_str]["fullname"] = new_fullname
    save_users(users_data)
    await message.answer("ПІБ успішно змінено!", reply_markup=remove_keyboard())
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Змінити ПІБ", "Змінити номер телефону")
    kb.add("Назад")
    await AdminReview.editing_approved_user.set()
    await message.answer("Оновлено! Оберіть наступну дію:", reply_markup=kb)


@dp.message_handler(state=AdminReview.editing_approved_user_phone)
async def admin_edit_approved_user_phone(message: types.Message, state: FSMContext):
    new_phone = re.sub(r"[^\d+]", "", message.text.strip())
    if not re.fullmatch(r"\+380\d{9}", new_phone):
        await message.answer("Невірний формат. Введіть номер у форматі +380XXXXXXXXX або «Назад» для відміни.")
        return
    data = await state.get_data()
    user_id_str = data.get("selected_approved_user_id", None)
    if user_id_str is None:
        await message.answer("Немає користувача для редагування.", reply_markup=get_admin_moderation_menu())
        await AdminMenuStates.moderation_section.set()
        return
    users_data = load_users()
    if user_id_str not in users_data.get("approved_users", {}):
        await message.answer("Користувача не знайдено в approved_users.", reply_markup=get_admin_moderation_menu())
        await AdminMenuStates.moderation_section.set()
        return
    users_data["approved_users"][user_id_str]["phone"] = new_phone
    save_users(users_data)
    await message.answer("Номер телефону успішно змінено!", reply_markup=remove_keyboard())
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Змінити ПІБ", "Змінити номер телефону")
    kb.add("Назад")
    await AdminReview.editing_approved_user.set()
    await message.answer("Оновлено! Оберіть наступну дію:", reply_markup=kb)


############################################
# Розділ "Заявки"
############################################

@dp.message_handler(state=AdminMenuStates.requests_section)
async def admin_requests_section_handler(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "Підтверджені":
        apps = load_applications()
        confirmed_apps = []
        for user_id, user_applications in apps.items():
            for idx, app_data in enumerate(user_applications):
                if app_data.get("proposal_status") == "confirmed":
                    confirmed_apps.append({
                        "user_id": user_id,
                        "app_index": idx,
                        "app_data": app_data
                    })
        if not confirmed_apps:
            await message.answer("Немає підтверджених заявок.", reply_markup=get_admin_requests_menu())
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for i, entry in enumerate(confirmed_apps, start=1):
            culture = entry["app_data"].get("culture", "Невідомо")
            quantity = entry["app_data"].get("quantity", "Невідомо")
            btn_text = f"{i}. {culture} | {quantity}"
            row.append(btn_text)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.add("Назад")
        await state.update_data(confirmed_apps=confirmed_apps, from_requests_menu=True)
        await AdminReview.viewing_confirmed_list.set()
        await message.answer("Список підтверджених заявок:", reply_markup=kb)

    elif text == "Видалені":
        apps = load_applications()
        deleted_apps = []
        for user_id, user_applications in apps.items():
            for idx, app_data in enumerate(user_applications):
                if app_data.get("proposal_status") == "deleted":
                    deleted_apps.append({
                        "user_id": user_id,
                        "app_index": idx,
                        "app_data": app_data
                    })
        if not deleted_apps:
            await message.answer("Немає видалених заявок.", reply_markup=get_admin_requests_menu())
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for i, entry in enumerate(deleted_apps, start=1):
            culture = entry["app_data"].get("culture", "Невідомо")
            quantity = entry["app_data"].get("quantity", "Невідомо")
            btn_text = f"{i}. {culture} | {quantity}"
            row.append(btn_text)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.add("Назад")
        await state.update_data(deleted_apps=deleted_apps, from_requests_menu=True)
        await AdminReview.viewing_deleted_list.set()
        await message.answer("Список «видалених» заявок:", reply_markup=kb)

    elif text == "Видалення заявок":
        try:
            ws = get_worksheet1()
            rows = ws.get_all_values()
            if len(rows) <= 1:
                await message.answer("У таблиці немає заявок.", reply_markup=get_admin_requests_menu())
                return
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            temp_row = []
            for i, row in enumerate(rows[1:], start=2):
                if row and row[0].strip():
                    request_number = row[0].strip()
                    btn_text = f"{request_number} (рядок {i})"
                    temp_row.append(btn_text)
                    if len(temp_row) == 3:
                        kb.row(*temp_row)
                        temp_row = []
            if temp_row:
                kb.row(*temp_row)
            kb.add("Назад")
            await message.answer("Оберіть заявку для видалення:", reply_markup=kb)
            await AdminReview.confirm_deletion_app.set()
        except Exception as e:
            logging.exception("Помилка отримання заявок з Google Sheets")
            await message.answer("Помилка отримання заявок.", reply_markup=get_admin_requests_menu())

    elif text == "Редагування заявок":
        apps = load_applications()
        users_with_active_apps = {}
        for uid, user_apps in apps.items():
            active_apps = [app for app in user_apps if app.get("proposal_status") == "active"]
            if active_apps:
                user_info = load_users().get("approved_users", {}).get(uid, {})
                display_name = user_info.get("fullname", f"User {uid}")
                users_with_active_apps[display_name] = uid
        if not users_with_active_apps:
            await message.answer("Немає користувачів з активними заявками.", reply_markup=get_admin_requests_menu())
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        for name in users_with_active_apps.keys():
            kb.add(name)
        kb.add("Назад")
        await state.update_data(editing_users=users_with_active_apps)
        await message.answer("Оберіть користувача для редагування заявок:", reply_markup=kb)
        await AdminReview.editing_applications_list.set()

    # Ось тут додаємо пункт «Ціна бота»:
    elif text == "Ціна бота":
        # Імпортуємо глобальну змінну з bot.py:
        from auto_calc import load_auto_calc_setting
        AUTO_CALC_ENABLED = load_auto_calc_setting()
        status_text = "Увімкнена" if AUTO_CALC_ENABLED else "Вимкнена"
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        # Додаємо кнопку залежно від стану
        if AUTO_CALC_ENABLED:
            kb.add("Вимкнути автопрайс")
        else:
            kb.add("Увімкнути автопрайс")
        kb.add("Назад")
        await message.answer(
            f"Функція автоматичного прорахунку ціни ботом: {status_text}",
            reply_markup=kb
        )
        # Переходимо в стан auto_price_section
        await AdminReview.auto_price_section.set()

    elif text == "Назад":
        await message.answer("Головне меню адміна:", reply_markup=get_admin_root_menu())
        await AdminMenuStates.choosing_section.set()

    else:
        await message.answer("Оберіть дію: «Підтверджені», «Видалені», «Редагування заявок», «Видалення заявок» або «Назад».")


############################################
# Видалення заявки (вручну з таблиці)
############################################

@dp.message_handler(lambda message: re.match(r"^\d+\s\(рядок\s\d+\)$", message.text), state=AdminReview.confirm_deletion_app)
async def handle_delete_application_selection(message: types.Message, state: FSMContext):
    text = message.text.strip()
    match = re.search(r"\(рядок\s(\d+)\)$", text)
    if not match:
        await message.answer("Невірний формат вибору.", reply_markup=get_admin_requests_menu())
        return
    row_number = int(match.group(1))
    apps = load_applications()
    found = False
    for uid, app_list in apps.items():
        for idx, app in enumerate(app_list):
            if app.get("sheet_row") == row_number:
                await state.update_data(
                    deletion_uid=uid,
                    deletion_app_index=idx,
                    deletion_row_number=row_number
                )
                kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                kb.add("Видалити назавжди", "Назад")
                await message.answer(f"Ви обрали заявку з рядка {row_number}.\nОберіть дію:", reply_markup=kb)
                found = True
                break
        if found:
            break
    if not found:
        await message.answer("Заявку не знайдено.", reply_markup=get_admin_requests_menu())


@dp.message_handler(Text(equals="Назад"), state=AdminReview.confirm_deletion_app)
async def confirm_deletion_cancel(message: types.Message, state: FSMContext):
    data = await state.get_data()
    from_requests_menu = data.get("from_requests_menu", False)
    if from_requests_menu:
        await message.answer("Повертаємось до розділу 'Заявки':", reply_markup=get_admin_requests_menu())
        await AdminMenuStates.requests_section.set()
    else:
        await state.finish()
        await message.answer("Головне меню адміна:", reply_markup=get_admin_root_menu())


@dp.message_handler(Text(equals="Видалити назавжди"), state=AdminReview.confirm_deletion_app)
async def confirm_deletion_yes(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("deletion_uid")
    app_index = data.get("deletion_app_index")
    row_number = data.get("deletion_row_number")
    if uid is None or app_index is None:
        await message.answer("Інформацію про заявку не знайдено.", reply_markup=get_admin_requests_menu())
        await state.finish()
        return
    success = await admin_remove_app_permanently(int(uid), app_index)
    if success:
        apps = load_applications()
        deleted_apps = []
        for user_id, user_apps in apps.items():
            for idx, app_data in enumerate(user_apps):
                if app_data.get("proposal_status") == "deleted":
                    deleted_apps.append({
                        "user_id": user_id,
                        "app_index": idx,
                        "app_data": app_data
                    })
        if deleted_apps:
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            temp_row = []
            for i, entry in enumerate(deleted_apps, start=1):
                culture = entry["app_data"].get("culture", "Невідомо")
                quantity = entry["app_data"].get("quantity", "Невідомо")
                btn_text = f"{i}. {culture} | {quantity}"
                temp_row.append(btn_text)
                if len(temp_row) == 3:
                    kb.row(*temp_row)
                    temp_row = []
            if temp_row:
                kb.row(*temp_row)
            kb.add("Назад")
            await state.update_data(deleted_apps=deleted_apps, from_requests_menu=True)
            await message.answer(
                f"Заявку з рядка {row_number} успішно видалено.\nОберіть заявку зі списку для подальших дій:",
                reply_markup=kb
            )
        else:
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.add("Назад")
            await message.answer(
                f"Заявку з рядка {row_number} успішно видалено.\nБільше немає заявок, позначених як 'видалені'.",
                reply_markup=kb
            )
        await AdminReview.viewing_deleted_list.set()
    else:
        await message.answer("Помилка видалення заявки.", reply_markup=get_admin_requests_menu())
        await AdminMenuStates.requests_section.set()


############################################
# Перегляд "Підтверджених" заявок
############################################

@dp.message_handler(state=AdminReview.viewing_confirmed_list)
async def admin_view_confirmed_list_choice(message: types.Message, state: FSMContext):
    data = await state.get_data()
    confirmed_apps = data.get("confirmed_apps", [])
    from_requests_menu = data.get("from_requests_menu", False)
    if message.text == "Назад":
        if from_requests_menu:
            await message.answer("Розділ 'Заявки':", reply_markup=get_admin_requests_menu())
            await AdminMenuStates.requests_section.set()
        else:
            await state.finish()
            await message.answer("Адмін меню:", reply_markup=get_admin_root_menu())
        return
    split_msg = message.text.split('.', 1)
    if len(split_msg) < 2 or not split_msg[0].isdigit():
        await message.answer("Оберіть номер заявки у форматі 'X. Культура | Кількість' або натисніть 'Назад'.")
        return
    choice = int(split_msg[0])
    if choice < 1 or choice > len(confirmed_apps):
        await message.answer("Невірний вибір.", reply_markup=remove_keyboard())
        return
    selected_entry = confirmed_apps[choice - 1]
    app_data = selected_entry["app_data"]
    timestamp = app_data.get("timestamp", "")
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp
    details = [
        "<b>ЗАЯВКА ПІДТВЕРДЖЕНА:</b>",
        f"Дата створення: <b>{formatted_date}</b>",
        f"ФГ: <b>{app_data.get('fgh_name', '')}</b>",
        f"ЄДРПОУ: <b>{app_data.get('edrpou', '')}</b>",
        f"Область: <b>{app_data.get('region', '')}</b>",
        f"Номер ФГ: {app_data.get('phone', '')}",
        f"Район: <b>{app_data.get('district', '')}</b>",
        f"Місто: <b>{app_data.get('city', '')}</b>",
        f"Група: <b>{app_data.get('group', '')}</b>",
        f"Культура: <b>{app_data.get('culture', '')}</b>",
        f"Кількість: <b>{app_data.get('quantity', '')} т</b>",
        f"Форма оплати: <b>{app_data.get('payment_form', '')}</b>",
        f"Валюта: <b>{app_data.get('currency', '')}</b>",
        f"Бажана ціна: <b>{app_data.get('price', '')}</b>",
        f"Пропозиція ціни: <b>{app_data.get('proposal', '')}</b>",
    ]
    extra = app_data.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Видалити", "Назад")
    await state.update_data(selected_confirmed=selected_entry, chosen_confirmed_index=choice - 1)
    await AdminReview.viewing_confirmed_app.set()
    await message.answer("\n".join(details), parse_mode="HTML", reply_markup=kb)


@dp.message_handler(state=AdminReview.viewing_confirmed_app)
async def admin_view_confirmed_app_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    selected_entry = data.get("selected_confirmed")
    confirmed_apps = data.get("confirmed_apps", [])
    chosen_index = data.get("chosen_confirmed_index")
    if not selected_entry or chosen_index is None:
        await message.answer("Немає заявки для опрацювання.", reply_markup=get_admin_requests_menu())
        await state.finish()
        return
    if message.text == "Назад":
        if not confirmed_apps:
            await state.finish()
            await message.answer("Список підтверджених заявок тепер порожній.", reply_markup=get_admin_requests_menu())
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for i, entry in enumerate(confirmed_apps, start=1):
            culture = entry["app_data"].get("culture", "Невідомо")
            quantity = entry["app_data"].get("quantity", "Невідомо")
            btn_text = f"{i}. {culture} | {quantity}"
            row.append(btn_text)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.add("Назад")
        await AdminReview.viewing_confirmed_list.set()
        await message.answer("Список підтверджених заявок:", reply_markup=kb)
        return
    elif message.text == "Видалити":
        user_id = int(selected_entry["user_id"])
        app_index = selected_entry["app_index"]
        update_application_status(user_id, app_index, "deleted")
        if 0 <= chosen_index < len(confirmed_apps):
            confirmed_apps.pop(chosen_index)
        await state.update_data(confirmed_apps=confirmed_apps, selected_confirmed=None, chosen_confirmed_index=None)
        await message.answer("Заявка перенесена у 'видалені'.", reply_markup=get_admin_requests_menu())
        await AdminMenuStates.requests_section.set()
    else:
        await message.answer("Оберіть «Видалити» або «Назад».")


############################################
# Перегляд "Видалених"
############################################

@dp.message_handler(state=AdminReview.viewing_deleted_list)
async def admin_view_deleted_list_choice(message: types.Message, state: FSMContext):
    data = await state.get_data()
    deleted_apps = data.get("deleted_apps", [])
    from_requests_menu = data.get("from_requests_menu", False)
    if message.text == "Назад":
        if from_requests_menu:
            await message.answer("Розділ 'Заявки':", reply_markup=get_admin_requests_menu())
            await AdminMenuStates.requests_section.set()
        else:
            await state.finish()
            await message.answer("Адмін меню:", reply_markup=get_admin_root_menu())
        return
    split_msg = message.text.split('.', 1)
    if len(split_msg) < 2 or not split_msg[0].isdigit():
        await message.answer("Оберіть номер заявки у форматі 'X. Культура | Кількість' або натисніть 'Назад'.")
        return
    choice = int(split_msg[0])
    if choice < 1 or choice > len(deleted_apps):
        await message.answer("Невірний вибір.", reply_markup=remove_keyboard())
        return
    selected_entry = deleted_apps[choice - 1]
    app_data = selected_entry["app_data"]
    timestamp = app_data.get("timestamp", "")
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(timestamp)
        formatted_date = dt.strftime("%d.%m.%Y")
    except:
        formatted_date = timestamp
    details = [
        "<b>«ВИДАЛЕНА» ЗАЯВКА:</b>",
        f"Дата створення: <b>{formatted_date}</b>",
        f"ФГ: <b>{app_data.get('fgh_name', '')}</b>",
        f"ЄДРПОУ: <b>{app_data.get('edrpou', '')}</b>",
        f"Номер ФГ: {app_data.get('phone', '')}",
        f"Область: <b>{app_data.get('region', '')}</b>",
        f"Район: <b>{app_data.get('district', '')}</b>",
        f"Місто: <b>{app_data.get('city', '')}</b>",
        f"Група: <b>{app_data.get('group', '')}</b>",
        f"Культура: <b>{app_data.get('culture', '')}</b>",
        f"Кількість: <b>{app_data.get('quantity', '')} т</b>",
        f"Форма оплати: <b>{app_data.get('payment_form', '')}</b>",
        f"Валюта: <b>{app_data.get('currency', '')}</b>",
        f"Бажана ціна: <b>{app_data.get('price', '')}</b>",
        f"Пропозиція ціни: <b>{app_data.get('proposal', '')}</b>",
        "\nЦя заявка позначена як «deleted»."
    ]
    extra = app_data.get("extra_fields", {})
    if extra:
        details.append("Додаткові параметри:")
        for key, value in extra.items():
            details.append(f"{friendly_names.get(key, key.capitalize())}: {value}")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Видалити назавжди", "Назад")
    await state.update_data(selected_deleted=selected_entry, chosen_deleted_index=choice - 1)
    await AdminReview.viewing_deleted_app.set()
    await message.answer("\n".join(details), parse_mode="HTML", reply_markup=kb)


@dp.message_handler(state=AdminReview.viewing_deleted_app)
async def admin_view_deleted_app_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    selected_entry = data.get("selected_deleted")
    deleted_apps = data.get("deleted_apps", [])
    chosen_index = data.get("chosen_deleted_index")
    if not selected_entry or chosen_index is None:
        await message.answer("Немає заявки для опрацювання.", reply_markup=get_admin_requests_menu())
        await state.finish()
        return
    if message.text == "Назад":
        if not deleted_apps:
            await message.answer("Список видалених заявок порожній.", reply_markup=get_admin_requests_menu())
            await AdminMenuStates.requests_section.set()
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row = []
        for i, entry in enumerate(deleted_apps, start=1):
            culture = entry["app_data"].get("culture", "Невідомо")
            quantity = entry["app_data"].get("quantity", "Невідомо")
            btn_text = f"{i}. {culture} | {quantity}"
            row.append(btn_text)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.add("Назад")
        await AdminReview.viewing_deleted_list.set()
        await message.answer("Список видалених заявок:", reply_markup=kb)
        return
    elif message.text == "Видалити назавжди":
        user_id = int(selected_entry["user_id"])
        app_index = selected_entry["app_index"]
        success = await admin_remove_app_permanently(user_id, app_index)
        if success:
            if 0 <= chosen_index < len(deleted_apps):
                deleted_apps.pop(chosen_index)
            await state.update_data(deleted_apps=deleted_apps, selected_deleted=None, chosen_deleted_index=None)
            await message.answer("Заявку остаточно видалено з файлу та таблиць.", reply_markup=get_admin_requests_menu())
        else:
            await message.answer("Помилка: Заявка не знайдена або вже була видалена.", reply_markup=get_admin_requests_menu())
        await AdminMenuStates.requests_section.set()
    else:
        await message.answer("Оберіть «Видалити назавжди» або «Назад».")


############################################
# РЕДАГУВАННЯ ЗАЯВОК
############################################

@dp.message_handler(Text(equals="Редагування заявок"), state=AdminMenuStates.requests_section)
async def handle_editing_applications(message: types.Message, state: FSMContext):
    apps = load_applications()
    users_with_active_apps = {}
    for uid, user_apps in apps.items():
        active_apps = [app for app in user_apps if app.get("proposal_status") == "active"]
        if active_apps:
            user_info = load_users().get("approved_users", {}).get(uid, {})
            display_name = user_info.get("fullname", f"User {uid}")
            users_with_active_apps[display_name] = uid
    if not users_with_active_apps:
        await message.answer("Немає користувачів з активними заявками.", reply_markup=get_admin_requests_menu())
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for name in users_with_active_apps.keys():
        kb.add(name)
    kb.add("Назад")
    await state.update_data(editing_users=users_with_active_apps)
    await message.answer("Оберіть користувача для редагування заявок:", reply_markup=kb)
    await AdminReview.editing_applications_list.set()


@dp.message_handler(state=AdminReview.editing_applications_list)
async def admin_select_user_for_editing(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await message.answer("Повертаємось до меню заявок.", reply_markup=get_admin_requests_menu())
        await AdminMenuStates.requests_section.set()
        return
    data = await state.get_data()
    editing_users = data.get("editing_users", {})
    uid = editing_users.get(message.text)
    if not uid:
        await message.answer("Будь ласка, оберіть користувача зі списку або натисніть 'Назад'.")
        return
    user_apps = load_applications().get(uid, [])
    if not user_apps:
        await message.answer("Для цього користувача немає заявок.", reply_markup=get_admin_requests_menu())
        await AdminMenuStates.requests_section.set()
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for i, app in enumerate(user_apps, start=1):
        culture = app.get("culture", "Невідомо")
        quantity = app.get("quantity", "Невідомо")
        btn_text = f"{i}. {culture} | {quantity} т"
        kb.add(btn_text)
    kb.add("Назад")
    await state.update_data(editing_uid=uid)
    await message.answer("Список заявок користувача:", reply_markup=kb)
    await AdminReview.editing_single_application.set()


@dp.message_handler(Regexp(r"^\d+\.\s.+\s\|\s.+\sт$"), state=AdminReview.editing_single_application)
async def admin_select_application_for_editing(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("editing_uid")
    if not uid:
        await message.answer("Помилка даних.", reply_markup=get_admin_requests_menu())
        await state.finish()
        return
    user_apps = load_applications().get(uid, [])
    match = re.match(r"^(\d+)\.", message.text.strip())
    if not match:
        await message.answer("Невірний формат. Спробуйте ще раз.")
        return
    index = int(match.group(1)) - 1
    if index < 0 or index >= len(user_apps):
        await message.answer("Невірний вибір заявки.")
        return
    selected_app = user_apps[index]
    details = [
        "<b>Повна інформація по заявці:</b>",
        f"Дата: {selected_app.get('timestamp', 'Невідомо')}",
        f"ФГ: {selected_app.get('fgh_name', '')}",
        f"ЄДРПОУ: {selected_app.get('edrpou', '')}",
        f"Номер ФГ: {selected_app.get('phone', '')}",
        f"Область: {selected_app.get('region', '')}",
        f"Район: {selected_app.get('district', '')}",
        f"Місто: {selected_app.get('city', '')}",
        f"Група: {selected_app.get('group', '')}",
        f"Культура: {selected_app.get('culture', '')}",
        f"Кількість: {selected_app.get('quantity', '')}",
        f"Форма оплати: {selected_app.get('payment_form', '')}",
        f"Валюта: {selected_app.get('currency', '')}",
        f"Бажана ціна: {selected_app.get('price', '')}",
        f"Пропозиція: {selected_app.get('proposal', '—')}",
        f"Поточний статус: {selected_app.get('proposal_status', '')}"
    ]
    await state.update_data(editing_app_index=index)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Активна", "Видалена", "Підтверджена")
    kb.add("Назад")
    details.append("\nЯкий статус призначити заявці?")
    await message.answer("\n".join(details), parse_mode="HTML", reply_markup=kb)
    await AdminReview.select_new_status.set()


@dp.message_handler(lambda message: message.text in ["Активна", "Видалена", "Підтверджена"], state=AdminReview.select_new_status)
async def update_app_status_via_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("editing_uid")
    app_index = data.get("editing_app_index")
    if uid is None or app_index is None:
        await message.answer("Помилка даних.", reply_markup=get_admin_requests_menu())
        await state.finish()
        return
    status_map = {
        "Активна": "active",
        "Видалена": "deleted",
        "Підтверджена": "confirmed"
    }
    new_status = status_map.get(message.text, "")
    update_application_status(int(uid), app_index, new_status)
    await message.answer(f"Статус заявки оновлено на '{message.text}'.", reply_markup=get_admin_requests_menu())
    await AdminMenuStates.requests_section.set()
    await state.finish()


@dp.message_handler(Text(equals="Назад"), state=AdminReview.select_new_status)
async def editing_app_status_back(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("editing_uid")
    if not uid:
        await message.answer("Помилка даних.", reply_markup=get_admin_requests_menu())
        await state.finish()
        return
    user_apps = load_applications().get(uid, [])
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for i, app in enumerate(user_apps, start=1):
        culture = app.get("culture", "Невідомо")
        quantity = app.get("quantity", "Невідомо")
        btn_text = f"{i}. {culture} | {quantity} т"
        kb.add(btn_text)
    kb.add("Назад")
    await message.answer("Список заявок користувача:", reply_markup=kb)
    await AdminReview.editing_single_application.set()


############################################
# УПРАВЛІННЯ «ЦІНОЮ БОТА»
############################################

@dp.message_handler(lambda m: m.text in ["Увімкнути автопрайс", "Вимкнути автопрайс"], state=AdminReview.auto_price_section)
async def toggle_auto_price(message: types.Message, state: FSMContext):
    # Визначаємо новий стан залежно від отриманого тексту
    new_status = True if message.text == "Увімкнути автопрайс" else False
    # Записуємо новий стан у файл
    save_auto_calc_setting(new_status)
    
    status_text = "Увімкнена" if new_status else "Вимкнена"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if new_status:
        kb.add("Вимкнути автопрайс")
    else:
        kb.add("Увімкнути автопрайс")
    kb.add("Назад")
    await message.answer(
        f"Функція автоматичного прорахунку ціни ботом: {status_text}",
        reply_markup=kb
    )


@dp.message_handler(Text(equals="Назад"), state=AdminReview.auto_price_section)
async def back_to_requests_section(message: types.Message, state: FSMContext):
    # Повертаємось до розділу 'Заявки'
    await message.answer("Розділ 'Заявки':", reply_markup=get_admin_requests_menu())
    await AdminMenuStates.requests_section.set()
