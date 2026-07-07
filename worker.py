import os
import json
import asyncio
import deepl
import pyrogram.utils
from pyrogram import Client

# Safe Channel / Peer ID Invalid Bypass
pyrogram.utils.get_peer_type = lambda p: "channel" if str(p).startswith("-100") else "chat" if str(p).startswith("-") else "user"

# ============ CONFIGURATION ============
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Multiple DeepL API Keys (Auto-Failover Support)
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
DEEPL_API_KEY2 = os.getenv("DEEPL_API_KEY2", "")
DEEPL_API_KEY3 = os.getenv("DEEPL_API_KEY3", "")
DEEPL_API_KEY4 = os.getenv("DEEPL_API_KEY4", "")

# List of all available keys (only non-empty ones)
DEEPL_KEYS = [
    key for key in [
        DEEPL_API_KEY, 
        DEEPL_API_KEY2, 
        DEEPL_API_KEY3, 
        DEEPL_API_KEY4
    ] 
    if key and key.strip() != ""
]

# GitHub Workflow Inputs
inputs = json.loads(os.getenv("INPUTS", "{}"))
FILE_ID = inputs.get("file_id")
CHAT_ID = int(inputs.get("chat_id"))
MSG_ID = int(inputs.get("msg_id"))
USER_ID = int(inputs.get("user_id"))
LANG = inputs.get("lang")
FNAME = inputs.get("fname")
PROMPT = inputs.get("prompt", "none")
STYLE = inputs.get("style", "style1")

# Language mapping for DeepL
LANG_MAP = {
    "english": "EN-US",
    "hindi": "HI",
    "hienglish": "EN-US",
    "japanese": "JA",
    "korean": "KO",
    "chinese": "ZH",
    "spanish": "ES",
    "french": "FR",
    "german": "DE",
    "italian": "IT",
    "portuguese": "PT",
    "russian": "RU",
    "arabic": "AR"
}

# Telegram Client
app = Client("MangaWorker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ============ MULTI-KEY TRANSLATOR CLASS ============
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
                print(f"✅ Key initialized: {key[:10]}...")
            except Exception as e:
                print(f"⚠️ Failed to initialize key {key[:10]}...: {e}")
                self.failed_keys.add(key)
    
    def get_next_key(self):
        """Get next working API key"""
        if not self.translators:
            return None, None
            
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
        """Mark a key as failed and remove it"""
        self.failed_keys.add(key)
        print(f"❌ Key marked as failed: {key[:10]}...")
        
        # Remove from lists
        if key in self.api_keys:
            idx = self.api_keys.index(key)
            if idx < len(self.translators):
                self.translators.pop(idx)
                self.api_keys.pop(idx)
    
    def translate_text(self, text, target_lang):
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
                print(f"✅ Translation successful using key: {key[:10]}...")
                return result.text, key
                
            except Exception as e:
                error_msg = str(e).lower()
                print(f"⚠️ Error with key {key[:10]}...: {e}")
                
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

# Initialize translator
if DEEPL_KEYS:
    translator = MultiDeepLTranslator(DEEPL_KEYS)
    print(f"\n✅ Loaded {len(DEEPL_KEYS)} DeepL API keys\n")
else:
    translator = None
    print("\n⚠️ WARNING: No DeepL API keys found!")
    print("Please add DEEPL_API_KEY in GitHub Secrets.\n")

# ============ MAIN WORKER FUNCTION ============
async def main():
    async with app:
        try:
            # Step 1: Worker Started
            await app.edit_message_text(
                CHAT_ID, 
                MSG_ID, 
                "🔄 **Worker Started!**\n\n⏳ Processing your file..."
            )
            
            # Step 2: Get file from safe channel
            print(f"📥 Getting file: {FILE_ID}")
            msg = await app.get_messages(-1003962165512, FILE_ID)  # SAFE_CHANNEL_ID
            
            if not msg:
                await app.edit_message_text(
                    CHAT_ID, 
                    MSG_ID, 
                    "❌ **File not found in safe channel!**"
                )
                return
            
            # Step 3: Download file
            await app.edit_message_text(
                CHAT_ID, 
                MSG_ID, 
                f"📥 **Downloading:** `{FNAME}`"
            )
            
            file_path = await app.download_media(msg)
            
            if not file_path:
                await app.edit_message_text(
                    CHAT_ID, 
                    MSG_ID, 
                    "❌ **Failed to download file!**"
                )
                return
            
            print(f"✅ File downloaded: {file_path}")
            
            # Step 4: Translate sample text (for testing)
            sample_text = "Hello, this is a manga translation test. The artwork is amazing!"
            
            # Count available keys
            available_keys = len(DEEPL_KEYS)
            key_status = f"✅ {available_keys} API keys available" if available_keys > 0 else "⚠️ No API keys available!"
            
            await app.edit_message_text(
                CHAT_ID, 
                MSG_ID, 
                f"🌐 **Translating...**\n\n"
                f"📂 File: `{FNAME}`\n"
                f"🌍 Target Language: `{LANG}`\n"
                f"🔑 {key_status}\n\n"
                f"⏳ This may take a few seconds..."
            )
            
            # Step 5: Perform translation
            if translator:
                translated_text, used_key = translator.translate_text(sample_text, LANG)
                key_preview = used_key[:10] + "..." if used_key else "N/A"
            else:
                translated_text = "⚠️ No API key configured! Please add DEEPL_API_KEY"
                key_preview = "N/A"
            
            # Step 6: Send result
            result_msg = f"""✅ **Translation Complete!**

📂 **File:** `{FNAME}`
🌐 **Target Language:** `{LANG}`
🎨 **Style:** `{STYLE}`
🔑 **Used API Key:** `{key_preview}`
📊 **Available Keys:** `{len(DEEPL_KEYS)}`

📝 **Sample Translation:**
```

{translated_text}

```

💡 _Full file translation will be available in the next update._"""

            await app.edit_message_text(CHAT_ID, MSG_ID, result_msg)
            
            # Step 7: Send the file back
            await app.send_document(
                CHAT_ID,
                file_path,
                caption=f"📤 **Processed File:** {FNAME}\n\n🌐 Language: `{LANG}`\n🔑 Used Key: `{key_preview}`"
            )
            
            # Step 8: Cleanup
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🧹 Cleaned up: {file_path}")
            
            print("✅ Worker completed successfully!")
            
        except Exception as e:
            error_msg = str(e)
            print(f"❌ Error: {error_msg}")
            
            await app.edit_message_text(
                CHAT_ID, 
                MSG_ID, 
                f"❌ **Error:**\n```\n{error_msg}\n```\n\n"
                f"💡 **Troubleshooting:**\n"
                f"• Check DeepL API key is valid\n"
                f"• Check API key has quota left\n"
                f"• Try again after some time"
            )

# ============ RUN WORKER ============
if __name__ == "__main__":
    if not DEEPL_KEYS:
        print("\n" + "="*50)
        print("⚠️  WARNING: No DeepL API keys configured!")
        print("Please add DEEPL_API_KEY in GitHub Secrets.")
        print("="*50 + "\n")
    
    asyncio.run(main())
