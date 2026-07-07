import os
import sys
import zipfile
import shutil
import asyncio
from pyrogram import Client

# Safe Channel / Peer ID Invalid Bypass
import pyrogram.utils
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

# Flat environment variables load karein (No JSON dependency)
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

print("=== STARTING MANGA WORKER ===")
print(f"FILE_ID: {FILE_ID}")
print(f"CHAT_ID: {CHAT_ID}")
print(f"MSG_ID: {MSG_ID}")
print(f"LANG: {LANG}")
print(f"STYLE: {STYLE}")

# Credentials verify karein
if API_ID == 0 or not API_HASH or not BOT_TOKEN:
    print("❌ CRITICAL ERROR: Credentials (API_ID, API_HASH, BOT_TOKEN) are missing or 0!")

# Bind DeepL Auth Key
if DEEPL_KEY:
    os.environ["DEEPL_AUTH_KEY"] = DEEPL_KEY

# Set state for romanized Hindi transliterator
if LANG == "hienglish":
    os.environ["TRANSLITERATE_TO_ROMAN_HINDI"] = "1"
else:
    os.environ["TRANSLITERATE_TO_ROMAN_HINDI"] = "0"

def patch_translator():
    """
    Translators folder ke common.py ko patch karega taaki 
    Devanagari Hindi automatically Roman (Hinglish) me convert ho jaye.
    """
    paths = [
        "manga-image-translator/manga_translator/translators/common.py",
        "manga_translator/translators/common.py"
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                target = "_translations = await self._translate(*self.parse_language_codes(from_lang, to_lang, fatal=True), queries)"
                replacement = """_translations = await self._translate(*self.parse_language_codes(from_lang, to_lang, fatal=True), queries)
        # Hinglish Transliterator Hook
        import os
        if os.getenv("TRANSLITERATE_TO_ROMAN_HINDI") == "1":
            try:
                from anyascii import anyascii
                _translations = [anyascii(t) for t in _translations]
            except Exception as e:
                print("Transliteration Hook Error:", e)"""
                
                if target in content and "Hinglish Transliterator Hook" not in content:
                    content = content.replace(target, replacement)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"✅ Patched {path} successfully!")
                    return True
            except Exception as e:
                print(f"Error patching: {e}")
    return False

def force_format(src_path, dest_ext):
    """
    Ek image format ko dusre image format me force-convert karta hai (e.g. PNG to JPG)
    """
    current_ext = os.path.splitext(src_path)[1].lower()
    if current_ext == dest_ext:
        return src_path
    
    new_path = os.path.splitext(src_path)[0] + dest_ext
    try:
        from PIL import Image
        with Image.open(src_path) as img:
            if dest_ext in ['.jpg', '.jpeg']:
                img = img.convert('RGB')
            img.save(new_path)
        if os.path.exists(src_path) and src_path != new_path:
            os.remove(src_path)
        return new_path
    except Exception as e:
        print(f"Format conversion failed: {e}")
        return src_path

def make_progress_bar(current, total, length=10):
    percent = min(1.0, max(0.0, current / total if total > 0 else 0))
    filled = int(round(length * percent))
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {int(percent * 100)}%"

