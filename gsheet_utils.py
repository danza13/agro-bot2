#gsheet_utils.py
import logging
from datetime import datetime
import requests
import json

import gspread
from config import (
    gspread_creds_dict, GOOGLE_SPREADSHEET_ID, SHEET1_NAME,
    GOOGLE_SPREADSHEET_ID2, SHEET2_NAME, SHEET2_NAME_2,
    friendly_names, GOOGLE_MAPS_API_KEY,
    ODESSA_LAT, ODESSA_LNG
)
from oauth2client.service_account import ServiceAccountCredentials
from gspread_formatting import (
    format_cell_range, cellFormat, Color, set_column_width, CellFormat, TextFormat
)
from gspread.utils import rowcol_to_a1

from db import load_applications, save_applications, load_users

############################################
# Ініціалізація gspread
############################################

############################################
# Ініціалізація gspread
############################################

def init_gspread():
    logging.debug("Ініціалізація gspread...")
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(gspread_creds_dict, scope)
        client = gspread.authorize(creds)
        logging.debug("gspread ініціалізовано успішно.")
        return client
    except Exception as e:
        logging.exception(f"Помилка ініціалізації gspread: {e}")
        raise

def get_worksheet1():
    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
    ws = sheet.worksheet(SHEET1_NAME)
    logging.debug(f"Отримано worksheet1: {SHEET1_NAME}")
    return ws

def get_worksheet2():
    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID2)
    ws = sheet.worksheet(SHEET2_NAME)
    logging.debug(f"Отримано worksheet2: {SHEET2_NAME}")
    return ws

def get_worksheet2_2():
    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID2)
    ws = sheet.worksheet(SHEET2_NAME_2)
    logging.debug(f"Отримано worksheet2_2: {SHEET2_NAME_2}")
    return ws

def ensure_columns(ws, required_col: int):
    logging.debug(f"Перевірка кількості стовпців, потрібно: {required_col}, фактично: {ws.col_count}")
    if ws.col_count < required_col:
        ws.resize(rows=ws.row_count, cols=required_col)
        logging.debug("Виконано зміну розміру таблиці для забезпечення потрібної кількості стовпців.")

############################################
# Форматування клітинок
############################################

red_format = cellFormat(backgroundColor=Color(1, 0.8, 0.8))
green_format = cellFormat(backgroundColor=Color(0.8, 1, 0.8))
yellow_format = cellFormat(backgroundColor=Color(1, 1, 0.8))

def color_price_cell_in_table2(row: int, fmt: cellFormat, col: int = 12):
    logging.debug(f"Застосування форматування до клітинки (рядок {row}, стовпець {col})")
    ws2 = get_worksheet2()
    cell_range = f"{rowcol_to_a1(row, col)}:{rowcol_to_a1(row, col)}"
    format_cell_range(ws2, cell_range, fmt)

def color_cell_red(row: int, col: int = 12):
    logging.debug(f"Зафарбування клітинки червоним (рядок {row}, стовпець {col})")
    color_price_cell_in_table2(row, red_format, col)

def color_cell_green(row: int, col: int = 12):
    logging.debug(f"Зафарбування клітинки зеленим (рядок {row}, стовпець {col})")
    color_price_cell_in_table2(row, green_format, col)

def color_cell_yellow(row: int, col: int = 12):
    logging.debug(f"Зафарбування клітинки жовтим (рядок {row}, стовпець {col})")
    color_price_cell_in_table2(row, yellow_format, col)

def delete_price_cell_in_table2(row: int, col: int = 12):
    logging.debug(f"Видалення значення та форматування клітинки (рядок {row}, стовпець {col}) у таблиці2")
    ws2 = get_worksheet2()
    format_cell_range(
        ws2,
        f"{rowcol_to_a1(row, col)}:{rowcol_to_a1(ws2.row_count, col)}",
        cellFormat(backgroundColor=Color(1, 1, 1))
    )
    col_values = ws2.col_values(col)
    if row - 1 >= len(col_values):
        logging.warning("Рядок для видалення перевищує кількість заповнених рядків.")
        return
    col_values.pop(row - 1)
    for i in range(row - 1, len(col_values)):
        ws2.update_cell(i + 1, col, col_values[i])
    last_row_to_clear = len(col_values) + 1
    ws2.update_cell(last_row_to_clear, col, "")
    logging.debug("Видалення клітинки завершено.")

