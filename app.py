import os
import requests
import asyncio
import threading
import time
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from flask import Flask

# ======== CONFIGURATIONS & IDs ========
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# TUMHARA NAYA GITHUB REPO YAHAN ADD KAR DIYA HAI
REPO_NAME = os.environ.get("REPO_NAME", "killmasterkillingmaster-glitch/Lovelifep")

# Security Password
AUTH_KEY = os.environ.get("RECONNECT1234555", "12366555GGG")

ALLOWED_USERS = [5351848105, 5344078567] # Owner & Allowed User
ALLOWED_GROUPS = [-1003899919015]
SAFE_CHANNEL_ID = -1003962165512

app = Client("manga_frontend", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Databases (In-Memory)
user_states = {}
user_prompts = {}
pending_tasks = {}

def is_authorized(user_id, chat_id):
    return user_id in ALLOWED_USERS or chat_id in ALLOWED_GROUPS

# ======== ANTI-SLEEP MECHANISM ========
def keep_alive():
    while True:
        try:
            # Apne HF Space URL ko ping karega taaki soye nahi (Change with your actual HF URL)
            requests.get("https://ikhanking-lifelove.hf.space")
        except:
            pass
        time.sleep(600) # Har 10 minute me ping karega

# ======== ADD & DELETE PROMPT ========
@app.on_message(filters.command("addprompt") & filters.group)
async def add_prompt(client, message: Message):
    if not is_authorized(message.from_user.id, message.chat.id): return
    uid = message.from_user.id
    if uid not in user_prompts: user_prompts[uid] = []
    
    if len(user_prompts[uid]) >= 2:
        return await message.reply_text("❌ Aap max 2 prompts save kar chuke hain. Pehle /deleteprompt use karein.")
    
    user_states[uid] = "WAITING_NAME"
    await message.reply_text("📝 **Send Prompt Name:**\n(Example: Mangaki)")

@app.on_message(filters.command("deleteprompt") & filters.group)
async def del_prompt(client, message: Message):
    uid = message.from_user.id
    if uid in user_prompts and user_prompts[uid]:
        user_prompts[uid].clear()
        await message.reply_text("🗑️ Aapke saare saved prompts delete ho gaye hain.")
    else:
        await message.reply_text("❌ Aapka koi prompt save nahi hai.")

@app.on_message((filters.text | filters.document) & filters.group & ~filters.command(["addprompt", "deleteprompt", "english", "hienglish"]))
async def handle_inputs(client, message: Message):
    uid = message.from_user.id
    if uid in user_states:
        if user_states[uid] == "WAITING_NAME" and message.text:
            user_states[uid] = {"state": "WAITING_FILE", "name": message.text}
            await message.reply_text(f"✅ Name Saved: `{message.text}`\n📄 Ab apna Prompt `.txt` format me send karein.")
        
        elif isinstance(user_states[uid], dict) and user_states[uid].get("state") == "WAITING_FILE":
            if message.document and message.document.file_name.endswith('.txt'):
                file_path = await message.download()
                with open(file_path, "r", encoding="utf-8") as f:
                    txt_data = f.read()
                os.remove(file_path)
                
                p_name = user_states[uid]["name"]
                user_prompts[uid].append({"name": p_name, "text": txt_data})
                del user_states[uid]
                
                pin_msg = await message.reply_text(f"📌 **New Prompt Saved!**\n**Name:** {p_name}\n**UserID:** `{uid}`\n**Link:** Saved Internally.")
                await pin_msg.pin()
            else:
                await message.reply_text("❌ Sirf .txt file bhejein!")

# ======== TRANSLATION WORKFLOW ========
@app.on_message(filters.command(["english", "hienglish"]) & filters.group)
async def start_translation(client, message: Message):
    if not is_authorized(message.from_user.id, message.chat.id): return
    if not message.reply_to_message or not getattr(message.reply_to_message, 'document', None) and not getattr(message.reply_to_message, 'photo', None):
        return await message.reply_text("❌ Kisi ZIP/Image file ko reply karein.")
    
    target_lang = "hienglish" if "hienglish" in message.command[0].lower() else "english"
    uid, cid = message.from_user.id, message.chat.id
    
    sts = await message.reply_text("⏳ File ko Safe Channel me bheja jaa raha hai...")
    copied = await message.reply_to_message.copy(SAFE_CHANNEL_ID)
    file_id = copied.document.file_id if copied.document else copied.photo.file_id
    file_name = copied.document.file_name if copied.document else "image.jpg"
    
    await message.reply_to_message.delete()
    await message.delete()

    task_id = f"t_{message.id}"
    pending_tasks[task_id] = {"file_id": file_id, "uid": uid, "cid": cid, "lang": target_lang, "fname": file_name, "prompt": "none"}

    kb = [[InlineKeyboardButton("Default Prompt", callback_data=f"p_default_{task_id}")]]
    if uid in user_prompts:
        for idx, p in enumerate(user_prompts[uid]):
            kb.append([InlineKeyboardButton(f"Custom: {p['name']}", callback_data=f"p_{idx}_{task_id}")])
            
    await sts.edit_text("🔍 **Step 1: Choose Translation Prompt**", reply_markup=InlineKeyboardMarkup(kb))

@app.on_callback_query()
async def cbs(client, query: CallbackQuery):
    data = query.data
    uid = query.from_user.id

    if data.startswith("p_"):
        _, p_idx, t_id = data.split("_", 2)
        if t_id not in pending_tasks: return
        if p_idx != "default": pending_tasks[t_id]["prompt"] = user_prompts[uid][int(p_idx)]["text"]

        kb = [
            [InlineKeyboardButton("1️⃣ Standard Clean (Default)", callback_data=f"s_style1_{t_id}")],
            [InlineKeyboardButton("2️⃣ Bold Outline (Action)", callback_data=f"s_style2_{t_id}")],
            [InlineKeyboardButton("3️⃣ Soft Manga (Light)", callback_data=f"s_style3_{t_id}")]
        ]
        await query.message.edit_text("🎨 **Step 2: Choose Text Style**", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("s_"):
        _, style, t_id = data.split("_", 2)
        if t_id not in pending_tasks: return
        t = pending_tasks[t_id]
        
        try:
            await client.send_message(uid, f"✅ **Get Started!**\nAapka Task '{t['fname']}' processing me hai. Yahi par file aayegi.")
        except: pass

        # Dispatch GitHub Action with Security Password
        url = f"https://api.github.com/repos/{REPO_NAME}/actions/workflows/manga.yml/dispatches"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        payload = {
            "ref": "main",
            "inputs": {
                "password": AUTH_KEY, 
                "file_id": t["file_id"], "chat_id": str(t["cid"]), "msg_id": str(query.message.id),
                "user_id": str(uid), "lang": t["lang"], "prompt": t["prompt"], "style": style, "fname": t["fname"]
            }
        }
        requests.post(url, headers=headers, json=payload)
        
        await query.message.edit_text("🚀 **Task Assigned to GitHub Worker!**\n`Wait for live progress...`")
        del pending_tasks[t_id]

flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "HF Bot Active & Pinged!"

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start() # Ping Thread
    threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=7860, use_reloader=False), daemon=True).start()
    app.run()
