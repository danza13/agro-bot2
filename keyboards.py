# keyboards.py
from aiogram import types

def remove_keyboard():
    return types.ReplyKeyboardRemove()

def get_main_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Подати заявку", "Переглянути мої заявки")
    return kb

def get_admin_root_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Модерація", "Заявки")
    kb.add("Вийти з адмін-меню")
    return kb

def get_admin_moderation_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Користувачі на модерацію", "База користувачів")
    kb.add("Назад")
    return kb

def get_admin_requests_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Підтверджені", "Видалені")
    kb.add("Видалення заявок", "Редагування заявок")
    kb.add("Ціна бота")
    kb.add("Назад")
    return kb
    
def get_topicality_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Актуальна", "Потребує змін", "Видалити")
    return kb
