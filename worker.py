# worker.py
import os
import sys
import zipfile
import shutil
import asyncio
from pyrogram import Client

# Safe Channel / Peer ID Invalid Bypass
import pyrogram.utils
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

# Flat environment variables (Passed directly from manga.yml)
FILE_ID = os.getenv("FILE_ID", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
MSG_ID = int(os.getenv("MSG_ID", "0"))
USER_ID = int(os.getenv("USER_ID", "0"))
LANG = os.getenv("LANG", "english").strip()
PROMPT = os.getenv("PROMPT", "none").strip()
STYLE = os.getenv("STYLE", "style1").strip()
FNAME = os.getenv("FNAME", "translated_manga.zip").strip()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEEPL_KEY = os.getenv("DEEPL_API_KEY", "").strip()

SAFE_CHANNEL_ID = -1003962165512

print("=== STARTING OPTIMIZED MANGA WORKER ===")

if API_ID == 0 or not API_HASH or not BOT_TOKEN:
    print("❌ CRITICAL ERROR: Credentials missing!")

if DEEPL_KEY:
    os.environ["DEEPL_AUTH_KEY"] = DEEPL_KEY

os.environ["TRANSLITERATE_TO_ROMAN_HINDI"] = "1" if LANG == "hienglish" else "0"

def patch_translator():
    paths = [
        "manga-image-translator/manga_translator/translators/common.py",
        "manga_translator/translators/common.py"
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f: content = f.read()
                target = "_translations = await self._translate(*self.parse_language_codes(from_lang, to_lang, fatal=True), queries)"
                replacement = """_translations = await self._translate(*self.parse_language_codes(from_lang, to_lang, fatal=True), queries)
        import os
        if os.getenv("TRANSLITERATE_TO_ROMAN_HINDI") == "1":
            try:
                from anyascii import anyascii
                _translations = [anyascii(t) for t in _translations]
            except Exception as e: pass"""
                if target in content and "anyascii" not in content:
                    content = content.replace(target, replacement)
                    with open(path, "w", encoding="utf-8") as f: f.write(content)
                    return True
            except: pass
    return False

def make_progress_bar(current, total, length=15):
    percent = min(1.0, max(0.0, current / total if total > 0 else 0))
    filled = int(round(length * percent))
    return f"[{'█' * filled}{'░' * (length - filled)}] {int(percent * 100)}%"

async def main():
    if not FILE_ID or not CHAT_ID or not MSG_ID: return
    patch_translator()

    bot = Client("MangaWorker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, no_updates=True)
    await bot.start()

    async def update_status(text):
        try: await bot.edit_message_text(chat_id=CHAT_ID, message_id=MSG_ID, text=text)
        except: pass

    await update_status("⏳ **Worker Initialized:** Downloading document...")
    try: download_path = await bot.download_media(FILE_ID)
    except Exception as e:
        await update_status(f"❌ **Download Error:** `{e}`")
        return await bot.stop()

    # STRICT FORMAT HANDLING BASED ON FNAME
    original_ext = os.path.splitext(FNAME)[1].lower()
    if not original_ext: original_ext = ".zip"

    # ---- YAHAN SE REPLACE KAREIN ----
    
    # FIX 1: ABSOLUTE PATHS (Taki folder humesha sahi jagah mile)
    workspace = os.path.abspath("manga_workspace")
    input_dir = os.path.join(workspace, "input")
    output_dir = os.path.join(workspace, "output")
    
    if os.path.exists(workspace): shutil.rmtree(workspace)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    pages = []
    await update_status(f"📦 **Analyzing:** Processing `{original_ext}` format...")

    # EXTRACTING FILES
    if original_ext in [".zip", ".cbz"]:
        with zipfile.ZipFile(download_path, 'r') as zip_ref: zip_ref.extractall(input_dir)
    elif original_ext == ".pdf":
        import fitz
        doc = fitz.open(download_path)
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            pix.save(os.path.join(input_dir, f"page_{page_num:03d}.png"))
        doc.close()
    else:
        shutil.copy(download_path, input_dir)

    for root, _, files in os.walk(input_dir):
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                pages.append(os.path.join(root, f))
                
    total_pages = len(pages)
    if total_pages == 0:
        await update_status("❌ **Error:** No image panels found inside the file.")
        return await bot.stop()

    translator_to_use = "deepl" if DEEPL_KEY else "google"
    target_lang = "HIN" if LANG == "hienglish" else "ENG"
    style_flags = ["--manga2eng"] if STYLE == "style2" else []

    await update_status(f"🔄 **AI Engine Started:** Processing {total_pages} pages in batch mode...\n⚡ *This prevents bot from hanging!*")

    # BATCH COMMAND
    cli_cmd = [
        "python", "-m", "manga_translator",
        "--translator", translator_to_use,
        "-l", target_lang,
        "-i", input_dir,
        "--dest", output_dir
    ] + style_flags

    cwd_dir = "manga-image-translator" if os.path.exists("manga-image-translator") else None
    
    process = await asyncio.create_subprocess_exec(
        *cli_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd_dir
    )

    # LIVE BACKGROUND PROGRESS TRACKER
    async def progress_tracker():
        while process.returncode is None:
            if os.path.exists(output_dir):
                # FIX 2: Check inside subfolders for progress
                done = 0
                for r, _, f_list in os.walk(output_dir):
                    done += len([f for f in f_list if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))])
                if done > 0:
                    pbar = make_progress_bar(done, total_pages)
                    await update_status(f"🔄 **Translating AI (Batch Mode):**\n{pbar}\n⚡ Processed: {done}/{total_pages} panels")
            await asyncio.sleep(15)

    tracker_task = asyncio.create_task(progress_tracker())
    await process.communicate()
    tracker_task.cancel()

    await update_status(f"🎨 **Structuring Output:** Rebuilding your `{original_ext}` file...")

    # FIX 3: RECURSIVE FILE COLLECTION (Andar ke subfolders se bhi images nikalna)
    translated_files = []
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                translated_files.append(os.path.join(root, f))
    translated_files.sort()

    # FIX 4: EMPTY CHECK (Agar 0 images mili toh 22B ki zip nahi banayega)
    if len(translated_files) == 0:
        await update_status("❌ **Translation Failed!**\nAI Engine could not generate any translated images. Check format or logs.")
        shutil.rmtree(workspace, ignore_errors=True)
        return await bot.stop()

    output_file_to_send = ""

    # REBUILDING SAME formation
    if original_ext in [".zip", ".cbz"]:
        output_file_to_send = "translated_" + FNAME
        with zipfile.ZipFile(output_file_to_send, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for f in translated_files: 
                # Preserve inner folder structure in ZIP
                rel_path = os.path.relpath(f, output_dir)
                zipf.write(f, rel_path)
                
    elif original_ext == ".pdf":
        output_file_to_send = "translated_" + FNAME
        from PIL import Image
        pil_images = [Image.open(f).convert('RGB') for f in translated_files]
        if pil_images: pil_images[0].save(output_file_to_send, save_all=True, append_images=pil_images[1:])
    else:
        output_file_to_send = translated_files[0] if translated_files else download_path

    # ================== UPLOAD SECTION WITH RETRY & SAFETY ==================
    await update_status("📤 **Uploading:** Delivering file to Chat & PM...")
    
    caption = f"✅ **Translation Completed!**\n🌐 Language: `{LANG}`\n🎨 Style: `{STYLE}`"
    upload_success = False
    error_logs = ""
    max_retries = 3

    # Check File Size locally before uploading
    file_size_mb = os.path.getsize(output_file_to_send) / (1024 * 1024)
    if file_size_mb > 49.5:
        await update_status(f"❌ **Upload Failed!**\nFile size (`{file_size_mb:.1f} MB`) exceeds Telegram's 50MB Bot limit.")
        shutil.rmtree(workspace, ignore_errors=True)
        if os.path.exists(output_file_to_send): os.remove(output_file_to_send)
        return await bot.stop()

    # Upload to Group with retry
    for attempt in range(1, max_retries + 1):
        try:
            await bot.send_document(chat_id=CHAT_ID, document=output_file_to_send, caption=caption)
            upload_success = True
            break
        except Exception as e:
            error_logs += f"Group Upload Attempt {attempt}: {e}\n"
            print(f"Group Upload Error (attempt {attempt}): {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

    # Upload to PM if not same as group
    if CHAT_ID != USER_ID:
        for attempt in range(1, max_retries + 1):
            try:
                await bot.send_document(chat_id=USER_ID, document=output_file_to_send, caption=f"📬 **Here is your requested Manga:**\n\n{caption}")
                break
            except Exception as e:
                error_logs += f"PM Upload Attempt {attempt}: {e}\n"
                print(f"PM Upload Error (attempt {attempt}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)

    # Final Status Update
    if upload_success:
        try:
            await bot.delete_messages(chat_id=CHAT_ID, message_ids=MSG_ID)
        except:
            pass
    else:
        await update_status(f"❌ **Upload Failed!**\n{error_logs}\n*(Tip: Please start bot in PM first!)*")

    # CLEANUP
    shutil.rmtree(workspace, ignore_errors=True)
    if os.path.exists(download_path): os.remove(download_path)
    if os.path.exists(output_file_to_send) and original_ext in [".zip", ".cbz", ".pdf"]: os.remove(output_file_to_send)
    
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
