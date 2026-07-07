import os, json, subprocess, time, shutil

# --- PYROGRAM ID BUG FIX PATCH ---
import pyrogram.utils
pyrogram.utils.MIN_CHAT_ID = -999999999999
pyrogram.utils.MIN_CHANNEL_ID = -100999999999999

def get_peer_type_new(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"

pyrogram.utils.get_peer_type = get_peer_type_new
# ---------------------------------

from pyrogram import Client

# Configurations
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
DESK_CHANNEL_ID = -1003974162679 # Hardcoded Desk Channel
INPUTS = json.loads(os.environ["INPUTS"])

# Pyrogram Client initialized with HIGH SPEED settings
app = Client(
    "worker_session", 
    api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, 
    max_concurrent_transmissions=10, 
    in_memory=True
)

last_time = 0

# --- LIVE PROGRESS BAR UI ---
def get_download_bar(percent):
    filled = int(percent / 100 * 15)
    return f"[{'>' * filled}{'-' * (15 - filled)}]"

def get_send_bar(percent):
    filled = int(percent / 100 * 15)
    return f"[{'▓' * filled}{'░' * (15 - filled)}]"

def get_process_bar():
    return "[•°:°•:----]"

async def progress_tracker(current, total, c_id, m_id, action_type):
    global last_time
    now = time.time()
    if now - last_time > 5 or current == total:
        percent = (current / total) * 100 if total > 0 else 0
        speed = (current / 1048576) / (now - last_time + 0.1) if last_time > 0 else 0
        
        if action_type == "download":
            bar = get_download_bar(percent)
            text = f"📥 **Fast Downloading...**\n{bar} [{percent:.0f}%]\n🚀 Speed: `Max` | 📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
        else:
            bar = get_send_bar(percent)
            text = f"📤 **Fast Uploading...**\n{bar} [{percent:.0f}%]\n🚀 Speed: `Max` | 📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
            
        try:
            await app.edit_message_text(c_id, m_id, text)
        except: pass
        last_time = now

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
        
        # STEP 1: FAST DOWNLOAD
        os.makedirs("./manga-image-translator/input_folder", exist_ok=True)
        dl_path = f"./manga-image-translator/input_{u_id}.{ext}"
        
        await app.download_media(INPUTS["file_id"], file_name=dl_path, progress=progress_tracker, progress_args=(c_id, m_id, "download"))

        # Prepare for Translator (If Zip, unpack it)
        os.chdir("manga-image-translator")
        process_target = f"input_{u_id}.{ext}"
        is_zip = ext in ['zip', 'cbz']
        if is_zip:
            shutil.unpack_archive(process_target, "input_folder")
            process_target = "input_folder"

        # STEP 2: TRANSLATING
        await app.edit_message_text(c_id, m_id, f"⚙️ **Processing Engine Active...**\n{get_process_bar()} [Translating]\n\n*Using Google AI without API limit...*")
        
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

        # Output preparation
        out_target = f"{process_target}_translated"
        if is_zip:
            final_file = f"Translated_{fname}"
            subprocess.run(["zip", "-r", final_file, out_target])
        else:
            files = os.listdir(out_target)
            final_file = f"{out_target}/{files[0]}" if files else process_target

        # STEP 3: FAST UPLOAD
        last_time = time.time()
        
        # Delivery 1: DM to User
        await app.send_document(chat_id=u_id, document=final_file, caption=f"✅ **Task Ready!**\n📄 `{fname}`")
        
        # Delivery 2: Desk Channel
        await app.send_document(
            chat_id=DESK_CHANNEL_ID, document=final_file, 
            caption=f"📁 **Backup File**\nUser: `{u_id}`\nFormat: `{ext}`",
            progress=progress_tracker, progress_args=(c_id, m_id, "upload")
        )

        # Clean Up Group Message
        try: await app.delete_messages(c_id, m_id)
        except: pass

app.run(main())
