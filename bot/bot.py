import os
import asyncio
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from dotenv import load_dotenv
import httpx
import logging
import sqlite3

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL")
ADMIN_CHAT_ID = int(os.getenv("BOT_ADMIN_CHAT_ID"))
BOT_LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
BOT_DB_PATH = os.getenv("BOT_DB_PATH", "bot_storage.db")

logging.basicConfig(level=getattr(logging, BOT_LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# Память о пользователях, писавших боту (для рассылки)
KNOWN_CHAT_IDS = set()


def init_bot_db():
    """Создать локальную БД для хранения chat_id, если её ещё нет."""
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS bot_chats ("  # простая таблица: один chat_id
            "chat_id INTEGER PRIMARY KEY"
            ")"
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("Failed to init bot DB")


def load_known_chats():
    """Загрузить chat_id из БД в память при старте бота."""
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM bot_chats")
        rows = cur.fetchall()
        conn.close()
        for (cid,) in rows:
            KNOWN_CHAT_IDS.add(cid)
        logger.info("Loaded %d chat_ids from bot DB", len(KNOWN_CHAT_IDS))
    except Exception:
        logger.exception("Failed to load chat ids from DB")


def remember_chat(msg: types.Message):
    """Сохранить chat_id пользователя, который взаимодействует с ботом (память + БД)."""
    try:
        if msg.chat and msg.chat.id:
            cid = msg.chat.id
            KNOWN_CHAT_IDS.add(cid)
            try:
                conn = sqlite3.connect(BOT_DB_PATH)
                cur = conn.cursor()
                cur.execute("INSERT OR IGNORE INTO bot_chats(chat_id) VALUES (?)", (cid,))
                conn.commit()
                conn.close()
            except Exception:
                logger.exception("Failed to persist chat id")
    except Exception:
        logger.exception("Failed to remember chat id")

kb = ReplyKeyboardMarkup(resize_keyboard=True)
kb.add(KeyboardButton("Регистрация"))
kb.add(KeyboardButton("Получить API-ключ"))
kb.add(KeyboardButton("Проверить кодовое слово"))


class Register(StatesGroup):
    username = State()
    password = State()

class Auth(StatesGroup):
    username = State()
    password = State()

class CheckWord(StatesGroup):
    awaiting_pair = State()
    awaiting_api_key = State()

@dp.message_handler(commands=['start'])
async def start(msg: types.Message):
    logger.info("/start from user_id=%s username=%s", msg.from_user.id if msg.from_user else None, getattr(msg.from_user, 'username', None))
    remember_chat(msg)
    await msg.answer(
        "Привет! Это бот API Challenge РКСИ.\n" \
        "Доступные действия: \n" \
        "- Регистрация (создать аккаунт)\n" \
        "- Получить API-ключ\n" \
        "- Проверить кодовое слово",
        reply_markup=kb
    )


@dp.message_handler(lambda m: m.text == 'Регистрация', state='*')
async def register_start(msg: types.Message, state: FSMContext):
    logger.info("Register start user_id=%s text=%r", msg.from_user.id if msg.from_user else None, msg.text)
    remember_chat(msg)
    await state.finish()
    await msg.answer("Введите логин (ФАМИЛИЯ_ГРУППА (например: ДОВИРЯК_ИС-24)):")
    await Register.username.set()


@dp.message_handler(state=Register.username, content_types=['text'])
async def register_username(msg: types.Message, state: FSMContext):
    username = msg.text.strip()
    logger.info("Register username user_id=%s username=%r", msg.from_user.id if msg.from_user else None, username)
    if not username or ' ' in username:
        await msg.answer("Неверный логин. Введите одно слово без пробелов:")
        return
    await state.update_data(username=username)
    await msg.answer("Установите пароль (минимум 6 символов):")
    await Register.password.set()


@dp.message_handler(state=Register.password, content_types=['text'])
async def register_password(msg: types.Message, state: FSMContext):
    password = msg.text.strip()
    data = await state.get_data()
    username = data.get('username')
    logger.info("Register password user_id=%s username=%r len=%d", msg.from_user.id if msg.from_user else None, username, len(password))
    if len(password) < 6:
        await msg.answer("Пароль слишком короткий, введите другой (>=6):")
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            url = f"{BACKEND_BASE_URL}/bot/register"
            payload = {"username": username, "password": password}
            logger.info("POST %s payload=%s", url, {"username": username, "password": "***"})
            r = await client.post(url, json=payload)
            logger.info("Response %s status=%s body=%s", url, r.status_code, (r.text[:200] if r.text else ""))
            if r.status_code in (200, 201):
                await msg.answer("Регистрация выполнена. Теперь можно получить API-ключ.")
            else:
                await msg.answer("Не удалось зарегистрировать. Попробуйте другое имя или позже.")
        except Exception as e:
            logger.exception("Register API error: %s", e)
            await msg.answer("API недоступно. Попробуйте позже.")
    await state.finish()


@dp.message_handler(lambda m: m.text == 'Получить API-ключ', state='*')
async def auth_start(msg: types.Message, state: FSMContext):
    logger.info("Auth start user_id=%s", msg.from_user.id if msg.from_user else None)
    remember_chat(msg)
    await state.finish()
    await msg.answer("Введите логин (ФАМИЛИЯ_ГРУППА (например: ДОВИРЯК_ИС-24)):")
    await Auth.username.set()


@dp.message_handler(state=Auth.username, content_types=['text'])
async def auth_username(msg: types.Message, state: FSMContext):
    username = msg.text.strip()
    logger.info("Auth username user_id=%s username=%r", msg.from_user.id if msg.from_user else None, username)
    if not username or ' ' in username:
        await msg.answer("Неверный логин. Введите одно слово без пробелов:")
        return
    await state.update_data(username=username)
    await msg.answer("Введите пароль:")
    await Auth.password.set()


@dp.message_handler(state=Auth.password, content_types=['text'])
async def auth_password(msg: types.Message, state: FSMContext):
    password = msg.text.strip()
    data = await state.get_data()
    username = data.get('username')
    logger.info("Auth password user_id=%s username=%r len=%d", msg.from_user.id if msg.from_user else None, username, len(password))
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            url = f"{BACKEND_BASE_URL}/challenge/auth"
            logger.info("POST %s payload=%s", url, {"username": username, "password": "***"})
            r = await client.post(url, json={"username": username, "password": password})
            logger.info("Response %s status=%s body=%s", url, r.status_code, (r.text[:200] if r.text else ""))
            if r.status_code == 200:
                api_key = r.json().get("api_key")
                await msg.answer(f"Ваш API-ключ: \n`{api_key}`\nСохраните его. Используйте в заголовке X-API-Key.", parse_mode="Markdown")
            else:
                await msg.answer("Неверные данные или ограничение по попыткам. Попробуйте позже.")
        except Exception as e:
            logger.exception("Auth API error: %s", e)
            await msg.answer("API недоступно. Попробуйте позже.")
    await state.finish()


@dp.message_handler(lambda m: m.text == 'Проверить кодовое слово', state='*')
async def check_word_start(msg: types.Message, state: FSMContext):
    logger.info("Check word start user_id=%s", msg.from_user.id if msg.from_user else None)
    remember_chat(msg)
    await state.finish()
    await msg.answer("Отправьте в формате: endpoint_id значение (например: 1 secret)")
    await CheckWord.awaiting_pair.set()


@dp.message_handler(state=CheckWord.awaiting_pair, content_types=['text'])
async def check_word_pair(msg: types.Message, state: FSMContext):
    try:
        eid_str, value = msg.text.split(" ", 1)
        endpoint_id = int(eid_str)
        logger.info("Check word parse user_id=%s endpoint_id=%s value_len=%d", msg.from_user.id if msg.from_user else None, endpoint_id, len(value))
    except Exception:
        logger.warning("Check word parse failed user_id=%s text=%r", msg.from_user.id if msg.from_user else None, msg.text)
        await msg.answer("Неверный формат. Пример: 1 secret")
        return
    await state.update_data(endpoint_id=endpoint_id, value=value)
    await msg.answer("Введите ваш API-ключ:")
    await CheckWord.awaiting_api_key.set()


@dp.message_handler(state=CheckWord.awaiting_api_key, content_types=['text'])
async def check_word_submit(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    endpoint_id = data.get('endpoint_id')
    value = data.get('value', '')
    api_key = msg.text.strip()
    logger.info("Submit value user_id=%s endpoint_id=%s value_len=%d api_key_len=%d", msg.from_user.id if msg.from_user else None, endpoint_id, len(value), len(api_key))
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            url = f"{BACKEND_BASE_URL}/challenge/submit"
            logger.info("POST %s json=%s headers=%s", url, {"endpoint_id": endpoint_id, "value": "***"}, {"X-API-Key": f"***{api_key[-4:]}" if len(api_key)>=4 else "***"})
            r = await client.post(url, json={"endpoint_id": endpoint_id, "value": value}, headers={"X-API-Key": api_key})
            logger.info("Response %s status=%s body=%s", url, r.status_code, (r.text[:200] if r.text else ""))
            if r.status_code == 200 and r.json().get("ok"):
                await msg.answer("Верно! Поздравляем, вы прошли этап.")
            elif r.status_code == 200:
                await msg.answer("Неверно. Попробуйте ещё.")
            else:
                await msg.answer("Ошибка или неправильный ключ.")
        except Exception as e:
            logger.exception("Submit API error: %s", e)
            await msg.answer("API недоступно. Попробуйте позже.")
    await state.finish()


@dp.message_handler(commands=['admin_progress'])
async def admin_progress(msg: types.Message):
    if ADMIN_CHAT_ID and msg.from_user.id != ADMIN_CHAT_ID:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{BACKEND_BASE_URL}/challenge/info")
            await msg.answer(f"Текущее состояние: {r.json()}")
        except Exception:
            await msg.answer("API недоступно.")


@dp.message_handler(commands=['broadcast'])
async def broadcast(msg: types.Message):
    """Рассылка сообщения по всем известным чатам. Только для админа."""
    if ADMIN_CHAT_ID and msg.from_user and msg.from_user.id != ADMIN_CHAT_ID:
        return
    text = msg.get_args()
    if not text:
        await msg.answer("Использование: /broadcast текст сообщения")
        return
    # Обновим список чатов из БД на всякий случай
    load_known_chats()
    if not KNOWN_CHAT_IDS:
        await msg.answer("Нет известных чатов для рассылки.")
        return
    sent = 0
    failed = 0
    for chat_id in KNOWN_CHAT_IDS:
        try:
            await bot.send_message(chat_id, text)
            sent += 1
        except Exception as e:
            failed += 1
            logger.exception("Broadcast to %s failed: %s", chat_id, e)
    await msg.answer(f"Рассылка завершена. Успешно: {sent}, ошибок: {failed}.")


if __name__ == '__main__':
    init_bot_db()
    load_known_chats()
    executor.start_polling(dp, skip_updates=True)
