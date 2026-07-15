from flask import Flask, request
from threading import Thread
import asyncio
import os
import logging
import time
import requests

app = Flask('')
discord_bot = None  # Passed from main.py

# This is a fallback helper to find the active log channel ID
def get_log_channel_id():
    if discord_bot and hasattr(discord_bot, 'dynamic_log_channel_id') and discord_bot.dynamic_log_channel_id:
        return discord_bot.dynamic_log_channel_id
    
    channel_env = os.getenv("LOG_CHANNEL_ID")
    return int(channel_env) if channel_env and channel_env.isdigit() else None

# Send raw, literal log strings directly to the active logging channel
async def send_literal_log_to_discord(log_message):
    if not discord_bot:
        return
        
    channel_id = get_log_channel_id()
    if not channel_id:
        return

    channel = discord_bot.get_channel(channel_id)
    if channel:
        formatted_log = f"```log\n{log_message}\n```"
        await channel.send(formatted_log)

def trigger_discord_log(log_message):
    if discord_bot and discord_bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(
            send_literal_log_to_discord(log_message), 
            discord_bot.loop
        )

# Custom logging handler that redirects system logs to your Discord channel
class DiscordLoggingHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        trigger_discord_log(log_entry)

# Set up the logger
logger = logging.getLogger('ServAuth')
logger.setLevel(logging.INFO)

if not any(isinstance(h, DiscordLoggingHandler) for h in logger.handlers):
    handler = DiscordLoggingHandler()
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


@app.route('/')
def home():
    return "ServAuth is alive and kicking!"


@app.route('/log', methods=['GET'])
def log_api():
    if discord_bot is None:
        return {"status": "error", "message": "Bot not ready"}, 503

    event_name = request.args.get('event', 'UNKNOWN_EVENT')
    details = request.args.get('details', 'No details provided.')
    
    logger.info(f"API_EVENT | Event: {event_name} | Details: {details}")
    return {"status": "logged", "event": event_name}, 200


def run():
    app.run(host='0.0.0.0', port=8080)


def ping_loop():
    # Wait 5 seconds for the server to actually start up before we start pinging
    time.sleep(5)
    
    # Grab the public Render URL, default to localhost if not set
    url = os.getenv("RENDER_EXTERNAL_URL") or "http://127.0.0.1:8080/"
    print(f"[Keep Alive] Pinging target initialized: {url}")
    
    while True:
        try:
            response = requests.get(url)
            # Just print to terminal so it doesn't spam your Discord log channel
            print(f"[Keep Alive] Ping sent to {url}. Response: {response.status_code}")
        except Exception as e:
            print(f"[Keep Alive] Ping failed: {e}")
            
        time.sleep(30)


def keep_alive():
    # Thread for the Flask web server
    server_thread = Thread(target=run)
    server_thread.daemon = True
    server_thread.start()
    
    # Thread for the 30-second ping loop
    ping_thread = Thread(target=ping_loop)
    ping_thread.daemon = True
    ping_thread.start()
