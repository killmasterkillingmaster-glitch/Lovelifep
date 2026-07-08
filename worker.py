# worker.py - FINAL FIX - Groq via gpt3.5 + No 50MB limit
import os, zipfile, shutil, asyncio
from pyrogram import Client
import pyrogram.utils
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

FILE_ID = os.getenv("FILE_ID","").strip()
CHAT_ID = int(os.getenv("CHAT_ID","0"))
MSG_ID = int(os.getenv("MSG_ID","0"))
USER_ID = int(os.getenv("USER_ID","0"))
LANG = os.getenv("LANG","english").lower()
STYLE = os.getenv("STYLE","style1")
FNAME = os.getenv("FNAME","translated.zip")

API_ID = int(os.getenv("API_ID","0"))
API_HASH = os.getenv("API_HASH","").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()

def get_keys(n):
    v=os.getenv(n,"").strip()
    return [k.strip() for k in v.split(",") if k.strip()] if v else []

DEEPL_KEYS=get_keys("DEEPL_API_KEY")
GROQ_KEYS=get_keys("GROQ_API_KEY")
GEMINI_KEYS=get_keys("GEMINI_API_KEY")
OPENAI_KEYS=get_keys("OPENAI_API_KEY")

print(f"START LANG:{LANG} FILE:{FNAME}")
print(f"KEYS DEEPL:{len(DEEPL_KEYS)} GROQ:{len(GROQ_KEYS)}")

async def run_translator(input_dir, output_dir, ws):
    cwd = "manga-image-translator" if os.path.exists("manga-image-translator") else None

    # FIX: purane version ke liye groq = gpt3.5 + base_url
    providers=[]
    if LANG=="hienglish":
        for k in GROQ_KEYS: providers.append(("gpt3.5", "groq", k))
        for k in OPENAI_KEYS: providers.append(("gpt4", "openai", k))
        for k in DEEPL_KEYS: providers.append(("deepl", "deepl", k))
    else:
        for k in DEEPL_KEYS: providers.append(("deepl", "deepl", k))
        for k in GROQ_KEYS: providers.append(("gpt3.5", "groq", k))

    if not providers: providers=[("offline","none","none")]

    style_flag=["--manga2eng"] if STYLE=="style2" else []

    for idx,(trans_type, provider_name, api_key) in enumerate(providers):
        print(f"[{idx+1}/{len(providers)}] Trying {trans_type} via {provider_name}...{api_key[-6:]}")

        if provider_name=="deepl":
            os.environ["DEEPL_AUTH_KEY"]=api_key
            os.environ["DEEPL_API_KEY"]=api_key
        if provider_name=="groq":
            os.environ["OPENAI_API_KEY"]=api_key
        if provider_name=="openai":
            os.environ["OPENAI_API_KEY"]=api_key

        # Config file
        config_path=os.path.join(ws,"gpt_config.yml")
        cfg_content=""
        if provider_name=="groq":
            cfg_content=f"""gpt:
  api_key: "{api_key}"
  model: "llama-3.1-70b-versatile"
  base_url: "https://api.groq.com/openai/v1"
  temperature: 0.3
  prompt_template: "Translate to Hinglish: {{text}}"
  chat_system_template: "You are manga translator. MUST output Hinglish - Hindi in Roman English ONLY. Never Devanagari. Ex: 'Where are you?' -> 'Tum kaha ho?'. Output ONLY translation."
"""
            with open(config_path,"w",encoding="utf-8") as f: f.write(cfg_content)

        cmd=["python","-m","manga_translator","-i",input_dir,"--dest",output_dir,"--translator",trans_type,"-l","ENG"]+style_flag
        if provider_name=="groq":
            cmd+=["--config-file",config_path]

        if os.path.exists(output_dir): shutil.rmtree(output_dir)
        os.makedirs(output_dir,exist_ok=True)

        proc=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,cwd=cwd)
        out,_=await proc.communicate()
        log=out.decode('utf-8',errors='ignore')

        cnt=0
        if os.path.exists(output_dir):
            for r,_,fs in os.walk(output_dir):
                cnt+=len([f for f in fs if f.lower().endswith(('.png','.jpg','.jpeg','.webp'))])

        print(f"RC={proc.returncode} Images={cnt}")
        if proc.returncode==0 and cnt>0:
            return True, f"{provider_name}-{trans_type}", log
        else:
            print(log[-1000:])
            continue

    return False,"All failed","All providers failed"

