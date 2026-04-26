from aiogram.client.default import DefaultBotProperties
import asyncio
import logging
import random
import html as html_module
import json
from typing import Optional

import os
import telebot
from dotenv import load_dotenv

import asyncpg
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.enums import ParseMode, ContentType
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaAnimation,
)

# ===================== НАСТРОЙКИ =====================

load_dotenv()

API_TOKEN = os.getenv("MAIN_BOT_TOKEN")
COMPLAINT_BOT_TOKEN = os.getenv("HELPER_BOT_TOKEN")

# PostgreSQL
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
DB_NAME = os.getenv("DB_NAME", "dating_bot")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))

# Путь к старой SQLite-базе (для миграции)
if os.path.exists("/app/data"):
    SQLITE_DB_PATH = "/app/data/dating_bot.db"
else:
    SQLITE_DB_PATH = "dating_bot.db"

ADMIN_IDS = {1056843400, 5002429263}
SUPPORT_USERNAME = "@hekomar"
HIDE_MATCHED_PROFILES = True

# === LIKE MESSAGE === Управление доступом
# Вписывай сюда Telegram ID пользователей, которым доступна кнопка 💌
ALLOWED_SENDER_IDS: list[int] = []
# Если True — кнопка 💌 доступна ВСЕМ пользователям
ALLOW_MESSAGES_FOR_ALL: bool = True

# ===================== БОТ ДЛЯ ЖАЛОБ =====================
COMPLAINT_CHAT_ID = 1056843400

main_bot = telebot.TeleBot(API_TOKEN)
helper_bot = telebot.TeleBot(COMPLAINT_BOT_TOKEN)

# ===================== PREMIUM EMOJI IDS =====================

EMOJI_SETTINGS = "5870982283724328568"
EMOJI_PROFILE = "5870994129244131212"
EMOJI_PEOPLE = "5870772616305839506"
EMOJI_CHECK = "5870633910337015697"
EMOJI_CROSS = "5870657884844462243"
EMOJI_PENCIL = "5870676941614354370"
EMOJI_HEART = "5963103826075456248"
EMOJI_INFO = "6028435952299413210"
EMOJI_BOT = "6030400221232501136"
EMOJI_BELL = "6039486778597970865"
EMOJI_PARTY = "6041731551845159060"
EMOJI_STATS = "5870921681735781843"
EMOJI_MEGAPHONE = "6039422865189638057"
EMOJI_LOCK = "6037249452824072506"
EMOJI_UNLOCK = "6037496202990194718"
EMOJI_TRASH = "5870875489362513438"
EMOJI_MEDIA = "6035128606563241721"
EMOJI_BACK = "5345906554510012647"
EMOJI_SMILE = "5870764288364252592"
EMOJI_EYE = "6037397706505195857"
EMOJI_HIDDEN = "6037243349675544634"
EMOJI_GIFT = "6032644646587338669"
EMOJI_SEND = "5963103826075456248"
EMOJI_DOWNLOAD = "6039802767931871481"

# ===================== LOGGING =====================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===================== BOT & DISPATCHER =====================

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ===================== FSM STATES =====================


class ProfileForm(StatesGroup):
    photo = State()
    name = State()
    age = State()
    faculty = State()
    about = State()
    gender = State()
    looking_for = State()


class EditPhotoForm(StatesGroup):
    waiting_photo = State()


class EditTextForm(StatesGroup):
    waiting_text = State()


class BroadcastForm(StatesGroup):
    waiting_message = State()


class BlacklistForm(StatesGroup):
    waiting_id = State()


class ComplaintForm(StatesGroup):
    waiting_text = State()


# === LIKE MESSAGE === Новое состояние для ожидания сообщения к лайку
class UserStates(StatesGroup):
    waiting_for_like_message = State()


# ===================== IN-MEMORY STORES =====================

current_targets: dict[int, int] = {}
user_queues: dict[int, list[int]] = {}

# ===================== ГЛОБАЛЬНЫЙ ПУЛ ASYNCPG =====================

pool: Optional[asyncpg.Pool] = None


async def create_pool():
    global pool
    pool = await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        host=DB_HOST,
        port=DB_PORT,
        min_size=2,
        max_size=10,
    )
    logger.info("PostgreSQL connection pool created")


# ===================== DATABASE — INIT =====================


