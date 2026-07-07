hereimport os
import sys
import time
import asyncio
import json
import re
import subprocess
import requests
import traceback
import pyrogram.utils
import pyrogram
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from PIL import Image

# Client peer resolver bypass (Decoupled and aligned)
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DISPATCH_PASSWORD = os.getenv("DISPATCH_PASSWORD")

INPUTS_RAW = os.getenv("INPUTS")
if not INPUTS_RAW:
    print("Environment variables missing. Termination triggered.")
    sys.exit(1)
    
INPUTS = json.loads(INPUTS_RAW)

CHAT_ID = int(INPUTS["chat_id"])
USER_ID = int(INPUTS["user_id"])
TRIGGER_MSG_ID = int(INPUTS["msg_id"])
FILE_ID = INPUTS["file_id"]
LANG = INPUTS["lang"]
STYLE = INPUTS["style"]
FNAME = INPUTS["fname"]
EXT = FNAME.split('.')[-1].lower()

DESK_CHANNEL_ID = -1003974162679

last_time = 0
start_time = 0
status_msg_id = TRIGGER_MSG_ID # Update exact matching target

# Aligned inline button payloads
cancel_markup_payload = {
    "inline_keyboard": [[
        {"text": "🛑 Skip / Cancel", "callback_data": "cancel_active_run"}
    ]]
}

def reset_prog():
    global last_time, start_time
    last_time = time.time()
    start_time = time.time()

# --- CUSTOM PROGRESS BAR STYLES MATCHED WITH HARDSUB ---
def get_download_bar(percent):
    total = 20
    filled = int(percent / 100 * total)
    return f"[{'>' * filled}{'-' * (total - filled)}]"

def get_process_bar(percent):
    total = 20
    filled = int(percent / 100 * total)
    seq = ["•", "°", ":", "°", "•", ":"]
    bar = "".join(seq[i % len(seq)] for i in range(filled))
    return f"[{bar}{'-' * (total - filled)}]"

def get_send_bar(percent):
    total = 20
    filled = int(percent / 100 * total)
    return f"[{'▓' * filled}{'▒' * (total - filled)}]"

# --- SYNC HTTP UI UPDATER (DETACHED PROCESS) ---
def _sync_http_edit(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": CHAT_ID,
        "message_id": status_msg_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": cancel_markup_payload
    }
    try: requests.post(url, json=payload, timeout=8)
    except: pass

async def update_http_status(text):
    await asyncio.to_thread(_sync_http_edit, text)