def color_entire_row_green(ws, row: int):
    """
    Застосовує зелений фон до всього рядка у вказаному аркуші (ws).
    Форматування встановлюється для діапазону від першої до останньої клітинки рядка.
    При цьому, якщо дані зміщуються (наприклад, після видалення рядка),
    форматування також «пересувається» разом із рядком.
    """
    # Отримуємо загальну кількість колонок на аркуші
    total_columns = ws.col_count
    # Обчислюємо адресу останньої клітинки (наприклад, для 26 колонок це буде "Z")
    last_cell = rowcol_to_a1(row, total_columns)
    cell_range = f"A{row}:{last_cell}"
    # Використовуємо green_format, який вже визначено (фон із значенням Color(0.8, 1, 0.8))
    format_cell_range(ws, cell_range, green_format)
    logging.debug(f"Рядок {row} зафарбовано зеленим у аркуші {ws.title}.")

def reapply_confirmed_formatting():
    """
    Перескановує worksheet1 та для кожного рядка, що відповідає заявці
    зі статусом 'confirmed', застосовує зелений фон до всього рядка.
    Це дозволяє «перенести» форматування на нові позиції після зміщення рядків.
    """
    ws = get_worksheet1()
    apps = load_applications()
    for uid, app_list in apps.items():
        for app in app_list:
            # Якщо заявка підтверджена та вказано номер рядка
            if app.get("proposal_status") == "confirmed" and app.get("sheet_row"):
                try:
                    row = app.get("sheet_row")
                    color_entire_row_green(ws, row)
                    logging.debug(f"Застосовано зелений фон для рядка {row} (UID: {uid}).")
                except Exception as e:
                    logging.exception(f"Помилка при повторному застосуванні форматування для UID {uid}, рядок {row}: {e}")


############################################
# Експорт бази даних у Google Sheets
############################################

def export_database():
    logging.info("Початок експорту бази даних у Google Sheets.")
    users_data = load_users()
    approved = users_data.get("approved_users", {})
    apps = load_applications()

    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)

    today = datetime.now().strftime("%d.%m")
    new_title = f"База {today}"
    new_ws = sheet.add_worksheet(title=new_title, rows="1000", cols="5")
    logging.debug(f"Створено новий лист: {new_title}")

    headers = ["ID", "ПІБ", "Номер телефону", "Остання заявка", "Загальна кількість заявок"]
    data_matrix = [headers]

    for uid, info in approved.items():
        user_apps = apps.get(uid, [])
        count_apps = len(user_apps)
        last_timestamp = ""
        if count_apps > 0:
            last_app = max(user_apps, key=lambda a: a.get("timestamp", ""))
            ts = last_app.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                last_timestamp = dt.strftime("%d.%m.%Y\n%H:%M")
            except Exception as e:
                logging.error(f"Помилка форматування дати: {e}")
                last_timestamp = ts
        row = [uid, info.get("fullname", ""), info.get("phone", ""), last_timestamp, count_apps]
        data_matrix.append(row)

    end_row = len(data_matrix)
    cell_range = f"A1:E{end_row}"
    new_ws.update(cell_range, data_matrix, value_input_option="USER_ENTERED")
    logging.debug("Дані експорту записані у лист.")

    cell_format = CellFormat(
        horizontalAlignment='CENTER',
        verticalAlignment='MIDDLE',
        textFormat=TextFormat(bold=True)
    )
    format_cell_range(new_ws, cell_range, cell_format)

    num_cols = 5
    for col in range(1, num_cols + 1):
        col_letter = rowcol_to_a1(1, col)[0]
        col_range = f"{col_letter}:{col_letter}"
        max_len = max(len(str(row[col-1])) for row in data_matrix)
        width = max_len * 10
        set_column_width(new_ws, col_range, width)
    logging.info("Експорт бази даних завершено.")