async def init_db():
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT UNIQUE,
                tg_username TEXT,
                username TEXT,
                photo_file_id TEXT,
                gender TEXT,
                age INTEGER,
                faculty TEXT,
                about TEXT,
                is_active INTEGER DEFAULT 1,
                looking_for TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS swipes (
                id SERIAL PRIMARY KEY,
                viewer_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                is_like INTEGER NOT NULL DEFAULT 0,
                viewed_in_incoming INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                user_a_id INTEGER NOT NULL,
                user_b_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_a_id, user_b_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # === LIKE MESSAGE === Таблица для хранения сообщений к лайкам
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS like_messages (
                id SERIAL PRIMARY KEY,
                sender_tg_id BIGINT NOT NULL,
                target_tg_id BIGINT NOT NULL,
                content_type TEXT NOT NULL,
                file_id TEXT,
                text_content TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        row = await conn.fetchrow("SELECT value FROM settings WHERE key=$1", "hide_matched")
        if row is None:
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES ($1, $2)",
                "hide_matched", "1",
            )
    logger.info("Database tables initialized")


# ===================== МИГРАЦИЯ ИЗ SQLITE =====================


async def migrate_from_sqlite():
    marker = SQLITE_DB_PATH + ".migrated"
    if not os.path.exists(SQLITE_DB_PATH):
        logger.info("SQLite file not found — skipping migration")
        return
    if os.path.exists(marker):
        logger.info("Migration marker found — skipping migration")
        return

    logger.info("Starting migration from SQLite -> PostgreSQL ...")

    import aiosqlite

    sqlite_db = await aiosqlite.connect(SQLITE_DB_PATH)
    sqlite_db.row_factory = aiosqlite.Row

    # --- users ---
    rows = await sqlite_db.execute_fetchall("SELECT * FROM users")
    async with pool.acquire() as conn:
        for r in rows:
            r = dict(r)
            await conn.execute(
                """
                INSERT INTO users (tg_id, tg_username, username, photo_file_id,
                                   gender, age, faculty, about, is_active,
                                   looking_for, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
                ON CONFLICT (tg_id) DO NOTHING
                """,
                r.get("tg_id"), r.get("tg_username"), r.get("username"),
                r.get("photo_file_id"), r.get("gender"), r.get("age"),
                r.get("faculty"), r.get("about"), r.get("is_active"),
                r.get("looking_for")
            )
    logger.info(f"Migrated {len(rows)} users with current timestamp")

    # Маппинг старых SQLite id -> новые PG id
    id_map: dict[int, int] = {}
    async with pool.acquire() as conn:
        pg_users = await conn.fetch("SELECT id, tg_id FROM users")
    tg_to_pg = {row["tg_id"]: row["id"] for row in pg_users}

    sqlite_users = await sqlite_db.execute_fetchall("SELECT id, tg_id FROM users")
    for su in sqlite_users:
        su = dict(su)
        old_id = su["id"]
        tg_id = su["tg_id"]
        if tg_id in tg_to_pg:
            id_map[old_id] = tg_to_pg[tg_id]

    # --- swipes ---
    rows = await sqlite_db.execute_fetchall("SELECT * FROM swipes")
    async with pool.acquire() as conn:
        migrated_swipes = 0
        for r in rows:
            r = dict(r)
            new_viewer = id_map.get(r.get("viewer_id"))
            new_target = id_map.get(r.get("target_id"))

            if new_viewer is None or new_target is None:
                continue

            await conn.execute(
                """
                INSERT INTO swipes (viewer_id, target_id, is_like,
                                   viewed_in_incoming, created_at)
                VALUES ($1, $2, $3, $4, NOW())
                """,
                new_viewer,
                new_target,
                r.get("is_like", 0),
                r.get("viewed_in_incoming", 0)
            )
            migrated_swipes += 1
        logger.info(f"Migrated {migrated_swipes} swipes")

    # --- matches ---
    rows = await sqlite_db.execute_fetchall("SELECT * FROM matches")
    async with pool.acquire() as conn:
        migrated_matches = 0
        for r in rows:
            r = dict(r)
            new_a = id_map.get(r.get("user_a_id"))
            new_b = id_map.get(r.get("user_b_id"))
            if new_a is None or new_b is None:
                continue
            await conn.execute(
                """
                INSERT INTO matches (user_a_id, user_b_id, created_at)
                VALUES ($1,$2,$3)
                ON CONFLICT (user_a_id, user_b_id) DO NOTHING
                """,
                new_a, new_b, r.get("created_at"),
            )
            migrated_matches += 1
        logger.info(f"Migrated {migrated_matches} matches")

    # --- blacklist ---
    rows = await sqlite_db.execute_fetchall("SELECT * FROM blacklist")
    async with pool.acquire() as conn:
        for r in rows:
            r = dict(r)
            await conn.execute(
                """
                INSERT INTO blacklist (tg_id, created_at)
                VALUES ($1,$2)
                ON CONFLICT (tg_id) DO NOTHING
                """,
                r.get("tg_id"), r.get("created_at"),
            )
        logger.info(f"Migrated {len(rows)} blacklist entries")

    # --- settings ---
    rows = await sqlite_db.execute_fetchall("SELECT * FROM settings")
    async with pool.acquire() as conn:
        for r in rows:
            r = dict(r)
            await conn.execute(
                """
                INSERT INTO settings (key, value)
                VALUES ($1,$2)
                ON CONFLICT (key) DO NOTHING
                """,
                r.get("key"), r.get("value"),
            )
        logger.info(f"Migrated {len(rows)} settings")

    await sqlite_db.close()

    with open(marker, "w") as f:
        f.write("done")

    logger.info("Migration from SQLite -> PostgreSQL completed!")


# ===================== DATABASE — ФУНКЦИИ =====================


async def is_blacklisted(tg_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM blacklist WHERE tg_id=$1", tg_id)
    return row is not None


async def add_to_blacklist(tg_id: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blacklist (tg_id) VALUES ($1) ON CONFLICT (tg_id) DO NOTHING",
            tg_id,
        )


async def remove_from_blacklist(tg_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blacklist WHERE tg_id=$1", tg_id)


async def get_setting(key: str) -> Optional[str]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key=$1", key)
    return row["value"] if row else None


async def set_setting(key: str, value: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
            """,
            key, value,
        )


async def get_hide_matched() -> bool:
    val = await get_setting("hide_matched")
    return val == "1"


async def save_or_update_username(tg_id: int, tg_username: Optional[str]):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (tg_id, tg_username)
            VALUES ($1, $2)
            ON CONFLICT (tg_id) DO UPDATE SET tg_username=EXCLUDED.tg_username
            """,
            tg_id, tg_username,
        )


async def upsert_profile(
    tg_id: int,
    username: str,
    tg_username: Optional[str],
    photo_file_id: str,
    gender: str,
    age: int,
    faculty: Optional[str],
    about: str,
    is_active: int,
    looking_for: str,
):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM users WHERE tg_id=$1", tg_id)
        if row:
            await conn.execute(
                """
                UPDATE users SET
                    username=$1, tg_username=$2, photo_file_id=$3, gender=$4,
                    age=$5, faculty=$6, about=$7, is_active=$8, looking_for=$9,
                    updated_at=CURRENT_TIMESTAMP
                WHERE tg_id=$10
                """,
                username, tg_username, photo_file_id, gender,
                age, faculty, about, is_active, looking_for, tg_id,
            )
        else:
            await conn.execute(
                """
                INSERT INTO users (tg_id, tg_username, username, photo_file_id,
                                   gender, age, faculty, about, is_active, looking_for)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                tg_id, tg_username, username, photo_file_id,
                gender, age, faculty, about, is_active, looking_for,
            )


async def get_user_by_tg_id(tg_id: int) -> Optional[dict]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)
    return dict(row) if row else None


async def get_user_db_id(tg_id: int) -> Optional[int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM users WHERE tg_id=$1", tg_id)
    return row["id"] if row else None


async def set_user_active(tg_id: int, is_active: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_active=$1 WHERE tg_id=$2",
            is_active, tg_id,
        )


async def update_user_photo(tg_id: int, photo_file_id: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET photo_file_id=$1, updated_at=CURRENT_TIMESTAMP WHERE tg_id=$2",
            photo_file_id, tg_id,
        )


async def update_user_about(tg_id: int, about: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET about=$1, updated_at=CURRENT_TIMESTAMP WHERE tg_id=$2",
            about, tg_id,
        )


async def has_profile(tg_id: int) -> bool:
    user = await get_user_by_tg_id(tg_id)
    if not user:
        return False
    return user.get("username") is not None and user.get("photo_file_id") is not None


async def get_candidate_ids(viewer_db_id: int) -> list[int]:
    async with pool.acquire() as conn:
        viewer_row = await conn.fetchrow(
            "SELECT gender, looking_for FROM users WHERE id=$1", viewer_db_id
        )
        if not viewer_row:
            return []

        looking_for = viewer_row["looking_for"] or "all"

        gender_clause = ""
        if looking_for == "m":
            gender_clause = "AND u.gender='m'"
        elif looking_for == "f":
            gender_clause = "AND u.gender='f'"

        hide_matched = await get_hide_matched()
        match_clause = ""
        if hide_matched:
            match_clause = f"""
                AND u.id NOT IN (
                    SELECT user_b_id FROM matches WHERE user_a_id={viewer_db_id}
                    UNION
                    SELECT user_a_id FROM matches WHERE user_b_id={viewer_db_id}
                )
            """

        query = f"""
            SELECT u.id FROM users u
            WHERE u.id != $1
              AND u.is_active = 1
              AND u.username IS NOT NULL
              AND u.photo_file_id IS NOT NULL
              AND u.tg_id NOT IN (SELECT tg_id FROM blacklist)
              {gender_clause}
              {match_clause}
        """
        rows = await conn.fetch(query, viewer_db_id)
    return [r["id"] for r in rows]


async def get_next_profile_for_view(viewer_tg_id: int) -> Optional[dict]:
    viewer_db_id = await get_user_db_id(viewer_tg_id)
    if viewer_db_id is None:
        return None

    q = user_queues.get(viewer_db_id)
    if not q:
        candidates = await get_candidate_ids(viewer_db_id)
        if not candidates:
            return None
        random.shuffle(candidates)
        q = candidates
        user_queues[viewer_db_id] = q

    if not q:
        return None

    target_db_id = q.pop(0)
    user_queues[viewer_db_id] = q

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, tg_id, username, age, gender, looking_for,
                   faculty, about, photo_file_id
            FROM users WHERE id=$1
            """,
            target_db_id,
        )

    if not row:
        return await get_next_profile_for_view(viewer_tg_id)

    return dict(row)


async def get_incoming_likes_count(viewer_tg_id: int) -> int:
    viewer_db_id = await get_user_db_id(viewer_tg_id)
    if viewer_db_id is None:
        return 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as cnt FROM swipes
            WHERE target_id=$1 AND is_like=1 AND viewed_in_incoming=0
            """,
            viewer_db_id,
        )
    return row["cnt"] if row else 0


async def get_one_incoming_like_profile(viewer_tg_id: int) -> Optional[dict]:
    viewer_db_id = await get_user_db_id(viewer_tg_id)
    if viewer_db_id is None:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.id as swipe_id, u.tg_id, u.username, u.age,
                   u.faculty, u.about, u.photo_file_id
            FROM swipes s
            JOIN users u ON u.id = s.viewer_id
            WHERE s.target_id=$1 AND s.is_like=1 AND s.viewed_in_incoming=0
            ORDER BY s.created_at ASC
            LIMIT 1
            """,
            viewer_db_id,
        )

        if not row:
            return None

        result = dict(row)
        swipe_id = result["swipe_id"]
        await conn.execute(
            "UPDATE swipes SET viewed_in_incoming=1 WHERE id=$1", swipe_id
        )

    return result


async def add_like(viewer_tg_id: int, target_tg_id: int) -> bool:
    async with pool.acquire() as conn:
        v_row = await conn.fetchrow("SELECT id FROM users WHERE tg_id=$1", viewer_tg_id)
        t_row = await conn.fetchrow("SELECT id FROM users WHERE tg_id=$1", target_tg_id)
        if not v_row or not t_row:
            return False

        viewer_id = v_row["id"]
        target_id = t_row["id"]

        existing = await conn.fetchrow(
            "SELECT 1 FROM swipes WHERE viewer_id=$1 AND target_id=$2 AND is_like=1",
            viewer_id, target_id,
        )
        if not existing:
            await conn.execute(
                "INSERT INTO swipes (viewer_id, target_id, is_like) VALUES ($1, $2, 1)",
                viewer_id, target_id,
            )

        mutual_row = await conn.fetchrow(
            "SELECT 1 FROM swipes WHERE viewer_id=$1 AND target_id=$2 AND is_like=1 LIMIT 1",
            target_id, viewer_id,
        )
        mutual = mutual_row is not None

        if mutual:
            a, b = min(viewer_id, target_id), max(viewer_id, target_id)
            existing_match = await conn.fetchrow(
                "SELECT 1 FROM matches WHERE user_a_id=$1 AND user_b_id=$2",
                a, b,
            )
            if existing_match:
                return False
            await conn.execute(
                """
                INSERT INTO matches (user_a_id, user_b_id) VALUES ($1, $2)
                ON CONFLICT (user_a_id, user_b_id) DO NOTHING
                """,
                a, b,
            )

    return mutual


async def add_dislike(viewer_tg_id: int, target_tg_id: int):
    async with pool.acquire() as conn:
        v_row = await conn.fetchrow("SELECT id FROM users WHERE tg_id=$1", viewer_tg_id)
        t_row = await conn.fetchrow("SELECT id FROM users WHERE tg_id=$1", target_tg_id)
        if not v_row or not t_row:
            return
        await conn.execute(
            "INSERT INTO swipes (viewer_id, target_id, is_like) VALUES ($1, $2, 0)",
            v_row["id"], t_row["id"],
        )


async def get_total_likes_for_user(tg_id: int) -> int:
    db_id = await get_user_db_id(tg_id)
    if db_id is None:
        return 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(DISTINCT viewer_id) as cnt FROM swipes WHERE target_id=$1 AND is_like=1",
            db_id,
        )
    return row["cnt"] if row else 0


async def get_top_profiles(limit: int = 10) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.tg_id, u.username, u.age, u.faculty,
                   COUNT(DISTINCT s.viewer_id) as likes_count
            FROM users u
            LEFT JOIN swipes s ON s.target_id=u.id AND s.is_like=1
            WHERE u.username IS NOT NULL
            GROUP BY u.id
            ORDER BY likes_count DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def get_all_user_tg_ids() -> list[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_id FROM users WHERE tg_id IS NOT NULL")
    return [r["tg_id"] for r in rows]


# ===================== COMPLAINT HELPER =====================


async def send_complaint_to_bot(target_user: dict, complaint_text: str, complainant_username: Optional[str]):
    import aiohttp

    complainant_tag = f"@{complainant_username}" if complainant_username else "нет username"

    profile_text = format_profile_text(target_user)
    target_tg_id = target_user.get("tg_id", "?")
    msg1 = (
        f"🚨 <b>ЖАЛОБА НА АНКЕТУ</b>\n\n"
        f"{profile_text}\n\n"
        f"tg_id: <code>{target_tg_id}</code>"
    )

    msg2 = (
        f"📝 <b>Текст жалобы:</b>\n"
        f"{html_module.escape(complaint_text)}\n\n"
        f"👤 Жалобу отправил: {complainant_tag}"
    )

    url = f"https://api.telegram.org/bot{COMPLAINT_BOT_TOKEN}/sendMessage"

    async with aiohttp.ClientSession() as session:
        photo_id = target_user.get("photo_file_id")
        if photo_id:
            photo_url = f"https://api.telegram.org/bot{COMPLAINT_BOT_TOKEN}/sendPhoto"
            payload_photo = {
                "chat_id": COMPLAINT_CHAT_ID,
                "photo": photo_id,
                "caption": msg1,
                "parse_mode": "HTML",
            }
            async with session.post(photo_url, json=payload_photo) as resp:
                result = await resp.json()
                if not result.get("ok"):
                    payload_text = {
                        "chat_id": COMPLAINT_CHAT_ID,
                        "text": msg1 + "\n\n⚠️ (фото недоступно для этого бота)",
                        "parse_mode": "HTML",
                    }
                    async with session.post(url, json=payload_text) as resp2:
                        pass
        else:
            payload1 = {
                "chat_id": COMPLAINT_CHAT_ID,
                "text": msg1,
                "parse_mode": "HTML",
            }
            async with session.post(url, json=payload1) as resp:
                pass

        payload2 = {
            "chat_id": COMPLAINT_CHAT_ID,
            "text": msg2,
            "parse_mode": "HTML",
        }
        async with session.post(url, json=payload2) as resp:
            pass


# ===================== KEYBOARDS =====================


def main_menu_kb(has_profile_flag: bool = True) -> dict:
    if has_profile_flag:
        keyboard = {
            "keyboard": [
                [{"text": "1"}, {"text": "2"}, {"text": "3"}, {"text": "4"}, {"text": "5"}],
            ],
            "resize_keyboard": True,
        }
    else:
        keyboard = {
            "keyboard": [
                [{"text": "2"}, {"text": "4"}, {"text": "5"}],
            ],
            "resize_keyboard": True,
        }
    return keyboard


def my_profile_menu_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "📋 Смотреть анкеты"}, {"text": "🔄 Заполнить заново"}],
            [{"text": "🖼 Изменить фото"}, {"text": "✏️ Изменить текст"}],
            [{"text": "🔙 Главное меню"}],
        ],
        "resize_keyboard": True,
    }


