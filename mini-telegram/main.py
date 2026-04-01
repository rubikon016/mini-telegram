import sqlite3
import os
import uuid
from datetime import datetime
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Mini Telegram")

# Папка для фото
os.makedirs("uploads", exist_ok=True)

# ====================== БАЗА ДАННЫХ ======================
conn = sqlite3.connect("telegram.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    text TEXT,
                    image_path TEXT,
                    timestamp TEXT NOT NULL)""")
conn.commit()

# ====================== WebSocket менеджер ======================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}  # chat_id -> list of sockets

    async def connect(self, websocket: WebSocket, chat_id: str):
        await websocket.accept()
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = []
        self.active_connections[chat_id].append(websocket)

    def disconnect(self, websocket: WebSocket, chat_id: str):
        if chat_id in self.active_connections:
            self.active_connections[chat_id].remove(websocket)

    async def broadcast(self, chat_id: str, message: dict):
        if chat_id in self.active_connections:
            for connection in self.active_connections[chat_id][:]:
                try:
                    await connection.send_json(message)
                except:
                    pass

manager = ConnectionManager()

# ====================== УТИЛИТЫ ======================
def get_chat_id(user1: str, user2: str) -> str:
    return "_".join(sorted([user1, user2]))

# ====================== HTML ИНТЕРФЕЙС ======================
HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mini Telegram</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        body { background: #0f172a; color: #e2e8f0; font-family: system-ui, -apple-system, sans-serif; }
        .chat-bubble { max-width: 75%; padding: 12px 16px; border-radius: 20px; word-break: break-word; }
        .my-bubble { background: #3b82f6; border-bottom-right-radius: 4px; }
        .their-bubble { background: #1e2937; border-bottom-left-radius: 4px; }
        .message img { max-width: 300px; border-radius: 12px; margin-top: 8px; display: block; }
        .scrollbar::-webkit-scrollbar { display: none; }
    </style>
</head>
<body class="flex h-screen overflow-hidden">
    <!-- Сайдбар -->
    <div class="w-80 bg-slate-900 flex flex-col border-r border-slate-700">
        <div class="p-4 border-b border-slate-700 flex items-center gap-3 bg-slate-950">
            <i class="fas fa-paper-plane text-blue-500 text-3xl"></i>
            <h1 class="text-3xl font-bold tracking-tight">Mini TG</h1>
        </div>
        
        <div class="p-4 border-b border-slate-700">
            <input id="usernameInput" type="text" placeholder="Твой username (например ivan)" 
                   class="w-full bg-slate-800 text-white px-5 py-4 rounded-3xl focus:outline-none text-lg placeholder:text-slate-400">
            <button onclick="login()" 
                    class="mt-3 w-full bg-blue-600 hover:bg-blue-700 py-4 rounded-3xl font-semibold text-lg transition">
                Войти / Зарегистрироваться
            </button>
        </div>

        <div class="px-4 pt-4 text-xs uppercase text-slate-400 mb-2 font-medium">Все пользователи</div>
        <div id="userList" class="flex-1 overflow-y-auto scrollbar"></div>
    </div>

    <!-- Чат -->
    <div class="flex-1 flex flex-col">
        <div id="chatHeader" class="h-16 bg-slate-900 border-b border-slate-700 flex items-center px-6 font-semibold text-xl">
            Выберите чат слева
        </div>
        
        <div id="messages" class="flex-1 p-6 overflow-y-auto scrollbar bg-slate-950 space-y-7"></div>
        
        <!-- Ввод сообщения -->
        <div class="bg-slate-900 p-4 border-t border-slate-700 flex items-center gap-4">
            <label class="cursor-pointer flex-shrink-0">
                <i class="fas fa-paperclip text-3xl text-slate-400 hover:text-white transition"></i>
                <input id="fileInput" type="file" accept="image/*" class="hidden" onchange="sendImage()">
            </label>
            
            <input id="messageInput" type="text" placeholder="Напишите сообщение..." 
                   class="flex-1 bg-slate-800 text-white px-6 py-4 rounded-3xl focus:outline-none text-lg"
                   onkeydown="if(event.keyCode===13) sendMessage()">
            
            <button onclick="sendMessage()" 
                    class="bg-blue-600 hover:bg-blue-700 w-14 h-14 rounded-3xl flex items-center justify-center text-2xl transition">
                <i class="fas fa-paper-plane"></i>
            </button>
        </div>
    </div>

    <script>
        let username = "";
        let currentChat = "";
        let ws = null;

        async function login() {
            username = document.getElementById("usernameInput").value.trim();
            if (!username) return alert("Введите username!");
            
            const res = await fetch("/login", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({username: username})
            });
            
            if (res.ok) {
                loadUsers();
                connectWebSocket();
            } else {
                alert("Ошибка входа");
            }
        }

        async function loadUsers() {
            const res = await fetch("/users");
            const users = await res.json();
            const html = users
                .filter(u => u !== username)
                .map(u => `
                    <div onclick="openChat('${u}')" class="px-4 py-4 hover:bg-slate-800 cursor-pointer flex items-center gap-3 border-b border-slate-700 last:border-none">
                        <div class="w-11 h-11 bg-gradient-to-br from-blue-500 to-cyan-500 rounded-2xl flex items-center justify-center text-white text-2xl font-bold shadow-inner">${u[0].toUpperCase()}</div>
                        <div class="font-semibold text-lg">${u}</div>
                    </div>`).join("");
            document.getElementById("userList").innerHTML = html || "<div class='p-4 text-slate-400'>Пока никого нет</div>";
        }

        function openChat(withUser) {
            currentChat = [username, withUser].sort().join("_");
            document.getElementById("chatHeader").innerHTML = `
                <div class="flex items-center gap-4">
                    <div class="w-10 h-10 bg-gradient-to-br from-blue-500 to-cyan-500 rounded-2xl flex items-center justify-center text-white text-2xl font-bold">${withUser[0].toUpperCase()}</div>
                    <div>
                        <div class="font-semibold">${withUser}</div>
                        <div class="text-sm text-green-400 flex items-center gap-1"><span class="w-2 h-2 bg-green-400 rounded-full animate-pulse"></span> онлайн</div>
                    </div>
                </div>`;
            loadMessages();
        }

        async function loadMessages() {
            const res = await fetch(`/messages/${currentChat}`);
            const msgs = await res.json();
            const html = msgs.map(m => {
                const isMine = m.sender === username;
                return `
                <div class="flex ${isMine ? 'justify-end' : 'justify-start'}">
                    <div class="${isMine ? 'my-bubble' : 'their-bubble'} chat-bubble">
                        <div class="text-xs text-slate-400 mb-1">${m.sender}</div>
                        ${m.text ? `<div class="text-base">${m.text}</div>` : ''}
                        ${m.image ? `<img src="${m.image}" class="rounded-2xl mt-3 shadow">` : ''}
                        <div class="text-[10px] text-right opacity-60 mt-2">${m.timestamp}</div>
                    </div>
                </div>`;
            }).join("");
            const msgContainer = document.getElementById("messages");
            msgContainer.innerHTML = html;
            msgContainer.scrollTop = msgContainer.scrollHeight;
        }

        function connectWebSocket() {
            if (ws) ws.close();
            ws = new WebSocket(`ws://${location.host}/ws/${username}`);
            ws.onmessage = () => {
                if (currentChat) loadMessages();
            };
        }

        async function sendMessage() {
            const input = document.getElementById("messageInput");
            const text = input.value.trim();
            if (!text || !currentChat) return;
            
            await fetch(`/send/${currentChat}`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({sender: username, text: text})
            });
            input.value = "";
            loadMessages();
        }

        async function sendImage() {
            const file = document.getElementById("fileInput").files[0];
            if (!file || !currentChat) return;
            
            const form = new FormData();
            form.append("file", file);
            
            const res = await fetch(`/upload/${currentChat}`, { method: "POST", body: form });
            const data = await res.json();
            
            await fetch(`/send/${currentChat}`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({sender: username, image: data.url})
            });
            loadMessages();
        }
    </script>
</body>
</html>
"""

