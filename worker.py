import os, json, subprocess, time, asyncio, shutil
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from PIL import Image

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
DESK_CHANNEL_ID = -1003974162679
INPUTS = json.loads(os.environ["INPUTS"])

app = Client(
    "worker_session", 
    api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, 
    max_concurrent_transmissions=10, 
    in_memory=True
)

last_time = 0
cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Skip / Cancel", callback_data="cancel_active_run")]])

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

async def progress_tracker(current, total, c_id, m_id, action_type):
    global last_time
    now = time.time()
    if now - last_time > 5 or current == total:
        percent = (current / total) * 100 if total > 0 else 0
        speed_mb = ((current / 1048576) / (now - last_time + 0.1)) if last_time > 0 else 0
        
        if action_type == "download":
            bar = get_download_bar(percent)
            text = f"📥 **Downloading File...**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
        else:
            bar = get_send_bar(percent)
            text = f"📤 **Uploading Manga...**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
            
        try: await app.edit_message_text(c_id, m_id, text, reply_markup=cancel_markup)
        except: pass
        last_time = now

# --- AUTO RESIZE AND COMPRESS ---
def optimize_images(directory):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                file_path = os.path.join(root, file)
                try:
                    img = Image.open(file_path)
                    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                    # Agar image ki width 1200 se badi hai, toh chota kardo taaki size same rahe
                    if img.width > 1200:
                        ratio = 1200 / img.width
                        new_h = int(img.height * ratio)
                        img = img.resize((1200, new_h), Image.LANCZOS)
                    # Compress with quality 80% to save space
                    img.save(file_path, "JPEG", optimize=True, quality=80)
                except Exception: pass

async def main():
    async with app:
        c_id = int(INPUTS["chat_id"])
        m_id = int(INPUTS["msg_id"])
        u_id = int(INPUTS["user_id"])
        fname = INPUTS["fname"]
        lang = INPUTS["lang"]
        ext = fname.split('.')[-1].lower()

        global last_time
        last_time = time.time()
        
        # 1. DOWNLOAD
        os.makedirs("./manga-image-translator/input_folder", exist_ok=True)
        dl_path = f"./manga-image-translator/input_{u_id}.{ext}"
        await app.download_media(INPUTS["file_id"], file_name=dl_path, progress=progress_tracker, progress_args=(c_id, m_id, "download"))

        os.chdir("manga-image-translator")
        process_target = f"input_{u_id}.{ext}"
        is_zip = ext in ['zip', 'cbz']
        
        if is_zip:
            shutil.unpack_archive(process_target, "input_folder")
            process_target = "input_folder"

        # Count Pages
        if os.path.isdir(process_target):
            img_files = [f for f in os.listdir(process_target) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
            total_pages = len(img_files) if img_files else 1
        else:
            total_pages = 1

        # 2. TRANSLATION (WITH UNBUFFERED LOGGING)
        target_lang_code = "HIN" if lang == "hienglish" else "ENG"
        cmd = [
            "python", "-m", "manga_translator", "-i", process_target, 
            "--translator", "google", "--target-lang", target_lang_code, "--use-cuda", "False"
        ]
        if INPUTS['style'] == "style2": cmd.extend(["--font-size", "28", "--text-color", "black", "--outline-color", "white"])
        elif INPUTS['style'] == "style3": cmd.extend(["--font-size", "22"])

        out_target = f"{process_target}_translated"

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        
        start_time = time.time()
        last_edit = time.time()
        current_log = "Initializing Models..."
        
        # Live Loop
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=2.0)
                if line:
                    decoded = line.decode('utf-8', errors='ignore').strip()
                    if decoded and "100%" not in decoded:
                        if "download" in decoded.lower(): current_log = "Downloading AI Models (Takes time)..."
                        elif "detecting" in decoded.lower(): current_log = "Detecting Text Bubbles..."
                        elif "translating" in decoded.lower(): current_log = "Translating text..."
                        else: current_log = decoded[:50] + "..."
                if not line and process.returncode is not None: break
            except asyncio.TimeoutError: pass
            
            if process.returncode is not None: break
                
            now = time.time()
            if now - last_edit > 5:
                translated_files = 0
                if os.path.exists(out_target):
                    translated_files = len([f for f in os.listdir(out_target) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))])
                
                percent = min((translated_files / total_pages) * 100, 100.0) if total_pages > 0 else 0
                elapsed = now - start_time
                speed_ppm = (translated_files / elapsed) * 60 if elapsed > 0 else 0
                
                bar = get_process_bar(percent)
                text = f"⚙️ **Translation Engine Active**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_ppm:.1f} Pages/min`\n📄 `{translated_files} / {total_pages} Pages Done`\n\n📝 **Log:** `{current_log}`"
                
                try: await app.edit_message_text(c_id, m_id, text, reply_markup=cancel_markup)
                except: pass
                last_edit = now
        
        await process.wait()

        # 3. AUTO RESIZE / COMPRESSION
        if os.path.exists(out_target):
            await app.edit_message_text(c_id, m_id, f"🗜️ **Optimizing Manga Size...**\n{get_process_bar(100)} [Compressing]")
            optimize_images(out_target)

        # Output preparation
        if is_zip:
            final_file = f"Translated_{fname}"
            subprocess.run(["zip", "-r", final_file, out_target])
        else:
            files = os.listdir(out_target)
            final_file = f"{out_target}/{files[0]}" if files else process_target

        # 4. FAST UPLOAD
        last_time = time.time()
        desk_msg = await app.send_document(
            chat_id=DESK_CHANNEL_ID, document=final_file, 
            caption=f"📁 **Logs Archive**\nUser: `{u_id}`\nFormat: `{ext}`",
            progress=progress_tracker, progress_args=(c_id, m_id, "upload")
        )
        
        await app.send_document(chat_id=u_id, document=desk_msg.document.file_id, caption=f"✅ **Task Ready!**\n📄 `{fname}`")

        # 5. CLEANUP
        try:
            await app.delete_messages(c_id, m_id)
            if c_id != u_id:
                await app.send_message(c_id, f"✅ **Check Bot!**\nManga Task Complete for <a href='tg://user?id={u_id}'>User</a>. File delivered in PM.", parse_mode=pyrogram.enums.ParseMode.HTML)
        except: pass

app.run(main())
