from flask import Flask, request
from threading import Thread
import asyncio
import os
import logging

app = Flask('')
discord_bot = None  # Passed from main.py

# This is a fallback helper to find the active log channel ID (either from .env or dynamically discovered)
def get_log_channel_id():
    # If the main bot script found or created a log channel, use that dynamic ID first
    if discord_bot and hasattr(discord_bot, 'dynamic_log_channel_id') and discord_bot.dynamic_log_channel_id:
        return discord_bot.dynamic_log_channel_id
    
    # Otherwise, fall back to the .env file
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
        # Wrap it in a code block with log syntax highlighting
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

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
