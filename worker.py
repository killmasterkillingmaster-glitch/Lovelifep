here# worker.py - Multi API Failover Edition
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

# Multi keys support (comma separated)
def get_keys(name):
    val = os.getenv(name, "").strip()
    if not val: return []
    return [k.strip() for k in val.split(",") if k.strip()]

DEEPL_KEYS = get_keys("DEEPL_API_KEY")
GROQ_KEYS = get_keys("GROQ_API_KEY")
GEMINI_KEYS = get_keys("GEMINI_API_KEY")
OPENAI_KEYS = get_keys("OPENAI_API_KEY")

print(f"=== WORKER START | LANG: {LANG} ===")
print(f"Keys loaded: DEEPL={len(DEEPL_KEYS)} GROQ={len(GROQ_KEYS)} GEMINI={len(GEMINI_KEYS)} OPENAI={len(OPENAI_KEYS)}")

def make_progress_bar(current, total, length=15):
    percent = min(1.0, max(0.0, current / total if total > 0 else 0))
    filled = int(round(length * percent))
    return f"[{'█' * filled}{'░' * (length - filled)}] {int(percent * 100)}%"

# Limit keywords to detect quota over
LIMIT_KEYWORDS = ["429", "rate limit", "quota", "limit exceeded", "resource exhausted", "too many requests", "payment required", "billing", "free quota"]

def is_limit_error(text):
    t = text.lower()
    return any(k in t for k in LIMIT_KEYWORDS)

async def run_translator_with_fallback(input_dir, output_dir, workspace):
    cwd_dir = "manga-image-translator" if os.path.exists("manga-image-translator") else None

    # Define priority for Hinglish and English
    if LANG == "hienglish":
        # For Hinglish, best is Groq -> Gemini -> OpenAI (DeepL Hinglish nahi karta)
        providers = []
        for k in GROQ_KEYS: providers.append(("groq", "GROQ_API_KEY", k))
        for k in GEMINI_KEYS: providers.append(("gemini", "GEMINI_API_KEY", k))
        for k in OPENAI_KEYS: providers.append(("openai", "OPENAI_API_KEY", k))
        for k in DEEPL_KEYS: providers.append(("deepl", "DEEPL_API_KEY", k)) # last fallback
    else:
        # For English, best is DeepL -> Groq -> Gemini -> OpenAI
        providers = []
        for k in DEEPL_KEYS: providers.append(("deepl", "DEEPL_API_KEY", k))
        for k in GROQ_KEYS: providers.append(("groq", "GROQ_API_KEY", k))
        for k in GEMINI_KEYS: providers.append(("gemini", "GEMINI_API_KEY", k))
        for k in OPENAI_KEYS: providers.append(("openai", "OPENAI_API_KEY", k))

    if not providers:
        # No keys at all, use offline
        providers = [("offline", "NONE", "none")]

    style_flags = ["--manga2eng"] if STYLE == "style2" else []

    last_error = ""

    for idx, (translator, env_name, api_key) in enumerate(providers):
        # Set env for this try
        if api_key!= "none":
            os.environ[env_name] = api_key
            print(f"Trying {translator} [{idx+1}/{len(providers)}] with key...{api_key[-6:]}")

        # Create Hinglish config if needed
        gpt_config_path = os.path.join(workspace, "gpt_config.yml")
        cli_cmd = ["python", "-m", "manga_translator", "-i", input_dir, "--dest", output_dir, "--translator", translator, "-l", "ENG"] + style_flags

        if LANG == "hienglish":
            if translator in ["groq", "gemini", "openai", "custom_openai"]:
                gpt_config_content = f"""
{translator}:
  temperature: 0.3
  prompt_template: "Translate to Hinglish: "
  chat_system_template: "You are a professional manga translator. You MUST translate everything to Hinglish - Hindi language written in English Roman letters ONLY. NEVER use Devanagari script. Examples: 'I am at home' -> 'Me ghar par hu', 'Where are you from?' -> 'Tum kaha se aaye ho', 'What is your work?' -> 'Tumhara kaam kya hai'. Keep it natural, short, like daily spoken Hindi in English letters. Output ONLY Hinglish."
"""
                # For groq, model config might be needed, use default
                with open(gpt_config_path, "w", encoding="utf-8") as f:
                    f.write(gpt_config_content)
                cli_cmd += ["--config-file", gpt_config_path]
            else:
                # DeepL can't do Hinglish, but we still try ENG if no other option
                cli_cmd = ["python", "-m", "manga_translator", "-i", input_dir, "--dest", output_dir, "--translator", translator, "-l", "ENG"] + style_flags

        # Clean output dir for fresh try
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        process = await asyncio.create_subprocess_exec(
            *cli_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=cwd_dir
        )
        stdout, _ = await process.communicate()
        log_text = stdout.decode('utf-8', errors='ignore')

        translated_count = 0
        if os.path.exists(output_dir):
            for root, _, files in os.walk(output_dir):
                translated_count += len([f for f in files if f.lower().endswith(('.png','.jpg','.jpeg','.webp'))])

        if process.returncode == 0 and translated_count > 0:
            print(f"SUCCESS with {translator}")
            return True, f"Translated with {translator} [{idx+1}/{len(providers)}]", log_text
        else:
            # Check if limit error
            if is_limit_error(log_text):
                print(f"LIMIT HIT on {translator}, switching...")
                last_error = f"Limit hit on {translator}"
                continue # try next provider
            else:
                # Other error, try next provider if many left, but save log
                print(f"FAILED on {translator}: {log_text[-500:]}")
                last_error = log_text[-1000:]
                if translator == "offline":
                    return False, last_error, log_text
                continue

    return False, last_error, "All providers exhausted"

