# loader.py
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from config import TELEGRAM_TOKEN

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())