# === LIKE MESSAGE === Клавиатура просмотра анкет с кнопкой 💌 (для разрешённых пользователей)
def browse_kb(show_message_button: bool = False) -> dict:
    if show_message_button:
        return {
            "keyboard": [
                [{"text": "❤️"}, {"text": "💌"}, {"text": "👎"}, {"text": "⚠️"}, {"text": "💤"}],
            ],
            "resize_keyboard": True,
        }
    return {
        "keyboard": [
            [{"text": "❤️"}, {"text": "👎"}, {"text": "⚠️"}, {"text": "💤"}],
        ],
        "resize_keyboard": True,
    }


def complaint_confirm_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "Пожаловаться"}, {"text": "Назад"}],
        ],
        "resize_keyboard": True,
    }


def incoming_like_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "❤️"}, {"text": "👎"}],
        ],
        "resize_keyboard": True,
    }


def view_likes_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "Посмотреть"}],
        ],
        "resize_keyboard": True,
    }


def gender_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "Парень"}],
            [{"text": "Девушка"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def looking_for_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "Парня"}],
            [{"text": "Девушку"}],
            [{"text": "Всех"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def admin_menu_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "Топ-10 анкет"}],
            [{"text": "Чёрный список"}],
            [{"text": "Рассылка"}],
            [{"text": "Тумблер мэтча"}],
            [{"text": "Выйти из админки"}],
        ],
        "resize_keyboard": True,
    }


def blacklist_menu_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "Добавить в ЧС"}],
            [{"text": "Убрать из ЧС"}],
            [{"text": "Назад в админку"}],
        ],
        "resize_keyboard": True,
    }


async def send_with_custom_kb(chat_id: int, text: str, keyboard_dict: dict, **kwargs):
    import aiohttp

    url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard_dict),
    }
    payload.update(kwargs)

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            result = await resp.json()
            return result


async def send_photo_with_custom_kb(chat_id: int, photo: str, caption: str, keyboard_dict: dict):
    import aiohttp

    url = f"https://api.telegram.org/bot{API_TOKEN}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard_dict),
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()


# ===================== HELPERS =====================


# === LIKE MESSAGE === Проверка, доступна ли кнопка 💌 пользователю
def can_send_like_message(tg_id: int) -> bool:
    if ALLOW_MESSAGES_FOR_ALL:
        return True
    return tg_id in ALLOWED_SENDER_IDS


def get_clickable_username(user: dict) -> str:
    tg_username = user.get("tg_username")
    tg_id = user.get("tg_id")
    if tg_username:
        tg_username = tg_username.lstrip("@")
        safe = html_module.escape(tg_username)
        return f'<a href="https://t.me/{safe}">@{safe}</a>'
    return f'<a href="tg://user?id={tg_id}">профиль</a>'


