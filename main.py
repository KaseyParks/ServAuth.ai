import os
import base64
import asyncio
import re
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import keep_alive

# 1. Load configuration and setup base variables
load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'))

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_KEY:
    print("❌ ERROR: OPENROUTER_API_KEY not found in your environment variables!")
if not DISCORD_TOKEN:
    print("❌ ERROR: DISCORD_TOKEN not found in your environment variables!")

# 2. Initialize Discord Bot Settings
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True  

bot = commands.Bot(command_prefix="!", intents=intents)

# Dynamically mapped channel storage for Flask interaction
bot.dynamic_log_channel_id = None
bot.dynamic_ai_chat_channel_id = None

# Bind bot reference to the keep_alive environment and start it
keep_alive.discord_bot = bot
keep_alive.keep_alive()

MODEL_NAME = "openai/gpt-oss-20b:free"
SAFETY_MODEL = "nvidia/nemotron-3.5-content-safety"

GLOBAL_INSTRUCTION = (
    "You are a highly capable, adaptive, and witty AI assistant running inside a Discord server. "
    "You are chatting with users in real-time. Keep your tone natural, engaging, and match the "
    "energy of the users. Avoid sounding overly robotic, formal, or repetitive. You remember "
    "the flow of the conversation up to your memory limit. If an image is provided in the message "
    "history, use your vision capabilities to analyze it and discuss it naturally."
)

