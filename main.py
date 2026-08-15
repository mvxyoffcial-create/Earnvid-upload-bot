import os
import time
import json
import html
import asyncio
import aiohttp
import aiofiles
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

# Configuration
API_ID = 36282056
API_HASH = "3a948acece533f362b4c90b2b3c14b60"
BOT_TOKEN = "8840164284:AAGNTcrtD0I_CXenRU_Ur0X8HqWxPlInN2A"
EARNVIDS_KEY = "46838mq8i750x5kmqaobb"
DOWNLOAD_DIR = "./downloads"
PORT = int(os.getenv("PORT", "8000"))

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Initialize Pyrogram with in_memory=True to eliminate auth byte errors on cloud deployments
app = Client(
    "earnvids_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

# --- Koyeb Health Check Web Server ---
async def health_check(request):
    return web.Response(text="Bot is running!", status=200)

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    server.router.add_get("/health", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Health check server running on port {PORT}")

# --- Helper & Processing Functions ---
def create_progress_bar(current: int, total: int) -> str:
    """Generates a visual progress bar string."""
    percentage = (current / total) * 100 if total > 0 else 0
    filled = int(percentage // 10)
    bar = "█" * filled + "░" * (10 - filled)
    
    mb_current = current / (1024 * 1024)
    mb_total = total / (1024 * 1024)
    
    return f"[{bar}] {percentage:.1f}%\n📊 {mb_current:.1f} MB / {mb_total:.1f} MB"

async def progress_callback(current: int, total: int, status_msg: Message, action_label: str, last_update: list):
    """Throttled progress update callback to prevent Telegram API rate-limiting."""
    now = time.time()
    if now - last_update[0] >= 2.0 or current == total:
        last_update[0] = now
        bar_text = create_progress_bar(current, total)
        try:
            await status_msg.edit_text(f"⚡ **{action_label}**\n\n{bar_text}")
        except Exception:
            pass

async def get_upload_server(api_key: str) -> str:
    url = f"https://earnvidsapi.com/api/upload/server?key={api_key}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            res = await response.json()
            if res.get("status") == 200:
                return res.get("result")
            raise Exception(f"Failed to fetch upload server: {res.get('msg', 'Unknown Error')}")

async def upload_file_stream(upload_url: str, file_path: str, api_key: str, status_msg: Message):
    file_name = os.path.basename(file_path)
    
    data = aiohttp.FormData()
    data.add_field('key', api_key)
    
    with open(file_path, 'rb') as f:
        data.add_field('file', f, filename=file_name)

        connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=7200) # 2-hour timeout for large files
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            try:
                await status_msg.edit_text("⚡ **Uploading to EarnVids...**\n\n⏳ Transferring file and processing...")
            except Exception:
                pass
                    
            async with session.post(upload_url, data=data) as response:
                raw_text = await response.text()
                try:
                    return json.loads(raw_text)
                except json.JSONDecodeError:
                    clean_res = raw_text[:150].replace("\n", " ")
                    raise Exception(f"Server returned non-JSON output (HTTP {response.status}): {clean_res}")

@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    await message.reply_text("👋 **Welcome to Ultra-Fast EarnVids Uploader!**\n\nSend any video or file up to 4GB to start.")

@app.on_message(filters.document | filters.video)
async def process_media(client, message: Message):
    status_msg = await message.reply_text("⏳ **Initializing download...**")
    file_path = None
    last_update = [time.time()]

    try:
        # 1. Download from Telegram
        file_path = await message.download(
            file_name=os.path.join(DOWNLOAD_DIR, ""),
            progress=progress_callback,
            progress_args=(status_msg, "Downloading from Telegram...", last_update)
        )
        
        # 2. Fetch EarnVids Upload Server
        await status_msg.edit_text("🔍 **Requesting EarnVids server...**")
        upload_server = await get_upload_server(EARNVIDS_KEY)
        
        # 3. Upload to EarnVids
        upload_res = await upload_file_stream(upload_server, file_path, EARNVIDS_KEY, status_msg)
        
        # Extract filecode safely from server response
        file_code = None
        if isinstance(upload_res, list) and len(upload_res) > 0:
            file_code = upload_res[0].get("filecode")
        elif isinstance(upload_res, dict):
            if upload_res.get("status") == 200 and "files" in upload_res and len(upload_res["files"]) > 0:
                file_code = upload_res["files"][0].get("filecode")
            elif "files" in upload_res and len(upload_res["files"]) > 0:
                file_code = upload_res["files"][0].get("filecode")
            elif "result" in upload_res and isinstance(upload_res["result"], dict):
                file_code = upload_res["result"].get("filecode")

        if file_code:
            embed_url = f"https://morencius.com/embed/{file_code}"
            iframe_code = f'<iframe src="{embed_url}" width="640" height="360" frameborder="0" allowfullscreen></iframe>'
            
            response_text = (
                f"✅ **Upload Complete!**\n\n"
                f"🔗 **Embed URL:**\n`{embed_url}`\n\n"
                f"💻 **HTML Embed Code:**\n`{iframe_code}`"
            )
            await status_msg.edit_text(response_text)
        else:
            msg = upload_res.get("msg", "Unknown error or missing filecode") if isinstance(upload_res, dict) else str(upload_res)
            await status_msg.edit_text(f"❌ **Upload failed:** {msg}")
            
    except Exception as e:
        safe_err = html.escape(str(e))
        await status_msg.edit_text(f"⚠️ <b>Error:</b> {safe_err}", parse_mode=ParseMode.HTML)
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

async def main():
    await start_web_server()
    await app.start()
    print("Bot and Web Server started successfully!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
