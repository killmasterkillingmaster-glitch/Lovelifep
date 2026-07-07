hereimport os
import sys
import json
import zipfile
import shutil
import asyncio
import traceback
import requests
from pyrogram import Client

# Safe Channel / Peer ID Invalid Bypass
import pyrogram.utils
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

# GitHub se inputs load karein
INPUTS_JSON = os.getenv("INPUTS", "{}")
try:
    inputs = json.loads(INPUTS_JSON)
except Exception:
    inputs = {}

FILE_ID = inputs.get("file_id")
CHAT_ID = inputs.get("chat_id")
MSG_ID = inputs.get("msg_id")
USER_ID = inputs.get("user_id")
LANG = inputs.get("lang", "english")
PROMPT = inputs.get("prompt", "none")
STYLE = inputs.get("style", "style1")
FNAME = inputs.get("fname", "translated_manga.zip")

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEEPL_KEY = os.getenv("DEEPL_API_KEY", "").strip()

if DEEPL_KEY:
    os.environ["DEEPL_AUTH_KEY"] = DEEPL_KEY
if LANG == "hienglish":
    os.environ["TRANSLITERATE_TO_ROMAN_HINDI"] = "1"
else:
    os.environ["TRANSLITERATE_TO_ROMAN_HINDI"] = "0"

# Telegram HTTP API (Bypasses Pyrogram session crashes)
def edit_telegram_message(text):
    if not CHAT_ID or not MSG_ID: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    requests.post(url, json={"chat_id": CHAT_ID, "message_id": MSG_ID, "text": text, "parse_mode": "Markdown"})

def patch_translator():
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
        import os
        if os.getenv("TRANSLITERATE_TO_ROMAN_HINDI") == "1":
            try:
                from anyascii import anyascii
                _translations = [anyascii(t) for t in _translations]
            except Exception as e:
                pass"""
                if target in content and "anyascii" not in content:
                    content = content.replace(target, replacement)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    return True
            except Exception:
                pass
    return False

def make_progress_bar(current, total, length=10):
    percent = min(1.0, max(0.0, current / total if total > 0 else 0))
    filled = int(round(length * percent))
    return f"[{'█' * filled}{'░' * (length - filled)}] {int(percent * 100)}%"

async def main():
    if not FILE_ID or not CHAT_ID or not MSG_ID:
        print("Required dispatch variables missing.")
        return

    edit_telegram_message("⏳ **Worker Activated:** Booting up Python environment...")
    patch_translator()

    bot = Client("MangaWorker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, no_updates=True)
    await bot.start()

    try:
        edit_telegram_message("⏳ **Downloading:** Fetching file from Safe Channel...")
        download_path = await bot.download_media(FILE_ID)
        
        if not download_path or not os.path.exists(download_path):
            raise Exception("File format rejected or download path not found.")

        original_ext = os.path.splitext(download_path)[1].lower()
        if not original_ext:
            original_ext = ".jpg"

        workspace = "manga_workspace"
        input_dir = os.path.join(workspace, "input")
        output_dir = os.path.join(workspace, "output")
        
        if os.path.exists(workspace): shutil.rmtree(workspace)
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        pages = []
        edit_telegram_message(f"📦 **Analyzing:** Detected valid format `{original_ext}`...")

        if original_ext in [".zip", ".cbz"]:
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(input_dir)
            for root, _, files in os.walk(input_dir):
                for f in files:
                    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                        pages.append(os.path.join(root, f))
            pages.sort()
                
        elif original_ext == ".pdf":
            import fitz
            doc = fitz.open(download_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                img_path = os.path.join(input_dir, f"page_{page_num:03d}.png")
                pix.save(img_path)
                pages.append(img_path)
            doc.close()
        else:
            # Bug Fix: single file processing bypasses original filename errors
            safe_filename = "image_file" + original_ext
            safe_path = os.path.join(input_dir, safe_filename)
            shutil.copy(download_path, safe_path)
            pages.append(safe_path)

        total_pages = len(pages)
        if total_pages == 0:
            raise Exception("No supported image pages (jpg/png/webp) found inside the document.")

        translator_to_use = "deepl" if DEEPL_KEY else "google"
        target_lang = "HIN" if LANG == "hienglish" else "ENG"
        style_flags = ["--manga2eng"] if STYLE == "style2" else []

        translated_files = []
        
        for idx, page_path in enumerate(pages):
            current_page = idx + 1
            pbar = make_progress_bar(idx, total_pages)
            edit_telegram_message(f"🔄 **Translating ({idx}/{total_pages}):** Processing page {current_page}...\n{pbar}\n⚡ *Active Fast Runner*")

            rel_path = os.path.relpath(page_path, input_dir)
            out_page_path = os.path.join(output_dir, rel_path)
            os.makedirs(os.path.dirname(out_page_path), exist_ok=True)

            cli_cmd = [
                sys.executable, "-m", "manga_translator",
                "--translator", translator_to_use,
                "-l", target_lang,
                "-i", page_path,
                "-o", out_page_path
            ] + style_flags

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

            translated_files.append(out_page_path)

        edit_telegram_message("🎨 **Compiling Output:** Rebuilding your document...")

        output_file_to_send = ""
        if original_ext in [".zip", ".cbz"]:
            out_archive = "translated_" + (FNAME if FNAME.lower().endswith(original_ext) else "archive" + original_ext)
            output_file_to_send = out_archive
            with zipfile.ZipFile(out_archive, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(output_dir):
                    for file in files:
                        full_p = os.path.join(root, file)
                        rel_p = os.path.relpath(full_p, output_dir)
                        zipf.write(full_p, rel_p)
                        
        elif original_ext == ".pdf":
            output_pdf = "translated_" + os.path.basename(download_path)
            if not output_pdf.lower().endswith(".pdf"): output_pdf += ".pdf"
            output_file_to_send = output_pdf
            from PIL import Image
            pil_images = [Image.open(f).convert('RGB') for f in sorted(translated_files)]
            if pil_images:
                pil_images[0].save(output_pdf, save_all=True, append_images=pil_images[1:])
        else:
            output_file_to_send = translated_files[0] if translated_files else download_path

        edit_telegram_message("📤 **Uploading:** Sending file directly to you...")
        
        # Finally Upload Document
        await bot.send_document(
            chat_id=int(CHAT_ID),
            document=output_file_to_send,
            caption=f"✅ **Translation Finished!**\n🌐 Language: `{LANG}`\n🎨 Style: `{STYLE}`"
        )
        
        # Cleanup Status Message safely
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage", json={"chat_id": CHAT_ID, "message_id": MSG_ID})

    except Exception as e:
        # AGAR KOI ERROR AAYA TOH TELEGRAM PAR BHEJ DEGA CHUPCHAP BAND NAHI HOGA
        err_str = traceback.format_exc()
        edit_telegram_message(f"❌ **Worker Failed! Reason:**\n`{str(e)}`\n\n**Log:**\n```python\n{err_str[-800:]}\n```")

    finally:
        await bot.stop()
        try: shutil.rmtree("manga_workspace", ignore_errors=True)
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
