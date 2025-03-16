# states.py
from aiogram.dispatcher.filters.state import State, StatesGroup

class RegistrationStates(StatesGroup):
    waiting_for_fullname = State()
    waiting_for_phone = State()
    preview = State()
    editing = State()
    editing_fullname = State()
    editing_phone = State()

class ApplicationStates(StatesGroup):
    waiting_for_webapp_data = State()        # Користувач відкриває WebApp для створення заявки
    confirm_application = State()            # Користувач переглядає заявку, підтверджує
    editing_application = State()            # (старий стан) коли редагуємо перед створенням
    viewing_applications = State()           # Перегляд списку заявок
    viewing_application = State()            # Перегляд детально однієї заявки
    viewing_proposal = State()               # Перегляд пропозиції
    proposal_reply = State()                 # Підтвердити/Відхилити пропозицію
    editing_choice = State()                 # Кнопки: "Відкрити форму редагування" / "Скасувати"
    waiting_for_webapp2_data = State()       # Чекаємо даних зі скороченого WebApp2
    deletion_confirmation = State()          # Підтвердження видалення заявки
    viewing_topicality = State()            # Створення сповіщення (запит: "Ваша заявка ... актуальна?")
    topicality_editing = State()            # Обробка натискання "Потребує змін"
    topicality_deletion_confirmation = State()  # Обробка видалення заявки
    
class AdminMenuStates(StatesGroup):
    choosing_section = State()
    moderation_section = State()
    requests_section = State()

class AdminReview(StatesGroup):
    waiting_for_application_selection = State()
    waiting_for_decision = State()
    viewing_confirmed_list = State()
    viewing_confirmed_app = State()
    viewing_deleted_list = State()
    viewing_deleted_app = State()
    confirm_deletion_app = State()
    viewing_approved_list = State()
    viewing_approved_user = State()
    editing_approved_user = State()
    editing_approved_user_fullname = State()
    editing_approved_user_phone = State()
    editing_applications_list = State()
    editing_single_application = State()
    select_new_status = State()