async def main():
    bot=Client("Worker",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN,no_updates=True)
    await bot.start()
    async def edit(t):
        try: await bot.edit_message_text(CHAT_ID,MSG_ID,t)
        except: pass

    await edit(f"⏳ **Downloading {FNAME}...**")

    # Download retry
    dl_path=None
    for i in range(1,6):
        try:
            dl_path=await bot.download_media(FILE_ID)
            if dl_path and os.path.exists(dl_path) and os.path.getsize(dl_path)>1024:
                break
            await asyncio.sleep(2)
        except Exception as e:
            print(f"DL {i} fail {e}")
            await edit(f"⚠️ Retry {i}/5...")
            await asyncio.sleep(5)

    if not dl_path or not os.path.exists(dl_path):
        await edit("❌ Download failed 5 retries")
        return await bot.stop()

    ext=os.path.splitext(FNAME)[1].lower() or ".zip"
    ws=os.path.abspath("manga_workspace")
    inp=os.path.join(ws,"input")
    out=os.path.join(ws,"output")
    if os.path.exists(ws): shutil.rmtree(ws)
    os.makedirs(inp,exist_ok=True); os.makedirs(out,exist_ok=True)

    try:
        if ext in [".zip",".cbz"]:
            with zipfile.ZipFile(dl_path,'r') as z: z.extractall(inp)
        else:
            shutil.copy(dl_path,inp)
    except zipfile.BadZipFile:
        sz=os.path.getsize(dl_path)//1024
        await edit(f"❌ **BadZip {sz}KB corrupt** - File Telegram se adhuri download hui. Chota zip bhejo ya dubara forward karo.")
        return await bot.stop()

    pages=[os.path.join(r,f) for r,_,fs in os.walk(inp) for f in fs if f.lower().endswith(('.png','.jpg','.jpeg','.webp','.bmp'))]
    if not pages:
        await edit("❌ No images in zip")
        return await bot.stop()

    await edit(f"🔄 **Translating {len(pages)} panels with {LANG}**...")

    ok, prov_msg, _ = await run_translator(inp,out,ws)
    if not ok:
        await edit(f"❌ **Translation Failed** - All keys limit / invalid\nTried: GROQ={len(GROQ_KEYS)} DEEPL={len(DEEPL_KEYS)}")
        return await bot.stop()

    await edit(f"🎨 **Done {prov_msg} - Uploading...**")

    files=sorted([os.path.join(r,f) for r,_,fs in os.walk(out) for f in fs if f.lower().endswith(('.png','.jpg','.jpeg','.webp'))])
    final_file="translated_"+FNAME
    with zipfile.ZipFile(final_file,'w',zipfile.ZIP_DEFLATED) as z:
        for f in files: z.write(f, os.path.relpath(f,out))

    # NO 50MB LIMIT - Pyrogram can send 2GB
    size_mb=os.path.getsize(final_file)/1024/1024
    print(f"Final size {size_mb:.2f} MB")

    try:
        await bot.send_document(CHAT_ID, final_file, caption=f"✅ **Done! [{prov_msg}]** 🌐 {LANG}")
        if CHAT_ID!=USER_ID:
            try: await bot.send_document(USER_ID, final_file, caption=f"✅ **Done! [{prov_msg}]**")
            except: pass
        try: await bot.delete_messages(CHAT_ID,MSG_ID)
        except: pass
    except Exception as e:
        await edit(f"❌ Upload fail: {e}")

    shutil.rmtree(ws,ignore_errors=True)
    try: os.remove(dl_path)
    except: pass
    try: os.remove(final_file)
    except: pass
    await bot.stop()

if __name__=="__main__":
    asyncio.run(main())
