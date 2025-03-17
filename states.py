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
    waiting_for_webapp_data = State()
    confirm_application = State()
    editing_application = State()
    viewing_applications = State()
    viewing_application = State()
    viewing_proposal = State()
    proposal_reply = State()
    editing_choice = State()
    waiting_for_webapp2_data = State()
    deletion_confirmation = State()
    viewing_topicality = State()
    topicality_editing = State()
    topicality_deletion_confirmation = State()
    
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
    sending_mass_message = State()
    sending_private_message = State()
    auto_price_section = State()
