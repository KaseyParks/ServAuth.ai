import os
import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI
from dotenv import load_dotenv
import keep_alive  # Import keep-alive to register bot and run webserver

# 1. Load environment variables
load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'))

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_KEY:
    print("❌ ERROR: OPENROUTER_API_KEY not found in your .env file!")
if not DISCORD_TOKEN:
    print("❌ ERROR: DISCORD_TOKEN not found in your .env file!")

# 2. Set up Discord bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True  # Required to manage channels and categories
bot = commands.Bot(command_prefix="!", intents=intents)

# Dynamically store active IDs so the web server can find them if they change
bot.dynamic_log_channel_id = None
bot.dynamic_ai_chat_channel_id = None

# 3. Start the web server immediately
keep_alive.discord_bot = bot
keep_alive.keep_alive()

# Set up the OpenAI client pointing to OpenRouter
ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
)

# Configuration Variables
MODEL_NAME = "openai/gpt-oss-20b:free"
GLOBAL_INSTRUCTION = (
    "You are a highly capable, adaptive, and witty AI assistant running inside a Discord server. "
    "You are chatting with users in real-time. Keep your tone natural, engaging, and match the "
    "energy of the users. Avoid sounding overly robotic, formal, or repetitive. You remember "
    "the flow of the conversation up to your memory limit."
)


# ----------------------------------------------------------------
# ADVANCED AUTO-SETUP ON READY
# ----------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    
    # Run auto-setup configuration for every guild the bot is currently in
    for guild in bot.guilds:
        print(f"Checking configuration for server: {guild.name}...")
        
        # 1. Check or create "DevC" Category
        category = discord.utils.get(guild.categories, name="DevC")
        if not category:
            try:
                category = await guild.create_category(name="DevC")
                print(f"✅ Created 'DevC' Category in {guild.name}")
            except Exception as e:
                print(f"❌ Failed to create category in {guild.name}: {e}")
                continue

        # 2. Check or create Log Channel (Admin-Only)
        log_channel = discord.utils.get(guild.text_channels, name="servauth-logs")
        if not log_channel:
            try:
                # Set permissions: Only Admins can view this channel
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
                # Grant access to anyone who has manage_channels
                for role in guild.roles:
                    if role.permissions.manage_channels or role.permissions.administrator:
                        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                log_channel = await guild.create_text_channel(
                    name="servauth-logs", 
                    category=category,
                    overwrites=overwrites,
                    topic="Raw system API logs and execution outputs."
                )
                print(f"✅ Created private log channel '#servauth-logs' in {guild.name}")
            except Exception as e:
                print(f"❌ Failed to create log channel in {guild.name}: {e}")
                
        if log_channel:
            bot.dynamic_log_channel_id = log_channel.id

        # 3. Check or create "ai-chat" channel
        ai_channel = discord.utils.get(guild.text_channels, name="ai-chat")
        if not ai_channel:
            try:
                ai_channel = await guild.create_text_channel(
                    name="ai-chat", 
                    category=category,
                    topic="Talk with ServAuth here! Fully powered by custom AI."
                )
                print(f"✅ Created public channel '#ai-chat' in {guild.name}")
            except Exception as e:
                print(f"❌ Failed to create '#ai-chat' in {guild.name}: {e}")
                
        if ai_channel:
            bot.dynamic_ai_chat_channel_id = ai_channel.id

    # Sync Slash Commands
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
    if message.author == bot.user:
        return

    if message.content.startswith("/") or message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    # Check against the dynamically verified AI chat channel ID
    if bot.dynamic_ai_chat_channel_id and message.channel.id == bot.dynamic_ai_chat_channel_id:
        async with message.channel.typing():
            try:
                conversation_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]

                raw_messages = []
                async for msg in message.channel.history(limit=100):
                    raw_messages.append(msg)

                raw_messages.reverse()

                for msg in raw_messages:
                    if not msg.content or msg.content.startswith("/") or msg.content.startswith(bot.command_prefix):
                        continue

                    role = "assistant" if msg.author == bot.user else "user"
                    username = msg.author.name.replace(" ", "_")

                    conversation_history.append({
                        "role": role,
                        "name": username,
                        "content": msg.content
                    })

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

    await bot.process_commands(message)


# ----------------------------------------------------------------
# SLASH COMMAND: /prompt (Context-Aware with 45-message memory)
# ----------------------------------------------------------------
@bot.tree.command(name="prompt", description="Ask the AI a question using gpt-oss-20b (reads past 45 messages)")
@app_commands.describe(question="The question or prompt you want to send to the AI")
async def prompt(interaction: discord.Interaction, question: str):
    await interaction.response.defer()

    try:
        conversation_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]

        raw_messages = []
        async for msg in interaction.channel.history(limit=45):
            raw_messages.append(msg)

        raw_messages.reverse()

        for msg in raw_messages:
            if not msg.content or msg.content.startswith("/") or msg.content.startswith(bot.command_prefix):
                continue

            role = "assistant" if msg.author == bot.user else "user"
            username = msg.author.name.replace(" ", "_")

            conversation_history.append({
                "role": role,
                "name": username,
                "content": msg.content
            })

        conversation_history.append({"role": "user", "content": question})

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
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("You don't have permission to use this command!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
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
    ai_channel_display = f"<#{bot.dynamic_ai_chat_channel_id}>" if bot.dynamic_ai_chat_channel_id else "`#ai-chat`"
    
    embed = discord.Embed(
        title="🤖 Custom AI Bot Help Menu",
        description="Here is everything I can do. Designed with zero limitations.",
        color=discord.Color.purple()
    )

    embed.add_field(
        name="🧠 AI Features",
        value=(
            "**Auto-Chat Channel**\n"
            f"Send a regular message in {ai_channel_display} and I will respond! "
            "I remember up to **100 messages** of conversation history.\n\n"
            "**Slash Command Prompt**\n"
            "Use `/prompt` to query me from any channel! I read up to **45 messages** of channel context."
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ Slash Commands (/) ",
        value=(
            "**`/prompt [question]`** - Ask the AI a question using the `gpt-oss-20b` model.\n"
            "**`/clear [amount]`** - Purge up to 1000 messages in the channel. (Requires *Manage Messages*)\n"
            "**`/help`** - Shows this help menu."
        ),
        inline=False
    )

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
        color=discord.Color.from_rgb(139, 90, 43)
    )
    embed.add_field(name="Command Prefix", value="`!`", inline=True)
    embed.add_field(name="Engine", value="`gpt-oss-20b`", inline=True)

    gif_url = "https://files.catbox.moe/7xiuy9.gif"
    if gif_url:
        embed.set_image(url=gif_url)

    await ctx.send(embed=embed)


# Run the bot
bot.run(DISCORD_TOKEN)
