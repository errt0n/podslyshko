import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import re
import time
from typing import Dict, Optional
from urllib.parse import parse_qsl

import asyncpg
import emoji
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ==========================================
# 1. НАСТРОЙКИ (всё секретное - в переменных окружения Render)
# ==========================================
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", "8692465912"))
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://your-app-name.onrender.com")
# Строка подключения к Postgres (Neon / Supabase), хранится в переменных окружения
# asyncpg не понимает параметр channel_binding из строк Neon - вырезаем его
DATABASE_URL = re.sub(r"[&?]channel_binding=[^&]*", "", os.environ["DATABASE_URL"])

logging.basicConfig(level=logging.INFO)

# ==========================================
# 2. БАЗА ДАННЫХ (PostgreSQL - данные не пропадают при перезапуске)
# ==========================================
pool: Optional[asyncpg.Pool] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username    TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL,
    value       TEXT NOT NULL,
    value_key   TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    identity    TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_user_time ON messages (telegram_id, created_at DESC);
"""


async def init_db():
    global pool
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        statement_cache_size=0,  # совместимость с пулером Neon (pgbouncer)
        max_inactive_connection_lifetime=30,  # бесплатная БД "засыпает" и рвёт старые соединения
    )
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    logging.info("База данных подключена")


COOLDOWN_SECONDS = 10 * 60
MAX_TEXT_LEN = 500

AVAILABLE_EMOJIS = [
    "🔥", "⚡", "🎲", "🎯", "👑", "🦊", "🐯", "🐼", "🦁", "🚀",
    "💎", "🍀", "🌙", "⭐", "🍕", "🍔", "🎮", "🎸", "🎧", "👾",
    "🦄", "🐉", "🤖", "👻", "🌶️", "🔮", "🎩", "🏆", "💣", "🎨",
    "🍎", "🍌", "🍉", "🍇", "🍓", "🍒", "🍍", "🥥", "🥑", "🥕",
    "🚗", "🚕", "🚙", "🚌", "🏎️", "🚓", "🚑", "🚒", "✈️", "🛸",
    "⚽", "🏀", "🏈", "⚾", "🎾", "🏐", "🏉", "🎱", "🏓", "🏸",
    "🐶", "🐱", "🐭", "🐹", "🐰", "🐻", "🐨", "🐮", "🐷", "🐸",
    "🐵", "🐔", "🐧", "🐦", "🐤", "🦆", "🦅", "🦉", "🦇", "🐺",
    "🦋", "🐛", "🐝", "🐞", "🐢", "🐍", "🐙", "🦀", "🦞", "🐬",
    "🌍", "🌋", "☀️", "⛅", "🌀", "🌊", "⛄", "💥", "☄️", "⚓",
]
AVAILABLE_EMOJIS = list(dict.fromkeys(AVAILABLE_EMOJIS))


def contains_emoji(text: str) -> bool:
    return emoji.emoji_count(text) > 0


# ==========================================
# 3. ПРОВЕРКА ПОДЛИННОСТИ ПОЛЬЗОВАТЕЛЯ (Telegram initData)
# ==========================================
def get_user_from_init_data(init_data: str) -> Dict:
    """Проверяет подпись Telegram. Без этого любой мог бы подставить чужой ID (в т.ч. админа)."""
    if not init_data:
        raise HTTPException(status_code=401, detail="Откройте приложение через Telegram")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Нет подписи")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        raise HTTPException(status_code=401, detail="Неверная подпись")
    try:
        if time.time() - int(parsed.get("auth_date", "0")) > 7 * 24 * 3600:
            raise HTTPException(status_code=401, detail="Сессия устарела, откройте приложение заново")
        return json.loads(parsed["user"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Некорректные данные")


# ==========================================
# 4. FASTAPI
# ==========================================
app = FastAPI(title="Podslyshko App")


class RegisterIdentityRequest(BaseModel):
    identity_type: str
    value: str


class SendMessageRequest(BaseModel):
    text: str


HTML_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Подслушано</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
body { background: var(--tg-theme-bg-color, #17212b); color: var(--tg-theme-text-color, #fff); height: 100vh; display: flex; flex-direction: column; }
.header { padding: 15px; text-align: center; font-size: 18px; font-weight: bold; background: var(--tg-theme-secondary-bg-color, #232e3c); border-bottom: 1px solid rgba(255,255,255,0.1); }
.user-info { font-size: 13px; color: var(--tg-theme-hint-color, #7f91a4); margin-top: 4px; font-weight: normal; }
.content { flex: 1; overflow-y: auto; padding: 15px; display: flex; flex-direction: column; }
.form-title { font-size: 15px; text-align: center; margin-bottom: 15px; line-height: 1.4; color: var(--tg-theme-hint-color, #7f91a4); }
.tabs-selector { display: flex; background: var(--tg-theme-secondary-bg-color, #232e3c); border-radius: 12px; padding: 4px; margin-bottom: 15px; }
.mode-btn { flex: 1; padding: 10px; background: none; border: none; color: var(--tg-theme-hint-color, #7f91a4); font-size: 14px; border-radius: 9px; cursor: pointer; }
.mode-btn.active { background: #2481cc; color: #fff; font-weight: bold; }
.emoji-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; padding-bottom: 10px; }
.emoji-btn { font-size: 26px; padding: 10px; background: var(--tg-theme-secondary-bg-color, #232e3c); border: 2px solid transparent; border-radius: 12px; cursor: pointer; }
.emoji-btn:disabled { opacity: 0.2; cursor: not-allowed; border-color: #ff4d4d; }
.nick-box, .send-form { display: flex; flex-direction: column; gap: 12px; }
.nick-input, textarea { width: 100%; background: var(--tg-theme-secondary-bg-color, #232e3c); color: var(--tg-theme-text-color, #fff); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 14px; font-size: 16px; outline: none; }
textarea { height: 120px; resize: none; }
.chat-list { display: flex; flex-direction: column; gap: 10px; }
.message-item { background: var(--tg-theme-secondary-bg-color, #232e3c); padding: 12px 15px; border-radius: 12px; line-height: 1.4; word-wrap: break-word; font-size: 15px; }
.message-author { margin-right: 6px; font-weight: bold; color: #52b788; }
.submit-btn { background: #2481cc; color: #fff; border: none; padding: 14px; border-radius: 12px; font-size: 16px; font-weight: bold; cursor: pointer; }
.submit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.timer-warning { color: #e53935; font-size: 14px; text-align: center; font-weight: 500; }
.nav-bar { display: flex; background: var(--tg-theme-secondary-bg-color, #232e3c); border-top: 1px solid rgba(255,255,255,0.1); }
.nav-btn { flex: 1; padding: 14px; background: none; border: none; color: var(--tg-theme-hint-color, #7f91a4); font-size: 14px; cursor: pointer; }
.nav-btn.active { color: #2481cc; font-weight: bold; border-bottom: 2px solid #2481cc; }
.hidden { display: none !important; }
</style>
</head>
<body>
<div class="header">Подслушано<div id="user-status" class="user-info">Загрузка...</div></div>

<div id="screen-identity" class="content hidden">
  <div class="form-title">Выберите подпись: уникальный смайлик или ник (без смайликов). Изменить нельзя!</div>
  <div class="tabs-selector">
    <button id="tab-mode-emoji" class="mode-btn active" onclick="switchIdentityMode('emoji')">😊 Смайлик</button>
    <button id="tab-mode-nick" class="mode-btn" onclick="switchIdentityMode('nick')">🏷️ Никнейм</button>
  </div>
  <div id="section-emoji"><div id="emoji-grid" class="emoji-grid"></div></div>
  <div id="section-nick" class="hidden">
    <div class="nick-box">
      <input type="text" id="nick-input" class="nick-input" placeholder="Введите ник..." maxlength="20">
      <button class="submit-btn" onclick="submitNick()">Сохранить ник</button>
    </div>
  </div>
</div>

<div id="screen-chat" class="content hidden"><div id="chat-list" class="chat-list"></div></div>

<div id="screen-send" class="content hidden">
  <div class="send-form">
    <textarea id="message-text" maxlength="500" placeholder="Напишите сообщение..."></textarea>
    <button id="send-btn" class="submit-btn" onclick="sendMessage()">Отправить</button>
    <div id="cooldown-timer" class="timer-warning"></div>
  </div>
</div>

<div id="nav-bar" class="nav-bar hidden">
  <button id="tab-send" class="nav-btn" onclick="switchTab('send')">✍️ Отправить</button>
  <button id="tab-chat" class="nav-btn" onclick="switchTab('chat')">💬 Чат</button>
</div>

<script>
const tg = window.Telegram.WebApp;
tg.expand();
let cooldownTimer = null;

function esc(t) {
  return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function api(path, options = {}) {
  options.headers = Object.assign({ 'X-Init-Data': tg.initData || '' }, options.headers || {});
  if (options.body) options.headers['Content-Type'] = 'application/json';
  const res = await fetch(path, options);
  let data = {};
  try { data = await res.json(); } catch (e) {}
  return { ok: res.ok, data };
}

async function init() {
  const { ok, data } = await api('/api/user_status');
  if (!ok) { document.getElementById('user-status').innerText = data.detail || 'Ошибка'; return; }
  if (data.identity) {
    enterApp(data.identity);
  } else {
    document.getElementById('user-status').innerText = 'Выбор подписи';
    showIdentityScreen(data.taken_values, data.available_emojis);
  }
}

function enterApp(identity) {
  document.getElementById('user-status').innerText = 'Твой маркер: ' + identity;
  document.getElementById('screen-identity').classList.add('hidden');
  document.getElementById('nav-bar').classList.remove('hidden');
  switchTab('chat');
}

function showIdentityScreen(taken, emojis) {
  document.getElementById('screen-identity').classList.remove('hidden');
  const grid = document.getElementById('emoji-grid');
  grid.innerHTML = '';
  emojis.forEach(e => {
    const btn = document.createElement('button');
    btn.className = 'emoji-btn';
    btn.innerText = e;
    if (taken.includes(e.toLowerCase())) btn.disabled = true;
    else btn.onclick = () => registerIdentity('emoji', e);
    grid.appendChild(btn);
  });
}

function switchIdentityMode(mode) {
  const isEmoji = mode === 'emoji';
  document.getElementById('tab-mode-emoji').classList.toggle('active', isEmoji);
  document.getElementById('tab-mode-nick').classList.toggle('active', !isEmoji);
  document.getElementById('section-emoji').classList.toggle('hidden', !isEmoji);
  document.getElementById('section-nick').classList.toggle('hidden', isEmoji);
}

async function submitNick() {
  const nick = document.getElementById('nick-input').value.trim();
  if (!nick) { alert('Введите ник!'); return; }
  await registerIdentity('nick', nick);
}

async function registerIdentity(type, value) {
  if (!confirm('Закрепить ' + (type === 'emoji' ? 'смайлик' : 'ник') + ' "' + value + '" за тобой? Изменить нельзя!')) return;
  const { ok, data } = await api('/api/register_identity', {
    method: 'POST',
    body: JSON.stringify({ identity_type: type, value: value })
  });
  if (ok) enterApp(data.identity);
  else { alert(data.detail || 'Ошибка'); init(); }
}

function switchTab(tab) {
  document.getElementById('screen-chat').classList.add('hidden');
  document.getElementById('screen-send').classList.add('hidden');
  document.getElementById('tab-chat').classList.remove('active');
  document.getElementById('tab-send').classList.remove('active');
  if (cooldownTimer) { clearInterval(cooldownTimer); cooldownTimer = null; }
  if (tab === 'chat') {
    document.getElementById('screen-chat').classList.remove('hidden');
    document.getElementById('tab-chat').classList.add('active');
    loadChat();
  } else {
    document.getElementById('screen-send').classList.remove('hidden');
    document.getElementById('tab-send').classList.add('active');
    checkCooldown();
  }
}

async function loadChat() {
  const { data } = await api('/api/messages');
  const list = document.getElementById('chat-list');
  list.innerHTML = '';
  if (!Array.isArray(data) || data.length === 0) {
    list.innerHTML = '<div style="text-align:center; color:#7f91a4; margin-top:30px;">Сообщений нет. Будь первым!</div>';
    return;
  }
  data.forEach(m => {
    const item = document.createElement('div');
    item.className = 'message-item';
    item.innerHTML = '<span class="message-author">' + esc(m.identity) + ':</span> ' + esc(m.text);
    list.appendChild(item);
  });
  window.scrollTo(0, document.body.scrollHeight);
}

async function checkCooldown() {
  const { data } = await api('/api/cooldown');
  const btn = document.getElementById('send-btn');
  const timerDiv = document.getElementById('cooldown-timer');
  if (cooldownTimer) { clearInterval(cooldownTimer); cooldownTimer = null; }
  if (data.cooldown_active) {
    let left = data.remaining_seconds_total;
    btn.disabled = true;
    const tick = () => {
      if (left <= 0) { clearInterval(cooldownTimer); cooldownTimer = null; btn.disabled = false; timerDiv.innerText = ''; return; }
      timerDiv.innerText = 'Кулдаун! Через ' + Math.floor(left / 60) + ' мин ' + (left % 60) + ' сек';
      left--;
    };
    tick();
    cooldownTimer = setInterval(tick, 1000);
  } else {
    btn.disabled = false;
    timerDiv.innerText = '';
  }
}

async function sendMessage() {
  const textarea = document.getElementById('message-text');
  const text = textarea.value.trim();
  if (!text) { alert('Введите текст'); return; }
  const { ok, data } = await api('/api/send_message', { method: 'POST', body: JSON.stringify({ text: text }) });
  if (ok) { textarea.value = ''; switchTab('chat'); }
  else { alert(data.detail || 'Ошибка'); checkCooldown(); }
}

init();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def get_web_app():
    return HTML_PAGE


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/user_status")
async def get_user_status(x_init_data: str = Header(default="")):
    user = get_user_from_init_data(x_init_data)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM users WHERE telegram_id = $1", user["id"])
        taken = await conn.fetch("SELECT value_key FROM users")
    return {
        "identity": row["value"] if row else None,
        "taken_values": [r["value_key"] for r in taken],
        "available_emojis": AVAILABLE_EMOJIS,
    }


@app.post("/api/register_identity")
async def register_identity(req: RegisterIdentityRequest, x_init_data: str = Header(default="")):
    user = get_user_from_init_data(x_init_data)
    uid = user["id"]
    is_admin = uid == ADMIN_TELEGRAM_ID
    username = user.get("username", "") or ""

    value = req.value.strip()
    if req.identity_type not in ("emoji", "nick"):
        raise HTTPException(status_code=400, detail="Неверный тип")

    if req.identity_type == "nick":
        if len(value) < 2 or len(value) > 20:
            raise HTTPException(status_code=400, detail="Ник должен быть от 2 до 20 символов!")
        if not is_admin and contains_emoji(value):
            raise HTTPException(status_code=400, detail="В никах нельзя ставить смайлики!")
    elif not is_admin and value not in AVAILABLE_EMOJIS:
        raise HTTPException(status_code=400, detail="Такого смайлика нет в списке!")

    key = value.lower()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE telegram_id = $1", uid)
        if exists and not is_admin:
            raise HTTPException(status_code=400, detail="Вы уже выбрали подпись, изменить нельзя!")
        try:
            if is_admin:
                # админ может менять свою подпись сколько угодно
                await conn.execute(
                    """
                    INSERT INTO users (telegram_id, username, type, value, value_key)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (telegram_id) DO UPDATE
                    SET username = EXCLUDED.username, type = EXCLUDED.type,
                        value = EXCLUDED.value, value_key = EXCLUDED.value_key
                    """,
                    uid, username, req.identity_type, value, key,
                )
            else:
                await conn.execute(
                    "INSERT INTO users (telegram_id, username, type, value, value_key) VALUES ($1, $2, $3, $4, $5)",
                    uid, username, req.identity_type, value, key,
                )
        except asyncpg.UniqueViolationError:
            # UNIQUE на value_key гарантирует, что одну подпись не возьмут двое одновременно
            raise HTTPException(status_code=400, detail="Уже занято!")
    return {"success": True, "identity": value}


@app.get("/api/messages")
async def get_messages(x_init_data: str = Header(default="")):
    get_user_from_init_data(x_init_data)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, identity, text FROM messages ORDER BY id DESC LIMIT 50"
        )
    # telegram_id намеренно НЕ отдаём - чат анонимный
    return [{"id": r["id"], "identity": r["identity"], "text": r["text"]} for r in reversed(rows)]


async def _remaining(conn, uid: int) -> int:
    """Сколько секунд осталось до конца кулдауна (время считает сама БД)."""
    age = await conn.fetchval(
        """
        SELECT EXTRACT(EPOCH FROM (now() - created_at))::float8
        FROM messages WHERE telegram_id = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        uid,
    )
    if age is None:
        return 0
    return max(0, int(COOLDOWN_SECONDS - age))


