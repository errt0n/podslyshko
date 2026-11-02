import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
import emoji

# ==========================================
# 1. НАСТРОЙКИ И ТОКЕНЫ
# ==========================================
BOT_TOKEN = "9196550617:AAFf_hBeEIc_94l1jkznvIP8YZ4YR78YdaU"
ADMIN_TELEGRAM_ID = 8692465912

# Render автоматически подставит WEBAPP_URL из переменных окружения
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://your-app-name.onrender.com")

logging.basicConfig(level=logging.INFO)

# ==========================================
# 2. БАЗА ДАННЫХ В ПАМЯТИ
# ==========================================
db_users: Dict[int, Dict[str, str]] = {}
db_taken_values: set = set()
db_messages: List[Dict] = []

COOLDOWN_MINUTES = 10

AVAILABLE_EMOJIS = [
    "🔥", "⚡", "🎲", "🎯", "👑", "🦊", "🐯", "🐼", "🦁", "🚀",
    "💎", "🍀", "🌙", "⭐", "🍕", "🍔", "🎮", "🎸", "🎧", "👾",
    "🦄", "🐉", "🤖", "👻", "🌶️", "🔮", "🎩", "🏆", "💣", "🎨",
    "🍎", "🍌", "🍉", "🍇", "🍓", "🍒", "🍍", "🥥", "🥑", "🥕",
    "🚗", "🚕", "🚙", "🚌", "🏎️", "🚓", "🚑", "🚒", "✈️", "🚀",
    "⚽", "🏀", "🏈", "⚾", "🎾", "🏐", "🏉", "🎱", "🏓", "🏸",
    "🐶", "🐱", "🐭", "🐹", "🐰", "🐻", "🐨", "🐮", "🐷", "🐸",
    "🐵", "🐔", "🐧", "🐦", "🐤", "🦆", "🦅", "🦉", "🦇", "🐺",
    "🦋", "🐛", "🐝", "🐞", "🐢", "🐍", "🐙", "🦀", "🦞", "🐬",
    "🌍", "🌋", "☀️", "⛅", "🌀", "🌊", "⛄", "🔥", "☄️", "⚓"
]
AVAILABLE_EMOJIS = list(dict.fromkeys(AVAILABLE_EMOJIS))[:100]

def contains_emoji(text: str) -> bool:
    return emoji.emoji_count(text) > 0