def format_profile_text(user: dict, show_status: bool = False) -> str:
    username = user.get("username", "—")
    age = user.get("age", "?")
    faculty = user.get("faculty")
    about = user.get("about", "")

    text = f"<b>{html_module.escape(str(username))}</b>, {age}\n"

    if faculty:
        text += f"Факультет: {html_module.escape(str(faculty))}\n"

    text += f"\n{html_module.escape(str(about))}"

    if show_status:
        is_active = user.get("is_active", 1)
        gender = user.get("gender", "?")
        looking_for = user.get("looking_for", "all")

        gender_text = "Парень" if gender == "m" else "Девушка" if gender == "f" else "?"
        lf_text = "Парней" if looking_for == "m" else "Девушек" if looking_for == "f" else "Всех"

        text += f"\n\nПол: {gender_text}"
        text += f"\nИщу: {lf_text}"

        if is_active == 1:
            text += f"\nСтатус: ✅ активна"
        else:
            text += f"\nСтатус: ❌ неактивна"

        text += f"\n\n🟢 Включить анкету — /activate"
        text += f"\n🔴 Выключить анкету — /deactivate"

    return text


async def send_profile_card(chat_id: int, user: dict, keyboard_dict: dict, show_status: bool = False):
    text = format_profile_text(user, show_status=show_status)
    photo_id = user.get("photo_file_id")

    if photo_id:
        await send_photo_with_custom_kb(chat_id, photo_id, text, keyboard_dict)
    else:
        await send_with_custom_kb(chat_id, text, keyboard_dict)


def main_menu_text(has_profile_flag: bool = True) -> str:
    if has_profile_flag:
        return (
            "📋 <b>Меню:</b>\n\n"
            "1. Смотреть анкеты\n"
            "2. Заполнить анкету\n"
            "3. Моя анкета\n"
            "4. Поддержка\n"
            "***\n"
            "5. Поддержать автора"
        )
    else:
        return (
            "📋 <b>Меню:</b>\n\n"
            "2. Заполнить анкету\n"
            "4. Поддержка\n"
            "***\n"
            "5. Поддержать автора"
        )


async def show_main_menu(message: Message):
    tg_id = message.from_user.id
    hp = await has_profile(tg_id)
    text = main_menu_text(hp)
    await send_with_custom_kb(message.chat.id, text, main_menu_kb(hp))


# ===================== BLACKLIST CHECK =====================


async def check_blacklist(message: Message) -> bool:
    if await is_blacklisted(message.from_user.id):
        await message.answer("🔒 <b>Вы в чёрном списке.</b>")
        return True
    return False


# ===================== /start =====================


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id
    tg_username = message.from_user.username

    if await is_blacklisted(tg_id):
        await message.answer("🔒 <b>Вы в чёрном списке.</b>")
        return

    await save_or_update_username(tg_id, tg_username)

    hp = await has_profile(tg_id)

    if hp:
        await message.answer("🙂 <b>Привет! Это бот знакомств.</b>")
        await show_main_menu(message)
    else:
        text = (
            "🙂 <b>Привет! Это бот знакомств.</b>\n\n"
            "У тебя ещё нет анкеты. Давай заполним!\n\n"
        )
        text += main_menu_text(False)
        await send_with_custom_kb(message.chat.id, text, main_menu_kb(False))


# ===================== /activate /deactivate =====================