@app.get("/api/cooldown")
async def check_cooldown(x_init_data: str = Header(default="")):
    user = get_user_from_init_data(x_init_data)
    async with pool.acquire() as conn:
        rem = await _remaining(conn, user["id"])
    return {"cooldown_active": rem > 0, "remaining_seconds_total": rem}


@app.post("/api/send_message")
async def send_message(req: SendMessageRequest, x_init_data: str = Header(default="")):
    user = get_user_from_init_data(x_init_data)
    uid = user["id"]

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустое сообщение!")
    if len(text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"Максимум {MAX_TEXT_LEN} символов!")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # блокировка на пользователя: два одновременных запроса не обойдут кулдаун
            await conn.execute("SELECT pg_advisory_xact_lock($1)", uid)
            identity = await conn.fetchval("SELECT value FROM users WHERE telegram_id = $1", uid)
            if not identity:
                raise HTTPException(status_code=400, detail="Сначала выберите маркер!")
            if await _remaining(conn, uid) > 0:
                raise HTTPException(status_code=400, detail="Кулдаун 10 минут!")
            await conn.execute(
                "INSERT INTO messages (telegram_id, identity, text) VALUES ($1, $2, $3)",
                uid, identity, text,
            )
    return {"success": True}


# ==========================================
# 5. TELEGRAM БОТ
# ==========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📱 Открыть Подслушано", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await message.answer("Привет! Открой веб-приложение снизу и ещё введи /guide", reply_markup=keyboard)


