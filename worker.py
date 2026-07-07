import os
import time
import asyncio
import threading
import requests
import psutil
import pyrogram.utils
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatType
from flask import Flask

# Safe Channel / Peer ID Invalid Bypass
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
REPO_NAME = os.getenv("REPO_NAME", "killmasterkillingmaster-glitch/Lovelifep").strip()

OWNER_ID = 5344078567
ALLOWED_USER = 5351848105
GROUP_ID = -1003899919015
SAFE_CHANNEL_ID = -1003962165512

app = Client("MangaFrontend", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=16)

user_states, user_prompts, pending_tasks = {}, {}, {}

def is_authorized(m: Message):
    if not m.from_user: return False
    u_id = m.from_user.id
    return u_id in [OWNER_ID, ALLOWED_USER] or (m.chat and m.chat.id == GROUP_ID)

@app.on_message(filters.command("start"))
async def start_cmd(c, m: Message):
    await m.reply("✅ **Manga Translator Ready!**\nSend file here or in group with `/english` or `/hienglish`.")

@app.on_message(filters.command("stats"))
async def stats_cmd(c, m: Message):
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent()
    await m.reply(f"📊 **HF Diagnostics:**\n🖥️ CPU: `{cpu}%`\n💾 RAM: `{ram.percent}%`")

@app.on_message(filters.command("addprompt"))
async def add_prompt(c, m: Message):
    if not is_authorized(m): return
    uid = m.from_user.id
    if uid not in user_prompts: user_prompts[uid] = []
    if len(user_prompts[uid]) >= 2: return await m.reply("❌ Max 2 prompts allowed. Use /deleteprompt")
    user_states[uid] = {"state": "WAITING_NAME"}
    await m.reply("📝 **Send Prompt Name:**")

@app.on_message(filters.command("deleteprompt"))
async def del_prompt(c, m: Message):
    uid = m.from_user.id
    if uid in user_prompts: user_prompts[uid].clear()
    await m.reply("🗑️ All saved prompts deleted.")

@app.on_message((filters.text | filters.document) & ~filters.command(["start", "stats", "addprompt", "deleteprompt", "english", "hienglish"]))
async def handle_inputs(c, m: Message):
    if not m.from_user: return
    uid = m.from_user.id
    session = user_states.get(uid)
    if not session: return

    if session.get("state") == "WAITING_NAME" and m.text:
        session["state"] = "WAITING_FILE"
        session["name"] = m.text
        await m.reply(f"✅ Name Saved: `{m.text}`\n📄 Now send `.txt` prompt file.")
    elif session.get("state") == "WAITING_FILE" and m.document and m.document.file_name.endswith('.txt'):
        st = await m.reply("⏳ Saving...")
        file_path = await m.download()
        with open(file_path, "r", encoding="utf-8") as f: txt_data = f.read()
        os.remove(file_path)
        user_prompts[uid].append({"name": session["name"], "text": txt_data})
        del user_states[uid]
        await st.delete()
        pin = await m.reply(f"📌 **Prompt Saved:** {session['name']}")
        try: await pin.pin()
        except: pass

@app.on_message(filters.command(["english", "hienglish"]))
async def translate_cmd(c, m: Message):
    if not is_authorized(m): return
    
    target_msg = m.reply_to_message if m.reply_to_message else m
    if not target_msg.document and not target_msg.photo:
        return await m.reply("❌ Reply to a ZIP/Image/PDF file.")
    
    lang = "hienglish" if "hienglish" in m.command[0].lower() else "english"
    uid, cid = m.from_user.id, m.chat.id
    
    st = await m.reply("⏳ **Verification:** File sending to Safe Channel...")
    
    # --- SAFE CHANNEL LOGIC ---
    try:
        copied = await target_msg.copy(SAFE_CHANNEL_ID)
        file_id = copied.document.file_id if copied.document else copied.photo.file_id
        file_name = copied.document.file_name if copied.document else "image.jpg"
    except Exception as e:
        return await st.edit(f"❌ **Safe Channel Error:** Bot is not admin in {SAFE_CHANNEL_ID} or Invalid ID.\nLogs: `{e}`")

    # Delete original message (Group ho ya PM, dono me delete karega)
    try:
        if m.reply_to_message: await m.reply_to_message.delete()
        await m.delete()
    except: pass

    task_id = f"task_{m.id}"
    pending_tasks[task_id] = {"file_id": file_id, "uid": uid, "cid": cid, "lang": lang, "fname": file_name, "prompt": "none"}

    kb = [[InlineKeyboardButton("Default Prompt", callback_data=f"p_default_{task_id}")]]
    if uid in user_prompts:
        for idx, p in enumerate(user_prompts[uid]):
            kb.append([InlineKeyboardButton(f"Custom: {p['name']}", callback_data=f"p_{idx}_{task_id}")])
            
    await st.edit("🔍 **Step 1: Choose Translation Prompt**", reply_markup=InlineKeyboardMarkup(kb))

