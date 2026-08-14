import os
import time
import json
import asyncio
import aiohttp
import aiofiles
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message

# Configuration - load from environment, do NOT hardcode secrets
API_ID = 36282056
API_HASH = "3a948acece533f362b4c90b2b3c14b60"
BOT_TOKEN = "8840164284:AAGNTcrtD0I_CXenRU_Ur0X8HqWxPlInN2A"
EARNVIDS_KEY = "46838mq8i750x5kmqaobb"
DOWNLOAD_DIR = "./downloads"
HEALTH_PORT = "8000"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

if not (API_ID and API_HASH and BOT_TOKEN and EARNVIDS_KEY):
    raise RuntimeError(
        "Missing required environment variables: API_ID, API_HASH, BOT_TOKEN, EARNVIDS_KEY"
    )

app = Client("earnvids_uploader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


def create_progress_bar(current: int, total: int) -> str:
    """Generates a visual progress bar string."""
    percentage = (current / total) * 100 if total > 0 else 0
    filled = int(percentage // 10)
    bar = "█" * filled + "░" * (10 - filled)

    mb_current = current / (1024 * 1024)
    mb_total = total / (1024 * 1024)

    return f"[{bar}] {percentage:.1f}%\n📊 {mb_current:.1f} MB / {mb_total:.1f} MB"


async def safe_edit(status_msg: Message, text: str, parse_mode=None):
    """Edit a status message without letting a secondary error crash the handler."""
    try:
        await status_msg.edit_text(text, parse_mode=parse_mode)
    except Exception as edit_err:
        print(f"[safe_edit] failed to edit message: {edit_err}")


async def progress_callback(current: int, total: int, status_msg: Message, action_label: str, last_update: list):
    """Throttled progress update callback to prevent Telegram API rate-limiting."""
    now = time.time()
    # Update UI only once every 2 seconds
    if now - last_update[0] >= 2.0 or current == total:
        last_update[0] = now
        bar_text = create_progress_bar(current, total)
        await safe_edit(status_msg, f"⚡ **{action_label}**\n\n{bar_text}")


async def get_upload_server(api_key: str) -> str:
    url = f"https://earnvidsapi.com/api/upload/server?key={api_key}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            raw_text = await response.text()
            try:
                res = json.loads(raw_text)
            except json.JSONDecodeError:
                raise Exception(f"Upload-server endpoint returned non-JSON: {raw_text[:200]}")
            if res.get("status") == 200:
                return res.get("result")
            raise Exception(f"Failed to fetch upload server: {res.get('msg')}")


async def upload_file_stream(upload_url: str, file_path: str, api_key: str, status_msg: Message):
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    bytes_uploaded = 0
    last_update = [time.time()]

    async def file_sender():
        nonlocal bytes_uploaded
        # 32MB buffer chunk size for high-throughput upload
        chunk_size = 32 * 1024 * 1024
        async with aiofiles.open(file_path, 'rb') as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                bytes_uploaded += len(chunk)
                await progress_callback(bytes_uploaded, file_size, status_msg, "Uploading to EarnVids...", last_update)
                yield chunk

    data = aiohttp.FormData()
    data.add_field('key', api_key)
    data.add_field('html_redirect', '0')
    data.add_field('file', file_sender(), filename=file_name)

    # High-performance client session settings
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(upload_url, data=data) as response:
            raw_text = await response.text()
            try:
                return json.loads(raw_text)
            except json.JSONDecodeError:
                raise Exception(f"Server returned non-JSON output: {raw_text[:200]}")


@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    await message.reply_text("👋 **Welcome to Ultra-Fast EarnVids Uploader!**\n\nSend any video or file up to 4GB to start.")


@app.on_message(filters.document | filters.video)
async def process_media(client, message: Message):
    status_msg = await message.reply_text("⏳ **Initializing download...**")
    file_path = None
    last_update = [time.time()]

    try:
        # Download with real-time Telegram progress bar
        file_path = await message.download(
            file_name=os.path.join(DOWNLOAD_DIR, ""),
            progress=progress_callback,
            progress_args=(status_msg, "Downloading from Telegram...", last_update)
        )

        await safe_edit(status_msg, "🔍 **Requesting EarnVids server...**")
        upload_server = await get_upload_server(EARNVIDS_KEY)

        # Upload with real-time progress bar
        upload_res = await upload_file_stream(upload_server, file_path, EARNVIDS_KEY, status_msg)

        if upload_res.get("status") == 200 and "files" in upload_res and len(upload_res["files"]) > 0:
            file_code = upload_res["files"][0]["filecode"]
            embed_url = f"https://earnvids.com/embed-{file_code}.html"
            iframe_code = f'<iframe src="{embed_url}" width="640" height="360" frameborder="0" allowfullscreen></iframe>'

            response_text = (
                f"✅ **Upload Complete!**\n\n"
                f"🔗 **Embed URL:** `{embed_url}`\n\n"
                f"💻 **HTML Embed Code:**\n`{iframe_code}`"
            )
            await safe_edit(status_msg, response_text)
        else:
            await safe_edit(status_msg, f"❌ Upload failed: {upload_res.get('msg', 'Unknown Error')}", parse_mode=None)

    except Exception as e:
        # Never let error reporting itself crash the handler:
        # - strip Markdown-breaking characters
        # - cap length
        # - disable parse_mode entirely as a fallback
        err_text = str(e)[:300].replace("`", "'").replace("*", "")
        print(f"[process_media] error: {e}")
        await safe_edit(status_msg, f"⚠️ Error: {err_text}", parse_mode=None)
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


# ---------------------------------------------------------------------------
# Health check server (port 8000) — required by the hosting platform so it
# can verify the container is alive. Runs alongside the Pyrogram client.
# ---------------------------------------------------------------------------
async def health(request):
    return web.json_response({"status": "ok"})


async def run_health_server():
    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    print(f"Health check server listening on 0.0.0.0:{HEALTH_PORT}")


async def main():
    print("Bot starting with high-speed transfer support...")
    await run_health_server()
    await app.start()
    print("Bot started.")
    await asyncio.Event().wait()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
