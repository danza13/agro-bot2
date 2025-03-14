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

from db import load_applications, save_applications

############################################
# Ініціалізація gspread
############################################

def init_gspread():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(gspread_creds_dict, scope)
    client = gspread.authorize(creds)
    return client

def get_worksheet1():
    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
    return sheet.worksheet(SHEET1_NAME)

def get_worksheet2():
    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID2)
    return sheet.worksheet(SHEET2_NAME)

def get_worksheet2_2():
    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID2)
    return sheet.worksheet(SHEET2_NAME_2)

def ensure_columns(ws, required_col: int):
    if ws.col_count < required_col:
        ws.resize(rows=ws.row_count, cols=required_col)

############################################
# Форматування клітинок
############################################

red_format = cellFormat(backgroundColor=Color(1, 0.8, 0.8))
green_format = cellFormat(backgroundColor=Color(0.8, 1, 0.8))
yellow_format = cellFormat(backgroundColor=Color(1, 1, 0.8))

def color_price_cell_in_table2(row: int, fmt: cellFormat, col: int = 12):
    ws2 = get_worksheet2()
    cell_range = f"{rowcol_to_a1(row, col)}:{rowcol_to_a1(row, col)}"
    format_cell_range(ws2, cell_range, fmt)

def color_cell_red(row: int, col: int = 12):
    color_price_cell_in_table2(row, red_format, col)

def color_cell_green(row: int, col: int = 12):
    color_price_cell_in_table2(row, green_format, col)

def color_cell_yellow(row: int, col: int = 12):
    color_price_cell_in_table2(row, yellow_format, col)

def delete_price_cell_in_table2(row: int, col: int = 12):
    ws2 = get_worksheet2()
    format_cell_range(
        ws2,
        f"{rowcol_to_a1(row, col)}:{rowcol_to_a1(ws2.row_count, col)}",
        cellFormat(backgroundColor=Color(1, 1, 1))
    )
    col_values = ws2.col_values(col)
    if row - 1 >= len(col_values):
        return
    col_values.pop(row - 1)
    for i in range(row - 1, len(col_values)):
        ws2.update_cell(i + 1, col, col_values[i])
    last_row_to_clear = len(col_values) + 1
    ws2.update_cell(last_row_to_clear, col, "")

############################################
# Експорт бази даних у Google Sheets
############################################

from db import load_users, load_applications, save_applications

def export_database():
    users_data = load_users()
    approved = users_data.get("approved_users", {})
    apps = load_applications()

    client = init_gspread()
    sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)

    today = datetime.now().strftime("%d.%m")
    new_title = f"База {today}"
    new_ws = sheet.add_worksheet(title=new_title, rows="1000", cols="5")

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
            except:
                last_timestamp = ts
        row = [uid, info.get("fullname", ""), info.get("phone", ""), last_timestamp, count_apps]
        data_matrix.append(row)

    end_row = len(data_matrix)
    cell_range = f"A1:E{end_row}"
    new_ws.update(cell_range, data_matrix, value_input_option="USER_ENTERED")

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

############################################
# Видалення заявки адміністратором
############################################

async def admin_remove_app_permanently(user_id: int, app_index: int):
    from db import load_applications, delete_application_from_file_entirely, save_applications
    apps = load_applications()
    uid = str(user_id)
    if uid not in apps or app_index < 0 or app_index >= len(apps[uid]):
        return False

    app = apps[uid][app_index]
    sheet_row = app.get("sheet_row")

    delete_application_from_file_entirely(user_id, app_index)

    if sheet_row:
        try:
            delete_price_cell_in_table2(sheet_row, 12)
            ws = get_worksheet1()
            ws.delete_rows(sheet_row)
            updated_apps = load_applications()
            for u_str, user_apps in updated_apps.items():
                for a in user_apps:
                    old_row = a.get("sheet_row", 0)
                    if old_row and old_row > sheet_row:
                        a["sheet_row"] = old_row - 1
            save_applications(updated_apps)
        except Exception as e:
            logging.exception(f"Помилка видалення рядка в Google Sheets: {e}")
    return True

############################################
# Оновлення Google Sheets з даними заявки
############################################

def update_google_sheet(data: dict) -> int:
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

    return new_row

############################################
# Routes API (ComputeRouteMatrix) та Geocoding API
############################################

