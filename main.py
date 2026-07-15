import os
import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI
from dotenv import load_dotenv
import keep_alive  # Import keep-alive to register bot and run webserver
import base64
import asyncio
import re

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
MODEL_NAME = "openrouter/free"
GLOBAL_INSTRUCTION = (
    "You are a highly capable, adaptive, and witty AI assistant running inside a Discord server. "
    "You are chatting with users in real-time. Keep your tone natural, engaging, and match the "
    "energy of the users. Avoid sounding overly robotic, formal, or repetitive. You remember "
    "the flow of the conversation up to your memory limit. If an image is provided in the message "
    "history, use your vision capabilities to analyze it and discuss it naturally."
)


# Helper function to convert Discord attachments to Base64 data strings
async def get_image_base64(attachment: discord.Attachment) -> str:
    supported_types = ["image/jpeg", "image/png", "image/webp", "image/gif"]
    if attachment.content_type not in supported_types:
        return None
    
    try:
        image_bytes = await attachment.read()
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:{attachment.content_type};base64,{encoded}"
    except Exception as e:
        print(f"Failed to process image attachment {attachment.filename}: {e}")
        return None


# Helper to parse reasoning out of AI output
def extract_reasoning(content: str):
    """
    Looks for <think>...</think> blocks in the response.
    Returns (reasoning_text, final_content_text)
    """
    if not content:
        return "", ""
    
    # Match everything inside <think> and </think> tags
    think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
    if think_match:
        reasoning = think_match.group(1).strip()
        # Remove the think tag block entirely from the main content output
        clean_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        return reasoning, clean_content
    
    return "", content


# ----------------------------------------------------------------
# LIVE GENERATION TIMING RUNNER
# ----------------------------------------------------------------
class GenerationTracker:
    def __init__(self, channel, initial_msg=None, edit_target=None):
        self.channel = channel
        self.initial_msg = initial_msg
        self.edit_target = edit_target  # Interaction response edit hook
        self.start_time = discord.utils.utcnow()
        self.is_running = True
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self._update_timer_loop())

    async def _update_timer_loop(self):
        while self.is_running:
            now = discord.utils.utcnow()
            duration = (now - self.start_time).total_seconds()
            
            embed = discord.Embed(
                title="Generating response please wait...",
                color=discord.Color.blue()
            )
            embed.description = f"⏱️ **Live of generation:** {duration:.2f}s"
            
            try:
                if self.edit_target:
                    await self.edit_target.edit_original_response(embed=embed)
                elif self.initial_msg:
                    await self.initial_msg.edit(embed=embed)
            except Exception:
                pass  # Avoid crashing if edit fails due to speed/rate limits
            
            await asyncio.sleep(1.0)

    def stop(self):
        self.is_running = False
        if self.task:
            self.task.cancel()


# ----------------------------------------------------------------
# DYNAMIC GENERATION VIEW (Regenerate & Arrows Pagination)
# ----------------------------------------------------------------
class GenerationView(discord.ui.View):
    def __init__(self, ai_client, model_name, conversation_history, initial_text, prefix="", show_reasoning=False):
        super().__init__(timeout=300)
        self.ai_client = ai_client
        self.model_name = model_name
        self.conversation_history = list(conversation_history)
        self.generations = [initial_text]
        self.current_index = 0
        self.prefix = prefix
        self.show_reasoning = show_reasoning
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        
        if len(self.generations) > 1:
            prev_button = discord.ui.Button(
                emoji="◀️", 
                style=discord.ButtonStyle.secondary, 
                disabled=(self.current_index == 0)
            )
            prev_button.callback = self.prev_callback
            self.add_item(prev_button)
        
        regen_button = discord.ui.Button(
            label="Regenerate", 
            emoji="🔄", 
            style=discord.ButtonStyle.primary
        )
        regen_button.callback = self.regen_callback
        self.add_item(regen_button)
        
        if len(self.generations) > 1:
            next_button = discord.ui.Button(
                emoji="▶️", 
                style=discord.ButtonStyle.secondary, 
                disabled=(self.current_index == len(self.generations) - 1)
            )
            next_button.callback = self.next_callback
            self.add_item(next_button)

    async def _send_or_edit_response(self, target, content_raw):
        reasoning, clean_text = extract_reasoning(content_raw)
        
        embeds = []
        # If reasoning exists and was requested
        if self.show_reasoning and reasoning:
            reasoning_embed = discord.Embed(
                title="🧠 AI Reasoning Process",
                description=reasoning,
                color=discord.Color.dark_grey()
            )
            embeds.append(reasoning_embed)
            
        final_content = f"{self.prefix}{clean_text}"
        
        # Determine if target is interaction or standard message
        if isinstance(target, discord.Interaction):
            await target.followup.edit_message(
                message_id=target.message.id,
                content=final_content,
                embeds=embeds,
                view=self
            )
        else:
            await target.edit(content=final_content, embeds=embeds, view=self)

    async def prev_callback(self, interaction: discord.Interaction):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_buttons()
            await self._send_or_edit_response(interaction, self.generations[self.current_index])

    async def next_callback(self, interaction: discord.Interaction):
        if self.current_index < len(self.generations) - 1:
            self.current_index += 1
            self.update_buttons()
            await self._send_or_edit_response(interaction, self.generations[self.current_index])

    async def regen_callback(self, interaction: discord.Interaction):
        # Fire up a live timer inside the regeneration update
        tracker = GenerationTracker(interaction.channel, edit_target=interaction)
        tracker.start()
        
        try:
            # Re-fetch from API running loop
            completion = await asyncio.to_thread(
                self.ai_client.chat.completions.create,
                extra_headers={
                    "HTTP-Referer": "https://localhost",
                    "X-Title": "My Discord Bot",
                },
                model=self.model_name,
                messages=self.conversation_history
            )
            response_text = completion.choices[0].message.content
            tracker.stop()
            
            if len(response_text) > 2000:
                response_text = response_text[:1990] + "..."
            
            self.generations.append(response_text)
            self.current_index = len(self.generations) - 1
            self.update_buttons()
            
            # Wipe loading state and present output
            await self._send_or_edit_response(interaction, response_text)
            
        except Exception as e:
            tracker.stop()
            print(f"Error during regeneration request: {e}")
            await interaction.followup.send("My neural channels got tangled trying to rebuild that. Try again!", ephemeral=True)