# ----------------------------------------------------------------
# ASYNCHRONOUS API DISPATCHERS
# ----------------------------------------------------------------
async def fetch_openrouter_completion(payload: dict) -> str:
    """Dispatches an asynchronous POST request to the OpenRouter gateway backend."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost",
        "X-Title": "ServAuth System Core",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status == 200:
                data = await response.json()
                return data['choices'][0]['message']['content']
            else:
                error_body = await response.text()
                raise RuntimeError(f"OpenRouter returned status {response.status}: {error_body}")

async def is_content_safe(user_prompt: str) -> bool:
    """Pre-filters the prompt utilizing the Nemotron Safety instance before main evaluation."""
    payload = {
        "model": SAFETY_MODEL,
        "messages": [{"role": "user", "content": user_prompt}]
    }
    try:
        raw_response = await fetch_openrouter_completion(payload)
        result = raw_response.lower().strip()
        print(f"[Safety Gate] Input evaluation check completed. Result: {result[:30]}")
        return "unsafe" not in result
    except Exception as e:
        print(f"[Safety Gate] Processing Exception encountered: {e}")
        return True

# ----------------------------------------------------------------
# FILE INTERACTION COMPONENT HANDLERS
# ----------------------------------------------------------------
async def get_image_base64(attachment: discord.Attachment) -> str:
    if attachment.content_type not in ["image/jpeg", "image/png", "image/webp", "image/gif"]:
        return None
    try:
        image_bytes = await attachment.read()
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:{attachment.content_type};base64,{encoded}"
    except Exception as e:
        print(f"Failed to encode image target {attachment.filename}: {e}")
        return None

def extract_reasoning(content: str):
    if not content:
        return "", ""
    think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
    if think_match:
        reasoning = think_match.group(1).strip()
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
        self.edit_target = edit_target  
        self.start_time = discord.utils.utcnow()
        self.is_running = True
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self._update_timer_loop())

    async def _update_timer_loop(self):
        while self.is_running:
            duration = (discord.utils.utcnow() - self.start_time).total_seconds()
            embed = discord.Embed(title="Processing Neural Tracks...", color=discord.Color.blue())
            embed.description = f"⏱️ **Live execution ticker:** {duration:.2f}s"
            try:
                if self.edit_target:
                    await self.edit_target.edit_original_response(embed=embed)
                elif self.initial_msg:
                    await self.initial_msg.edit(embed=embed)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    def stop(self):
        self.is_running = False
        if self.task:
            self.task.cancel()

# ----------------------------------------------------------------
# DYNAMIC GENERATION VIEW
# ----------------------------------------------------------------
class GenerationView(discord.ui.View):
    def __init__(self, model_name, conversation_history, initial_text, prefix="", show_reasoning=False):
        super().__init__(timeout=300)
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
            prev_btn = discord.ui.Button(emoji="◀️", style=discord.ButtonStyle.secondary, disabled=(self.current_index == 0))
            prev_btn.callback = self.prev_callback
            self.add_item(prev_btn)
        
        regen_btn = discord.ui.Button(label="Regenerate", emoji="🔄", style=discord.ButtonStyle.primary)
        regen_btn.callback = self.regen_callback
        self.add_item(regen_btn)
        
        if len(self.generations) > 1:
            next_btn = discord.ui.Button(emoji="▶️", style=discord.ButtonStyle.secondary, disabled=(self.current_index == len(self.generations) - 1))
            next_btn.callback = self.next_callback
            self.add_item(next_btn)

    async def _send_or_edit_response(self, target, content_raw):
        reasoning, clean_text = extract_reasoning(content_raw)
        embeds = []
        if self.show_reasoning and reasoning:
            embeds.append(discord.Embed(title="🧠 AI Reasoning Process", description=reasoning, color=discord.Color.dark_grey()))
            
        final_content = f"{self.prefix}{clean_text}"
        if isinstance(target, discord.Interaction):
            await target.followup.edit_message(message_id=target.message.id, content=final_content, embeds=embeds, view=self)
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
        tracker = GenerationTracker(interaction.channel, edit_target=interaction)
        tracker.start()
        payload = {"model": self.model_name, "messages": self.conversation_history}
        try:
            response_text = await fetch_openrouter_completion(payload)
            tracker.stop()
            if len(response_text) > 2000:
                response_text = response_text[:1990] + "..."
            self.generations.append(response_text)
            self.current_index = len(self.generations) - 1
            self.update_buttons()
            await self._send_or_edit_response(interaction, response_text)
        except Exception as e:
            tracker.stop()
            print(f"Regeneration pipeline fault: {e}")
            await interaction.followup.send("Neural architecture failed to compile response iteration.", ephemeral=True)

# ----------------------------------------------------------------
# CORE INITIALIZATION PROCESS
# ----------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    for guild in bot.guilds:
        category = discord.utils.get(guild.categories, name="DevC")
        if not category:
            try:
                category = await guild.create_category(name="DevC")
            except Exception as e:
                print(f"Category creation failure: {e}")
                continue

        log_channel = discord.utils.get(guild.text_channels, name="servauth-logs")
        if not log_channel:
            try:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
                log_channel = await guild.create_text_channel(name="servauth-logs", category=category, overwrites=overwrites)
            except Exception as e:
                print(f"System logging interface failure: {e}")
        if log_channel:
            bot.dynamic_log_channel_id = log_channel.id

        ai_channel = discord.utils.get(guild.text_channels, name="ai-chat")
        if not ai_channel:
            try:
                ai_channel = await guild.create_text_channel(name="ai-chat", category=category)
            except Exception as e:
                print(f"Dynamic chat channel generation failure: {e}")
        if ai_channel:
            bot.dynamic_ai_chat_channel_id = ai_channel.id

    try:
        synced = await bot.tree.sync()
        print(f"Synchronized {len(synced)} App Command trees securely.")
    except Exception as e:
        print(f"Tree sync architecture operation failed: {e}")

# ----------------------------------------------------------------
# CONTEXT MONITOR & ROUTING ENGINE
# ----------------------------------------------------------------
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.startswith("/") or message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    if bot.dynamic_ai_chat_channel_id and message.channel.id == bot.dynamic_ai_chat_channel_id:
        embed = discord.Embed(title="Processing Neural Tracks...", color=discord.Color.blue())
        embed.description = "⏱|️ **Live execution ticker:** 0.00s"
        status_msg = await message.reply(embed=embed)
        
        tracker = GenerationTracker(message.channel, initial_msg=status_msg)
        tracker.start()

        try:
            if message.content and not await is_content_safe(message.content):
                tracker.stop()
                warn = discord.Embed(title="⚠️ Request Flagged", description="Execution halted by content safety matrix.", color=discord.Color.red())
                await status_msg.edit(embed=warn)
                return

            conversation_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]
            raw_messages = [msg async for msg in message.channel.history(limit=50)]
            raw_messages.reverse()

            for msg in raw_messages:
                if msg.content.startswith("/") or msg.content.startswith(bot.command_prefix) or msg.id == status_msg.id or (not msg.content and not msg.attachments):
                    continue
                role = "assistant" if msg.author == bot.user else "user"
                username = msg.author.name.replace(" ", "_")

                if not msg.attachments:
                    conversation_history.append({"role": role, "name": username, "content": msg.content})
                else:
                    items = [{"type": "text", "text": msg.content}] if msg.content else []
                    for doc in msg.attachments:
                        img_uri = await get_image_base64(doc)
                        if img_uri:
                            items.append({"type": "image_url", "image_url": {"url": img_uri}})
                    if items:
                        conversation_history.append({"role": role, "name": username, "content": items})

            payload = {"model": MODEL_NAME, "messages": conversation_history}
            response_text = await fetch_openrouter_completion(payload)
            tracker.stop()

            if len(response_text) > 2000:
                response_text = response_text[:1990] + "..."

            _, clean_text = extract_reasoning(response_text)
            view = GenerationView(MODEL_NAME, conversation_history, response_text, show_reasoning=False)
            await status_msg.edit(content=clean_text, embed=None, view=view)

        except Exception as e:
            tracker.stop()
            print(f"Exception handled in running channel thread loop: {e}")
            await status_msg.edit(content="Runtime execution error encountered inside execution thread.", embed=None)
        return

    await bot.process_commands(message)

# ----------------------------------------------------------------
# COMPONENT UTILITY APPLICATION COMMANDS
# ----------------------------------------------------------------
@bot.tree.command(name="prompt", description="Interface with core model matrix directly.")
@app_commands.describe(question="Input prompt query details", reasoning="True renders system chain-of-thought metrics")
async def prompt(interaction: discord.Interaction, question: str, reasoning: bool = False):
    await interaction.response.defer()
    embed = discord.Embed(title="Processing Neural Tracks...", color=discord.Color.blue())
    embed.description = "⏱️ **Live execution ticker:** 0.00s"
    status_msg = await interaction.followup.send(embed=embed, wait=True)
    
    tracker = GenerationTracker(interaction.channel, initial_msg=status_msg)
    tracker.start()

    try:
        if not await is_content_safe(question):
            tracker.stop()
            warn = discord.Embed(title="⚠️ Request Flagged", description="Execution halted by content safety matrix.", color=discord.Color.red())
            await status_msg.edit(embed=warn)
            return

        ctx_history = [{"role": "system", "content": GLOBAL_INSTRUCTION}]
        raw_msgs = [m async for m in interaction.channel.history(limit=25)]
        raw_msgs.reverse()

        for m in raw_msgs:
            if m.content.startswith("/") or m.content.startswith(bot.command_prefix) or m.id == status_msg.id or (not m.content and not m.attachments):
                continue
            role = "assistant" if m.author == bot.user else "user"
            ctx_history.append({"role": role, "name": m.author.name.replace(" ", "_"), "content": m.content})

        ctx_history.append({"role": "user", "content": question})
        payload = {"model": MODEL_NAME, "messages": ctx_history}
        
        response_text = await fetch_openrouter_completion(payload)
        tracker.stop()

        if len(response_text) > 2000:
            response_text = response_text[:1990] + "..."

        thoughts, clean_text = extract_reasoning(response_text)
        prefix = f"**Prompt Input Query:** {question}\n\n"
        
        view = GenerationView(MODEL_NAME, ctx_history, response_text, prefix=prefix, show_reasoning=reasoning)
        embeds = [discord.Embed(title="🧠 System Evaluation Process Tracker", description=thoughts, color=discord.Color.dark_grey())] if reasoning and thoughts else []

        await status_msg.edit(content=f"{prefix}{clean_text}", embeds=embeds, view=view)
    except Exception as e:
        tracker.stop()
        print(f"Exception handling active prompt context instruction array: {e}")
        await status_msg.edit(content="Core array execution terminated unexpectedly.", embed=None)

@bot.tree.command(name="clear", description="Purge text context sequences within limitations safely.")
@app_commands.describe(amount="Quantity limits targeting purge actions")
async def clear(interaction: discord.Interaction, amount: int = 1000):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("Security clearance validation failed.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        purged = await interaction.channel.purge(limit=amount, bulk=True)
        await interaction.followup.send(f"Operation successful. Purged {len(purged)} message structures.", ephemeral=True)
    except Exception as e:
        print(f"Purge runtime failure tracking block: {e}")
        await interaction.followup.send("Execution sequence failed.", ephemeral=True)

@bot.tree.command(name="help", description="Displays configuration settings mapping indexes.")
async def help_command(interaction: discord.Interaction):
    ch = f"<#{bot.dynamic_ai_chat_channel_id}>" if bot.dynamic_ai_chat_channel_id else "`#ai-chat`"
    embed = discord.Embed(title="ServAuth Core Interface Operating Guide", color=discord.Color.purple())
    embed.add_field(name="🧠 Matrix Operations", value=f"**Real-Time Thread Context Parsing:** Available in {ch}.\n**System Application Query Interface:** `/prompt`", inline=False)
    embed.add_field(name="⚙️ App Hooks (/) ", value="`/prompt [question] [reasoning]`\n`/clear [amount]`\n`/help`", inline=False)
    embed.set_footer(text=f"Engine Array: {MODEL_NAME}")
    await interaction.response.send_message(embed=embed)

@bot.command()
async def ping(ctx):
    await ctx.send(f"Status Verify verified: 🏓 ({round(bot.latency * 1000)}ms)")

@bot.command()
async def about(ctx):
    embed = discord.Embed(title="ServAuth Identity Engine Configuration Profiles", description="Operating system automated script structures running on independent framework stacks.", color=discord.Color.from_rgb(139, 90, 43))
    embed.add_field(name="Deployment Engine Runtime Core", value=f"`{MODEL_NAME}`", inline=True)
    embed.set_image(url="https://files.catbox.moe/7xiuy9.gif")
    await ctx.send(embed=embed)

bot.run(DISCORD_TOKEN)