@app.get("/")
async def home():
    return HTMLResponse(HTML)

# ====================== API ======================
@app.post("/login")
async def login_user(data: dict):
    username = data.get("username")
    if not username or len(username) < 2:
        return {"error": "Неверный username"}
    cursor.execute("INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)", (username, "123"))
    conn.commit()
    return {"status": "ok"}

@app.get("/users")
async def get_users():
    cursor.execute("SELECT username FROM users")
    return [row[0] for row in cursor.fetchall()]

@app.get("/messages/{chat_id}")
async def get_messages(chat_id: str):
    cursor.execute("SELECT sender, text, image_path, timestamp FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    rows = cursor.fetchall()
    return [{"sender": r[0], "text": r[1], "image": r[2], "timestamp": r[3]} for r in rows]

@app.post("/send/{chat_id}")
async def send_message(chat_id: str, data: dict):
    sender = data.get("sender")
    text = data.get("text")
    image = data.get("image")
    timestamp = datetime.now().strftime("%H:%M")
    
    cursor.execute("INSERT INTO messages (chat_id, sender, text, image_path, timestamp) VALUES (?, ?, ?, ?, ?)",
                   (chat_id, sender, text, image, timestamp))
    conn.commit()
    
    await manager.broadcast(chat_id, {
        "chat_id": chat_id,
        "sender": sender,
        "text": text,
        "image": image,
        "timestamp": timestamp
    })
    return {"status": "ok"}

@app.post("/upload/{chat_id}")
async def upload_file(chat_id: str, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"{uuid.uuid4()}{ext}"
    path = f"uploads/{filename}"
    
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)
    
    return {"url": f"/uploads/{filename}"}

# Статические файлы (фото)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ====================== WebSocket ======================
@app.websocket("/ws/{client_username}")
async def websocket_endpoint(websocket: WebSocket, client_username: str):
    # Для простоты подключаем ко всем чатам через глобальный канал
    await manager.connect(websocket, "global")
    try:
        while True:
            await websocket.receive_text()  # держим соединение открытым
    except WebSocketDisconnect:
        manager.disconnect(websocket, "global")

# ====================== ЗАПУСК (для Railway + локально) ======================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)