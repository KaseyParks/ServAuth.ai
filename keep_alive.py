from flask import Flask, request
from threading import Thread
import asyncio
import os
import logging
import time
import requests

app = Flask('')
discord_bot = None  

def get_log_channel_id():
    if discord_bot and hasattr(discord_bot, 'dynamic_log_channel_id') and discord_bot.dynamic_log_channel_id:
        return discord_bot.dynamic_log_channel_id
    env_val = os.getenv("LOG_CHANNEL_ID")
    return int(env_val) if env_val and env_val.isdigit() else None

async def send_literal_log_to_discord(log_message):
    if not discord_bot:
        return
    channel_id = get_log_channel_id()
    if not channel_id:
        return
    channel = discord_bot.get_channel(channel_id)
    if channel:
        try:
            await channel.send(f"```log\n{log_message}\n```")
        except Exception:
            pass  

def trigger_discord_log(log_message):
    if discord_bot and discord_bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(
            send_literal_log_to_discord(log_message), 
            discord_bot.loop
        )

class DiscordLoggingHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        trigger_discord_log(log_entry)

logger = logging.getLogger('ServAuth')
logger.setLevel(logging.INFO)

if not any(isinstance(h, DiscordLoggingHandler) for h in logger.handlers):
    handler = DiscordLoggingHandler()
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

@app.route('/')
def home():
    return "ServAuth Core Network Interface Array initialized."

@app.route('/log', methods=['GET'])
def log_api():
    if discord_bot is None:
        return {"status": "internal server initialization failure State", "message": "Bot thread missing payload"}, 503
    event_name = request.args.get('event', 'UNKNOWN_EVENT')
    details = request.args.get('details', 'Context entry blank.')
    logger.info(f"API_ROUTER_METRIC | Event Key: {event_name} | Entry Value: {details}")
    return {"status": "logged", "event": event_name}, 200

def run():
    app.run(host='0.0.0.0', port=8080)

def ping_loop():
    time.sleep(5)
    url = os.getenv("RENDER_EXTERNAL_URL") or "http://127.0.0.1:8080/"
    print(f"[Keep Alive Thread Worker Active] Routing checks pointing to baseline target: {url}")
    while True:
        try:
            res = requests.get(url, timeout=10)
            print(f"[Keep Alive Connection Routine State] Active check status returned: {res.status_code}")
        except Exception as e:
            print(f"[Keep Alive Routine Exception Block] Verification trace faulted: {e}")
        time.sleep(30)

def keep_alive():
    server_thread = Thread(target=run)
    server_thread.daemon = True
    server_thread.start()
    
    ping_thread = Thread(target=ping_loop)
    ping_thread.daemon = True
    ping_thread.start()