# ----------------------------------------------------------------
# ADVANCED AUTO-SETUP ON READY
# ----------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    
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
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
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

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) successfully.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
    print("------")


# ----------------------------------------------------------------
# CHAT LISTENER (Handles the auto-chat channel with image support)
# ----------------------------------------------------------------
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.startswith("/") or message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    if bot.dynamic_ai_chat_channel_id and message.channel.id == bot.dynamic_ai_chat_channel_id:
        # Create a placeholder embed with a running clock timer
        placeholder_embed = discord.Embed(
            title="Generating response please wait...",
            color=discord.Color.blue()
        )
        placeholder_embed.description = "⏱️ **Live of generation:** 0.00s"
        status_msg = await message.reply(embed=placeholder_embed)
        
        tracker = GenerationTracker(message.channel, initial_msg=status_msg)
        tracker.start()

        try:
            conversation_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]

            raw_messages = []
            async for msg in message.channel.history(limit=100):
                raw_messages.append(msg)

            # Clean history parsing logic
            raw_messages.reverse()

            for msg in raw_messages:
                if msg.content.startswith("/") or msg.content.startswith(bot.command_prefix):
                    continue
                # Do not evaluate our own active loading message
                if msg.id == status_msg.id:
                    continue
                if not msg.content and not msg.attachments:
                    continue

                role = "assistant" if msg.author == bot.user else "user"
                username = msg.author.name.replace(" ", "_")

                if not msg.attachments:
                    conversation_history.append({
                        "role": role,
                        "name": username,
                        "content": msg.content
                    })
                else:
                    content_list = []
                    if msg.content:
                        content_list.append({"type": "text", "text": msg.content})

                    for attachment in msg.attachments:
                        image_data_url = await get_image_base64(attachment)
                        if image_data_url:
                            content_list.append({
                                "type": "image_url",
                                "image_url": {"url": image_data_url}
                            })

                    if content_list:
                        conversation_history.append({
                            "role": role,
                            "name": username,
                            "content": content_list
                        })

            generation_history = list(conversation_history)

            # Thread-safe async run for the API request
            completion = await asyncio.to_thread(
                ai_client.chat.completions.create,
                extra_headers={
                    "HTTP-Referer": "https://localhost",
                    "X-Title": "My Discord Bot",
                },
                model=MODEL_NAME,
                messages=conversation_history
            )

            response_text = completion.choices[0].message.content
            tracker.stop()

            if len(response_text) > 2000:
                response_text = response_text[:1990] + "..."

            # We cleanly extract the reasoning. (For dynamic chat channel we default thinking to False/Hidden)
            reasoning, clean_text = extract_reasoning(response_text)

            view = GenerationView(ai_client, MODEL_NAME, generation_history, response_text, show_reasoning=False)
            
            # Wipe loading state, update output view
            await status_msg.edit(content=clean_text, embed=None, view=view)

        except Exception as e:
            tracker.stop()
            print(f"Error calling OpenRouter for channel chat: {e}")
            await status_msg.edit(content="My brain just lagged out. Send another message to try again!", embed=None)
        return

    await bot.process_commands(message)