async def main():
    if not FILE_ID or not CHAT_ID or not MSG_ID:
        print("❌ CRITICAL ERROR: Required dispatch variables are empty.")
        return

    # Patch translator script
    patch_translator()

    # no_updates=True prevents conflict with Hugging Face Space
    bot = Client("MangaWorker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, no_updates=True)
    await bot.start()

    # --- CHAT RESOLUTION (PEER_ID_INVALID FIX) ---
    print("🔄 Resolving chat channels to prevent PeerIdInvalid...")
    try:
        await bot.get_chat(SAFE_CHANNEL_ID)
        await bot.get_chat(CHAT_ID)
        print("✅ Chats resolved successfully!")
    except Exception as e:
        print(f"⚠️ Resolution warning: {e}")

    async def update_status(text):
        try:
            print(f"[STATUS UPDATE]: {text}")
            await bot.edit_message_text(chat_id=CHAT_ID, message_id=MSG_ID, text=text)
        except Exception as e:
            print(f"❌ Failed to edit status message: {e}")

    await update_status("⏳ **Worker Initialized:** Downloading document...")
    
    try:
        print("Downloading media from safe channel...")
        download_path = await bot.download_media(FILE_ID)
        print(f"Downloaded media path: {download_path}")
    except Exception as e:
        await update_status(f"❌ **Download Error:** Failed to fetch from safe channel.\n`{e}`")
        await bot.stop()
        return

    original_ext = os.path.splitext(download_path)[1].lower()
    if not original_ext:
        original_ext = ".jpg"

    workspace = "manga_workspace"
    input_dir = os.path.join(workspace, "input")
    output_dir = os.path.join(workspace, "output")
    
    if os.path.exists(workspace):
        shutil.rmtree(workspace)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    pages = []
    
    await update_status(f"📦 **Analyzing:** Detecting format `{original_ext}`...")

    if original_ext in [".zip", ".cbz"]:
        try:
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(input_dir)
            for root, _, files in os.walk(input_dir):
                for f in files:
                    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                        pages.append(os.path.join(root, f))
            pages.sort()
        except Exception as e:
            await update_status(f"❌ **Unpack Error:** File is not a valid zip archive.\n`{e}`")
            await bot.stop()
            return
            
    elif original_ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(download_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                img_path = os.path.join(input_dir, f"page_{page_num:03d}.png")
                pix.save(img_path)
                pages.append(img_path)
            doc.close()
        except Exception as e:
            await update_status(f"❌ **PDF Conversion Error:** `{e}`")
            await bot.stop()
            return
    else:
        shutil.copy(download_path, input_dir)
        for f in os.listdir(input_dir):
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                pages.append(os.path.join(input_dir, f))

    total_pages = len(pages)
    if total_pages == 0:
        await update_status("❌ **Error:** No supported image assets found in document.")
        await bot.stop()
        return

    translator_to_use = "deepl" if DEEPL_KEY else "google"
    target_lang = "HIN" if LANG == "hienglish" else "ENG"

    style_flags = []
    if STYLE == "style2":
        style_flags = ["--manga2eng"]

    translated_files = []
    
    for idx, page_path in enumerate(pages):
        current_page = idx + 1
        pbar = make_progress_bar(idx, total_pages)
        await update_status(f"🔄 **Translating ({idx}/{total_pages}):** Processing page {current_page}...\n{pbar}\n⚡ *Active Fast Runner*")

        rel_path = os.path.relpath(page_path, input_dir)
        out_page_path = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(out_page_path), exist_ok=True)

        cli_cmd = [
            "python", "-m", "manga_translator",
            "--translator", translator_to_use,
            "-l", target_lang,
            "-i", page_path,
            "-o", out_page_path
        ] + style_flags

        try:
            print(f"Running translator for page {current_page}...")
            cwd_dir = "manga-image-translator" if os.path.exists("manga-image-translator") else None
            process = await asyncio.create_subprocess_exec(
                *cli_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_dir
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                print(f"Error on page {current_page}: {stderr.decode('utf-8', errors='ignore')}")
                shutil.copy(page_path, out_page_path)
        except Exception as e:
            print(f"Exception on page {current_page}: {e}")
            shutil.copy(page_path, out_page_path)

        translated_files.append(out_page_path)

    await update_status(f"🎨 **Compiling Output:** Building target document...")

    output_file_to_send = ""

    if original_ext in [".zip", ".cbz"]:
        out_archive = "translated_" + FNAME
        if not out_archive.lower().endswith(original_ext):
            out_archive = os.path.splitext(out_archive)[0] + original_ext
            
        output_file_to_send = out_archive
        with zipfile.ZipFile(out_archive, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(output_dir):
                for file in files:
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, output_dir)
                    zipf.write(full_p, rel_p)
                    
    elif original_ext == ".pdf":
        output_pdf = "translated_" + os.path.basename(download_path)
        if not output_pdf.lower().endswith(".pdf"):
            output_pdf += ".pdf"
            
        output_file_to_send = output_pdf
        from PIL import Image
        pil_images = []
        for f in sorted(translated_files):
            try:
                img = Image.open(f).convert('RGB')
                pil_images.append(img)
            except Exception as e:
                print(f"Error opening image {f}: {e}")
                
        if pil_images:
            pil_images[0].save(output_pdf, save_all=True, append_images=pil_images[1:])
        else:
            await update_status("❌ **PDF Generation Error:** No pages available.")
            await bot.stop()
            return
    else:
        if len(translated_files) > 0:
            output_file_to_send = force_format(translated_files[0], original_ext)
        else:
            output_file_to_send = download_path

    await update_status("📤 **Uploading:** Sending document to chat...")
    try:
        print("Sending document back via Pyrogram...")
        await bot.send_document(
            chat_id=CHAT_ID,
            document=output_file_to_send,
            caption=f"✅ **Translation Finished!**\n🌐 Language: `{LANG}`\n🎨 Style: `{STYLE}`"
        )
        print("Successfully sent document! Deleting status message...")
        await bot.delete_messages(chat_id=CHAT_ID, message_ids=MSG_ID)
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        await update_status(f"❌ **Upload Error:** Failed to dispatch output.\n`{e}`")

    try:
        shutil.rmtree(workspace)
        if os.path.exists(download_path):
            os.remove(download_path)
        if os.path.exists(output_file_to_send):
            os.remove(output_file_to_send)
    except Exception:
        pass

    await bot.stop()

if __name__ == "__main__":
    asyncio.run)