async def main():
    if not FILE_ID or not CHAT_ID or not MSG_ID: return
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

    original_ext = os.path.splitext(FNAME)[1].lower()
    if not original_ext: original_ext = ".zip"
    workspace = os.path.abspath("manga_workspace")
    input_dir = os.path.join(workspace, "input")
    output_dir = os.path.join(workspace, "output")
    if os.path.exists(workspace): shutil.rmtree(workspace)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    pages = []
    await update_status(f"📦 **Analyzing:** `{original_ext}` format...")

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
        await update_status("❌ **Error:** No images found")
        return await bot.stop()

    await update_status(f"🔄 **AI Engine Started:** {total_pages} panels | {LANG}\n⚡ Trying Groq -> Gemini -> DeepL...")

    success, msg, full_log = await run_translator_with_fallback(input_dir, output_dir, workspace)

    if not success:
        # ALL LIMITS EXHAUSTED
        if is_limit_error(full_log) or "All providers" in msg or "Limit hit" in msg:
            await update_status("⚠️ **Sabki limit khatam ho gayi!**\n\n😔 Groq, Gemini, DeepL sab ki aaj ki limit khatam ho gayi hai.\n\n🕐 **Abhi nahi, kal aana!** Kal fir se try karna, limit reset ho jayegi.\n\n💡 Tip: Owner se bolo aur API keys add kare.")
        else:
            await update_status(f"❌ **Translation Failed!**\n`{msg[-800:]}`\n\nLog: `{full_log[-800:]}`")
        shutil.rmtree(workspace, ignore_errors=True)
        return await bot.stop()

    await update_status(f"🎨 **Structuring Output...** ({msg})")
    translated_files = []
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                translated_files.append(os.path.join(root, f))
    if len(translated_files) == 0:
        await update_status(f"❌ **Translation Failed!** No output.\n{msg}")
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

    file_size_mb = os.path.getsize(output_file_to_send) / (1024 * 1024)
    if file_size_mb > 49.5:
        await update_status(f"❌ **File too big {file_size_mb:.1f} MB**")
        return await bot.stop()

    caption = f"✅ **Done! [{msg}]**\n🌐 Lang: `{LANG}` | Style: `{STYLE}`"
    success_up = False
    try:
        await bot.send_document(chat_id=CHAT_ID, document=output_file_to_send, caption=caption)
        success_up = True
    except Exception as e: print(e)
    if CHAT_ID!= USER_ID:
        try: await bot.send_document(chat_id=USER_ID, document=output_file_to_send, caption=caption)
        except: pass
    if success_up:
        try: await bot.delete_messages(chat_id=CHAT_ID, message_ids=MSG_ID)
        except: pass

    shutil.rmtree(workspace, ignore_errors=True)
    if os.path.exists(download_path): os.remove(download_path)
    if os.path.exists(output_file_to_send) and original_ext in [".zip", ".cbz", ".pdf"]: os.remove(output_file_to_send)
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
