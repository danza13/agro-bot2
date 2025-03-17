import json, os
from config import DATA_DIR

AUTO_CALC_FILE = os.path.join(DATA_DIR, "auto_calc.json")

def load_auto_calc_setting() -> bool:
    if os.path.exists(AUTO_CALC_FILE):
        with open(AUTO_CALC_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("AUTO_CALC_ENABLED", True)
    else:
        default = {"AUTO_CALC_ENABLED": True}
        with open(AUTO_CALC_FILE, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2)
        return default["AUTO_CALC_ENABLED"]

def save_auto_calc_setting(value: bool):
    data = {"AUTO_CALC_ENABLED": value}
    with open(AUTO_CALC_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
