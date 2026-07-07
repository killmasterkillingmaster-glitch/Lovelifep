import os
import sys
import json
import zipfile
import shutil
import asyncio
from pyrogram import Client

# Safe Channel / Peer ID Invalid Bypass
import pyrogram.utils
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

# GitHub trigger payload load karein
INPUTS_JSON = os.getenv("INPUTS", "{}")
try:
    inputs = json.loads(INPUTS_JSON)
except Exception:
    inputs = {}

FILE_ID = inputs.get("file_id")
CHAT_ID = int(inputs.get("chat_id", "0"))
MSG_ID = int(inputs.get("msg_id", "0"))
USER_ID = int(inputs.get("user_id", "0"))
LANG = inputs.get("lang", "english")
PROMPT = inputs.get("prompt", "none")
STYLE = inputs.get("style", "style1")
FNAME = inputs.get("fname", "translated_manga.zip")

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEEPL_KEY = os.getenv("DEEPL_API_KEY", "").strip()

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
                    print(f"Patched {path} successfully!")
                    return True
            except Exception as e:
                print(f"Error patching: {e}")
    return False

def make_progress_bar(current, total, length=10):
    percent = min(1.0, max(0.0, current / total if total > 0 else 0))
    filled = int(round(length * percent))
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {int(percent * 100)}%"

async def main():
    if not FILE_ID or not CHAT_ID or not MSG_ID:
        print("Required dispatch variables missing.")
        return

    # Patch the translator script before loading modules
    patch_translator()

    bot = Client("MangaWorker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await bot.start()

    async def update_status(text):
        try:
            await bot.edit_message_text(chat_id=CHAT_ID, message_id=MSG_ID, text=text)
        except Exception as e:
            print(f"Status update skipped: {e}")

    await update_status("⏳ **Worker Initialized:** Downloading document...")
    
    try:
        download_path = await bot.download_media(FILE_ID)
    except Exception as e:
        await update_status(f"❌ **Download Error:** Failed to fetch from safe channel.\n`{e}`")
        await bot.stop()
        return

    workspace = "manga_workspace"
    input_dir = os.path.join(workspace, "input")
    output_dir = os.path.join(workspace, "output")
    
    if os.path.exists(workspace):
        shutil.rmtree(workspace)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    pages = []
    is_archive = False
    
    await update_status("📦 **Analyzing:** Preparing archive structure...")

    if download_path.lower().endswith((".zip", ".cbz")):
        is_archive = True
        try:
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(input_dir)
            for root, _, files in os.walk(input_dir):
                for f in files:
                    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                        pages.append(os.path.join(root, f))
            pages.sort()
        except Exception as e:
            await update_status(f"❌ **ZIP Extraction Error:** `{e}`")
            await bot.stop()
            return
            
    elif download_path.lower().endswith(".pdf"):
        is_archive = True
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
        # Single image processing
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
        except Exception:
            shutil.copy(page_path, out_page_path)

        translated_files.append(out_page_path)

    await update_status("🎨 **Rendering:** Creating clean translated copy...")

    output_file_to_send = ""
    if is_archive:
        out_zip = "translated_" + FNAME
        if not out_zip.lower().endswith(".zip"):
            out_zip += ".zip"
        output_file_to_send = out_zip
        with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(output_dir):
                for file in files:
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, output_dir)
                    zipf.write(full_p, rel_p)
    else:
        output_file_to_send = translated_files[0] if translated_files else download_path

    await update_status("📤 **Uploading:** Sending document to chat...")
    try:
        await bot.send_document(
            chat_id=CHAT_ID,
            document=output_file_to_send,
            caption=f"✅ **Translation Finished!**\n🌐 Language: `{LANG}`\n🎨 Style: `{STYLE}`"
        )
        await bot.delete_messages(chat_id=CHAT_ID, message_ids=MSG_ID)
    except Exception as e:
        await update_status(f"❌ **Upload Error:** Failed to dispatch output.\n`{e}`")

    # Final cleanup
    try:
        shutil.rmtree(workspace)
        if os.path.exists(download_path):
            os.remove(download_path)
        if is_archive and os.path.exists(output_file_to_send):
            os.remove(output_file_to_send)
    except Exception:
        pass

    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
