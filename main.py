import os
import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI
from dotenv import load_dotenv
import keep_alive  # Import the module to configure it properly

# 1. Load environment variables first
load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'))

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

# Troubleshooting fallback checks
if not OPENROUTER_KEY:
    print("❌ ERROR: OPENROUTER_API_KEY not found in your .env file!")
if not DISCORD_TOKEN:
    print("❌ ERROR: DISCORD_TOKEN not found in your .env file!")

# 2. Set up Discord bot intents and initialize once
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 3. Start the web server immediately and pass the bot instance
keep_alive.discord_bot = bot
keep_alive.keep_alive()

# Set up the OpenAI client pointing to OpenRouter
ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
)

# Configuration Variables
AI_CHAT_CHANNEL_ID = 1525982227790565546
MODEL_NAME = "openai/gpt-oss-20b:free"

# Global Instruction (System Prompt)
GLOBAL_INSTRUCTION = (
    "You are a highly capable, adaptive, and witty AI assistant running inside a Discord server. "
    "You are chatting with users in real-time. Keep your tone natural, engaging, and match the "
    "energy of the users. Avoid sounding overly robotic, formal, or repetitive. You remember "
    "the flow of the conversation up to your memory limit."
)


# ----------------------------------------------------------------
# SYNCING SLASH COMMANDS
# ----------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) successfully.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
    print("------")


# ----------------------------------------------------------------
# CHAT LISTENER (Handles the auto-chat channel with 100-message memory)
# ----------------------------------------------------------------
@bot.event
async def on_message(message):
    # Never reply to our own bot messages
    if message.author == bot.user:
        return

    # IGNORE COMMANDS: If a message starts with '/' or '!', the AI completely ignores it
    if message.content.startswith("/") or message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    # 1. Detect if a message is sent in the designated ai-chat channel
    if message.channel.id == AI_CHAT_CHANNEL_ID:
        async with message.channel.typing():
            try:
                # Start history with the global instruction
                conversation_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]

                # Fetch the last 100 messages in this channel
                raw_messages = []
                async for msg in message.channel.history(limit=100):
                    raw_messages.append(msg)

                # Reverse them so they are in chronological order (oldest to newest)
                raw_messages.reverse()

                # Process the chronological history
                for msg in raw_messages:
                    # Ignore empty messages, system pins, embeds, and ANY commands (starting with / or !)
                    if not msg.content or msg.content.startswith("/") or msg.content.startswith(bot.command_prefix):
                        continue

                    role = "assistant" if msg.author == bot.user else "user"
                    username = msg.author.name.replace(" ", "_")

                    conversation_history.append({
                        "role": role,
                        "name": username,
                        "content": msg.content
                    })

                # Call OpenRouter
                completion = ai_client.chat.completions.create(
                    extra_headers={
                        "HTTP-Referer": "https://localhost",
                        "X-Title": "My Discord Bot",
                    },
                    model=MODEL_NAME,
                    messages=conversation_history
                )

                response_text = completion.choices[0].message.content

                if len(response_text) > 2000:
                    response_text = response_text[:1990] + "..."

                await message.reply(response_text)

            except Exception as e:
                print(f"Error calling OpenRouter for channel chat: {e}")
                await message.reply("My brain just lagged out. Send another message to try again!")
        return

    # Let normal prefix commands work in all other channels
    await bot.process_commands(message)


# ----------------------------------------------------------------
# SLASH COMMAND: /prompt (Context-Aware with 45-message memory)
# ----------------------------------------------------------------
@bot.tree.command(name="prompt", description="Ask the AI a question using gpt-oss-20b (reads past 45 messages)")
@app_commands.describe(question="The question or prompt you want to send to the AI")
async def prompt(interaction: discord.Interaction, question: str):
    await interaction.response.defer()

    try:
        # Start history with the global instruction
        conversation_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]

        # Fetch the last 45 messages in this channel
        raw_messages = []
        async for msg in interaction.channel.history(limit=45):
            raw_messages.append(msg)

        # Reverse them so they are chronological
        raw_messages.reverse()

        for msg in raw_messages:
            # Skip empty messages and any commands starting with / or !
            if not msg.content or msg.content.startswith("/") or msg.content.startswith(bot.command_prefix):
                continue

            role = "assistant" if msg.author == bot.user else "user"
            username = msg.author.name.replace(" ", "_")

            conversation_history.append({
                "role": role,
                "name": username,
                "content": msg.content
            })

        # Append the current active user prompt at the absolute end of the history
        conversation_history.append({"role": "user", "content": question})

        # Call OpenRouter
        completion = ai_client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": "https://localhost",
                "X-Title": "My Discord Bot",
            },
            model=MODEL_NAME,
            messages=conversation_history
        )

        response_text = completion.choices[0].message.content

        if len(response_text) > 2000:
            response_text = response_text[:1990] + "..."

        await interaction.followup.send(f"**Question:** {question}\n\n{response_text}")

    except Exception as e:
        print(f"Error calling OpenRouter: {e}")
        await interaction.followup.send("My neural pathways got blocked. Try asking again!")