# ----------------------------------------------------------------
# SLASH COMMAND: /prompt (Context-Aware with 45-message memory)
# ----------------------------------------------------------------
@bot.tree.command(name="prompt", description="Ask the AI a question using the active model (reads past 45 messages)")
@app_commands.describe(
    question="The question or prompt you want to send to the AI",
    reasoning="Set to True to see the AI's internal step-by-step thinking/reasoning process"
)
async def prompt(interaction: discord.Interaction, question: str, reasoning: bool = False):
    # Defer so we don't trigger interaction timeouts
    await interaction.response.defer()
    
    placeholder_embed = discord.Embed(
        title="Generating response please wait...",
        color=discord.Color.blue()
    )
    placeholder_embed.description = "⏱️ **Live of generation:** 0.00s"
    
    # Push immediate placeholder tracking
    await interaction.followup.send(embed=placeholder_embed)
    
    tracker = GenerationTracker(interaction.channel, edit_target=interaction)
    tracker.start()

    try:
        conversation_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]

        raw_messages = []
        async for msg in interaction.channel.history(limit=45):
            raw_messages.append(msg)

        raw_messages.reverse()

        for msg in raw_messages:
            if msg.content.startswith("/") or msg.content.startswith(bot.command_prefix):
                continue
            if not msg.content and not msg.attachments:
                continue

            role = "assistant" if msg.author == bot.user else "user"
            username = msg.author.name.replace(" ", "_")

            if not msg.attachments:
                conversation_history.append({
                    "role": role,
                    "name": username,
                    "content": msg.content
                })
            else:
                content_list = []
                if msg.content:
                    content_list.append({"type": "text", "text": msg.content})

                for attachment in msg.attachments:
                    image_data_url = await get_image_base64(attachment)
                    if image_data_url:
                        content_list.append({
                            "type": "image_url",
                            "image_url": {"url": image_data_url}
                        })

                if content_list:
                    conversation_history.append({
                        "role": role,
                        "name": username,
                        "content": content_list
                    })

        # Inject final user query
        conversation_history.append({"role": "user", "content": question})
        generation_history = list(conversation_history)

        completion = await asyncio.to_thread(
            ai_client.chat.completions.create,
            extra_headers={
                "HTTP-Referer": "https://localhost",
                "X-Title": "My Discord Bot",
            },
            model=MODEL_NAME,
            messages=conversation_history
        )

        response_text = completion.choices[0].message.content
        tracker.stop()

        if len(response_text) > 2000:
            response_text = response_text[:1990] + "..."

        # Parse potential reasoning out of payload
        extracted_thoughts, clean_text = extract_reasoning(response_text)

        prefix_text = f"**Question:** {question}\n\n"
        view = GenerationView(
            ai_client, 
            MODEL_NAME, 
            generation_history, 
            response_text, 
            prefix=prefix_text, 
            show_reasoning=reasoning
        )
        
        embeds = []
        # Compile secondary reasoning embed if enabled
        if reasoning and extracted_thoughts:
            embeds.append(
                discord.Embed(
                    title="🧠 AI Reasoning Process",
                    description=extracted_thoughts,
                    color=discord.Color.dark_grey()
                )
            )

        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            content=f"{prefix_text}{clean_text}",
            embeds=embeds,
            view=view
        )

    except Exception as e:
        tracker.stop()
        print(f"Error calling OpenRouter: {e}")
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            content="My neural pathways got blocked. Try asking again!",
            embed=None
        )


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
            f"Send a regular message or drop an image in {ai_channel_display} and I will respond! "
            "I remember up to **100 messages** of conversation history and analyze images.\n\n"
            "**Slash Command Prompt**\n"
            "Use `/prompt` to query me from any channel! I read up to **45 messages** of channel context."
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ Slash Commands (/) ",
        value=(
            "**`/prompt [question] [reasoning]`** - Ask the AI a question. Set `reasoning` to True to see step-by-step logic!\n"
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

    embed.set_footer(text=f"OpenRouter Powered • {MODEL_NAME}")
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
    embed.add_field(name="Engine", value=f"`{MODEL_NAME}`", inline=True)

    gif_url = "https://files.catbox.moe/7xiuy9.gif"
    if gif_url:
        embed.set_image(url=gif_url)

    await ctx.send(embed=embed)


# Run the bot
bot.run(DISCORD_TOKEN)