@dp.message(Command("guide"))
async def cmd_guide(message: Message):
    await message.answer(
        "📖 <b>Гайд по использованию Подслушано:</b>\n\n"
        "1. Открой веб-приложение по кнопке снизу.\n"
        "2. Выбери подпись: уникальный смайлик или ник (без смайликов). Изменить нельзя.\n"
        "3. В приложении две кнопки: отправка и чат.\n"
        "4. Кулдаун: 1 раз в 10 минут.\n"
        "5. Ваши данные полностью анонимны!",
        parse_mode="HTML",
    )


@dp.message(Command("fff"))
async def cmd_admin_panel(message: Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return
    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT telegram_id, username, type, value FROM users ORDER BY created_at")
        total_msgs = await conn.fetchval("SELECT count(*) FROM messages")
    lines = [
        "👑 <b>АДМИН-ПАНЕЛЬ ПОДСЛУШАНО</b> 👑\n",
        f"📊 Пользователей: {len(users)}",
        f"💬 Сообщений: {total_msgs}\n",
        "📋 <b>Аккаунты:</b>",
    ]
    if not users:
        lines.append("<i>Нет пользователей.</i>")
    for u in users:
        uname = html.escape(u["username"] or "нет")
        lines.append(f"• ID: <code>{u['telegram_id']}</code> | @{uname} | {u['type']}: <b>{html.escape(u['value'])}</b>")
    text = "\n".join(lines)
    # лимит Telegram - 4096 символов на сообщение
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i + 4000], parse_mode="HTML")


async def start_fastapi():
    port = int(os.environ.get("PORT", 8000))
    server = uvicorn.Server(uvicorn.Config(app=app, host="0.0.0.0", port=port, log_level="info"))
    await server.serve()


async def main():
    await init_db()
    await asyncio.gather(start_fastapi(), dp.start_polling(bot))


if __name__ == "__main__":
    asyncio.run(main())