# ----------------------------------------------------------------
# SLASH COMMAND: /clear (Deletes messages in the channel)
# ----------------------------------------------------------------
@bot.tree.command(name="clear",
                  description="Deletes up to 1000 messages (skips messages older than 14 days to prevent lag)")
@app_commands.describe(amount="The number of messages to delete (default: 1000)")
async def clear(interaction: discord.Interaction, amount: int = 1000):
    # Only allow users with Manage Messages permission to use this
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("You don't have permission to use this command!", ephemeral=True)
        return

    # Defer ephemerally so the "thinking..." response is only visible to the user cleaning up
    await interaction.response.defer(ephemeral=True)

    try:
        # bulk=True tells the bot to ONLY delete messages under 14 days old.
        deleted = await interaction.channel.purge(limit=amount, bulk=True)

        await interaction.followup.send(
            f"Successfully deleted {len(deleted)} messages!\n"
            f"*(Note: Messages older than 14 days were skipped to protect the bot from rate limits)*",
            ephemeral=True
        )
    except Exception as e:
        print(f"Error during purge: {e}")
        await interaction.followup.send("Failed to clear messages. Make sure I have 'Manage Messages' permission!",
                                        ephemeral=True)

# ----------------------------------------------------------------
# SLASH COMMAND: /help
# ----------------------------------------------------------------
@bot.tree.command(name="help", description="Displays all available commands and features")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Custom AI Bot Help Menu",
        description="Here is everything I can do. Designed with zero limitations.",
        color=discord.Color.purple()
    )

    # AI Features
    embed.add_field(
        name="🧠 AI Features",
        value=(
            "**Auto-Chat Channel**\n"
            f"Send a regular message in <#{AI_CHAT_CHANNEL_ID}> and I will respond! "
            "I remember up to **100 messages** of conversation history.\n\n"
            "**Slash Command Prompt**\n"
            "Use `/prompt` to query me from any channel! I read up to **45 messages** of channel context."
        ),
        inline=False
    )

    # Slash Commands
    embed.add_field(
        name="⚙️ Slash Commands (/) ",
        value=(
            "**`/prompt [question]`** - Ask the AI a question using the `gpt-oss-20b` model.\n"
            "**`/clear [amount]`** - Purge up to 1000 messages in the channel. (Requires *Manage Messages*)\n"
            "**`/help`** - Shows this help menu."
        ),
        inline=False
    )

    # Legacy / Prefix commands
    embed.add_field(
        name="⚡ Prefix Commands (!)",
        value=(
            "**`!ping`** - Check the bot's current connection latency.\n"
            "**`!about`** - Show details about the bot's custom design."
        ),
        inline=False
    )

    embed.set_footer(text="OpenRouter Powered • gpt-oss-20b")

    await interaction.response.send_message(embed=embed)


# ----------------------------------------------------------------
# TRADITIONAL PREFIX COMMANDS
# ----------------------------------------------------------------
@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"Pong! 🏓 ({latency}ms)")


@bot.command()
async def about(ctx):
    embed = discord.Embed(
        title="About ServAuth",
        description=(
            "I'm ServAuth, a custom AI-powered bot built with Python and OpenRouter.\n"
            "Operating with zero bloat and absolute flexibility."
        ),
        color=discord.Color.from_rgb(139, 90, 43)  # Matches your custom brand color!
    )
    embed.add_field(name="Command Prefix", value="`!`", inline=True)
    embed.add_field(name="Engine", value="`gpt-oss-20b`", inline=True)

    gif_url = "https://files.catbox.moe/7xiuy9.gif"
    if gif_url:
        embed.set_image(url=gif_url)

    await ctx.send(embed=embed)


# Run the bot
bot.run(DISCORD_TOKEN)