############################################
# Видалення заявки адміністратором
############################################

async def admin_remove_app_permanently(user_id: int, app_index: int):
    """
    Видаляє заявку адміністратора з файлу і таблиці.
    При цьому polling призупиняється на 20 секунд перед відновленням.
    """
    logging.info(f"Адміністратор видаляє заявку: user_id={user_id}, app_index={app_index}")
    from db import load_applications, delete_application_from_file_entirely, save_applications
    from loader import pause_polling, resume_polling
    import asyncio

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
            # Видаляємо клітинки у таблиці2 для стовпців L, M, N, O (індекси 12, 13, 14, 15)
            for col in [12, 13, 14, 15]:
                logging.debug(f"Видалення клітинки в таблиці2: рядок {sheet_row}, стовпець {col}")
                delete_price_cell_in_table2(sheet_row, col)

            # Видаляємо рядок у worksheet1
            ws = get_worksheet1()
            ws.delete_rows(sheet_row)
            logging.debug(f"Видалено рядок {sheet_row} у Google Sheets.")
            logging.debug(f"Видалено рядок {sheet_row} у worksheet1.")

            # Оновлюємо sheet_row для решти заявок
            updated_apps = load_applications()
            for u_str, user_apps in updated_apps.items():
                for a in user_apps:
                    old_row = a.get("sheet_row", 0)
                    if old_row and old_row > sheet_row:
                        a["sheet_row"] = old_row - 1
            save_applications(updated_apps)
            logging.debug("Оновлено номери рядків для заявок після видалення.")
            
            # Після зміщення рядків повторно застосовуємо форматування для підтверджених заявок
            reapply_confirmed_formatting()

        except Exception as e:
            logging.exception(f"Помилка видалення рядка в Google Sheets: {e}")


    # Чекаємо 20 секунд перед відновленням polling'у
    logging.info("Чекаємо 20 секунд перед відновленням polling'у.")
    await asyncio.sleep(20)
    resume_polling()
    logging.info("Polling відновлено після видалення заявки.")
    return True

############################################
# Оновлення Google Sheets з даними заявки
############################################

def update_google_sheet(data: dict) -> int:
    logging.info("Оновлення даних заявки в Google Sheets.")
    ws = get_worksheet1()
    ensure_columns(ws, 52)

    col_a = ws.col_values(1)
    numeric_values = []
    for value in col_a[1:]:
        try:
            numeric_values.append(int(value))
        except ValueError:
            continue
    last_number = numeric_values[-1] if numeric_values else 0
    new_request_number = last_number + 1
    new_row = len(col_a) + 1
    logging.debug(f"Новий номер заявки: {new_request_number}, рядок: {new_row}")
    ws.update_cell(new_row, 1, new_request_number)

    current_date = datetime.now().strftime("%d.%m")
    ws.update_cell(new_row, 2, current_date)

    fullname = data.get("fullname", "")
    if isinstance(fullname, dict):
        fullname = fullname.get("fullname", "")
    fullname_lines = "\n".join(fullname.split())
    ws.update_cell(new_row, 3, fullname_lines)

    ws.update_cell(new_row, 4, data.get("fgh_name", ""))
    ws.update_cell(new_row, 5, data.get("edrpou", ""))
    ws.update_cell(new_row, 6, data.get("group", ""))
    ws.update_cell(new_row, 7, data.get("culture", ""))

    quantity = data.get("quantity", "")
    if quantity:
        quantity = f"{quantity} Т"
    ws.update_cell(new_row, 8, quantity)

    region = data.get("region", "")
    district = data.get("district", "")
    city = data.get("city", "")
    location = f"Область: {region}\nРайон: {district}\nНас. пункт: {city}"
    ws.update_cell(new_row, 9, location)

    extra = data.get("extra_fields", {})
    extra_lines = []
    for key, value in extra.items():
        ukr_name = friendly_names.get(key, key.capitalize())
        extra_lines.append(f"{ukr_name}: {value}")
    ws.update_cell(new_row, 10, "\n".join(extra_lines))

    ws.update_cell(new_row, 11, data.get("payment_form", ""))

    currency_map = {"dollar": "Долар $", "euro": "Євро €", "uah": "Грн ₴"}
    curr = data.get("currency", "").lower()
    ws.update_cell(new_row, 12, currency_map.get(curr, data.get("currency", "")))
    ws.update_cell(new_row, 13, data.get("price", ""))

    ws.update_cell(new_row, 15, data.get("manager_price", ""))
    ws.update_cell(new_row, 16, data.get("phone", ""))
    ws.update_cell(new_row, 52, data.get("user_id", ""))

    logging.info(f"Дані заявки записано в рядок {new_row}.")
    return new_row