# --- RECURSIVE METRIC TRANSLATION & TRACKING ---
async def prog(current, total, app_instance, step_name):
    global last_time, start_time
    now = time.time()
    if start_time == 0:
        start_time = now
        last_time = now
        return
        
    if now - last_time > 4 or current == total:
        elapsed = now - start_time
        speed = current / elapsed if elapsed > 0 else 0
        speed_mb = (speed / 1024) / 1024
        percent = (current / total) * 100 if total > 0 else 0
        
        if step_name == "manga_download":
            bar = get_download_bar(percent)
            text = f"📥 **Downloading Document**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
        else:
            bar = get_send_bar(percent)
            text = f"📤 **Sending Processed Manga**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
            
        try: await app_instance.edit_message_text(CHAT_ID, status_msg_id, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Skip / Cancel", callback_data="cancel_active_run")]]))
        except: pass
        last_time = now

def optimize_images(directory):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                file_path = os.path.join(root, file)
                try:
                    img = Image.open(file_path)
                    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                    if img.width > 1200:
                        ratio = 1200 / img.width
                        new_h = int(img.height * ratio)
                        img = img.resize((1200, new_h), Image.LANCZOS)
                    img.save(file_path, "JPEG", optimize=True, quality=80)
                except: pass

async def worker_core():
    # Password verification validation
    provided_password = INPUTS.get("password")
    if DISPATCH_PASSWORD and provided_password != DISPATCH_PASSWORD:
        raise PermissionError("Security Check Failed: Dispatch passwords do not match.")

    # ================= PHASE 1: DIRECT HIGH-SPEED DOWNLOAD =================
    app_down = Client("worker_down", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, max_concurrent_transmissions=20, in_memory=True)
    await app_down.start()
    
    reset_prog()
    os.makedirs("./manga-image-translator/input_folder", exist_ok=True)
    dl_path = f"./manga-image-translator/input_{USER_ID}.{EXT}"
    
    await app_down.download_media(FILE_ID, file_name=dl_path, progress=prog, progress_args=(app_down, "manga_download"))
    await app_down.stop() # Client stopped cleanly! System decoupled from Telegram limits.

    # ================= PHASE 2: PROCESSING TRANSLATION (DETACHED) =================
    os.chdir("manga-image-translator")
    process_target = f"input_{USER_ID}.{EXT}"
    is_zip = EXT in ['zip', 'cbz']
    
    if is_zip:
        subprocess.run(["unzip", "-o", "-q", process_target, "-d", "input_folder"])
        process_target = "input_folder"

    img_files = []
    if os.path.isdir(process_target):
        for root, _, files in os.walk(process_target):
            for f in files:
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    img_files.append(os.path.join(root, f))
        total_pages = len(img_files) if img_files else 1
    else:
        total_pages = 1

    target_lang_code = "HIN" if LANG == "hienglish" else "ENG"
    cmd = [
        "python", "-m", "manga_translator", "local", "-i", process_target, 
        "--translator", "google", "--target-lang", target_lang_code
    ]
    if STYLE == "style2": cmd.extend(["--font-size", "28", "--text-color", "black", "--outline-color", "white"])
    elif STYLE == "style3": cmd.extend(["--font-size", "22"])

    out_target = f"{process_target}_translated"

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    
    start_time_proc = time.time()
    last_edit = time.time()
    current_log = "Initializing Models..."
    
    while True:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            if process.returncode is not None: break
            continue
        
        if not line: break
            
        decoded = line.decode('utf-8', errors='ignore').strip()
        if decoded:
            if "100%" not in decoded:
                if "download" in decoded.lower(): current_log = "Downloading AI Models..."
                elif "detecting" in decoded.lower(): current_log = "Detecting Text Bubbles..."
                elif "translating" in decoded.lower(): current_log = "Translating text..."
                else: current_log = decoded[:40] + "..."
            
        now = time.time()
        if now - last_edit > 4:
            translated_files = 0
            if os.path.exists(out_target):
                for root, _, files in os.walk(out_target):
                    for f in files:
                        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                            translated_files += 1
            
            percent = min((translated_files / total_pages) * 100, 100.0) if total_pages > 0 else 0
            elapsed = now - start_time_proc
            speed_ppm = (translated_files / elapsed) * 60 if elapsed > 0 else 0
            
            bar = get_process_bar(percent)
            text = f"⚙️ **Translation Engine Active**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_ppm:.1f} Pgs/min`\n📄 `{translated_files} / {total_pages} Pages Done`\n\n📝 **Log:** `{current_log}`"
            await update_http_status(text)
            last_edit = now
            
    await process.wait()

    if not os.path.exists(out_target):
        raise FileNotFoundError("Manga translation output target folder was not found.")

    await update_http_status("🗜️ **Optimizing Translated Manga Layouts...**")
    optimize_images(out_target)

    if is_zip:
        final_file = f"Translated_{FNAME}"
        subprocess.run(["zip", "-r", "-q", final_file, out_target])
    else:
        files = os.listdir(out_target)
        final_file = f"{out_target}/{files[0]}" if files else process_target

    # ================= PHASE 3: DIRECT HIGH-SPEED UPLOAD =================
    app_up = Client("worker_up", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, max_concurrent_transmissions=20, in_memory=True)
    await app_up.start()
    
    reset_prog()
    await update_http_status(f"📤 **Uploading Manga Backup to logs...**\n{get_send_bar(0)} [0.0%]")
    
    desk_msg = await app_up.send_document(
        chat_id=DESK_CHANNEL_ID, document=final_file, 
        caption=f"📁 **Logs Archive**\nUser: `{USER_ID}`\nFormat: `{EXT}`",
        progress=prog, progress_args=(app_up, "manga_upload")
    )
    
    await app_up.send_document(chat_id=USER_ID, document=desk_msg.document.file_id, caption=f"✅ **Manga Sub Complete!**\n📄 `{FNAME}`")

    try:
        await app_up.delete_messages(CHAT_ID, status_msg_id)
        if CHAT_ID != USER_ID:
            await app_up.send_message(CHAT_ID, f"✅ **Check Bot!**\nManga Task Complete for <a href='tg://user?id={USER_ID}'>User</a>. File delivered in PM.", parse_mode=ParseMode.HTML)
    except: pass
    await app_up.stop()

async def main():
    try:
        await worker_core()
    except Exception as e:
        tb = traceback.format_exc()
        err_text = f"❌ **Workflow Execution Error:**\n<code>{html.escape(str(e))}</code>\n\n**Traceback:**\n<code>{html.escape(tb[-800:])}</code>"
        try: _sync_http_edit(err_text)
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