@router.message(Command("activate"))
async def cmd_activate(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    tg_id = message.from_user.id
    if not await has_profile(tg_id):
        await message.answer("❌ У тебя нет анкеты. Сначала заполни!")
        return

    await set_user_active(tg_id, 1)

    db_id = await get_user_db_id(tg_id)
    if db_id:
        user_queues.pop(db_id, None)

    hp = await has_profile(tg_id)
    await send_with_custom_kb(
        message.chat.id,
        "✅ <b>Ваша анкета включена!</b>",
        main_menu_kb(hp),
    )
    await show_main_menu(message)


@router.message(Command("deactivate"))
async def cmd_deactivate(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    tg_id = message.from_user.id
    if not await has_profile(tg_id):
        await message.answer("❌ У тебя нет анкеты. Сначала заполни!")
        return

    await set_user_active(tg_id, 0)

    db_id = await get_user_db_id(tg_id)
    if db_id:
        user_queues.pop(db_id, None)

    hp = await has_profile(tg_id)
    await send_with_custom_kb(
        message.chat.id,
        "❌ <b>Ваша анкета выключена!</b>",
        main_menu_kb(hp),
    )
    await show_main_menu(message)


# ===================== /admin =====================


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await send_with_custom_kb(
        message.chat.id,
        "⚙️ <b>Админ-панель</b>",
        admin_menu_kb(),
    )


# ===================== КНОПКА 2 — ЗАПОЛНИТЬ АНКЕТУ =====================


@router.message(F.text == "2")
async def start_fill_profile(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return
    await state.clear()
    await state.set_state(ProfileForm.photo)
    await message.answer(
        "🖼 <b>Отправь своё фото</b> (одну фотографию).",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ProfileForm.photo, F.content_type == ContentType.PHOTO)
async def process_photo(message: Message, state: FSMContext):
    photo_file_id = message.photo[-1].file_id
    await state.update_data(photo_file_id=photo_file_id)
    await state.set_state(ProfileForm.name)
    await message.answer("🖋 Как тебя зовут? (имя или ник)")


@router.message(ProfileForm.photo)
async def process_photo_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Пришли своё <b>фото</b>, пожалуйста.")


@router.message(ProfileForm.name, F.text)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name or len(name) > 50:
        await message.answer("❌ Имя не может быть пустым или длиннее 50 символов.")
        return
    await state.update_data(username=name)
    await state.set_state(ProfileForm.age)
    await message.answer("🖋 Сколько тебе лет?")


@router.message(ProfileForm.name)
async def process_name_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Отправь <b>текст</b> — своё имя.")


@router.message(ProfileForm.age, F.text)
async def process_age(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❌ Напиши возраст <b>цифрами</b>.")
        return
    age = int(text)
    if age < 17 or age > 100:
        await message.answer("❌ Возраст должен быть от <b>17</b> до <b>100</b>.")
        return
    await state.update_data(age=age)
    await state.set_state(ProfileForm.faculty)
    await message.answer(
        "🖋 Напиши свой факультет/направление.\n"
        "Если не хочешь указывать — отправь <b>-</b>"
    )


@router.message(ProfileForm.age)
async def process_age_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Отправь <b>число</b> — свой возраст.")


@router.message(ProfileForm.faculty, F.text)
async def process_faculty(message: Message, state: FSMContext):
    faculty = message.text.strip()
    if faculty == "-":
        faculty = None
    await state.update_data(faculty=faculty)
    await state.set_state(ProfileForm.about)
    await message.answer("🖋 Напиши краткое описание о себе.")


@router.message(ProfileForm.faculty)
async def process_faculty_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Отправь <b>текст</b>.")


@router.message(ProfileForm.about, F.text)
async def process_about(message: Message, state: FSMContext):
    about = message.text.strip()
    if not about:
        await message.answer("❌ Описание не может быть пустым.")
        return
    await state.update_data(about=about)
    await state.set_state(ProfileForm.gender)

    await send_with_custom_kb(
        message.chat.id,
        "👤 <b>Я:</b>",
        gender_kb(),
    )


@router.message(ProfileForm.about)
async def process_about_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Отправь <b>текст</b> — описание о себе.")


@router.message(ProfileForm.gender, F.text.in_({"Парень", "Девушка"}))
async def process_gender(message: Message, state: FSMContext):
    gender = "m" if message.text == "Парень" else "f"
    await state.update_data(gender=gender)
    await state.set_state(ProfileForm.looking_for)

    await send_with_custom_kb(
        message.chat.id,
        "👥 <b>Кого ищу:</b>",
        looking_for_kb(),
    )


@router.message(ProfileForm.gender)
async def process_gender_invalid(message: Message, state: FSMContext):
    await send_with_custom_kb(
        message.chat.id,
        "❌ Выбери <b>Парень</b> или <b>Девушка</b>.",
        gender_kb(),
    )


@router.message(ProfileForm.looking_for, F.text.in_({"Парня", "Девушку", "Всех"}))
async def process_looking_for(message: Message, state: FSMContext):
    mapping = {"Парня": "m", "Девушку": "f", "Всех": "all"}
    looking_for = mapping[message.text]
    await state.update_data(looking_for=looking_for)

    data = await state.get_data()
    await state.clear()

    tg_id = message.from_user.id
    tg_username = message.from_user.username

    await upsert_profile(
        tg_id=tg_id,
        username=data["username"],
        tg_username=tg_username,
        photo_file_id=data["photo_file_id"],
        gender=data["gender"],
        age=data["age"],
        faculty=data.get("faculty"),
        about=data["about"],
        is_active=1,
        looking_for=looking_for,
    )

    db_id = await get_user_db_id(tg_id)
    if db_id:
        user_queues.pop(db_id, None)

    user = await get_user_by_tg_id(tg_id)

    await message.answer("✅ <b>Вот твоя анкета:</b>")
    await send_profile_card(message.chat.id, user, main_menu_kb(True), show_status=True)
    await show_main_menu(message)


@router.message(ProfileForm.looking_for)
async def process_looking_for_invalid(message: Message, state: FSMContext):
    await send_with_custom_kb(
        message.chat.id,
        "❌ Выбери: <b>Парня</b>, <b>Девушку</b> или <b>Всех</b>.",
        looking_for_kb(),
    )


# ===================== КНОПКА 3 — МОЯ АНКЕТА =====================


@router.message(F.text == "3")
async def my_profile(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return
    await state.clear()

    user = await get_user_by_tg_id(message.from_user.id)
    if not user or not user.get("username"):
        await send_with_custom_kb(
            message.chat.id,
            "❌ У тебя нет анкеты. Нажми <b>2</b> чтобы заполнить.",
            main_menu_kb(False),
        )
        return

    await send_profile_card(message.chat.id, user, my_profile_menu_kb(), show_status=True)


# ===================== ПОДМЕНЮ "МОЯ АНКЕТА" =====================


@router.message(F.text == "📋 Смотреть анкеты")
async def my_profile_browse(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return
    await state.clear()

    if not await has_profile(message.from_user.id):
        await send_with_custom_kb(
            message.chat.id,
            "❌ Сначала заполни анкету! Нажми <b>2</b>.",
            main_menu_kb(False),
        )
        return

    incoming_count = await get_incoming_likes_count(message.from_user.id)
    if incoming_count > 0:
        word = "человеку" if incoming_count == 1 else "людям"
        await send_with_custom_kb(
            message.chat.id,
            f"❤️ Ты понравился <b>{incoming_count}</b> {word}!",
            view_likes_kb(),
        )
        return

    await show_random_profile(message)


@router.message(F.text == "🔄 Заполнить заново")
async def my_profile_refill(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return
    await state.clear()
    await state.set_state(ProfileForm.photo)
    await message.answer(
        "🖼 <b>Отправь своё фото</b> (одну фотографию).",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == "🖼 Изменить фото")
async def my_profile_change_photo(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    if not await has_profile(message.from_user.id):
        await send_with_custom_kb(
            message.chat.id,
            "❌ У тебя нет анкеты. Нажми <b>2</b> чтобы заполнить.",
            main_menu_kb(False),
        )
        return

    await state.clear()
    await state.set_state(EditPhotoForm.waiting_photo)
    await message.answer(
        "🖼 <b>Отправь новое фото</b> для анкеты.\n\nДля отмены отправь /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel"), EditPhotoForm.waiting_photo)
async def cancel_edit_photo(message: Message, state: FSMContext):
    await state.clear()
    user = await get_user_by_tg_id(message.from_user.id)
    if user and user.get("username"):
        await send_profile_card(message.chat.id, user, my_profile_menu_kb(), show_status=True)
    else:
        await show_main_menu(message)


@router.message(EditPhotoForm.waiting_photo, F.content_type == ContentType.PHOTO)
async def process_edit_photo(message: Message, state: FSMContext):
    photo_file_id = message.photo[-1].file_id
    await update_user_photo(message.from_user.id, photo_file_id)
    await state.clear()

    user = await get_user_by_tg_id(message.from_user.id)
    await message.answer("✅ <b>Фото обновлено!</b>")
    await send_profile_card(message.chat.id, user, my_profile_menu_kb(), show_status=True)


@router.message(EditPhotoForm.waiting_photo)
async def process_edit_photo_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Пришли <b>фото</b>, пожалуйста. Для отмены: /cancel")


@router.message(F.text == "✏️ Изменить текст")
async def my_profile_change_text(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    if not await has_profile(message.from_user.id):
        await send_with_custom_kb(
            message.chat.id,
            "❌ У тебя нет анкеты. Нажми <b>2</b> чтобы заполнить.",
            main_menu_kb(False),
        )
        return

    await state.clear()
    await state.set_state(EditTextForm.waiting_text)
    await message.answer(
        "✏️ <b>Отправь новый текст описания</b> для анкеты.\n\nДля отмены отправь /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel"), EditTextForm.waiting_text)
async def cancel_edit_text(message: Message, state: FSMContext):
    await state.clear()
    user = await get_user_by_tg_id(message.from_user.id)
    if user and user.get("username"):
        await send_profile_card(message.chat.id, user, my_profile_menu_kb(), show_status=True)
    else:
        await show_main_menu(message)


@router.message(EditTextForm.waiting_text, F.text)
async def process_edit_text(message: Message, state: FSMContext):
    new_text = message.text.strip()
    if not new_text:
        await message.answer("❌ Текст не может быть пустым.")
        return

    await update_user_about(message.from_user.id, new_text)
    await state.clear()

    user = await get_user_by_tg_id(message.from_user.id)
    await message.answer("✅ <b>Текст анкеты обновлён!</b>")
    await send_profile_card(message.chat.id, user, my_profile_menu_kb(), show_status=True)


@router.message(EditTextForm.waiting_text)
async def process_edit_text_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Отправь <b>текст</b>. Для отмены: /cancel")


@router.message(F.text == "🔙 Главное меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message)


# ===================== КНОПКА 4 — ПОДДЕРЖКА =====================


@router.message(F.text == "4")
async def support(message: Message, state: FSMContext):
    await state.clear()
    hp = await has_profile(message.from_user.id)
    await send_with_custom_kb(
        message.chat.id,
        f"ℹ️ <b>Поддержка</b>\n\n"
        f"По вопросам бота пиши: {SUPPORT_USERNAME}",
        main_menu_kb(hp),
    )
    await show_main_menu(message)


# ===================== КНОПКА 5 — ПОДДЕРЖАТЬ АВТОРА =====================


@router.message(F.text == "5")
async def donate_author(message: Message, state: FSMContext):
    await state.clear()
    hp = await has_profile(message.from_user.id)

    donate_text = (
        "💝 <b>Поддержите донатом))</b>\n\n"
        "<blockquote>Реквизиты(Юmoney):\n\n"
        " 5599002134180282</blockquote>"
    )
    await send_with_custom_kb(
        message.chat.id,
        donate_text,
        {"remove_keyboard": True},
    )

    await asyncio.sleep(10)
    await show_main_menu(message)


# ===================== КНОПКА 1 — СМОТРЕТЬ АНКЕТЫ =====================


@router.message(F.text == "1")
async def browse_profiles(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return
    await state.clear()

    if not await has_profile(message.from_user.id):
        await send_with_custom_kb(
            message.chat.id,
            "❌ Сначала заполни анкету! Нажми <b>2</b>.",
            main_menu_kb(False),
        )
        return

    incoming_count = await get_incoming_likes_count(message.from_user.id)
    if incoming_count > 0:
        word = "человеку" if incoming_count == 1 else "людям"
        await send_with_custom_kb(
            message.chat.id,
            f"❤️ Ты понравился <b>{incoming_count}</b> {word}!",
            view_likes_kb(),
        )
        return

    await show_random_profile(message)


async def show_random_profile(message: Message):
    profile = await get_next_profile_for_view(message.from_user.id)
    if not profile:
        hp = await has_profile(message.from_user.id)
        await send_with_custom_kb(
            message.chat.id,
            "❌ Пока нет подходящих анкет. Попробуй позже!",
            main_menu_kb(hp),
        )
        current_targets.pop(message.from_user.id, None)
        return

    current_targets[message.from_user.id] = profile["tg_id"]
    # === LIKE MESSAGE === Показываем кнопку 💌 если у пользователя есть доступ
    show_msg_btn = can_send_like_message(message.from_user.id)
    await send_profile_card(message.chat.id, profile, browse_kb(show_message_button=show_msg_btn))


async def show_incoming_like_profile(message: Message):
    profile = await get_one_incoming_like_profile(message.from_user.id)
    if profile:
        current_targets[message.from_user.id] = profile["tg_id"]

        remaining = await get_incoming_likes_count(message.from_user.id)

        await send_profile_card(message.chat.id, profile, incoming_like_kb())

        if remaining > 0:
            word = "анкета" if remaining == 1 else "анкет"
            await send_with_custom_kb(
                message.chat.id,
                f"🔔 Ещё <b>{remaining}</b> {word}",
                incoming_like_kb(),
            )
    else:
        await show_random_profile(message)


# ===================== ПОСМОТРЕТЬ (входящие лайки) =====================


@router.message(F.text == "Посмотреть")
async def view_incoming_likes(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return
    await state.clear()
    await show_incoming_like_profile(message)


# ===================== 💌 СООБЩЕНИЕ К ЛАЙКУ (LIKE MESSAGE) =====================


@router.message(F.text == "💌")
async def handle_like_message_button(message: Message, state: FSMContext):
    """
    === LIKE MESSAGE ===
    Обработчик кнопки 💌 — переводит пользователя в режим ввода сообщения к анкете.
    """
    if await check_blacklist(message):
        return

    user_tg_id = message.from_user.id

    # Проверка доступа
    if not can_send_like_message(user_tg_id):
        await message.answer("❌ Эта функция пока недоступна.")
        return

    target_tg_id = current_targets.get(user_tg_id)
    if target_tg_id is None:
        await message.answer(
            "❌ Не могу понять, кому отправить сообщение. Нажми <b>1</b> чтобы смотреть анкеты."
        )
        return

    # Сохраняем target_tg_id в FSM и переводим в состояние ожидания сообщения
    await state.update_data(like_message_target_tg_id=target_tg_id)
    await state.set_state(UserStates.waiting_for_like_message)

    await message.answer(
        "💌 <b>Отправь сообщение, которое получит этот человек вместе с твоей анкетой.</b>\n\n"
        "Можно отправить: текст, фото, видео или кружок (видеосообщение).\n\n"
        "Для отмены отправь /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel"), UserStates.waiting_for_like_message)
async def cancel_like_message(message: Message, state: FSMContext):
    """
    === LIKE MESSAGE ===
    Отмена ввода сообщения к лайку — возвращаем обратно к анкете.
    """
    data = await state.get_data()
    target_tg_id = data.get("like_message_target_tg_id")
    await state.clear()

    if target_tg_id:
        # Восстанавливаем current_target, чтобы пользователь мог продолжить листать
        current_targets[message.from_user.id] = target_tg_id
        target_user = await get_user_by_tg_id(target_tg_id)
        if target_user:
            show_msg_btn = can_send_like_message(message.from_user.id)
            await send_profile_card(message.chat.id, target_user, browse_kb(show_message_button=show_msg_btn))
            return

    await show_random_profile(message)


@router.message(
    UserStates.waiting_for_like_message,
    F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.VIDEO_NOTE})
)
async def process_like_message(message: Message, state: FSMContext):
    """
    === LIKE MESSAGE ===
    Принимает сообщение (текст/фото/видео/кружок), записывает лайк,
    отправляет целевому пользователю сообщение + анкету отправителя с кнопками ❤️/💔.
    """
    if await check_blacklist(message):
        return

    data = await state.get_data()
    target_tg_id = data.get("like_message_target_tg_id")
    await state.clear()

    user_tg_id = message.from_user.id

    if not target_tg_id:
        await message.answer("❌ Ошибка. Попробуй заново.")
        await show_main_menu(message)
        return

    # Определяем тип контента и file_id
    content_type = message.content_type
    file_id = None
    text_content = None

    if content_type == ContentType.TEXT:
        text_content = message.text
    elif content_type == ContentType.PHOTO:
        file_id = message.photo[-1].file_id
        text_content = message.caption  # может быть None
    elif content_type == ContentType.VIDEO:
        file_id = message.video.file_id
        text_content = message.caption
    elif content_type == ContentType.VIDEO_NOTE:
        file_id = message.video_note.file_id

    # Сохраняем сообщение в БД
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO like_messages (sender_tg_id, target_tg_id, content_type, file_id, text_content, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            """,
            user_tg_id, target_tg_id, content_type, file_id, text_content,
        )

    # Записываем лайк (как обычный)
    mutual = await add_like(user_tg_id, target_tg_id)

    if mutual:
        # === Если уже взаимный лайк — сразу матч, как в обычном handle_like ===
        user_a = await get_user_by_tg_id(user_tg_id)
        user_b = await get_user_by_tg_id(target_tg_id)

        link_a = get_clickable_username(user_a) if user_a else "?"
        link_b = get_clickable_username(user_b) if user_b else "?"

        # Уведомляем отправителя
        await message.answer(
            f"🎉 <b>У вас взаимная симпатия!</b>\n\n"
            f"Лови ссылку: {link_b}\n\n"
            f"<i>Сообщение было доставлено. Чтобы продолжить — нажми 1.</i>",
            parse_mode=ParseMode.HTML,
        )

        # Уведомляем получателя
        try:
            # Сначала отправляем сообщение от отправителя
            await bot.send_message(
                target_tg_id,
                "💌 <b>Тебе прислали сообщение к анкете!</b>",
                parse_mode=ParseMode.HTML,
            )
            # Отправляем само медиа/текст
            await _send_like_media_to_target(target_tg_id, content_type, file_id, text_content)

            # Отправляем уведомление о мэтче
            await bot.send_message(
                target_tg_id,
                f"🎉 <b>У вас взаимная симпатия!</b>\n"
                f"Напиши: {link_a}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Failed to notify target {target_tg_id} about mutual match with message: {e}")

        # Показываем главное меню отправителю
        await show_main_menu(message)
        return

    # === Лайк НЕ взаимный — отправляем получателю сообщение + анкету отправителя с InlineKeyboard ===
    sender_user = await get_user_by_tg_id(user_tg_id)

    try:
        # 1) Заголовок
        await bot.send_message(
            target_tg_id,
            "💌 <b>Тебе прислали сообщение к анкете!</b>",
            parse_mode=ParseMode.HTML,
        )

        # 2) Само сообщение пользователя
        await _send_like_media_to_target(target_tg_id, content_type, file_id, text_content)

        # 3) Анкета отправителя с inline-кнопками ❤️ и 💔
        if sender_user:
            profile_text = format_profile_text(sender_user)
            # Inline-кнопки: like_msg_SENDER_TG_ID  /  dislike_msg_SENDER_TG_ID
            inline_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="❤️", callback_data=f"like_msg_{user_tg_id}"),
                        InlineKeyboardButton(text="💔", callback_data=f"dislike_msg_{user_tg_id}"),
                    ]
                ]
            )
            photo_id = sender_user.get("photo_file_id")
            if photo_id:
                await bot.send_photo(
                    target_tg_id,
                    photo=photo_id,
                    caption=profile_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=inline_kb,
                )
            else:
                await bot.send_message(
                    target_tg_id,
                    profile_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=inline_kb,
                )

    except Exception as e:
        logger.error(f"Failed to send like message to {target_tg_id}: {e}")

    # Подтверждаем отправителю
    await message.answer("✅ <b>Сообщение отправлено!</b> Продолжаем листать анкеты...")

    # Показываем следующую анкету
    incoming = await get_incoming_likes_count(user_tg_id)
    if incoming > 0:
        await show_incoming_like_profile(message)
    else:
        await show_random_profile(message)


@router.message(UserStates.waiting_for_like_message)
async def process_like_message_invalid(message: Message, state: FSMContext):
    """
    === LIKE MESSAGE ===
    Если пользователь отправил неподдерживаемый тип контента.
    """
    await message.answer(
        "❌ Отправь <b>текст</b>, <b>фото</b>, <b>видео</b> или <b>кружок</b>.\n"
        "Для отмены: /cancel"
    )


# === LIKE MESSAGE === Хелпер для отправки медиа получателю
async def _send_like_media_to_target(target_tg_id: int, content_type: str, file_id: Optional[str], text_content: Optional[str]):
    """Отправляет само медиа-сообщение (текст/фото/видео/кружок) получателю."""
    try:
        if content_type == ContentType.TEXT and text_content:
            await bot.send_message(
                target_tg_id,
                f"💬 {html_module.escape(text_content)}",
                parse_mode=ParseMode.HTML,
            )
        elif content_type == ContentType.PHOTO and file_id:
            if text_content:
                await bot.send_photo(
                    target_tg_id,
                    photo=file_id,
                    caption=f"💬 {html_module.escape(text_content)}",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await bot.send_photo(target_tg_id, photo=file_id)
        elif content_type == ContentType.VIDEO and file_id:
            if text_content:
                await bot.send_video(
                    target_tg_id,
                    video=file_id,
                    caption=f"💬 {html_module.escape(text_content)}",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await bot.send_video(target_tg_id, video=file_id)
        elif content_type == ContentType.VIDEO_NOTE and file_id:
            await bot.send_video_note(target_tg_id, video_note=file_id)
    except Exception as e:
        logger.error(f"Failed to send like media to {target_tg_id}: {e}")


# === LIKE MESSAGE === Inline callback: ❤️ ответный лайк на сообщение с анкетой
@router.callback_query(F.data.startswith("like_msg_"))
async def handle_like_msg_callback(callback: CallbackQuery, state: FSMContext):
    """
    === LIKE MESSAGE ===
    Обработчик нажатия ❤️ под анкетой, пришедшей вместе с сообщением.
    Засчитывает ответный лайк. Если мэтч — выдаёт контакты обоим.
    """
    await callback.answer()

    target_tg_id = callback.from_user.id  # тот, кто нажал ❤️ (получатель сообщения)
    sender_tg_id_str = callback.data.replace("like_msg_", "")

    try:
        sender_tg_id = int(sender_tg_id_str)
    except ValueError:
        return

    # Записываем ответный лайк
    mutual = await add_like(target_tg_id, sender_tg_id)

    if mutual:
        user_a = await get_user_by_tg_id(target_tg_id)
        user_b = await get_user_by_tg_id(sender_tg_id)

        link_a = get_clickable_username(user_a) if user_a else "?"
        link_b = get_clickable_username(user_b) if user_b else "?"

        # Уведомляем получателя (кто нажал ❤️)
        try:
            await bot.send_message(
                target_tg_id,
                f"🎉 <b>У вас взаимная симпатия!</b>\n\n"
                f"Напиши: {link_b}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Failed to notify {target_tg_id} about match: {e}")

        # Уведомляем отправителя (кто отправлял 💌)
        try:
            await bot.send_message(
                sender_tg_id,
                f"🎉 <b>У вас взаимная симпатия!</b>\n"
                f"Твоё сообщение понравилось! Напиши: {link_a}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Failed to notify sender {sender_tg_id} about match: {e}")
    else:
        # Не мэтч (лайк уже был, или первый ответный) — уведомим что лайк засчитан
        try:
            await bot.send_message(
                target_tg_id,
                "❤️ <b>Лайк отправлен!</b> Если симпатия взаимная — вы получите контакты друг друга.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        # Уведомляем отправителя о входящем лайке
        sender_db_id = await get_user_db_id(sender_tg_id)
        if sender_db_id:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM swipes WHERE target_id=$1 AND is_like=1 AND viewed_in_incoming=0",
                    sender_db_id,
                )
            count = row["cnt"] if row else 0
            if count > 0:
                word = "человеку" if count == 1 else "людям"
                try:
                    await send_with_custom_kb(
                        sender_tg_id,
                        f"❤️ Ты понравился <b>{count}</b> {word}!",
                        view_likes_kb(),
                    )
                except Exception:
                    pass

    # Убираем inline-кнопки с сообщения
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# === LIKE MESSAGE === Inline callback: 💔 ответный дизлайк на сообщение с анкетой
@router.callback_query(F.data.startswith("dislike_msg_"))
async def handle_dislike_msg_callback(callback: CallbackQuery, state: FSMContext):
    """
    === LIKE MESSAGE ===
    Обработчик нажатия 💔 под анкетой, пришедшей вместе с сообщением.
    Засчитывает дизлайк.
    """
    await callback.answer()

    target_tg_id = callback.from_user.id  # тот, кто нажал 💔
    sender_tg_id_str = callback.data.replace("dislike_msg_", "")

    try:
        sender_tg_id = int(sender_tg_id_str)
    except ValueError:
        return

    # Записываем дизлайк
    await add_dislike(target_tg_id, sender_tg_id)

    try:
        await bot.send_message(
            target_tg_id,
            "👎 <b>Анкета пропущена.</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    # Убираем inline-кнопки
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ===================== ❤️ ЛАЙК =====================


@router.message(F.text == "❤️")
async def handle_like(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    user_tg_id = message.from_user.id
    target_tg_id = current_targets.get(user_tg_id)

    if target_tg_id is None:
        await message.answer(
            "❌ Не могу понять, кого лайкать. Нажми <b>1</b> чтобы смотреть анкеты."
        )
        return

    # Записываем лайк и проверяем на взаимность
    mutual = await add_like(user_tg_id, target_tg_id)

    if mutual:
        user_a = await get_user_by_tg_id(user_tg_id)
        user_b = await get_user_by_tg_id(target_tg_id)

        link_a = get_clickable_username(user_a) if user_a else "?"
        link_b = get_clickable_username(user_b) if user_b else "?"

        # Отправляем сообщение ТЕБЕ (сразу даем ссылку)
        await message.answer(
            f"🎉 <b>У вас взаимная симпатия!</b>\n\n"
            f"Лови ссылку: {link_b}\n\n"
            f"<i>Чтобы продолжить смотреть анкеты, нажми ❤️ еще раз.</i>",
            parse_mode=ParseMode.HTML,
        )

        # Уведомляем ТОГО человека
        try:
            await bot.send_message(
                target_tg_id,
                f"🎉 <b>У вас взаимная симпатия!</b>\n"
                f"Напиши: {link_a}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        return

    else:
        # Если лайк НЕ взаимный (просто уведомляем цель, если нужно)
        target_db_id = await get_user_db_id(target_tg_id)
        if target_db_id:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM swipes WHERE target_id=$1 AND is_like=1 AND viewed_in_incoming=0",
                    target_db_id,
                )
            count = row["cnt"] if row else 0

            if count > 0:
                word = "человеку" if count == 1 else "людям"
                try:
                    await send_with_custom_kb(
                        target_tg_id,
                        f"❤️ Ты понравился <b>{count}</b> {word}!",
                        view_likes_kb(),
                    )
                except Exception:
                    pass

    # Если матча не было — листаем дальше автоматически
    incoming = await get_incoming_likes_count(user_tg_id)
    if incoming > 0:
        await show_incoming_like_profile(message)
    else:
        await show_random_profile(message)


# ===================== 👎 ДИЗЛАЙК =====================


@router.message(F.text == "👎")
async def handle_dislike(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    user_tg_id = message.from_user.id
    target_tg_id = current_targets.get(user_tg_id)

    if target_tg_id:
        await add_dislike(user_tg_id, target_tg_id)

    incoming = await get_incoming_likes_count(user_tg_id)
    if incoming > 0:
        await show_incoming_like_profile(message)
    else:
        await show_random_profile(message)


# ===================== ⚠️ ЖАЛОБА =====================


@router.message(F.text == "⚠️")
async def handle_complaint_button(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    user_tg_id = message.from_user.id
    target_tg_id = current_targets.get(user_tg_id)

    if target_tg_id is None:
        await message.answer(
            "❌ Не могу понять, на кого жаловаться. Нажми <b>1</b> чтобы смотреть анкеты."
        )
        return

    await state.update_data(complaint_target_tg_id=target_tg_id)

    await send_with_custom_kb(
        message.chat.id,
        "⚠️ <b>Пожаловаться на анкету?</b>",
        complaint_confirm_kb(),
    )


@router.message(F.text == "Назад")
async def handle_complaint_back(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state == ComplaintForm.waiting_text.state:
        await state.clear()

    user_tg_id = message.from_user.id
    target_tg_id = current_targets.get(user_tg_id)

    if target_tg_id:
        target_user = await get_user_by_tg_id(target_tg_id)
        if target_user:
            show_msg_btn = can_send_like_message(message.from_user.id)
            await send_profile_card(message.chat.id, target_user, browse_kb(show_message_button=show_msg_btn))
            return

    await show_random_profile(message)


@router.message(F.text == "Пожаловаться")
async def handle_complaint_confirm(message: Message, state: FSMContext):
    if await check_blacklist(message):
        return

    data = await state.get_data()
    target_tg_id = data.get("complaint_target_tg_id")

    if not target_tg_id:
        target_tg_id = current_targets.get(message.from_user.id)

    if not target_tg_id:
        await message.answer("❌ Ошибка. Попробуй заново.")
        await show_main_menu(message)
        return

    await state.update_data(complaint_target_tg_id=target_tg_id)
    await state.set_state(ComplaintForm.waiting_text)

    await message.answer(
        "📝 <b>Напишите жалобу, она будет рассмотрена в ближайшее время.</b>",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ComplaintForm.waiting_text, F.text)
async def handle_complaint_text(message: Message, state: FSMContext):
    complaint_text = message.text.strip()
    if not complaint_text:
        await message.answer("❌ Жалоба не может быть пустой. Напишите текст жалобы.")
        return

    data = await state.get_data()
    target_tg_id = data.get("complaint_target_tg_id")
    await state.clear()

    if not target_tg_id:
        await message.answer("❌ Ошибка. Попробуйте заново.")
        await show_main_menu(message)
        return

    target_user = await get_user_by_tg_id(target_tg_id)
    complainant_username = message.from_user.username

    try:
        await send_complaint_to_bot(target_user, complaint_text, complainant_username)
    except Exception as e:
        logger.error(f"Failed to send complaint: {e}")

    await message.answer("✅ <b>Ваша жалоба отправлена и будет рассмотрена. Спасибо!</b>")

    incoming = await get_incoming_likes_count(message.from_user.id)
    if incoming > 0:
        await show_incoming_like_profile(message)
    else:
        await show_random_profile(message)


@router.message(ComplaintForm.waiting_text)
async def handle_complaint_text_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Отправьте <b>текст</b> жалобы.")


# ===================== 💤 МЕНЮ (из просмотра) =====================


@router.message(F.text == "💤")
async def handle_sleep(message: Message, state: FSMContext):
    await state.clear()
    current_targets.pop(message.from_user.id, None)
    await show_main_menu(message)


# ===================== АДМИН: ТОП-10 =====================


@router.message(F.text == "Топ-10 анкет")
async def admin_top(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    top = await get_top_profiles(10)
    if not top:
        await send_with_custom_kb(
            message.chat.id,
            "📊 Пока нет статистики.",
            admin_menu_kb(),
        )
        return

    lines = []
    for i, p in enumerate(top, 1):
        name = p.get("username", "?")
        age = p.get("age", "?")
        likes = p.get("likes_count", 0)
        lines.append(f"{i}. <b>{html_module.escape(str(name))}</b> ({age}) — {likes} ❤️")

    await send_with_custom_kb(
        message.chat.id,
        "📊 <b>Топ-10 анкет по лайкам:</b>\n\n" + "\n".join(lines),
        admin_menu_kb(),
    )


# ===================== АДМИН: ЧЁРНЫЙ СПИСОК =====================


@router.message(F.text == "Чёрный список")
async def admin_blacklist(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await send_with_custom_kb(
        message.chat.id,
        "🔒 <b>Чёрный список</b>\n\nВыбери действие:",
        blacklist_menu_kb(),
    )


@router.message(F.text == "Добавить в ЧС")
async def admin_bl_add(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BlacklistForm.waiting_id)
    await state.update_data(bl_action="add")
    await message.answer(
        "🖋 Отправь <b>tg_id</b> пользователя для добавления в ЧС.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == "Убрать из ЧС")
async def admin_bl_remove(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BlacklistForm.waiting_id)
    await state.update_data(bl_action="remove")
    await message.answer(
        "🖋 Отправь <b>tg_id</b> пользователя для удаления из ЧС.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(BlacklistForm.waiting_id, F.text)
async def admin_bl_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❌ Отправь <b>числовой</b> tg_id.")
        return

    tg_id = int(text)
    data = await state.get_data()
    action = data.get("bl_action")
    await state.clear()

    if action == "add":
        await add_to_blacklist(tg_id)
        await send_with_custom_kb(
            message.chat.id,
            f"✅ Пользователь <b>{tg_id}</b> добавлен в ЧС.",
            admin_menu_kb(),
        )
    else:
        await remove_from_blacklist(tg_id)
        await send_with_custom_kb(
            message.chat.id,
            f"✅ Пользователь <b>{tg_id}</b> удалён из ЧС.",
            admin_menu_kb(),
        )


@router.message(F.text == "Назад в админку")
async def admin_back(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await send_with_custom_kb(
        message.chat.id,
        "⚙️ <b>Админ-панель</b>",
        admin_menu_kb(),
    )


# ===================== АДМИН: ТУМБЛЕР МЭТЧА =====================


@router.message(F.text == "Тумблер мэтча")
async def admin_match_toggle(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    current = await get_setting("hide_matched")
    if current == "1":
        await set_setting("hide_matched", "0")
        await send_with_custom_kb(
            message.chat.id,
            "🔓 Фильтр мэтчей <b>ВЫКЛЮЧЕН</b>.\n"
            "Анкеты с мэтчем будут показываться снова.",
            admin_menu_kb(),
        )
    else:
        await set_setting("hide_matched", "1")
        await send_with_custom_kb(
            message.chat.id,
            "🔒 Фильтр мэтчей <b>ВКЛЮЧЁН</b>.\n"
            "Анкеты с мэтчем больше не показываются.",
            admin_menu_kb(),
        )


# ===================== АДМИН: РАССЫЛКА =====================


@router.message(F.text == "Рассылка")
async def admin_broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastForm.waiting_message)
    await message.answer(
        "📣 <b>Отправь сообщение для рассылки.</b>\n\n"
        "Можно отправить текст, фото с подписью или GIF с подписью.\n"
        "Для отмены отправь /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel"), BroadcastForm.waiting_message)
async def admin_broadcast_cancel(message: Message, state: FSMContext):
    await state.clear()
    await send_with_custom_kb(
        message.chat.id,
        "❌ Рассылка отменена.",
        admin_menu_kb(),
    )


@router.message(BroadcastForm.waiting_message, F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.ANIMATION}))
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()

    sender_tg_id = message.from_user.id
    all_ids = await get_all_user_tg_ids()
    success = 0
    fail = 0

    for tg_id in all_ids:
        if tg_id == sender_tg_id:
            continue
        try:
            if message.content_type == ContentType.PHOTO:
                await bot.send_photo(
                    tg_id,
                    message.photo[-1].file_id,
                    caption=message.caption or "",
                    parse_mode=ParseMode.HTML,
                )
            elif message.content_type == ContentType.ANIMATION:
                await bot.send_animation(
                    tg_id,
                    message.animation.file_id,
                    caption=message.caption or "",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await bot.send_message(
                    tg_id,
                    message.text,
                    parse_mode=ParseMode.HTML,
                )
            success += 1
        except Exception:
            fail += 1

        await asyncio.sleep(0.05)

    await send_with_custom_kb(
        message.chat.id,
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Успешно: {success}\n"
        f"Ошибок: {fail}",
        admin_menu_kb(),
    )


@router.message(BroadcastForm.waiting_message)
async def admin_broadcast_invalid(message: Message, state: FSMContext):
    await message.answer(
        "❌ Отправь <b>текст</b>, <b>фото</b> или <b>GIF</b>.\n"
        "Для отмены: /cancel"
    )


# ===================== ВЫЙТИ ИЗ АДМИНКИ =====================


@router.message(F.text == "Выйти из админки")
async def admin_exit(message: Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message)


# ===================== FALLBACK =====================


@router.message()
async def fallback(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        return

    hp = await has_profile(message.from_user.id)
    await send_with_custom_kb(
        message.chat.id,
        "ℹ️ Не понимаю. Используй кнопки меню или нажми /start",
        main_menu_kb(hp),
    )


# ===================== MAIN =====================


async def on_startup():
    await create_pool()
    await init_db()
    await migrate_from_sqlite()
    logger.info("Bot started")


async def on_shutdown():
    global pool
    if pool:
        await pool.close()
        logger.info("PostgreSQL pool closed")


async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