############################################
# Routes API (ComputeRouteMatrix) та Geocoding API
############################################

def geocode_address(address: str) -> dict:
    """Геокодує адресу за допомогою Geocoding API і повертає словник з координатами (lat, lng)."""
    logging.debug(f"Геокодування адреси: {address}")
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": GOOGLE_MAPS_API_KEY
    }
    try:
        response = requests.get(geocode_url, params=params, timeout=10)
        response.raise_for_status()
        result = response.json()
        logging.debug(f"Отримано результат геокодування: {result}")
        if result.get("status") == "OK" and result.get("results"):
            loc = result["results"][0]["geometry"]["location"]
            logging.info(f"Адресу {address} геокодовано: {loc}")
            return loc  # повертає {"lat": ..., "lng": ...}
        else:
            logging.error(f"Не вдалося геокодувати адресу: {address}, статус: {result.get('status')}")
    except Exception as e:
        logging.exception(f"Помилка геокодування адреси: {address} - {e}")
    return None

def get_distance_km(region: str, district: str, city: str) -> float:
    """
    Обчислює відстань між початковою точкою (координати Одеси, ODESSA_LAT, ODESSA_LNG)
    та адресою, що формується за областю, районом і містом, використовуючи спочатку Geocoding API
    для отримання координат цільової адреси, а потім ComputeRouteMatrix API (Routes API) для розрахунку відстані.
    """
    logging.info(f"Обчислення відстані для адреси: {city}, {district} район, {region} область, Ukraine")
    if not GOOGLE_MAPS_API_KEY:
        logging.error("Відсутній GOOGLE_MAPS_API_KEY")
        return None

    # Формуємо адресу
    address = f"{city}, {district} район, {region} область, Ukraine"
    destination_location = geocode_address(address)
    if not destination_location:
        logging.error("Не вдалося отримати координати цільової адреси.")
        return None

    dest_lat = destination_location["lat"]
    dest_lng = destination_location["lng"]
    logging.debug(f"Координати цільової адреси: lat={dest_lat}, lng={dest_lng}")

    url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    body = {
        "origins": [
            {
                "waypoint": {
                    "location": {
                        "latLng": {
                            "latitude": ODESSA_LAT,
                            "longitude": ODESSA_LNG
                        }
                    }
                }
            }
        ],
        "destinations": [
            {
                "waypoint": {
                    "location": {
                        "latLng": {
                            "latitude": dest_lat,
                            "longitude": dest_lng
                        }
                    }
                }
            }
        ],
        "travelMode": "DRIVE"
    }
    logging.debug(f"Тіло запиту до Routes API: {json.dumps(body)}")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "duration,distanceMeters,originIndex,destinationIndex"
    }
    logging.debug(f"Заголовки запиту: {headers}")

    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        response_text = r.text.strip()
        logging.debug(f"Відповідь від Routes API: {response_text}")

        parsed = None
        if response_text.startswith("["):
            try:
                json_array = json.loads(response_text)
                if isinstance(json_array, list) and len(json_array) > 0:
                    parsed = json_array[0]
                    logging.debug("Відповідь розпарсовано як JSON-масив.")
            except Exception as e:
                logging.error(f"Помилка розбору JSON-масиву: {e}")
        else:
            for line in response_text.split('\n'):
                try:
                    parsed = json.loads(line)
                    if parsed is not None:
                        logging.debug("Відповідь розпарсовано з NDJSON.")
                        break
                except json.JSONDecodeError as e:
                    logging.error(f"JSON decode error for line: {line} - {e}")
                    continue

        if not parsed:
            logging.error("Не вдалося розпарсити відповідь від ComputeRouteMatrix.")
            return None

        if "distanceMeters" in parsed:
            dist_meters = parsed.get("distanceMeters", 0)
            dist_km = dist_meters / 1000.0
            logging.info(f"Обчислена відстань: {dist_km} км")
            return dist_km
        else:
            logging.error(f"ComputeRouteMatrix повернув помилку: {parsed}")
            return None
    except Exception as e:
        logging.exception(f"Помилка Routes API: {e}")
        return None

