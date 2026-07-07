import os
import json
import subprocess
import time
import asyncio
import shutil
import traceback
import requests
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from PIL import Image

last_time = 0
cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Skip / Cancel", callback_data="cancel_active_run")]])

def get_process_bar(percent):
    total = 20
    filled = int(percent / 100 * total)
    seq = ["•", "°", ":", "°", "•", ":"]
    bar = "".join(seq[i % len(seq)] for i in range(filled))
    return f"[{bar}{'-' * (total - filled)}]"

async def progress_tracker(app_instance, current, total, c_id, m_id, action_type):
    global last_time
    now = time.time()
    if now - last_time > 4 or current == total:
        percent = (current / total) * 100 if total > 0 else 0
        speed_mb = ((current / 1048576) / (now - last_time + 0.1)) if last_time > 0 else 0
        
        bar = f"[{'>' * int(percent / 100 * 20)}{'-' * (20 - int(percent / 100 * 20))}]"
        
        if action_type == "download":
            text = f"📥 **Downloading File...**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
        else:
            text = f"📤 **Uploading Manga...**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
            
        try: await app_instance.edit_message_text(c_id, m_id, text, reply_markup=cancel_markup)
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
    # 1. Environment Variable Parsing Safely Inside Try-Except Block
    if "INPUTS" not in os.environ:
        raise ValueError("Environment variable 'INPUTS' is completely missing from workflow context.")
        
    inputs_data = json.loads(os.environ["INPUTS"])
    api_id_raw = os.environ.get("API_ID")
    api_hash = os.environ.get("API_HASH")
    bot_token = os.environ.get("BOT_TOKEN")
    dispatch_password = os.environ.get("DISPATCH_PASSWORD")
    
    if not api_id_raw or not api_hash or not bot_token:
        raise ValueError("Critical GitHub Secrets (API_ID, API_HASH, BOT_TOKEN) are missing from Repository Secrets.")

    api_id = int(api_id_raw)
    c_id = int(inputs_data["chat_id"])
    m_id = int(inputs_data["msg_id"])
    u_id = int(inputs_data["user_id"])
    fname = inputs_data["fname"]
    lang = inputs_data["lang"]
    ext = fname.split('.')[-1].lower()

    # Dynamic Password Verification
    provided_password = inputs_data.get("password")
    if dispatch_password and provided_password != dispatch_password:
        raise PermissionError("Security Check Failed: Dispatch password mismatch.")

    # 2. Client Initialization Safely Inside Thread Context
    app = Client(
        "worker_session", 
        api_id=api_id, 
        api_hash=api_hash, 
        bot_token=bot_token, 
        max_concurrent_transmissions=20, 
        in_memory=True
    )

    async with app:
        global last_time
        last_time = time.time()
        
        os.makedirs("./manga-image-translator/input_folder", exist_ok=True)
        dl_path = f"./manga-image-translator/input_{u_id}.{ext}"
        
        await app.download_media(
            inputs_data["file_id"], 
            file_name=dl_path, 
            progress=progress_tracker, 
            progress_args=(app, c_id, m_id, "download")
        )

        os.chdir("manga-image-translator")
        process_target = f"input_{u_id}.{ext}"
        is_zip = ext in ['zip', 'cbz']
        
        if is_zip:
            shutil.unpack_archive(process_target, "input_folder")
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

        target_lang_code = "HIN" if lang == "hienglish" else "ENG"
        cmd = [
            "python", "-m", "manga_translator", "local", "-i", process_target, 
            "--translator", "google", "--target-lang", target_lang_code
        ]
        if inputs_data.get('style') == "style2": cmd.extend(["--font-size", "28", "--text-color", "black", "--outline-color", "white"])
        elif inputs_data.get('style') == "style3": cmd.extend(["--font-size", "22"])

        out_target = f"{process_target}_translated"

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        
        start_time = time.time()
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
                elapsed = now - start_time
                speed_ppm = (translated_files / elapsed) * 60 if elapsed > 0 else 0
                
                text = f"⚙️ **Translation Engine Active**\n{get_process_bar(percent)} [{percent:.1f}%]\n🚀 Speed: `{speed_ppm:.1f} Pgs/min`\n📄 `{translated_files} / {total_pages} Pages Done`\n\n📝 **Log:** `{current_log}`"
                try: await app.edit_message_text(c_id, m_id, text, reply_markup=cancel_markup)
                except: pass
                last_edit = now
        
        await process.wait()

        if not os.path.exists(out_target):
            error_text = f"❌ **Task Failed!**\nGitHub Worker crashed during translation (process exited with code `{process.returncode}`)."
            await app.edit_message_text(c_id, m_id, error_text)
            return

        await app.edit_message_text(c_id, m_id, f"🗜️ **Optimizing Manga Size...**\n{get_process_bar(100)} [Compressing]")
        optimize_images(out_target)

        if is_zip:
            final_file = f"Translated_{fname}"
            subprocess.run(["zip", "-r", final_file, out_target])
        else:
            files = os.listdir(out_target)
            final_file = f"{out_target}/{files[0]}" if files else process_target

        last_time = time.time()
        desk_msg = await app.send_document(
            chat_id=DESK_CHANNEL_ID, document=final_file, 
            caption=f"📁 **Logs Archive**\nUser: `{u_id}`\nFormat: `{ext}`",
            progress=progress_tracker, progress_args=(app, c_id, m_id, "upload")
        )
        
        await app.send_document(chat_id=u_id, document=desk_msg.document.file_id, caption=f"✅ **Task Ready!**\n📄 `{fname}`")

        try:
            await app.delete_messages(c_id, m_id)
            if c_id != u_id:
                await app.send_message(c_id, f"✅ **Check Bot!**\nManga Task Complete for <a href='tg://user?id={u_id}'>User</a>. File delivered in PM.", parse_mode=pyrogram.enums.ParseMode.HTML)
        except: pass

async def main():
    try:
        await worker_core()
    except Exception as e:
        tb = traceback.format_exc()
        # Direct fallback crash reporting straight to Telegram via robust HTTP POST
        err_msg = f"❌ **GitHub Worker Script Crash Report!**\n\n**Error:** `{str(e)}`\n\n**Traceback:**\n`{tb[-900:]}`"
        try:
            bot_token = os.environ.get("BOT_TOKEN")
            inputs_data = json.loads(os.environ.get("INPUTS", "{}"))
            chat_id = inputs_data.get("chat_id")
            if bot_token and chat_id:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": int(chat_id), "text": err_msg, "parse_mode": "Markdown"},
                    timeout=10
                )
        except Exception as reporting_err:
            print(f"Failed to post crash logs: {reporting_err}")

if __name__ == "__main__":
    asyncio.run(main())
