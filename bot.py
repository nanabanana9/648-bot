import os
import discord
import aiohttp
import asyncio
from datetime import datetime, time as dt_time, timedelta
from discord.ext import commands, tasks
from dotenv import load_dotenv
from deep_translator import GoogleTranslator, MyMemoryTranslator

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ============================================================
# ⚠️ FILL THESE IN before deploying — see the comments on each.
# ============================================================

# The channel where the weekly Stronghold/Fortress reminder gets posted
# (right-click the channel in Discord -> Copy Channel ID; enable Developer
# Mode in Discord settings first if you don't see that option).
STRONGHOLD_CHANNEL_ID = 1473318818477768765

# Only channel-restricted commands (!fullseason, !teststronghold, etc.) work
# here too, in addition to STRONGHOLD_CHANNEL_ID — handy for testing without
# spamming the real channel. Can be the same as STRONGHOLD_CHANNEL_ID if you
# don't want a separate test channel.
TEST_CHANNEL_ID = 1542064247649865749

# ---------- jsonbin.io persistent storage ----------
# Render's free tier wipes local files on every redeploy/restart, so reward
# corrections (via !setfortressreward etc.) are stored in jsonbin.io instead,
# same approach as the FUX bot. Sign up free at jsonbin.io, create ONE bin
# (content can just be `{}` to start), and fill in its ID + your API key below
# (or set them as environment variables JSONBIN_API_KEY / JSONBIN_STRONGHOLD_BIN
# on Render instead of hardcoding — either works, env vars are safer).
JSONBIN_API_KEY = os.getenv('JSONBIN_API_KEY')
JSONBIN_STRONGHOLD_BIN = os.getenv('JSONBIN_STRONGHOLD_BIN', "")  # <-- REPLACE if not using an env var

# ============================================================
# End of required setup — everything below should work as-is.
# ============================================================

