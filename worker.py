import os, json, subprocess, time, asyncio, shutil
from pyrogram import Client
from PIL import Image # For Auto Resizing

# Configurations
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
DESK_CHANNEL_ID = -1003974162679 # Manga Desk Channel
INPUTS = json.loads(os.environ["INPUTS"])

# Pyrogram Fast Client
app = Client(
    "worker_session", 
    api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, 
    max_concurrent_transmissions=10, 
    in_memory=True
)

last_time = 0

# --- PROGRESS BAR STYLES (FROM HARDSUB BOT) ---
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
            
        try: await app.edit_message_text(c_id, m_id, text)
        except: pass
        last_time = now

# --- AUTO RESIZE & COMPRESS IMAGE ---
def optimize_images(directory):
    print("Optimizing and Resizing images...")
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                file_path = os.path.join(root, file)
                try:
                    img = Image.open(file_path)
                    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                    
                    # Auto Resize logic if image is too large (Width > 1500)
                    if img.width > 1500:
                        ratio = 1500 / img.width
                        new_h = int(img.height * ratio)
                        img = img.resize((1500, new_h), Image.LANCZOS)
                        
                    # Save with High compression but good quality
                    img.save(file_path, "JPEG", optimize=True, quality=80)
                except Exception as e:
                    print(f"Error optimizing {file}: {e}")

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
        
        # 1. FAST DOWNLOAD
        os.makedirs("./manga-image-translator/input_folder", exist_ok=True)
        dl_path = f"./manga-image-translator/input_{u_id}.{ext}"
        
        await app.download_media(INPUTS["file_id"], file_name=dl_path, progress=progress_tracker, progress_args=(c_id, m_id, "download"))

        # Setup translation target
        os.chdir("manga-image-translator")
        process_target = f"input_{u_id}.{ext}"
        is_zip = ext in ['zip', 'cbz']
        if is_zip:
            shutil.unpack_archive(process_target, "input_folder")
            process_target = "input_folder"

        # 2. TRANSLATION PROCESS
        await app.edit_message_text(c_id, m_id, f"⚙️ **Translation Engine Active**\n{get_process_bar(50)} [Running]\n\n*Detecting bubbles, translating and typesetting...*")
        
        target_lang_code = "HIN" if lang == "hienglish" else "ENG"
        cmd = [
            "python", "-m", "manga_translator", "-i", process_target, 
            "--translator", "google", "--target-lang", target_lang_code, "--use-cuda", "False"
        ]

        if INPUTS['style'] == "style2":
            cmd.extend(["--font-size", "28", "--text-color", "black", "--outline-color", "white"])
        elif INPUTS['style'] == "style3":
            cmd.extend(["--font-size", "22"])

        subprocess.run(cmd)

        # 3. AUTO RESIZE / COMPRESSION
        out_target = f"{process_target}_translated"
        if os.path.exists(out_target):
            await app.edit_message_text(c_id, m_id, f"🗜️ **Optimizing Manga Size...**\n{get_process_bar(90)} [Compressing]")
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
        
        # Desk Channel
        desk_msg = await app.send_document(
            chat_id=DESK_CHANNEL_ID, document=final_file, 
            caption=f"📁 **Logs Archive**\nUser: `{u_id}`\nFormat: `{ext}`",
            progress=progress_tracker, progress_args=(c_id, m_id, "upload")
        )
        
        # User PM
        await app.send_document(chat_id=u_id, document=desk_msg.document.file_id, caption=f"✅ **Task Ready!**\n📄 `{fname}`")

        # 5. CLEANUP & GROUP NOTIFICATION
        try:
            await app.delete_messages(c_id, m_id)
            # Group me final completion message
            if c_id != u_id:
                await app.send_message(c_id, f"✅ **Check Bot!**\nManga Task Complete for <a href='tg://user?id={u_id}'>User</a>. File delivered in PM.", parse_mode=pyrogram.enums.ParseMode.HTML)
        except: pass

app.run(main())