@app.on_callback_query(filters.regex("cancel_active_run"))
async def cancel_run_callback(c, q: CallbackQuery):
    url = f"https://api.github.com/repos/{REPO_NAME}/actions/runs?event=workflow_dispatch"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    try:
        r = await asyncio.to_thread(requests.get, url, headers=headers)
        if r.status_code == 200:
            runs = r.json().get("workflow_runs", [])
            cancelled = False
            for run in runs:
                if run["status"] in ["in_progress", "queued"]:
                    cancel_url = f"https://api.github.com/repos/{REPO_NAME}/actions/runs/{run['id']}/cancel"
                    await asyncio.to_thread(requests.post, cancel_url, headers=headers)
                    cancelled = True
            if cancelled:
                await q.message.edit("🛑 **Task Cancelled!** Github Runner aborted successfully.")
                await q.answer("Task Aborted", show_alert=True)
            else: await q.answer("No active task found. (Pehle se cancel ho chuka hai)", show_alert=True)
        else: await q.answer("GitHub API error.", show_alert=True)
    except Exception as e: await q.answer(f"Abort Exception: {e}", show_alert=True)

@app.on_callback_query()
async def cbs(c, q: CallbackQuery):
    data = q.data
    uid = q.from_user.id
    if data == "cancel_active_run": return

    if data.startswith("p_"):
        _, p_idx, t_id = data.split("_", 2)
        if t_id not in pending_tasks: return await q.answer("Task Expired!", show_alert=True)
        if p_idx != "default": pending_tasks[t_id]["prompt"] = user_prompts[uid][int(p_idx)]["text"]

        kb = [
            [InlineKeyboardButton("1️⃣ Standard Clean (Default)", callback_data=f"s_style1_{t_id}")],
            [InlineKeyboardButton("2️⃣ Bold Outline (Action)", callback_data=f"s_style2_{t_id}")],
            [InlineKeyboardButton("3️⃣ Soft Manga (Light)", callback_data=f"s_style3_{t_id}")]
        ]
        await q.message.edit("🎨 **Step 2: Choose Text Style**", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("s_"):
        _, style, t_id = data.split("_", 2)
        if t_id not in pending_tasks: return
        t = pending_tasks[t_id]
        
        await q.message.edit("🚀 **Dispatching Task to GitHub Worker...**")
        url = f"https://api.github.com/repos/{REPO_NAME}/actions/workflows/manga.yml/dispatches"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        
        # PASSWORD REMOVED FROM PAYLOAD
        payload = {
            "ref": "main",
            "inputs": {
                "file_id": t["file_id"], "chat_id": str(t["cid"]), "msg_id": str(q.message.id),
                "user_id": str(uid), "lang": t["lang"], "prompt": t["prompt"], "style": style, "fname": t["fname"]
            }
        }
        r = requests.post(url, headers=headers, json=payload)
        
        if r.status_code == 204:
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Skip / Cancel", callback_data="cancel_active_run")]])
            await q.message.edit("🚀 **Task Queued in GitHub!**\n⚡ *Live Progress Bar Starting...*", reply_markup=cancel_kb)
        else:
            await q.message.edit(f"❌ **GitHub Worker Dispatch Failed:**\n`{r.text}`")
        del pending_tasks[t_id]

flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Manga HF Frontend Running!"

def keep_alive():
    while True:
        try: requests.get("http://127.0.0.1:7860")
        except: pass
        time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=7860, use_reloader=False), daemon=True).start()
    app.run()
