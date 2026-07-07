import os
import json
import subprocess
import time
import asyncio
import shutil
import fitz  # PyMuPDF (High speed document processor)
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from PIL import Image

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
DESK_CHANNEL_ID = -1003974162679
INPUTS = json.loads(os.environ["INPUTS"])

app = Client("worker_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, max_concurrent_transmissions=10, in_memory=True)

last_time = 0
cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Skip / Cancel", callback_data="cancel_active_run")]])

def get_process_bar(percent):
    total = 20
    filled = int(percent / 100 * total)
    bar = "█" * filled + "░" * (total - filled)
    return f"[{bar}]"

async def progress_tracker(current, total, c_id, m_id, action_type):
    global last_time
    now = time.time()
    # Throttled progress logs to prevent API rate limiting (429) during transfers
    if now - last_time > 4 or current == total:
        percent = (current / total) * 100 if total > 0 else 0
        speed_mb = ((current / 1048576) / (now - last_time + 0.1)) if last_time > 0 else 0
        
        bar = get_process_bar(percent)
        
        if action_type == "download":
            text = f"📥 **Downloading Document...**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
        else:
            text = f"📤 **Uploading Translated Manga...**\n{bar} [{percent:.1f}%]\n🚀 Speed: `{speed_mb:.2f} MB/s`\n📦 `{current/1048576:.1f}MB / {total/1048576:.1f}MB`"
            
        try: 
            await app.edit_message_text(c_id, m_id, text, reply_markup=cancel_markup)
        except Exception: 
            pass
        last_time = now

def optimize_images(directory):
    """Downscales images exceeding 1200px width while keeping original dimension constraints intact."""
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                file_path = os.path.join(root, file)
                try:
                    img = Image.open(file_path)
                    if img.mode in ("RGBA", "P"): 
                        img = img.convert("RGB")
                    if img.width > 1200:
                        ratio = 1200 / img.width
                        new_h = int(img.height * ratio)
                        img = img.resize((1200, new_h), Image.LANCZOS)
                    img.save(file_path, "JPEG", optimize=True, quality=85)
                except Exception: 
                    pass

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
        
        os.makedirs("./manga-image-translator/input_folder", exist_ok=True)
        dl_path = f"./manga-image-translator/input_{u_id}.{ext}"
        await app.download_media(INPUTS["file_id"], file_name=dl_path, progress=progress_tracker, progress_args=(c_id, m_id, "download"))

        os.chdir("manga-image-translator")
        process_target = f"input_{u_id}.{ext}"
        
        is_zip = ext in ['zip', 'cbz']
        is_pdf = ext == 'pdf'
        
        # Format Unwrapping Phase
        if is_zip:
            shutil.unpack_archive(process_target, "input_folder")
            process_target = "input_folder"
        elif is_pdf:
            pdf_doc = fitz.open(process_target)
            for page_num in range(len(pdf_doc)):
                page = pdf_doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                pix.save(f"input_folder/page_{page_num:04d}.png")
            process_target = "input_folder"

        # Scanning nested files recursively
        img_files = []
        if os.path.isdir(process_target):
            for root, _, files in os.walk(process_target):
                for f in files:
                    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        img_files.append(os.path.join(root, f))
            total_pages = len(img_files) if img_files else 1
        else:
            total_pages = 1

        # Command compilation for the modern translation library API
        target_lang_code = "HIN" if lang == "hienglish" else "ENG"
        cmd = [
            "python", "-m", "manga_translator", "local", "-i", process_target, 
            "--translator", "google", "--target-lang", target_lang_code
        ]
        
        if INPUTS.get('style') == "style2": 
            cmd.extend(["--font-size", "28", "--text-color", "black", "--outline-color", "white"])
        elif INPUTS.get('style') == "style3": 
            cmd.extend(["--font-size", "22"])

        out_target = f"{process_target}_translated"

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        
        start_time = time.time()
        last_edit = time.time()
        current_log = "Initializing Models..."
        
        while True:
            try:
                # Controlled polling avoids busy-looping CPU
                line = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                if process.returncode is not None: 
                    break
                continue
            
            # EOF Safe Exit Condition
            if not line: 
                break
                
            decoded = line.decode('utf-8', errors='ignore').strip()
            if decoded:
                if "100%" not in decoded:
                    if "download" in decoded.lower(): 
                        current_log = "Downloading Models..."
                    elif "detecting" in decoded.lower(): 
                        current_log = "Scanning Text..."
                    elif "translating" in decoded.lower(): 
                        current_log = "Translating text..."
                    else: 
                        current_log = decoded[:45] + "..."
                
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
                
                text = f"⚙️ **Translation Engine Active**\n{get_process_bar(percent)} [{percent:.1f}%]\n🚀 Speed: `{speed_ppm:.1f} Pages/min`\n📄 `{translated_files} / {total_pages} Pages Rendered`\n\n📝 **Log:** `{current_log}`"
                try: 
                    await app.edit_message_text(c_id, m_id, text, reply_markup=cancel_markup)
                except Exception: 
                    pass
                last_edit = now
        
        await process.wait()

        # Crash validation
        if not os.path.exists(out_target):
            error_text = f"❌ **Task Failed!**\nGitHub Worker crashed during translation (process exited with code `{process.returncode}`)."
            await app.edit_message_text(c_id, m_id, error_text)
            return

        await app.edit_message_text(c_id, m_id, f"🗜️ **Optimizing Manga Size...**\n{get_process_bar(100)} [Compressing]")
        optimize_images(out_target)

        # Packaging the output container formats
        if is_zip:
            final_file = f"Translated_{fname}"
            subprocess.run(["zip", "-r", final_file, out_target])
        elif is_pdf:
            final_file = f"Translated_{fname}"
            pdf_out = fitz.open()
            img_list = sorted([
                os.path.join(out_target, f) for f in os.listdir(out_target) 
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
            ])
            for img_path in img_list:
                img_doc = fitz.open(img_path)
                pdf_bytes = img_doc.convert_to_pdf()
                img_pdf = fitz.open("pdf", pdf_bytes)
                pdf_out.insert_pdf(img_pdf)
            pdf_out.save(final_file)
            pdf_out.close()
        else:
            files = os.listdir(out_target)
            final_file = f"{out_target}/{files[0]}" if files else process_target

        last_time = time.time()
        desk_msg = await app.send_document(
            chat_id=DESK_CHANNEL_ID, document=final_file, 
            caption=f"📁 **Logs Archive**\nUser: `{u_id}`\nFormat: `{ext}`",
            progress=progress_tracker, progress_args=(c_id, m_id, "upload")
        )
        
        await app.send_document(chat_id=u_id, document=desk_msg.document.file_id, caption=f"✅ **Manga Sub Complete!**\n📄 `{fname}`")

        try:
            await app.delete_messages(c_id, m_id)
            if c_id != u_id:
                await app.send_message(c_id, f"✅ **Check Bot!**\nManga Task Complete for <a href='tg://user?id={u_id}'>User</a>. File delivered in PM.", parse_mode=pyrogram.enums.ParseMode.HTML)
        except Exception: 
            pass

if __name__ == "__main__":
    asyncio.run(main())
