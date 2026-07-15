from flask import Flask, request
from threading import Thread
import asyncio

app = Flask('')
discord_bot = None  # We will pass the bot instance here from main.py
LOG_CHANNEL_ID = 1526784173887717497  # <-- REPLACE THIS with your actual Discord Channel ID

@app.route('/')
def home():
    return "ServAuth is alive and kicking!"

# This is the endpoint that listens for GET requests to log data!
@app.route('/log', methods=['GET'])
def log_api():
    if discord_bot is None:
        return {"status": "error", "message": "Bot not ready"}, 503

    # Grab info from the URL parameters (e.g., ?event=API_Call&status=Success)
    event_name = request.args.get('event', 'Unknown Event')
    details = request.args.get('details', 'No details provided.')
    
    # We use asyncio to run the async Discord send message from the sync Flask thread
    asyncio.run_coroutine_threadsafe(
        send_log_to_discord(event_name, details), 
        discord_bot.loop
    )
    
    return {"status": "logged", "event": event_name}, 200

async def send_log_to_discord(event_name, details):
    channel = discord_bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        import discord
        embed = discord.Embed(
            title="System Log | API Activity",
            color=discord.Color.from_rgb(139, 90, 43)  # Matches your custom brand color!
        )
        embed.add_field(name="Event", value=f"`{event_name}`", inline=False)
        embed.add_field(name="Details", value=details, inline=False)
        embed.set_footer(text="ServAuth Log System")
        await channel.send(embed=embed)
