# worker.py - Final Complete Fixed Version
import os
import sys
import zipfile
import shutil
import asyncio
from pyrogram import Client
import pyrogram.utils

pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

FILE_ID = os.getenv("FILE_ID", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
MSG_ID = int(os.getenv("MSG_ID", "0"))
USER_ID = int(os.getenv("USER_ID", "0"))
LANG = os.getenv("LANG", "english").strip().lower()
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

print(f"=== START LANG:{LANG} FILE:{FNAME} ===")
print(f"KEYS: DEEPL={len(DEEPL_KEYS)} GROQ={len(GROQ_KEYS)} GEMINI={len(GEMINI_KEYS)} OPENAI={len(OPENAI_KEYS)}")

LIMIT_KEYWORDS = ["429", "rate limit", "quota", "limit exceeded", "resource exhausted", "too many requests", "payment required", "billing", "free quota", "missingapikey", "deepl_auth_key"]
def is_limit_error(text):
    t = text.lower()
    return any(k in t for k in LIMIT_KEYWORDS)

async def run_translator_with_fallback(input_dir, output_dir, workspace):
    cwd_dir = "manga-image-translator" if os.path.exists("manga-image-translator") else None

    # Hinglish ke liye pehle Groq/Gemini, English ke liye pehle DeepL
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

    for idx, (translator, env_name, api_key) in enumerate(providers):
        if api_key!= "none":
            os.environ[env_name] = api_key
            # IMPORTANT FIX - manga-translator library alag naam expect karti hai
            if translator == "deepl":
                os.environ["DEEPL_AUTH_KEY"] = api_key
                os.environ["DEEPL_API_KEY"] = api_key
            if translator == "groq":
                os.environ["GROQ_API_KEY"] = api_key
            if translator == "gemini":
                os.environ["GEMINI_API_KEY"] = api_key
                os.environ["GOOGLE_API_KEY"] = api_key
            if translator == "openai":
                os.environ["OPENAI_API_KEY"] = api_key

            print(f"[{idx+1}/{len(providers)}] Trying {translator} with key...{api_key[-6:]}")

        gpt_config_path = os.path.join(workspace, "gpt_config.yml")
        cli_cmd = ["python", "-m", "manga_translator", "-i", input_dir, "--dest", output_dir, "--translator", translator, "-l", "ENG"] + style_flags

        if LANG == "hienglish" and translator in ["groq", "gemini", "openai"]:
            cfg = f"""{translator}:
  temperature: 0.3
  prompt_template: "Translate to Hinglish: "
  chat_system_template: "You are a professional manga translator. MUST translate to Hinglish - Hindi in Roman English ONLY. NEVER use Devanagari. Example: 'I am at home' -> 'Me ghar par hu'. Output ONLY Hinglish."
"""
            with open(gpt_config_path, "w", encoding="utf-8") as f:
                f.write(cfg)
            cli_cmd += ["--config-file", gpt_config_path]

        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(*cli_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=cwd_dir)
        out, _ = await proc.communicate()
        log = out.decode('utf-8', errors='ignore')

        cnt = 0
        if os.path.exists(output_dir):
            for r,_,fs in os.walk(output_dir):
                cnt += len([f for f in fs if f.lower().endswith(('.png','.jpg','.jpeg','.webp'))])

        print(f"Return code {proc.returncode}, images: {cnt}")

        if proc.returncode == 0 and cnt > 0:
            return True, f"{translator}", log
        else:
            if is_limit_error(log):
                print(f"LIMIT/MISSING KEY on {translator}, shifting...")
                continue
            else:
                print(f"FAILED {translator}: {log[-800:]}")
                # Missing key ko limit jaisa treat karke next pe jao
                if "MissingAPIKeyException" in log or "Please set the DEEPL" in log:
                    print("Key name mismatch, will try next provider with fixed env")
                    continue
                # Agar koi aur error hai to bhi next try karo, last tak
                continue

    return False, "All providers failed", "All keys exhausted"

async def main():
    if not FILE_ID: return
    bot = Client("Worker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, no_updates=True)
    await bot.start()
    async def edit(t):
        try: await bot.edit_message_text(CHAT_ID, MSG_ID, t)
        except: pass

    await edit(f"⏳ **Downloading {FNAME}...**")

    # Download with retry
    dl_path = None
    for i in range(1,6):
        try:
            dl_path = await bot.download_media(FILE_ID)
            if dl_path and os.path.exists(dl_path) and os.path.getsize(dl_path) > 1024:
                break
            await asyncio.sleep(3)
        except Exception as e:
            print(f"DL retry {i}: {e}")
            await edit(f"⚠️ **Retry {i}/5** downloading...")
            await asyncio.sleep(5)

    if not dl_path or not os.path.exists(dl_path):
        await edit("❌ **Download failed after 5 retries** - Telegram timeout. Chota zip bhejo ya dubara try karo.")
        return await bot.stop()

    ext = os.path.splitext(FNAME)[1].lower() or ".zip"
    ws = os.path.abspath("manga_workspace")
    inp = os.path.join(ws,"input")
    out = os.path.join(ws,"output")
    if os.path.exists(ws): shutil.rmtree(ws)
    os.makedirs(inp, exist_ok=True); os.makedirs(out, exist_ok=True)

    try:
        if ext in [".zip",".cbz"]:
            with zipfile.ZipFile(dl_path,'r') as z: z.extractall(inp)
        elif ext == ".pdf":
            import fitz
            doc = fitz.open(dl_path)
            for n in range(len(doc)):
                pg = doc.load_page(n)
                pg.get_pixmap(dpi=150).save(os.path.join(inp, f"page_{n:03d}.png"))
            doc.close()
        else:
            shutil.copy(dl_path, inp)
    except zipfile.BadZipFile:
        await edit(f"❌ **BadZipFile** - Download corrupt ({os.path.getsize(dl_path)//1024} KB). Dubara bhejo.")
        return await bot.stop()

    pages = [os.path.join(r,f) for r,_,fs in os.walk(inp) for f in fs if f.lower().endswith(('.png','.jpg','.jpeg','.webp','.bmp'))]
    if not pages:
        await edit("❌ No images inside zip")
        return await bot.stop()

    await edit(f"🔄 **AI Translating** {len(pages)} panels | {LANG} | Groq→Gemini→DeepL")

    ok, provider_msg, full_log = await run_translator_with_fallback(inp, out, ws)

    if not ok:
        if "exhausted" in full_log.lower() or "limit" in full_log.lower():
            await edit("⚠️ **Sabki limit khatam ho gayi!**\n\n😔 Groq, Gemini, DeepL sab ki limit khatam ho gayi.\n\n🕐 **Abhi nahi, kal aana!** Kal reset hogi.")
        else:
            await edit(f"❌ **Translation Failed!**\n```{full_log[-1000:]}```")
        return await bot.stop()

    await edit(f"🎨 **Done with {provider_msg}** - Uploading...")

    files = sorted([os.path.join(r,f) for r,_,fs in os.walk(out) for f in fs if f.lower().endswith(('.png','.jpg','.jpeg','.webp'))])
    if not files:
        await edit("❌ No output generated")
        return await bot.stop()

    final_file = ""
    if ext in [".zip",".cbz"]:
        final_file = "translated_"+FNAME
        with zipfile.ZipFile(final_file,'w',zipfile.ZIP_DEFLATED) as z:
            for f in files: z.write(f, os.path.relpath(f,out))
    elif ext == ".pdf":
        final_file = "translated_"+FNAME
        from PIL import Image
        imgs = [Image.open(f).convert('RGB') for f in files]
        if imgs: imgs[0].save(final_file, save_all=True, append_images=imgs[1:])
    else:
        final_file = files[0]

        # Telegram Bot can send up to 2GB via Pyrogram (MTProto) - No 50MB limit
    file_size_mb = os.path.getsize(output_file_to_send) / (1024*1024)
    print(f"Final file size: {file_size_mb:.2f} MB")
    if file_size_mb > 1900:
        await update_status(f"❌ **File too big {file_size_mb:.1f} MB** > 2GB Telegram limit")
        return await bot.stop()

    caption = f"✅ **Done! [{provider_msg}]** 🌐 {LANG} | {STYLE}"
    try:
        await bot.send_document(CHAT_ID, final_file, caption=caption)
        try: await bot.delete_messages(CHAT_ID, MSG_ID)
        except: pass
    except Exception as e:
        await edit(f"❌ Upload failed: {e}")

    if CHAT_ID!= USER_ID:
        try: await bot.send_document(USER_ID, final_file, caption=caption)
        except: pass

    shutil.rmtree(ws, ignore_errors=True)
    try: os.remove(dl_path)
    except: pass
    try:
        if ext in [".zip",".cbz",".pdf"]: os.remove(final_file)
    except: pass
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