############################################
# Парсинг прайс-листа
############################################

def parse_price_sheet():
    logging.info("Парсинг прайс-листа з Google Sheets.")
    ws = get_worksheet2_2()
    all_values = ws.get_all_values()
    logging.debug(f"Отримано {len(all_values)} рядків з прайс-листа.")

    distance_data = []
    row_idx = 2
    while True:
        if row_idx > len(all_values):
            break
        row_vals = all_values[row_idx - 1]
        if not row_vals or len(row_vals) < 2:
            break
        cell_a = row_vals[0].strip() if len(row_vals) >= 1 else ""
        if not cell_a:
            break

        splitted = cell_a.split("-")
        if len(splitted) != 2:
            row_idx += 1
            continue

        try:
            dist_min = float(splitted[0])
            dist_max = float(splitted[1])
            logging.debug(f"Рядок {row_idx}: від {dist_min} до {dist_max}")
        except Exception as e:
            logging.error(f"Помилка перетворення відстані в рядку {row_idx}: {e}")
            row_idx += 1
            continue

        try:
            tarif_grn = float(row_vals[1].strip())
        except:
            tarif_grn = None
        try:
            tarif_usd = float(row_vals[2].strip())
        except:
            tarif_usd = None
        try:
            tarif_eur = float(row_vals[3].strip())
        except:
            tarif_eur = None

        distance_data.append((dist_min, dist_max, tarif_grn, tarif_usd, tarif_eur))
        row_idx += 1

    logging.info(f"Знайдено {len(distance_data)} діапазонів відстаней.")

    blocks = {
        "грн": {},
        "долар": {},
        "євро": {}
    }

    max_rows = len(all_values)
    for r in range(3, max_rows + 1):
        row_vals = all_values[r - 1]
        if len(row_vals) < 19:
            continue

        group_grn = row_vals[5].strip()
        culture_grn = row_vals[6].strip()
        pay_pdv = row_vals[7].strip()
        pay_bez = row_vals[8].strip()
        pay_cash = row_vals[9].strip()

        group_usd = row_vals[11].strip()
        culture_usd = row_vals[12].strip()
        pay_valut = row_vals[13].strip()
        pay_cash_usd = row_vals[14].strip()

        group_eur = row_vals[16].strip()
        culture_eur = row_vals[17].strip()
        pay_valut_eur = row_vals[18].strip() if len(row_vals) > 18 else ""

        def try_float(x):
            try:
                return float(x)
            except:
                return None

        p_pdv = try_float(pay_pdv)
        p_bez = try_float(pay_bez)
        p_cash = try_float(pay_cash)
        p_valut = try_float(pay_valut)
        p_cash_usd_val = try_float(pay_cash_usd)
        p_valut_eur_val = try_float(pay_valut_eur)

        if group_grn and culture_grn:
            gdict = blocks["грн"].setdefault(group_grn.lower(), {})
            cdict = gdict.setdefault(culture_grn.lower(), {})
            cdict["перерахунок з пдв"] = p_pdv
            cdict["перерахунок без пдв"] = p_bez
            cdict["готівка"] = p_cash

        if group_usd and culture_usd:
            gdict = blocks["долар"].setdefault(group_usd.lower(), {})
            cdict = gdict.setdefault(culture_usd.lower(), {})
            cdict["валютний контракт"] = p_valut
            cdict["готівка"] = p_cash_usd_val

        if group_eur and culture_eur:
            gdict = blocks["євро"].setdefault(group_eur.lower(), {})
            cdict = gdict.setdefault(culture_eur.lower(), {})
            cdict["валютний контракт"] = p_valut_eur_val

    logging.info("Парсинг прайс-листа завершено.")
    return {
        "distance_ranges": distance_data,
        "blocks": blocks
    }

