import os
import json
import asyncio
import time
import deepl
import pyrogram.utils
from pyrogram import Client
from pyrogram.types import Message

# Bypass
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

# Telegram Config
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# DeepL API Keys (Multiple Keys Support)
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
DEEPL_API_KEY2 = os.getenv("DEEPL_API_KEY2", "")
DEEPL_API_KEY3 = os.getenv("DEEPL_API_KEY3", "")
DEEPL_API_KEY4 = os.getenv("DEEPL_API_KEY4", "")

# List of all available API keys
DEEPL_KEYS = [
    key for key in [DEEPL_API_KEY, DEEPL_API_KEY2, DEEPL_API_KEY3, DEEPL_API_KEY4] 
    if key and key.strip() != ""
]

# Inputs from GitHub
inputs = json.loads(os.getenv("INPUTS", "{}"))
FILE_ID = inputs.get("file_id")
CHAT_ID = int(inputs.get("chat_id"))
MSG_ID = int(inputs.get("msg_id"))
USER_ID = int(inputs.get("user_id"))
LANG = inputs.get("lang")
FNAME = inputs.get("fname")
PROMPT = inputs.get("prompt", "none")
STYLE = inputs.get("style", "style1")

# Language mapping
LANG_MAP = {
    "english": "EN-US",
    "hindi": "HI",
    "hienglish": "EN-US"
}

app = Client("MangaWorker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

class MultiDeepLTranslator:
    """Multiple DeepL API keys with auto-failover"""
    
    def __init__(self, api_keys):
        self.api_keys = api_keys
        self.current_key_index = 0
        self.translators = []
        self.failed_keys = set()
        
        # Initialize all translators
        for key in self.api_keys:
            try:
                translator = deepl.Translator(key)
                self.translators.append(translator)
            except Exception as e:
                print(f"⚠️ Failed to initialize key: {e}")
                self.failed_keys.add(key)
    
    def get_next_key(self):
        """Get next working API key"""
        if not self.translators:
            return None
            
        # Try each translator
        for i in range(len(self.translators)):
            idx = (self.current_key_index + i) % len(self.translators)
            translator = self.translators[idx]
            key = self.api_keys[idx]
            
            if key not in self.failed_keys:
                self.current_key_index = idx
                return translator, key
        
        return None, None
    
    def mark_key_failed(self, key):
        """Mark a key as failed"""
        self.failed_keys.add(key)
        print(f"❌ Key failed: {key[:10]}...")
        
        # Remove failed translator
        idx = self.api_keys.index(key)
        if idx < len(self.translators):
            self.translators.pop(idx)
            self.api_keys.pop(idx)
    
    def translate_text(self, text: str, target_lang: str) -> tuple:
        """Translate text with auto-failover"""
        if not self.translators:
            raise Exception("❌ No working DeepL API keys available!")
        
        # Try translation with each key
        for attempt in range(len(self.translators)):
            translator, key = self.get_next_key()
            if not translator:
                break
                
            try:
                result = translator.translate_text(
                    text,
                    target_lang=LANG_MAP.get(target_lang, "EN-US")
                )
                return result.text, key  # Success!
                
            except Exception as e:
                error_msg = str(e).lower()
                print(f"⚠️ Translation error with key {key[:10]}...: {e}")
                
                # Check if it's a quota/limit error
                if any(x in error_msg for x in ["quota", "limit", "exceeded", "429", "too many"]):
                    self.mark_key_failed(key)
                    print(f"🔄 Switching to next key...")
                    continue
                else:
                    # Other error, try next key anyway
                    self.current_key_index = (self.current_key_index + 1) % len(self.translators)
                    continue
        
        # If all keys failed
        if not self.translators:
            raise Exception("❌ All API keys exhausted! Please add new keys.")
        
        raise Exception("❌ Translation failed with all keys.")

# Initialize translator with multiple keys
if DEEPL_KEYS:
    translator = MultiDeepLTranslator(DEEPL_KEYS)
    print(f"✅ Loaded {len(DEEPL_KEYS)} DeepL API keys")
else:
    translator = None
    print("⚠️ No DeepL API keys found!")

async def translate_with_keys(text, lang):
    """Helper function to translate with multiple keys"""
    if not translator:
        return f"⚠️ No API keys configured. Please add DEEPL_API_KEY in GitHub Secrets.", None
    
    try:
        translated, used_key = translator.translate_text(text, lang)
        key_preview = used_key[:10] + "..." if used_key else "unknown"
        print(f"✅ Translation successful using key: {key_preview}")
        return translated, used_key
    except Exception as e:
        return f"❌ Translation Error: {str(e)}", None

async def main():
    async with app:
        try:
            # Initial status
            await app.edit_message_text(CHAT_ID, MSG_ID, "🔄 **Worker Started! Processing...**")
            
            # Get file from safe channel
            print(f"📥 Getting file: {FILE_ID}")
            msg = await app.get_messages(-1003962165512, FILE_ID)  # SAFE_CHANNEL_ID
            
            if not msg:
                await app.edit_message_text(CHAT_ID, MSG_ID, "❌ **File not found in safe channel!**")
                return
            
            # Download file
            await app.edit_message_text(CHAT_ID, MSG_ID, f"📥 **Downloading:** `{FNAME}`")
            file_path = await app.download_media(msg)
            
            if not file_path:
                await app.edit_message_text(CHAT_ID, MSG_ID, "❌ **Failed to download file!**")
                return
            
            # Count available keys
            available_keys = len(DEEPL_KEYS)
            key_status = f"✅ {available_keys} API keys available" if available_keys > 0 else "⚠️ No API keys available!"
            
            # For demonstration - translate sample text
            sample_text = "Hello, this is a manga translation test."
            
            await app.edit_message_text(
                CHAT_ID, 
                MSG_ID, 
                f"🌐 **Translating...**\n\n📂 File: `{FNAME}`\n🌍 Target: `{LANG}`\n🔑 {key_status}"
            )
            
            # Translate using multiple keys
            translated_text, used_key = await translate_with_keys(sample_text, LANG)
            
            # Show which key was used
            key_preview = used_key[:10] + "..." if used_key else "N/A"
            
            # Send result
            result_msg = f"""✅ **Translation Complete!**

📂 **File:** `{FNAME}`
🌐 **Language:** `{LANG}`
🎨 **Style:** `{STYLE}`
🔑 **Used Key:** `{key_preview}`
📊 **Available Keys:** `{len(DEEPL_KEYS)}`

📝 **Translation Sample:**
