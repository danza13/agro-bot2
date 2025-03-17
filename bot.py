# config.py
import os
import json
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

########################################################
# Зчитуємо змінні оточення
########################################################

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ADMINS = [uid.strip() for uid in os.getenv("ADMINS", "").split(",") if uid.strip()]

GOOGLE_SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "")
SHEET1_NAME = os.getenv("SHEET1_NAME", "Лист1")

GOOGLE_SPREADSHEET_ID2 = os.getenv("GOOGLE_SPREADSHEET_ID2", "")
SHEET2_NAME = os.getenv("SHEET2_NAME", "Лист1")

# Лист з тарифами і цінами:
SHEET2_NAME_2 = os.getenv("SHEET2_NAME_2", "Ціни")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
API_PORT = int(os.getenv("API_PORT", "8080"))

# API-ключ для Routes API
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
# Координати Одеси
ODESSA_LAT = 46.4123
ODESSA_LNG = 30.7343

DATA_DIR = os.getenv("DATA_DIR", "/data")

GSPREAD_CREDENTIALS_JSON = os.getenv("GSPREAD_CREDENTIALS_JSON", "")
if not GSPREAD_CREDENTIALS_JSON:
    raise RuntimeError("Немає GSPREAD_CREDENTIALS_JSON у змінних оточення!")

try:
    gspread_creds_dict = json.loads(GSPREAD_CREDENTIALS_JSON)
except Exception as e:
    raise RuntimeError(f"Помилка парсингу GSPREAD_CREDENTIALS_JSON: {e}")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
APPLICATIONS_FILE = os.path.join(DATA_DIR, "applications_by_user.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.py")

if not os.path.exists(USERS_FILE):
    import codecs
    initial_users_data = {"approved_users": {}, "blocked_users": [], "pending_users": {}}
    with codecs.open(USERS_FILE, "w", "utf-8") as f:
        json.dump(initial_users_data, f, indent=2, ensure_ascii=False)

if not os.path.exists(APPLICATIONS_FILE):
    import codecs
    with codecs.open(APPLICATIONS_FILE, "w", "utf-8") as f:
        json.dump({}, f, indent=2, ensure_ascii=False)

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("config_module", CONFIG_FILE)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    CONFIG = config_module.CONFIG
except (ImportError, FileNotFoundError):
    default_config_content = '''# config.py
CONFIG = {
    "fgh_name_column": "D",
    "edrpou_column": "E",
    "region_column": "I",
    "district_column": "I",
    "city_column": "I",
    "group_column": "F",
    "culture_column": "G",
    "quantity_column": "H",
    "price_column": "M",
    "currency_column": "L",
    "payment_form_column": "K",
    "extra_fields_column": "J",
    "row_start": 2,
    "manager_price_column": "O",
    "user_id_column": "AZ",
    "phone_column": "P"
}
'''
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(default_config_content)
    spec = importlib.util.spec_from_file_location("config_module", CONFIG_FILE)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    CONFIG = config_module.CONFIG


############################################
# Словник для відображення назви поля (extra_fields)
############################################

friendly_names = {
    "natura": "Натура",
    "bilok": "Білок",
    "kleikovina": "Клейковина",
    "smitteva": "Сміттєва домішка",
    "vologhist": "Вологість",
    "sazhkov": "Сажкові зерна",
    "natura_ya": "Натура",
    "vologhist_ya": "Вологість",
    "smitteva_ya": "Сміттєва домішка",
    "vologhist_k": "Вологість",
    "zernovadomishka": "Зернова домішка",
    "poshkodjeni": "Пошкоджені зерна",
    "smitteva_k": "Сміттєва домішка",
    "zipsovani": "Зіпсовані зерна",
    "olijnist_na_suhu": "Олійність на суху",
    "vologhist_son": "Вологість",
    "smitteva_son": "Сміттєва домішка",
    "kislotne": "Кислотне число",
    "olijnist_na_siru": "Олійність на сиру",
    "vologhist_ripak": "Вологість",
    "glukozinolati": "Глюкозінолати",
    "smitteva_ripak": "Сміттєва домішка",
    "bilok_na_siru": "Білок на сиру",
    "vologhist_soya": "Вологість",
    "smitteva_soya": "Сміттєва домішка",
    "olijna_domishka": "Олійна домішка",
    "ambrizia": "Амброзія"
}