# ==========================================
# 3. FASTAPI И ВЕБ-ИНТЕРФЕЙС
# ==========================================
app = FastAPI(title="Podslyshko App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RegisterIdentityRequest(BaseModel):
    telegram_id: int
    username: Optional[str] = ""
    identity_type: str
    value: str

class SendMessageRequest(BaseModel):
    telegram_id: int
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
        body { background-color: var(--tg-theme-bg-color, #17212b); color: var(--tg-theme-text-color, #ffffff); height: 100vh; display: flex; flex-direction: column; }
        
        .header { padding: 15px; text-align: center; font-size: 18px; font-weight: bold; background: var(--tg-theme-secondary-bg-color, #232e3c); border-bottom: 1px solid rgba(255,255,255,0.1); }
        .user-info { font-size: 13px; color: var(--tg-theme-hint-color, #7f91a4); margin-top: 4px; font-weight: normal; }

        .content { flex: 1; overflow-y: auto; padding: 15px; display: flex; flex-direction: column; }

        .form-title { font-size: 15px; text-align: center; margin-bottom: 15px; line-height: 1.4; color: var(--tg-theme-hint-color, #7f91a4); }
        
        .tabs-selector { display: flex; background: var(--tg-theme-secondary-bg-color, #232e3c); border-radius: 12px; padding: 4px; margin-bottom: 15px; }
        .mode-btn { flex: 1; padding: 10px; text-align: center; background: none; border: none; color: var(--tg-theme-hint-color, #7f91a4); font-size: 14px; font-weight: 500; border-radius: 9px; cursor: pointer; }
        .mode-btn.active { background: #2481cc; color: white; font-weight: bold; }

        .emoji-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; max-height: 280px; overflow-y: auto; padding-bottom: 10px; }
        .emoji-btn { font-size: 26px; padding: 10px; background: var(--tg-theme-secondary-bg-color, #232e3c); border: 2px solid transparent; border-radius: 12px; cursor: pointer; text-align: center; transition: 0.2s; }
        .emoji-btn.disabled { opacity: 0.2; cursor: not-allowed; border-color: #ff4d4d; }

        .nick-box { display: flex; flex-direction: column; gap: 12px; }
        .nick-input { width: 100%; background: var(--tg-theme-secondary-bg-color, #232e3c); color: var(--tg-theme-text-color, #ffffff); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 14px; font-size: 16px; outline: none; }

        .chat-list { display: flex; flex-direction: column; gap: 10px; }
        .message-item { background: var(--tg-theme-secondary-bg-color, #232e3c); padding: 12px 15px; border-radius: 12px; line-height: 1.4; word-wrap: break-word; font-size: 15px; }
        .message-author { font-size: 15px; margin-right: 6px; font-weight: bold; color: #52b788; }

        .send-form { display: flex; flex-direction: column; gap: 12px; }
        textarea { width: 100%; height: 120px; background: var(--tg-theme-secondary-bg-color, #232e3c); color: var(--tg-theme-text-color, #ffffff); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 12px; font-size: 15px; resize: none; outline: none; }
        .submit-btn { background: #2481cc; color: white; border: none; padding: 14px; border-radius: 12px; font-size: 16px; font-weight: bold; cursor: pointer; }
        .submit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .timer-warning { color: #e53935; font-size: 14px; text-align: center; margin-top: 5px; font-weight: 500; }

        .nav-bar { display: flex; background: var(--tg-theme-secondary-bg-color, #232e3c); border-top: 1px solid rgba(255,255,255,0.1); }
        .nav-btn { flex: 1; padding: 14px; text-align: center; background: none; border: none; color: var(--tg-theme-hint-color, #7f91a4); font-size: 14px; font-weight: 500; cursor: pointer; }
        .nav-btn.active { color: #2481cc; font-weight: bold; border-bottom: 2px solid #2481cc; }
        
        .hidden { display: none !important; }
    </style>
</head>
<body>

    <div class="header">
        Подслушано
        <div id="user-status" class="user-info">Загрузка...</div>
    </div>

    <div id="screen-identity" class="content hidden">
        <div class="form-title">Выберите подпись: уникальный смайлик или ник (без смайликов). Изменить нельзя!</div>
        
        <div class="tabs-selector">
            <button id="tab-mode-emoji" class="mode-btn active" onclick="switchIdentityMode('emoji')">😊 Смайлик</button>
            <button id="tab-mode-nick" class="mode-btn" onclick="switchIdentityMode('nick')">🏷️ Никнейм</button>
        </div>

        <div id="section-emoji" class="identity-section">
            <div id="emoji-grid" class="emoji-grid"></div>
        </div>

        <div id="section-nick" class="identity-section hidden">
            <div class="nick-box">
                <input type="text" id="nick-input" class="nick-input" placeholder="Введите ник..." maxlength="20">
                <button class="submit-btn" onclick="submitNick()">Сохранить ник</button>
            </div>
        </div>
    </div>

    <div id="screen-chat" class="content hidden">
        <div id="chat-list" class="chat-list"></div>
    </div>

    <div id="screen-send" class="content hidden">
        <div class="send-form">
            <textarea id="message-text" placeholder="Напишите сообщение..."></textarea>
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

        let user = tg.initDataUnsafe?.user || { id: 8692465912, username: "admin_test" };
        let currentIdentity = null;

        async function init() {
            try {
                const res = await fetch(`/api/user_status?telegram_id=${user.id}`);
                const data = await res.json();

                if (data.identity) {
                    currentIdentity = data.identity;
                    document.getElementById('user-status').innerText = `Твой маркер: ${currentIdentity}`;
                    document.getElementById('nav-bar').classList.remove('hidden');
                    switchTab('chat');
                } else {
                    document.getElementById('user-status').innerText = 'Выбор подписи';
                    showIdentityScreen(data.taken_values, data.available_emojis);
                }
            } catch (err) {
                alert('Ошибка соединения');
            }
        }

        function showIdentityScreen(takenValues, availableEmojis) {
            document.getElementById('screen-identity').classList.remove('hidden');
            const grid = document.getElementById('emoji-grid');
            grid.innerHTML = '';

            availableEmojis.forEach(emoji => {
                const btn = document.createElement('button');
                btn.className = 'emoji-btn';
                btn.innerText = emoji;

                if (takenValues.includes(emoji)) {
                    btn.classList.add('disabled');
                    btn.disabled = true;
                } else {
                    btn.onclick = () => registerIdentity('emoji', emoji);
                }
                grid.appendChild(btn);
            });
        }

        function switchIdentityMode(mode) {
            const btnEmoji = document.getElementById('tab-mode-emoji');
            const btnNick = document.getElementById('tab-mode-nick');
            const secEmoji = document.getElementById('section-emoji');
            const secNick = document.getElementById('section-nick');

            if (mode === 'emoji') {
                btnEmoji.classList.add('active');
                btnNick.classList.remove('active');
                secEmoji.classList.remove('hidden');
                secNick.classList.add('hidden');
            } else {
                btnNick.classList.add('active');
                btnEmoji.classList.remove('active');
                secNick.classList.remove('hidden');
                secEmoji.classList.add('hidden');
            }
        }

        async function submitNick() {
            const input = document.getElementById('nick-input');
            const nick = input.value.trim();
            if (!nick) { alert('Введите ник!'); return; }
            await registerIdentity('nick', nick);
        }

        async function registerIdentity(type, value) {
            if (!confirm(`Закрепить ${type === 'emoji' ? 'смайлик' : 'ник'} "${value}" за тобой? Изменить нельзя!`)) return;

            const res = await fetch('/api/register_identity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    telegram_id: user.id,
                    username: user.username || '',
                    identity_type: type,
                    value: value
                })
            });

            const data = await res.json();
            if (res.ok) {
                currentIdentity = value;
                document.getElementById('user-status').innerText = `Твой маркер: ${currentIdentity}`;
                document.getElementById('screen-identity').classList.add('hidden');
                document.getElementById('nav-bar').classList.remove('hidden');
                switchTab('chat');
            } else {
                alert(data.detail || 'Ошибка');
                init();
            }
        }

        function switchTab(tab) {
            document.getElementById('screen-chat').classList.add('hidden');
            document.getElementById('screen-send').classList.add('hidden');
            document.getElementById('tab-chat').classList.remove('active');
            document.getElementById('tab-send').classList.remove('active');

            if (tab === 'chat') {
                document.getElementById('screen-chat').classList.remove('hidden');
                document.getElementById('tab-chat').classList.add('active');
                loadChat();
            } else if (tab === 'send') {
                document.getElementById('screen-send').classList.remove('hidden');
                document.getElementById('tab-send').classList.add('active');
                checkCooldown();
            }
        }

        async function loadChat() {
            const res = await fetch('/api/messages');
            const messages = await res.json();
            const list = document.getElementById('chat-list');
            list.innerHTML = '';

            if (messages.length === 0) {
                list.innerHTML = '<div style="text-align:center; color:#7f91a4; margin-top:30px;">Сообщений нет. Будь первым!</div>';
                return;
            }

            messages.forEach(msg => {
                const item = document.createElement('div');
                item.className = 'message-item';
                item.innerHTML = `<span class="message-author">${msg.identity}:</span> ${escapeHtml(msg.text)}`;
                list.appendChild(item);
            });
        }

        async function checkCooldown() {
            const res = await fetch(`/api/cooldown?telegram_id=${user.id}`);
            const data = await res.json();
            const btn = document.getElementById('send-btn');
            const timerDiv = document.getElementById('cooldown-timer');

            if (data.cooldown_active) {
                btn.disabled = true;
                timerDiv.innerText = `Кулдаун! Через ${data.remaining_minutes} мин ${data.remaining_seconds} сек`;
            } else {
                btn.disabled = false;
                timerDiv.innerText = '';
            }
        }

        async function sendMessage() {
            const textarea = document.getElementById('message-text');
            const text = textarea.value.trim();
            if (!text) { alert('Введите текст'); return; }

            const res = await fetch('/api/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ telegram_id: user.id, text: text })
            });

            const data = await res.json();
            if (res.ok) {
                textarea.value = '';
                alert('Отправлено!');
                switchTab('chat');
            } else {
                alert(data.detail || 'Ошибка');
                checkCooldown();
            }
        }

        function escapeHtml(text) {
            return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }

        init();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_web_app():
    return HTML_PAGE

@app.get("/api/user_status")
async def get_user_status(telegram_id: int):
    user_info = db_users.get(telegram_id)
    return {
        "identity": user_info["value"] if user_info else None,
        "taken_values": list(db_taken_values),
        "available_emojis": AVAILABLE_EMOJIS
    }

@app.post("/api/register_identity")
async def register_identity(req: RegisterIdentityRequest):
    val_lower = req.value.strip().lower()
    is_admin = (req.telegram_id == ADMIN_TELEGRAM_ID)

    if req.identity_type == "nick":
        if not is_admin and contains_emoji(req.value):
            raise HTTPException(status_code=400, detail="В никах нельзя ставить смайлики!")
        if len(req.value.strip()) < 2:
            raise HTTPException(status_code=400, detail="Ник слишком короткий!")

    if val_lower in db_taken_values:
        raise HTTPException(status_code=400, detail="Уже занято!")

    old_user = db_users.get(req.telegram_id)
    if old_user:
        db_taken_values.discard(old_user["value"].lower())

    db_taken_values.add(val_lower)
    db_users[req.telegram_id] = {
        "username": req.username,
        "type": req.identity_type,
        "value": req.value.strip()
    }
    return {"success": True, "identity": req.value.strip()}

@app.get("/api/messages")
async def get_messages():
    return db_messages[-50:]

@app.get("/api/cooldown")
async def check_cooldown(telegram_id: int):
    user_msgs = [m for m in db_messages if m["telegram_id"] == telegram_id]
    if not user_msgs:
        return {"cooldown_active": False}

    last_msg_time = user_msgs[-1]["timestamp"]
    elapsed = datetime.now() - last_msg_time
    cooldown_seconds = COOLDOWN_MINUTES * 60

    if elapsed.total_seconds() < cooldown_seconds:
        remaining = int(cooldown_seconds - elapsed.total_seconds())
        return {
            "cooldown_active": True,
            "remaining_minutes": remaining // 60,
            "remaining_seconds": remaining % 60
        }
    return {"cooldown_active": False}

@app.post("/api/send_message")
async def send_message(req: SendMessageRequest):
    user_info = db_users.get(req.telegram_id)
    if not user_info or not user_info.get("value"):
        raise HTTPException(status_code=400, detail="Сначала выберите маркер!")

    cooldown_data = await check_cooldown(req.telegram_id)
    if cooldown_data["cooldown_active"]:
        raise HTTPException(status_code=400, detail="Кулдаун 10 минут!")

    msg_obj = {
        "id": len(db_messages) + 1,
        "telegram_id": req.telegram_id,
        "identity": user_info["value"],
        "text": req.text,
        "timestamp": datetime.now()
    }
    db_messages.append(msg_obj)
    return {"success": True}

# ==========================================
# 4. TELEGRAM БОТ
# ==========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Открыть Подслушано", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )
    await message.answer("Привет! Открой веб-приложение снизу и ещё введи /guide", reply_markup=keyboard)

@dp.message(Command("guide"))
async def cmd_guide(message: Message):
    guide_text = (
        "📖 **Гайд по использованию Подслушано:**\n\n"
        "1. Открой веб-приложение по кнопке снизу.\n"
        "2. Выбери подпись: уникальный смайлик или ник (без смайликов). Изменить нельзя.\n"
        "3. В приложении две кнопки: отправка и чат.\n"
        "4. Кулдаун: 1 раз в 10 минут.\n"
        "5. Ваши данные полностью анонимны!"
    )
    await message.answer(guide_text, parse_mode="Markdown")

@dp.message(Command("fff"))
async def cmd_admin_panel(message: Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        return

    total_users = len(db_users)
    total_msgs = len(db_messages)
    
    report = f"👑 **АДМИН-ПАНЕЛЬ ПОДСЛУШАНО** 👑\n\n"
    report += f"📊 Пользователей: {total_users}\n"
    report += f"💬 Сообщений: {total_msgs}\n\n"
    report += f"📋 **Аккаунты:**\n"

    if not db_users:
        report += "_Нет пользователей._\n"
    else:
        for uid, udata in db_users.items():
            report += f"• ID: `{uid}` | @{udata.get('username', 'нет')} | {udata.get('type')}: **{udata.get('value')}**\n"

    report += f"\n🔒 Занято: {list(db_taken_values)}"
    await message.answer(report, parse_mode="Markdown")

async def start_fastapi():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app=app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    await asyncio.gather(
        start_fastapi(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())