def geocode_address(address: str) -> dict:
    """
    Геокодує адресу за допомогою Geocoding API і повертає словник з координатами (lat, lng)
    або None, якщо геокодування не вдалося.
    """
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": GOOGLE_MAPS_API_KEY
    }
    try:
        response = requests.get(geocode_url, params=params, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "OK" and result.get("results"):
            return result["results"][0]["geometry"]["location"]  # Повертає, наприклад, {"lat": 50.4501, "lng": 30.5234}
        else:
            logging.error(f"Не вдалося геокодувати адресу: {address}, статус: {result.get('status')}")
    except Exception as e:
        logging.exception(f"Помилка геокодування адреси: {address} - {e}")
    return None

def get_distance_km(region: str, district: str, city: str) -> float:
    """
    Обчислює відстань між точкою з координатами (ODESSA_LAT, ODESSA_LNG)
    та місцем, яке задається адресою (формується за областю, районом і містом).
    Спочатку використовується Geocoding API для отримання координат адреси,
    а потім ComputeRouteMatrix API для розрахунку відстані (у метрах), яка конвертується в кілометри.
    """
    if not GOOGLE_MAPS_API_KEY:
        return None

    # Формуємо адресу
    address = f"{city}, {district} район, {region} область, Ukraine"
    destination_location = geocode_address(address)
    if not destination_location:
        return None

    dest_lat = destination_location["lat"]
    dest_lng = destination_location["lng"]

    # Формуємо запит до ComputeRouteMatrix API
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

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        # Вказуємо FieldMask для отримання необхідних полів
        "X-Goog-FieldMask": "duration,distanceMeters,originIndex,destinationIndex"
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        response_text = r.text.strip()
        parsed = None

        # Якщо відповідь починається з "[" – спробуємо обробити її як JSON-масив
        if response_text.startswith("["):
            try:
                json_array = json.loads(response_text)
                if isinstance(json_array, list) and json_array:
                    parsed = json_array[0]
            except Exception as e:
                logging.error(f"Помилка розбору JSON-масиву: {e}")
        else:
            # Якщо це NDJSON – розбиваємо за рядками та намагаємося розпарсити кожен рядок
            lines = response_text.split('\n')
            for line in lines:
                try:
                    parsed = json.loads(line)
                    if parsed is not None:
                        break
                except json.JSONDecodeError as e:
                    logging.error(f"JSON decode error for line: {line} - {e}")
                    continue

        if not parsed:
            return None

        if parsed.get("status") == "OK":
            dist_meters = parsed.get("distanceMeters", 0)
            return dist_meters / 1000.0
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
    ws = get_worksheet2_2()
    all_values = ws.get_all_values()

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
        except:
            row_idx += 1
            continue

        cell_b = row_vals[1].strip() if len(row_vals) >= 2 else ""
        try:
            tarif_grn = float(cell_b)
        except:
            tarif_grn = None

        cell_c = row_vals[2].strip() if len(row_vals) >= 3 else ""
        try:
            tarif_usd = float(cell_c)
        except:
            tarif_usd = None

        cell_d = row_vals[3].strip() if len(row_vals) >= 4 else ""
        try:
            tarif_eur = float(cell_d)
        except:
            tarif_eur = None

        distance_data.append((dist_min, dist_max, tarif_grn, tarif_usd, tarif_eur))
        row_idx += 1

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

    return {
        "distance_ranges": distance_data,
        "blocks": blocks
    }

def find_tariff_for_distance(distance_km, distance_data, currency_str):
    cur_index = 0
    if currency_str == "грн":
        cur_index = 2
    elif currency_str == "долар":
        cur_index = 3
    elif currency_str == "євро":
        cur_index = 4
    else:
        return None

    for (dmin, dmax, tg, tu, te) in distance_data:
        if distance_km >= dmin and distance_km < dmax:
            arr = [tg, tu, te]
            val = arr[cur_index - 2]
            return val
    return None

def find_price_in_block(currency_str, group_str, culture_str, pay_form, blocks):
    cur = currency_str.lower()
    grp = group_str.lower()
    cul = culture_str.lower()
    pay = pay_form.lower()

    if cur not in blocks:
        return None
    if grp not in blocks[cur]:
        return None
    if cul not in blocks[cur][grp]:
        return None

    pay_dict = blocks[cur][grp][cul]
    if pay not in pay_dict:
        return None
    return pay_dict[pay]

def set_bot_price_in_table2(row: int, price):
    ws2 = get_worksheet2()
    ws2.update_cell(row, 13, price)

def calculate_and_set_bot_price(app, row, price_config):
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

    dist_km = get_distance_km(region, district, city)
    if dist_km is None:
        return None

    distance_data = price_config["distance_ranges"]
    blocks = price_config["blocks"]

    tariff_value = find_tariff_for_distance(dist_km, distance_data, currency_str)
    if tariff_value is None:
        return None

    base_price = find_price_in_block(currency_str, group_str, culture_str, payment_str, blocks)
    if base_price is None:
        return None

    final_price = base_price - tariff_value
    if final_price < 0:
        final_price = 0

    set_bot_price_in_table2(row, final_price)
    return final_price
