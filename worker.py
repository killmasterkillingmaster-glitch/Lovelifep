# worker.py - Final Fixed with Download Retry + Multi API Failover
import os
import sys
import zipfile
import shutil
import asyncio
import traceback
from pyrogram import Client
import pyrogram.utils

pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

FILE_ID = os.getenv("FILE_ID", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
MSG_ID = int(os.getenv("MSG_ID", "0"))
USER_ID = int(os.getenv("USER_ID", "0"))
LANG = os.getenv("LANG", "english").strip().lower()
PROMPT = os.getenv("PROMPT", "none").strip()
STYLE = os.getenv("STYLE", "style1").strip()
FNAME = os.getenv("FNAME", "translated_manga.zip").strip()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

def get_keys(name):
    val = os.getenv(name, "").strip()
    if not val: return []
    return [k.strip() for k in val.split(",") if k.strip()]

DEEPL_KEYS = get_keys("DEEPL_API_KEY")
GROQ_KEYS = get_keys("GROQ_API_KEY")
GEMINI_KEYS = get_keys("GEMINI_API_KEY")
OPENAI_KEYS = get_keys("OPENAI_API_KEY")

print(f"=== WORKER START | LANG: {LANG} | FILE: {FNAME} ===")
print(f"Keys: DEEPL={len(DEEPL_KEYS)} GROQ={len(GROQ_KEYS)} GEMINI={len(GEMINI_KEYS)} OPENAI={len(OPENAI_KEYS)}")

LIMIT_KEYWORDS = ["429", "rate limit", "quota", "limit exceeded", "resource exhausted", "too many requests", "payment required", "billing", "free quota"]
def is_limit_error(text):
    t = text.lower()
    return any(k in t for k in LIMIT_KEYWORDS)

def make_progress_bar(current, total, length=15):
    percent = min(1.0, max(0.0, current / total if total > 0 else 0))
    filled = int(round(length * percent))
    return f"[{'█' * filled}{'░' * (length - filled)}] {int(percent * 100)}%"

async def run_translator_with_fallback(input_dir, output_dir, workspace):
    cwd_dir = "manga-image-translator" if os.path.exists("manga-image-translator") else None
    if LANG == "hienglish":
        providers = []
        for k in GROQ_KEYS: providers.append(("groq", "GROQ_API_KEY", k))
        for k in GEMINI_KEYS: providers.append(("gemini", "GEMINI_API_KEY", k))
        for k in OPENAI_KEYS: providers.append(("openai", "OPENAI_API_KEY", k))
        for k in DEEPL_KEYS: providers.append(("deepl", "DEEPL_API_KEY", k))
    else:
        providers = []
        for k in DEEPL_KEYS: providers.append(("deepl", "DEEPL_API_KEY", k))
        for k in GROQ_KEYS: providers.append(("groq", "GROQ_API_KEY", k))
        for k in GEMINI_KEYS: providers.append(("gemini", "GEMINI_API_KEY", k))
        for k in OPENAI_KEYS: providers.append(("openai", "OPENAI_API_KEY", k))

    if not providers:
        providers = [("offline", "NONE", "none")]

    style_flags = ["--manga2eng"] if STYLE == "style2" else []
    last_error = ""

    for idx, (translator, env_name, api_key) in enumerate(providers):
        if api_key!= "none":
            os.environ[env_name] = api_key
            print(f"Trying {translator} [{idx+1}/{len(providers)}]")

        gpt_config_path = os.path.join(workspace, "gpt_config.yml")
        cli_cmd = ["python", "-m", "manga_translator", "-i", input_dir, "--dest", output_dir, "--translator", translator, "-l", "ENG"] + style_flags

        if LANG == "hienglish" and translator in ["groq", "gemini", "openai"]:
            gpt_config_content = f"""{translator}:
  temperature: 0.3
  prompt_template: "Translate to Hinglish: "
  chat_system_template: "You are a professional manga translator. You MUST translate everything to Hinglish - Hindi language written in English Roman letters ONLY. NEVER use Devanagari script. Examples: 'I am at home' -> 'Me ghar par hu', 'Where are you from?' -> 'Tum kaha se aaye ho'. Output ONLY Hinglish."
"""
            with open(gpt_config_path, "w", encoding="utf-8") as f:
                f.write(gpt_config_content)
            cli_cmd += ["--config-file", gpt_config_path]

        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        process = await asyncio.create_subprocess_exec(*cli_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=cwd_dir)
        stdout, _ = await process.communicate()
        log_text = stdout.decode('utf-8', errors='ignore')

        translated_count = 0
        if os.path.exists(output_dir):
            for root, _, files in os.walk(output_dir):
                translated_count += len([f for f in files if f.lower().endswith(('.png','.jpg','.jpeg','.webp'))])

        if process.returncode == 0 and translated_count > 0:
            return True, f"Translated with {translator}", log_text
        else:
            if is_limit_error(log_text):
                print(f"LIMIT HIT on {translator}")
                last_error = f"Limit hit on {translator}"
                continue
            else:
                print(f"FAILED {translator}: {log_text[-500:]}")
                last_error = log_text[-1000:]
                continue

    return False, last_error, "All providers exhausted"

async def main():
    if not FILE_ID or not CHAT_ID or not MSG_ID:
        print("Missing envs")
        return

    bot = Client("MangaWorker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, no_updates=True)
    await bot.start()

    async def update_status(text):
        try:
            await bot.edit_message_text(chat_id=CHAT_ID, message_id=MSG_ID, text=text)
        except:
            pass

    await update_status(f"⏳ **Worker Started:** Downloading `{FNAME}`...")

    # ========== ROBUST DOWNLOAD WITH 5 RETRIES ==========
    download_path = None
    for attempt in range(1, 6):
        try:
            if attempt > 1:
                await update_status(f"⏳ **Retrying Download {attempt}/5...**")
            download_path = await bot.download_media(FILE_ID)
            if download_path and os.path.exists(download_path):
                size = os.path.getsize(download_path)
                if size > 1024: # At least 1KB
                    print(f"Download OK: {size} bytes")
                    break
                else:
                    print(f"File too small {size}, retrying")
                    os.remove(download_path)
            await asyncio.sleep(3)
        except Exception as e:
            print(f"Download attempt {attempt} failed: {e}")
            await asyncio.sleep(5 + attempt*2)

    if not download_path or not os.path.exists(download_path):
        await update_status("❌ **Download Failed:** Telegram se file download nahi hui after 5 retries.\n\n🔁 File ko dobara forward karke try karo ya zip ko chote parts me bhejo.")
        return await bot.stop()

    original_ext = os.path.splitext(FNAME)[1].lower()
    if not original_ext: original_ext = ".zip"

    workspace = os.path.abspath("manga_workspace")
    input_dir = os.path.join(workspace, "input")
    output_dir = os.path.join(workspace, "output")
    if os.path.exists(workspace): shutil.rmtree(workspace)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # ========== SAFE EXTRACT WITH BADZIP HANDLING ==========
    try:
        await update_status(f"📦 **Analyzing:** {original_ext} format...")
        if original_ext in [".zip", ".cbz"]:
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(input_dir)
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
    except zipfile.BadZipFile:
        size = os.path.getsize(download_path) if os.path.exists(download_path) else 0
        await update_status(f"❌ **Corrupt Zip File!**\nSize: {size/1024/1024:.2f} MB\nFile download corrupt hui hai. Telegram timeout ki wajah se.\n\n🔁 Please dubara upload karo, chote zip me.")
        shutil.rmtree(workspace, ignore_errors=True)
        if os.path.exists(download_path): os.remove(download_path)
        return await bot.stop()
    except Exception as e:
        await update_status(f"❌ **Extract Error:** `{e}`")
        return await bot.stop()

    pages = []
    for root, _, files in os.walk(input_dir):
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                pages.append(os.path.join(root, f))

    if len(pages) == 0:
        await update_status("❌ **Error:** No images found inside zip")
        return await bot.stop()

    await update_status(f"🔄 **AI Engine:** {len(pages)} panels | {LANG} | Trying Groq -> Gemini -> DeepL...")

    success, msg, full_log = await run_translator_with_fallback(input_dir, output_dir, workspace)

    if not success:
        if is_limit_error(full_log) or "All providers" in msg or "Limit hit" in msg:
            await update_status("⚠️ **Sabki limit khatam ho gayi!**\n\nGroq, Gemini, DeepL sab ki limit khatam.\n\n🕐 **Abhi nahi, kal aana!** Kal limit reset hogi.")
        else:
            await update_status(f"❌ **Translation Failed!**\n`{msg[-800:]}`")
        shutil.rmtree(workspace, ignore_errors=True)
        return await bot.stop()

    await update_status(f"🎨 **Structuring Output...** ({msg})")

    translated_files = []
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                translated_files.append(os.path.join(root, f))

    if len(translated_files) == 0:
        await update_status(f"❌ **No output generated.**\n{msg}")
        return await bot.stop()

    translated_files.sort()
    output_file_to_send = ""
    if original_ext in [".zip", ".cbz"]:
        output_file_to_send = "translated_" + FNAME
        with zipfile.ZipFile(output_file_to_send, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for f in translated_files:
                zipf.write(f, os.path.relpath(f, output_dir))
    elif original_ext == ".pdf":
        output_file_to_send = "translated_" + FNAME
        from PIL import Image
        pil_images = [Image.open(f).convert('RGB') for f in translated_files]
        if pil_images: pil_images[0].save(output_file_to_send, save_all=True, append_images=pil_images[1:])
    else:
        output_file_to_send = translated_files[0]

    file_size_mb = os.path.getsize(output_file_to_send) / (1024*1024)
    if file_size_mb > 49.5:
        await update_status(f"❌ **File too big {file_size_mb:.1f} MB** > 50MB limit")
        return await bot.stop()

    caption = f"✅ **Done! [{msg}]**\n🌐 Lang: `{LANG}` | Style: `{STYLE}`"
    success_up = False
    try:
        await bot.send_document(chat_id=CHAT_ID, document=output_file_to_send, caption=caption)
        success_up = True
    except Exception as e:
        print(f"Upload failed: {e}")

    if CHAT_ID!= USER_ID:
        try:
            await bot.send_document(chat_id=USER_ID, document=output_file_to_send, caption=caption)
        except:
            pass

    if success_up:
        try: await bot.delete_messages(chat_id=CHAT_ID, message_ids=MSG_ID)
        except: pass

    shutil.rmtree(workspace, ignore_errors=True)
    if os.path.exists(download_path): os.remove(download_path)
    if os.path.exists(output_file_to_send) and original_ext in [".zip", ".cbz", ".pdf"]:
        try: os.remove(output_file_to_send)
        except: pass

    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