async def jsonbin_read(bin_id, standard):
    if not JSONBIN_API_KEY or not bin_id:
        print("⚠️ JSONBIN_API_KEY or bin ID is not set — falling back to seed data.")
        return standard
    url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
    headers = {"X-Master-Key": JSONBIN_API_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    print(f"⚠️ jsonbin.io read failed ({response.status})")
                    return standard
                data = await response.json()
                return data.get("record", standard)
    except Exception as e:
        print(f"⚠️ jsonbin.io read error: {e}")
        return standard

async def jsonbin_write(bin_id, content):
    if not JSONBIN_API_KEY or not bin_id:
        print("⚠️ JSONBIN_API_KEY or bin ID is not set — cannot save data.")
        return False
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    headers = {"X-Master-Key": JSONBIN_API_KEY, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=content) as response:
                if response.status != 200:
                    print(f"⚠️ jsonbin.io write failed ({response.status})")
                    return False
                return True
    except Exception as e:
        print(f"⚠️ jsonbin.io write error: {e}")
        return False

# ---------- Stronghold & Fortress Rotating Schedule ----------
# Same underlying 8-week pattern and reward tables as the FUX bot (same
# state, same data), but here the alliance-to-slot mapping ROTATES
# automatically every season instead of being fixed to one alliance forever.
#
# Rotation rule (confirmed 2026-08-25): each season, every alliance takes
# over the slot that belonged to the NEXT alliance in ALLIANCE_ORDER —
# FUX -> FAM's old slot, FAM -> BLA's old slot, BLA -> OXY's, OXY -> AoC's,
# AoC -> FUX's old slot (wraps around). So the alliance at position i this
# season sits in "slot" (i + season_number) % 5, where season_number counts
# how many full 8-week seasons have elapsed since REGISTRATION_SEASON_START
# (season_number = 0 for the very first season).
REGISTRATION_SEASON_START = datetime(2026, 8, 3).date()  # Monday, Week 1 of Season 0 — same as FUX
REGISTRATION_SEASON_LENGTH_WEEKS = 8

ALLIANCE_ORDER = ["FUX", "FAM", "BLA", "OXY", "AoC"]

# ----- Slot schedules (index 0-4, NOT tied to any alliance) -----
# These are exactly the FUX bot's original per-alliance schedules, just
# re-keyed by slot position (slot 0 = what was FUX's pattern in Season 0,
# slot 1 = what was FAM's, etc.) so the rotation logic can reassign them.
SLOT_FORTRESS_SCHEDULE = [
    ["4,5,9", "4,5,9", "4,5,9", "4,5,7", "4,5", "4,5", "4,5", "4,5"],           # slot 0 (originally FUX)
    ["3,7,8", "3,8", "3,8", "3,8", "3,8", "3,8,9", "3,8", "3,8,9"],             # slot 1 (originally FAM)
    ["11,12", "11,12", "7,11,12", "9,11", "11,12", "1,11,12", "11,12", "7,11,12"],  # slot 2 (originally BLA)
    ["1,6", "1,6", "1,6", "1,6,12", "1,6,9", "6,7", "1,6,10", "1,6"],           # slot 3 (originally OXY)
    ["2,10", "2,7,10", "2,10", "2,10", "2,7,10", "2,10", "2,7,9", "2,10"],      # slot 4 (originally AoC)
]

SLOT_STRONGHOLD_SCHEDULE = [
    [1, None, 4, 3, 2, 1, 1, 4],       # slot 0 (originally FUX)
    [2, 1, 2, 4, 3, 2, None, None],    # slot 1 (originally FAM)
    [3, 2, 1, None, 4, 3, 2, 1],       # slot 2 (originally BLA)
    [4, 3, None, 1, None, 4, 4, 2],    # slot 3 (originally OXY)
    [None, 4, 3, 2, 1, None, 3, 3],    # slot 4 (originally AoC)
]

# Rewards by Fort/Stronghold NUMBER — unchanged from the FUX bot, since
# rewards are tied to the number, not to which alliance holds it.
FORTRESS_REWARDS = {
    1:  ["Eleonora shards", "Health Buff", "General speedup", "Advanced teleports", "Common wild mark", "Health Buff", "General speedup", "Hero gear XP"],
    2:  ["Advanced teleports", "Eleonora shards", "Health Buff", "Hero gear XP", "Hero gear XP", "Common wild mark", "Health Buff", "General speedup"],
    3:  ["General speedup", "Advanced teleports", "Eleonora shards", "Health Buff", "General speedup", "Hero gear XP", "Common wild mark", "Health Buff"],
    4:  ["Health Buff", "General speedup", "Advanced teleports", "Common wild mark", "Advanced teleports", "General speedup", "Hero gear XP", "Common wild mark"],
    5:  ["Eleonora shards", "Lethality buff", "General speedup", "Hero gear XP", "Common wild mark", "Lethality buff", "General speedup", "Hero gear XP"],
    6:  ["Advanced teleports", "Eleonora shards", "Lethality buff", "General speedup", "Hero gear XP", "Common wild mark", "Lethality buff", "General speedup"],
    7:  ["General speedup", "Advanced teleports", "Eleonora shards", "Advanced teleports", "General speedup", "Hero gear XP", "Common wild mark", "Lethality buff"],
    8:  ["Lethality buff", "General speedup", "Advanced teleports", "Common wild mark", "Advanced teleports", "General speedup", "Hero gear XP", "Common wild mark"],
    9:  ["Eleonora shards", "Deployment buff", "General speedup", "Hero gear XP", "Common wild mark", "Deployment buff", "General speedup", "Hero gear XP"],
    10: ["Advanced teleports", "Eleonora shards", "Deployment buff", "Lethality buff", "Hero gear XP", "Common wild mark", "Deployment buff", "General speedup"],
    11: ["General speedup", "Advanced teleports", "Eleonora shards", "Common wild mark", "General speedup", "Hero gear XP", "Common wild mark", "Deployment buff"],
    12: ["Deployment buff", "General speedup", "Advanced teleports", "General speedup", "Advanced teleports", "General speedup", "Hero gear XP", "Common wild mark"],
}

STRONGHOLD_REWARDS = {
    1: ["Lloyd shards", "Pet chest", "Hero gear chest", "Lloyd shards", "Lloyd shards", "Pet chest", "Hero gear chest", "Lloyd shards"],
    2: ["Fire crystal", "Lloyd shards", "Pet chest", "Fire crystal", "Fire crystal", "Lloyd shards", "Pet chest", "Fire crystal"],
    3: ["Hero gear chest", "Fire crystal", "Lloyd shards", "Pet chest", "Hero gear chest", "Fire crystal", "Fire crystal", "Pet chest"],
    4: ["Pet chest", "Hero gear chest", "Fire crystal", "Lloyd shards", "Pet chest", "Hero gear chest", "Lloyd shards", "Lloyd shards"],
}

# ----- Persist to jsonbin (seed on first run, load overrides after) -----
async def load_stronghold_season_data():
    global SLOT_FORTRESS_SCHEDULE, SLOT_STRONGHOLD_SCHEDULE, FORTRESS_REWARDS, STRONGHOLD_REWARDS
    seed = {
        "slot_fortress_schedule": SLOT_FORTRESS_SCHEDULE,
        "slot_stronghold_schedule": SLOT_STRONGHOLD_SCHEDULE,
        "fortress_rewards": {str(k): v for k, v in FORTRESS_REWARDS.items()},
        "stronghold_rewards": {str(k): v for k, v in STRONGHOLD_REWARDS.items()},
    }
    data = await jsonbin_read(JSONBIN_STRONGHOLD_BIN, {})
    if not data.get("slot_fortress_schedule"):
        await jsonbin_write(JSONBIN_STRONGHOLD_BIN, seed)
        return
    if data.get("slot_fortress_schedule"):
        SLOT_FORTRESS_SCHEDULE = data["slot_fortress_schedule"]
    if data.get("slot_stronghold_schedule"):
        SLOT_STRONGHOLD_SCHEDULE = data["slot_stronghold_schedule"]
    if data.get("fortress_rewards"):
        FORTRESS_REWARDS = {int(k): v for k, v in data["fortress_rewards"].items()}
    if data.get("stronghold_rewards"):
        STRONGHOLD_REWARDS = {int(k): v for k, v in data["stronghold_rewards"].items()}

async def save_stronghold_season_data():
    data = {
        "slot_fortress_schedule": SLOT_FORTRESS_SCHEDULE,
        "slot_stronghold_schedule": SLOT_STRONGHOLD_SCHEDULE,
        "fortress_rewards": {str(k): v for k, v in FORTRESS_REWARDS.items()},
        "stronghold_rewards": {str(k): v for k, v in STRONGHOLD_REWARDS.items()},
    }
    return await jsonbin_write(JSONBIN_STRONGHOLD_BIN, data)

def get_fortress_reward(fort_number: int, week_index: int):
    rewards = FORTRESS_REWARDS.get(fort_number)
    return rewards[week_index] if rewards else None

def get_stronghold_reward(sh_number: int, week_index: int):
    rewards = STRONGHOLD_REWARDS.get(sh_number)
    return rewards[week_index] if rewards else None

def get_season_and_week(today=None):
    """Returns (season_number, week_within_season). season_number=0 is the
    very first season (starting REGISTRATION_SEASON_START); it increments by
    1 every 8 weeks after that, driving the rotation below."""
    if today is None:
        today = datetime.now().date()
    days_elapsed = (today - REGISTRATION_SEASON_START).days
    if days_elapsed < 0:
        return 0, 0
    total_weeks = days_elapsed // 7
    season_number = total_weeks // REGISTRATION_SEASON_LENGTH_WEEKS
    week_within_season = total_weeks % REGISTRATION_SEASON_LENGTH_WEEKS
    return season_number, week_within_season

def get_alliance_slot(alliance_tag: str, season_number: int) -> int:
    """Which slot (0-4) this alliance sits in for a given season, per the
    rotation rule: each alliance moves to the position held by the NEXT
    alliance in ALLIANCE_ORDER, every season, wrapping around."""
    position = ALLIANCE_ORDER.index(alliance_tag)
    return (position + season_number) % len(ALLIANCE_ORDER)

def build_stronghold_embed(season_number: int, week_index: int, test: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=("🏰 [TEST] " if test else "🏰 ") + f"Stronghold & Fortress Reminder — Season {season_number + 1}, Week {week_index + 1}/{REGISTRATION_SEASON_LENGTH_WEEKS}",
        description="Register your alliance's Stronghold/Fortress assignments for this week!",
        color=discord.Color.gold(),
    )
    for tag in ALLIANCE_ORDER:
        slot = get_alliance_slot(tag, season_number)
        forts_str = SLOT_FORTRESS_SCHEDULE[slot][week_index]
        fort_numbers = [int(n) for n in forts_str.split(",")]
        fort_lines = []
        for n in fort_numbers:
            reward = get_fortress_reward(n, week_index)
            fort_lines.append(f"Fort {n}: {reward if reward else 'TBD'}")
        embed.add_field(name=f"🏯 {tag} — Fortress", value="\n".join(fort_lines), inline=False)

        sh_number = SLOT_STRONGHOLD_SCHEDULE[slot][week_index]
        if sh_number is not None:
            sh_reward = get_stronghold_reward(sh_number, week_index)
            sh_display = f"SH {sh_number}: {sh_reward if sh_reward else 'TBD'}"
        else:
            sh_display = "Sits out this week"
        embed.add_field(name=f"🏰 {tag} — Stronghold", value=sh_display, inline=False)
    return embed

async def send_stronghold_reminder(channel, ping_everyone: bool = True, test: bool = False):
    season_number, week_index = get_season_and_week()
    content = "@everyone" if ping_everyone else None
    await channel.send(content=content, embed=build_stronghold_embed(season_number, week_index, test=test))

# Anchored to a Thursday (multiple of 7 days from the season start) so every
# future reminder lands on a Thursday automatically.
STRONGHOLD_REMINDER_START_DATE = datetime(2026, 8, 6).date()
STRONGHOLD_REMINDER_INTERVAL_DAYS = 7

@tasks.loop(time=dt_time(hour=18, minute=0))
async def check_stronghold_reminder():
    try:
        today = datetime.now().date()
        if today < STRONGHOLD_REMINDER_START_DATE:
            return
        if (today - STRONGHOLD_REMINDER_START_DATE).days % STRONGHOLD_REMINDER_INTERVAL_DAYS == 0:
            channel = bot.get_channel(STRONGHOLD_CHANNEL_ID)
            if not channel:
                return
            await send_stronghold_reminder(channel)
    except Exception as e:
        print(f"Stronghold/Fortress reminder check background error: {e}")

def build_fullseason_embed(tag: str, season_number: int) -> discord.Embed:
    slot = get_alliance_slot(tag, season_number)
    embed = discord.Embed(
        title=f"📅 {tag} — Full Season {season_number + 1} Schedule (8 Weeks)",
        description="Fortress & Stronghold assignments and rewards for every week this season.",
        color=discord.Color.gold(),
    )
    for week_index in range(REGISTRATION_SEASON_LENGTH_WEEKS):
        forts_str = SLOT_FORTRESS_SCHEDULE[slot][week_index]
        fort_numbers = [int(n) for n in forts_str.split(",")]
        lines = [f"Fort {n}: {get_fortress_reward(n, week_index)}" for n in fort_numbers]

        sh_number = SLOT_STRONGHOLD_SCHEDULE[slot][week_index]
        if sh_number is not None:
            lines.append(f"SH {sh_number}: {get_stronghold_reward(sh_number, week_index)}")
        else:
            lines.append("Stronghold: sits out this week")

        embed.add_field(name=f"Week {week_index + 1}", value="\n".join(lines), inline=False)
    return embed

@bot.command()
async def fullseason(ctx, alliance: str = None):
    """
    Show the full 8-week Fortress & Stronghold schedule and rewards for the
    CURRENT season, either for one alliance or every alliance at once.
    Usage: !fullseason <FUX|FAM|BLA|OXY|AoC|all>
    """
    season_number, _ = get_season_and_week()

    if not alliance or alliance.lower() == "all":
        embeds = [build_fullseason_embed(t, season_number) for t in ALLIANCE_ORDER]
        await ctx.send(embeds=embeds)
        return

    tag = next((t for t in ALLIANCE_ORDER if t.upper() == alliance.upper()), None)
    if not tag:
        await ctx.send(f"❌ Please specify a valid alliance: {', '.join(ALLIANCE_ORDER)}, or `all` for every alliance.\nUsage: `!fullseason <alliance|all>`")
        return

    await ctx.send(embed=build_fullseason_embed(tag, season_number))

@bot.command()
@commands.has_permissions(administrator=True)
async def teststronghold(ctx):
    """Preview the weekly reminder right now, for testing. No @everyone ping."""
    if ctx.channel.id not in (STRONGHOLD_CHANNEL_ID, TEST_CHANNEL_ID):
        await ctx.send("❌ Wrong channel!")
        return
    await send_stronghold_reminder(ctx.channel, ping_everyone=False, test=True)

@bot.command()
@commands.has_permissions(administrator=True)
async def setfortressreward(ctx, fort_number: int, week: int, *, reward: str):
    """
    Correct one Fort's reward for a specific week (1-8) — saved permanently,
    no code change or redeploy needed. Applies to whichever alliance holds
    that fort in a given week, automatically.
    Usage: !setfortressreward 4 2 General speedup
    """
    if ctx.channel.id not in (STRONGHOLD_CHANNEL_ID, TEST_CHANNEL_ID):
        await ctx.send("❌ Wrong channel!")
        return
    if fort_number not in FORTRESS_REWARDS:
        await ctx.send(f"❌ Fort {fort_number} isn't a valid fort number (1-12).")
        return
    if not 1 <= week <= REGISTRATION_SEASON_LENGTH_WEEKS:
        await ctx.send(f"❌ Week must be between 1 and {REGISTRATION_SEASON_LENGTH_WEEKS}.")
        return
    FORTRESS_REWARDS[fort_number][week - 1] = reward
    saved = await save_stronghold_season_data()
    if saved:
        await ctx.send(f"✅ Fort {fort_number}, Week {week} is now: **{reward}**")
    else:
        await ctx.send("⚠️ Updated in memory, but saving to storage failed — check Render's logs.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setstrongholdreward(ctx, sh_number: int, week: int, *, reward: str):
    """
    Correct one Stronghold's reward for a specific week (1-8) — saved
    permanently, no code change or redeploy needed.
    Usage: !setstrongholdreward 1 4 Pet chest
    """
    if ctx.channel.id not in (STRONGHOLD_CHANNEL_ID, TEST_CHANNEL_ID):
        await ctx.send("❌ Wrong channel!")
        return
    if sh_number not in STRONGHOLD_REWARDS:
        await ctx.send(f"❌ Stronghold {sh_number} isn't a valid number (1-4).")
        return
    if not 1 <= week <= REGISTRATION_SEASON_LENGTH_WEEKS:
        await ctx.send(f"❌ Week must be between 1 and {REGISTRATION_SEASON_LENGTH_WEEKS}.")
        return
    STRONGHOLD_REWARDS[sh_number][week - 1] = reward
    saved = await save_stronghold_season_data()
    if saved:
        await ctx.send(f"✅ Stronghold {sh_number}, Week {week} is now: **{reward}**")
    else:
        await ctx.send("⚠️ Updated in memory, but saving to storage failed — check Render's logs.")

# ---------- Globe Translator ----------
# React with 🌐 under any message (including the bot's own embeds) to get a
# private DM dropdown to pick a language. Fully working version — same fixes
# as the FUX bot: raw reaction events (works regardless of message cache or
# repeat taps), embed-text extraction, and a MyMemory fallback (with a fixed
# English source, since MyMemory doesn't support 'auto') for when Google's
# scraper-based translator gets blocked by a cloud IP.

TRANSLATE_FLAGS = {
    "🇺🇸": "en",
    "🇳🇴": "no",
    "🇪🇸": "es",
    "🇫🇷": "fr",
    "🇩🇪": "de",
    "🇹🇷": "tr",
    "🇩🇰": "da",
    "🇮🇱": "iw",
    "🇵🇱": "pl",
    "🇷🇴": "ro",
    "🇮🇹": "it",
    "🇷🇺": "ru",
}

SPRAK_NAVN = {
    "en": "English", "no": "Norsk", "es": "Español", "fr": "Français",
    "de": "Deutsch", "tr": "Türkçe", "da": "Dansk", "iw": "Hebrew",
    "pl": "Polski", "ro": "Română", "it": "Italiano", "ru": "Русский",
}

GLOBUS_EMOJI = "🌐"

MYMEMORY_CODE_MAP = {
    "en": "en-GB", "no": "nb-NO", "es": "es-ES", "fr": "fr-FR", "de": "de-DE",
    "tr": "tr-TR", "da": "da-DK", "iw": "he-IL", "pl": "pl-PL", "ro": "ro-RO",
    "it": "it-IT", "ru": "ru-RU",
}

def looks_like_translation_failure(text: str) -> bool:
    if not text or not text.strip():
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in [
        "error 500", "server error", "that's an error", "that’s an error",
        "invalid source language", "invalid target language", "no translation was found",
    ])

async def translate_with_fallback(text: str, target_code: str) -> str:
    try:
        result = GoogleTranslator(source='auto', target=target_code).translate(text)
        if result and not looks_like_translation_failure(result):
            return result
    except Exception:
        pass

    mymemory_code = MYMEMORY_CODE_MAP.get(target_code, target_code)
    try:
        result = MyMemoryTranslator(source='en-GB', target=mymemory_code).translate(text)
        if result and not looks_like_translation_failure(result):
            return result
    except Exception:
        pass

    raise RuntimeError("Both translation providers failed or returned an error page — try again shortly.")

class LanguageSelect(discord.ui.Select):
    def __init__(self, original_text: str, original_author_name: str):
        self.original_text = original_text
        self.original_author_name = original_author_name
        options = [
            discord.SelectOption(label=SPRAK_NAVN.get(code, code), value=code, emoji=flagg)
            for flagg, code in TRANSLATE_FLAGS.items()
        ]
        super().__init__(placeholder="Choose language / Velg språk", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        code = self.values[0]
        text_to_translate = self.original_text
        if len(text_to_translate) > 4500:
            text_to_translate = text_to_translate[:4500] + "..."

        try:
            oversatt = await translate_with_fallback(text_to_translate, code)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Translation service is temporarily unavailable: {e}", ephemeral=True)
            return

        if len(oversatt) > 4096:
            oversatt = oversatt[:4093] + "..."

        embed = discord.Embed(description=oversatt, color=discord.Color.blurple())
        embed.set_footer(text=f"Translated to {SPRAK_NAVN.get(code, code)} • original by {self.original_author_name}")
        await interaction.response.edit_message(content=None, embed=embed, view=None)

class LanguageSelectView(discord.ui.View):
    def __init__(self, original_text: str, original_author_name: str):
        super().__init__(timeout=120)
        self.add_item(LanguageSelect(original_text, original_author_name))

def get_translatable_text(message) -> str:
    if message.content and message.content.strip():
        return message.content
    parts = []
    for embed in message.embeds:
        if embed.title:
            parts.append(str(embed.title))
        if embed.description:
            parts.append(str(embed.description))
        for field in embed.fields:
            if field.name:
                parts.append(str(field.name))
            if field.value:
                parts.append(str(field.value))
    return "\n".join(parts)

@bot.listen('on_message')
async def auto_add_globe_reaction(message):
    if message.author.bot and message.author.id != bot.user.id:
        return
    if message.guild is None:
        return
    if message.content.startswith(bot.command_prefix):
        return
    if not get_translatable_text(message):
        return
    try:
        await message.add_reaction(GLOBUS_EMOJI)
    except (discord.Forbidden, discord.HTTPException):
        pass

async def _handle_globe_reaction_payload(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != GLOBUS_EMOJI:
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(payload.channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    try:
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    user = bot.get_user(payload.user_id)
    if user is None:
        try:
            user = await bot.fetch_user(payload.user_id)
        except discord.HTTPException:
            return
    if user.bot:
        return

    original_text = get_translatable_text(message)
    if not original_text or not original_text.strip():
        return

    view = LanguageSelectView(original_text, message.author.display_name)
    try:
        await user.send("🌐 Choose the language you want this message translated to:", view=view)
    except discord.Forbidden:
        try:
            await message.reply(
                content=f"{user.mention} please enable DMs from server members to use the translator.",
                mention_author=False,
            )
        except Exception:
            pass

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await _handle_globe_reaction_payload(payload)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    # Tapping an emoji you've already reacted with REMOVES it instead of
    # adding it (Discord UI behavior) — handling this event too means a
    # second tap on the same message still works instead of doing nothing.
    await _handle_globe_reaction_payload(payload)

# ---------- Help ----------

@bot.command(name="help", aliases=["commands"])
async def help_command(ctx):
    embed = discord.Embed(
        title="📖 State Bot — Commands",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🏰 Stronghold & Fortress",
        value=(
            "**!fullseason <alliance|all>** — full 8-week schedule for the current season\n"
            "🔒 **!setfortressreward <fort#> <week> <text>** — correct a Fort's reward\n"
            "🔒 **!setstrongholdreward <sh#> <week> <text>** — correct a Stronghold's reward\n"
            "🔒 **!teststronghold** — preview the weekly reminder"
        ),
        inline=False,
    )
    embed.add_field(
        name="🌐 Translator",
        value="React with 🌐 on any message to get a private language-picker DM.",
        inline=False,
    )
    embed.set_footer(text="🔒 = admin only")
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f'Success! The bot is online as {bot.user.name}')
    await load_stronghold_season_data()
    if not check_stronghold_reminder.is_running():
        check_stronghold_reminder.start()

@bot.event
async def on_command_error(ctx, error):
    import traceback
    print(f"⚠️ Command error in '{ctx.command}': {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing an argument: `{error.param.name}`. Check the command syntax!")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Wrong argument type given to the command!")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command!")
    else:
        await ctx.send(f"⚠️ Something went wrong: `{error}`")

def run_bot_with_backoff():
    """
    Starts the bot. If Discord responds with a rate limit (429), waits 10
    minutes before letting Render restart the process — a shorter wait lets
    the bot keep reconnecting every ~60-90 seconds and can retrigger/extend
    the block before it clears (this bit the FUX bot for real on 2026-08-25).
    """
    import time
    try:
        bot.run(TOKEN)
    except discord.errors.HTTPException as e:
        if e.status == 429:
            print("⚠️ Rate limited by Discord (429). Waiting 10 minutes before allowing restart...")
            time.sleep(600)
        else:
            print(f"⚠️ HTTP error from Discord: {e}. Waiting 60 seconds before allowing restart...")
            time.sleep(60)
    except Exception as e:
        print(f"⚠️ Unexpected startup error: {e}. Waiting 60 seconds before allowing restart...")
        time.sleep(60)

run_bot_with_backoff()
