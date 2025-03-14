# db.py
import json
import os
import logging
from datetime import datetime

from config import USERS_FILE, APPLICATIONS_FILE

def load_users():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_applications():
    with open(APPLICATIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_applications(apps):
    with open(APPLICATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(apps, f, indent=2, ensure_ascii=False)

def approve_user(user_id):
    data = load_users()
    uid = str(user_id)
    if uid not in data.get("approved_users", {}):
        pending = data.get("pending_users", {}).get(uid, {})
        fullname = pending.get("fullname", "")
        phone = pending.get("phone", "")
        data.setdefault("approved_users", {})[uid] = {"fullname": fullname, "phone": phone}
        data.get("pending_users", {}).pop(uid, None)
        save_users(data)
        logging.info(f"Користувач {uid} схвалений.")

def block_user(user_id):
    data = load_users()
    uid = str(user_id)
    if uid not in data.get("blocked_users", []):
        data.setdefault("blocked_users", []).append(uid)
        data.get("pending_users", {}).pop(uid, None)
        data.get("approved_users", {}).pop(uid, None)
        save_users(data)
        logging.info(f"Користувач {uid} заблокований.")

def add_application(user_id, chat_id, application_data):
    application_data['timestamp'] = datetime.now().isoformat()
    application_data['user_id'] = user_id
    application_data['chat_id'] = chat_id
    application_data["proposal_status"] = "active"
    apps = load_applications()
    uid = str(user_id)
    if uid not in apps:
        apps[uid] = []
    apps[uid].append(application_data)
    save_applications(apps)
    logging.info(f"Заявка для user_id={user_id} збережена як active.")

def update_application_status(user_id, app_index, status, proposal=None):
    apps = load_applications()
    uid = str(user_id)
    if uid in apps and 0 <= app_index < len(apps[uid]):
        apps[uid][app_index]["proposal_status"] = status
        if proposal is not None:
            apps[uid][app_index]["proposal"] = proposal
        save_applications(apps)

def delete_application_soft(user_id, app_index):
    apps = load_applications()
    uid = str(user_id)
    if uid in apps and 0 <= app_index < len(apps[uid]):
        apps[uid][app_index]["proposal_status"] = "deleted"
        save_applications(apps)

def delete_application_from_file_entirely(user_id, app_index):
    apps = load_applications()
    uid = str(user_id)
    if uid in apps and 0 <= app_index < len(apps[uid]):
        del apps[uid][app_index]
        if not apps[uid]:
            apps.pop(uid, None)
        save_applications(apps)
