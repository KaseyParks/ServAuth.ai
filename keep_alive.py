from flask import Flask, request
from threading import Thread
import asyncio
import os

app = Flask('')
discord_bot = None  # Passed from main.py

# Fetch the ID and safely convert it to an integer
channel_env = os.getenv("LOG_CHANNEL_ID")
CHANNEL_ID = int(channel_env) if channel_env and channel_env.isdigit() else None

@app.route('/')
def home():
    return "ServAuth is alive and kicking!"

# Endpoint that listens for GET requests to log data
@app.route('/log', methods=['GET'])
def log_api():
    if discord_bot is None:
        return {"status": "error", "message": "Bot not ready"}, 503

    # Grab info from the URL parameters
    event_name = request.args.get('event', 'Unknown Event')
    details = request.args.get('details', 'No details provided.')
    
    # Safely send the log using the bot's running loop
    asyncio.run_coroutine_threadsafe(
        send_log_to_discord(event_name, details), 
        discord_bot.loop
    )
    
    return {"status": "logged", "event": event_name}, 200

async def send_log_to_discord(event_name, details):
    if not CHANNEL_ID:
        print("❌ Keep-Alive Error: LOG_CHANNEL_ID env variable is missing or invalid!")
        return

    channel = discord_bot.get_channel(CHANNEL_ID)
    if channel:
        import discord
        embed = discord.Embed(
            title="System Log | API Activity",
            color=discord.Color.from_rgb(139, 90, 43)  # Brand color
        )
        embed.add_field(name="Event", value=f"`{event_name}`", inline=False)
        embed.add_field(name="Details", value=details, inline=False)
        embed.set_footer(text="ServAuth Log System")
        await channel.send(embed=embed)

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True  # Allows the thread to exit when the main program stops
    t.start()