def find_tariff_for_distance(distance_km, distance_data, currency_str):
    logging.debug(f"Пошук тарифу для відстані {distance_km} км та валюти {currency_str}")
    cur_index = 0
    if currency_str == "грн":
        cur_index = 2
    elif currency_str == "долар":
        cur_index = 3
    elif currency_str == "євро":
        cur_index = 4
    else:
        logging.error(f"Невідома валюта: {currency_str}")
        return None

    for (dmin, dmax, tg, tu, te) in distance_data:
        if distance_km >= dmin and distance_km < dmax:
            arr = [tg, tu, te]
            val = arr[cur_index - 2]
            logging.debug(f"Тариф для цього діапазону: {val}")
            return val
    logging.error("Не знайдено тариф для заданої відстані.")
    return None

def find_price_in_block(currency_str, group_str, culture_str, pay_form, blocks):
    logging.debug(f"Пошук ціни для валюти: {currency_str}, групи: {group_str}, культури: {culture_str}, форми оплати: {pay_form}")
    cur = currency_str.lower()
    grp = group_str.lower()
    cul = culture_str.lower()
    pay = pay_form.lower()

    if cur not in blocks:
        logging.error(f"Не знайдено блок для валюти: {cur}")
        return None
    if grp not in blocks[cur]:
        logging.error(f"Не знайдено групу: {grp} у валюті {cur}")
        return None
    if cul not in blocks[cur][grp]:
        logging.error(f"Не знайдено культуру: {cul} у групі {grp} валюті {cur}")
        return None

    pay_dict = blocks[cur][grp][cul]
    if pay not in pay_dict:
        logging.error(f"Не знайдено форму оплати: {pay} для культури: {cul}")
        return None
    price = pay_dict[pay]
    logging.debug(f"Знайдена ціна: {price}")
    return price

def set_bot_price_in_table2(row: int, price):
    logging.info(f"Запис розрахованої ціни {price} у таблицю2, рядок {row}, стовпець 13")
    ws2 = get_worksheet2()
    ws2.update_cell(row, 13, price)

def calculate_and_set_bot_price(app, row, price_config):
    logging.info("Розрахунок автоматичної ціни для заявки.")
    region = app.get("region", "")
    district = app.get("district", "")
    city = app.get("city", "")
    group_str = app.get("group", "")
    culture_str = app.get("culture", "")
    payment_str = app.get("payment_form", "")
    currency_str = app.get("currency", "").lower()
    if currency_str == "uah":
        currency_str = "грн"
    elif currency_str == "dollar":
        currency_str = "долар"
    elif currency_str == "euro":
        currency_str = "євро"

    logging.debug(f"Параметри заявки: регіон={region}, район={district}, місто={city}, група={group_str}, культура={culture_str}, форма оплати={payment_str}, валюта={currency_str}")

    dist_km = get_distance_km(region, district, city)
    if dist_km is None:
        logging.error("Не вдалося отримати відстань для заявки.")
        return None
    logging.info(f"Відстань для заявки: {dist_km} км")

    distance_data = price_config["distance_ranges"]
    blocks = price_config["blocks"]

    tariff_value = find_tariff_for_distance(dist_km, distance_data, currency_str)
    if tariff_value is None:
        logging.error("Не знайдено тариф для заданої відстані.")
        return None

    base_price = find_price_in_block(currency_str, group_str, culture_str, payment_str, blocks)
    if base_price is None:
        logging.error("Не знайдено базову ціну для заявки.")
        return None

    final_price = base_price - tariff_value
    if final_price < 0:
        final_price = 0

    # Якщо ціна ціла, перетворюємо її на int для видалення ".0"
    if final_price == int(final_price):
        final_price = int(final_price)

    logging.info(f"Розрахована ціна: {final_price} (базова ціна {base_price} - тариф {tariff_value})")
    set_bot_price_in_table2(row, final_price)
    return final_price

