# Original Code:
import asyncio
import json
import math
import os
import traceback
import urllib.parse
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import asyncpg
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks
from pypdf import PdfReader


# ═══════════════════════════════════════════════════════════════
#  PATHS & ENVIRONMENT
# ═══════════════════════════════════════════════════════════════

PORT            = int(os.getenv("PORT", "8080"))
TOKEN           = os.getenv("DISCORD_TOKEN", "")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
OWNER_ID        = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL    = os.getenv("DATABASE_URL", "")
REPEATERBOOK_API_KEY = os.getenv("REPEATERBOOK_API_KEY", "")
NOAA_GEOMAG_API_KEY  = os.getenv("NOAA_GEOMAG_API_KEY", "")
FINDAHELPLINE_API_KEY = os.getenv("FINDAHELPLINE_API_KEY", "")



# ═══════════════════════════════════════════════════════════════
#  COLOURS & CORE CONSTANTS
# ═══════════════════════════════════════════════════════════════

OREGON_GREEN = discord.Color.from_rgb(34, 77, 23)
SAR_ORANGE   = discord.Color.from_rgb(255, 85, 0)
ERROR_RED    = discord.Color.from_rgb(180, 30, 30)
NIGHT_BLUE   = discord.Color.from_rgb(30, 34, 62)

AI_WARNING_FOOTER = "⚠️ Telemetry: AI usage at {count}/24 for today."
AI_LIMIT       = 24
DEFAULT_THEME_ID = "ranger"
VALID_MODES    = ("regular", "stealth", "blackbox")


# ═══════════════════════════════════════════════════════════════
#  THEME PRESETS
# ═══════════════════════════════════════════════════════════════

THEME_PRESETS = [
	{
		"id": "ranger",
		"label": "Normal Ranger",
		"emoji": "🟩",
		"color": discord.Color.from_rgb(34, 77, 23),
		"persona": "You are a calm, competent backcountry ranger. Be practical, clear, and reliable. Favor concise field notes and operational clarity.",
		"accent": "forest",
	},
	{
		"id": "rugged",
		"label": "Rugged",
		"emoji": "🪵",
		"color": discord.Color.from_rgb(92, 64, 51),
		"persona": "You are a rugged trail veteran. Speak plainly, keep details durable and field-tested, and avoid fluff.",
		"accent": "earth",
	},
	{
		"id": "preppy",
		"label": "Preppy",
		"emoji": "✨",
		"color": discord.Color.from_rgb(255, 105, 180),
		"persona": "You are upbeat, polished, and stylish. Keep the tone energetic and supportive while staying useful.",
		"accent": "rose",
	},
	{
		"id": "pride",
		"label": "Pride",
		"emoji": "🏳️‍🌈",
		"color": discord.Color.from_rgb(255, 80, 120),
		"persona": "You are warm, affirming, and inclusive. Stay encouraging, direct, and celebratory without getting saccharine.",
		"accent": "pride",
	},
	{
		"id": "alpine",
		"label": "Alpine",
		"emoji": "🏔️",
		"color": discord.Color.from_rgb(68, 110, 165),
		"persona": "You are high-elevation, crisp, and cool-headed. Use clean mountain ops language and a steady voice.",
		"accent": "ice",
	},
	{
		"id": "night_ops",
		"label": "Night Ops",
		"emoji": "🌙",
		"color": discord.Color.from_rgb(30, 34, 62),
		"persona": "You are low-light, stealthy, and deliberate. Keep responses compact, sharp, and tactical.",
		"accent": "midnight",
	},
	{
		"id": "radio_gear",
		"label": "Radio Gear",
		"emoji": "📻",
		"color": discord.Color.from_rgb(224, 133, 0),
		"persona": "You are a meticulous radio operator. Think in frequencies, signal paths, antenna setup, and concise comms discipline.",
		"accent": "amber",
	},
	{
		"id": "trail_medic",
		"label": "Trail Medic",
		"emoji": "⛑️",
		"color": discord.Color.from_rgb(170, 42, 42),
		"persona": "You are a calm EMT-minded trail medic. Prioritize safety, scene awareness, and simple field triage language. Never diagnose.",
		"accent": "red",
	},
	{
		"id": "fire_lookout",
		"label": "Fire Lookout",
		"emoji": "🔥",
		"color": discord.Color.from_rgb(184, 74, 24),
		"persona": "You are an observant fire lookout. Be vigilant, concise, and detail oriented with weather, smoke, and terrain awareness.",
		"accent": "ember",
	},
	{
		"id": "storm_watch",
		"label": "Storm Watch",
		"emoji": "⛈️",
		"color": discord.Color.from_rgb(56, 92, 140),
		"persona": "You are a weather watcher tracking pressure, cloud cover, and storm timing. Respond with crisp forecasts and alert language.",
		"accent": "storm",
	},
]

THEME_BY_ID = {t["id"]: t for t in THEME_PRESETS}

RED_FLAG_KEYWORDS = {
	"chest pain", "shortness of breath", "sob", "unresponsive",
	"altered mental", "stroke", "facial droop", "slurred speech",
	"seizure", "seizing", "major bleed", "massive bleeding",
	"anaphylaxis", "shock", "burn", "airway", "cyanosis",
}


# ═══════════════════════════════════════════════════════════════
#  SECURITY VOLATILE STORES
# ═══════════════════════════════════════════════════════════════

_TEMP_ALLOWED = set()
GATE_PASSPHRASE = os.getenv("GATE_PASSPHRASE", "SQUELCH0022")


# ═══════════════════════════════════════════════════════════════
#  UTILITY HELPERS
# ═══════════════════════════════════════════════════════════════

def utc_now() -> datetime:
	return datetime.now(timezone.utc)


def utc_date_key() -> str:
	return utc_now().date().isoformat()


def clean_text(value: str) -> str:
	return " ".join(value.split()).strip()


def truncate_text(value: str, limit: int = 4000) -> str:
	if len(value) <= limit:
		return value
	return value[: limit - 3] + "..."


def make_embed(
	title: str,
	description: str,
	color: discord.Color = OREGON_GREEN,
	timestamp: bool = False,
) -> discord.Embed:
	e = discord.Embed(title=title, description=description, color=color)
	if timestamp:
		e.timestamp = utc_now()
	return e


def scholar_url(query: str) -> str:
	return "https://scholar.google.com/scholar?q=" + urllib.parse.quote_plus(query)


def google_search_url(query: str) -> str:
	return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


def wolfram_url(query: str) -> str:
	return "https://www.wolframalpha.com/input?i=" + urllib.parse.quote_plus(query)


def stackoverflow_url(query: str) -> str:
	return "https://stackoverflow.com/search?q=" + urllib.parse.quote_plus(query)


def wikipedia_summary_url(query: str) -> str:
	safe = urllib.parse.quote(query.replace(" ", "_"), safe="")
	return f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}"


def wikipedia_desktop_url(page: str) -> str:
	return "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_"), safe="")


def decimal_to_ddm(value: float, is_latitude: bool) -> str:
	direction = "N" if is_latitude else "E"
	if value < 0:
		direction = "S" if is_latitude else "W"
	absolute_value = abs(value)
	degrees = int(absolute_value)
	minutes = (absolute_value - degrees) * 60.0
	if is_latitude:
		return f"{degrees:02d}° {minutes:06.3f}' {direction}"
	return f"{degrees:03d}° {minutes:06.3f}' {direction}"


def maidenhead_locator(latitude: float, longitude: float) -> str:
	lon = longitude + 180.0
	lat = latitude + 90.0
	field_lon = int(lon // 20)
	field_lat = int(lat // 10)
	square_lon = int((lon % 20) // 2)
	square_lat = int(lat % 10)
	subsquare_lon = int(((lon % 2) * 12))
	subsquare_lat = int(((lat % 1) * 24))
	return (
		chr(ord("A") + field_lon)
		+ chr(ord("A") + field_lat)
		+ str(square_lon)
		+ str(square_lat)
		+ chr(ord("a") + subsquare_lon)
		+ chr(ord("a") + subsquare_lat)
	)


def julian_day(moment: datetime) -> float:
	year, month, day = moment.year, moment.month, moment.day
	hour = moment.hour + (moment.minute / 60.0) + (moment.second / 3600.0)
	if month <= 2:
		year -= 1
		month += 12
	a = year // 100
	b = 2 - a + (a // 4)
	return int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524.5 + (hour / 24.0)


def ewbank_conversion(value: float) -> Tuple[str, str]:
	chart = [
		(1,"5.1","1a"),(2,"5.2","2a"),(3,"5.3","2b"),(4,"5.4","3a"),(5,"5.5","3b"),
		(6,"5.6","3c"),(7,"5.7","4a"),(8,"5.7+","4a+"),(9,"5.8","4b"),(10,"5.8+","4b+"),
		(11,"5.9-","4c-"),(12,"5.9","4c"),(13,"5.9+","4c+"),(14,"5.10a","5a"),
		(15,"5.10b","5a+"),(16,"5.10c","5b"),(17,"5.10d","5b+"),(18,"5.9","5c"),
		(19,"5.10a","6a"),(20,"5.10b","6a+"),(21,"5.10c","6b"),(22,"5.10d","6b+"),
		(23,"5.11a","6c"),(24,"5.11b","6c+"),(25,"5.11c","7a"),(26,"5.11d","7a+"),
		(27,"5.12a","7b"),(28,"5.12b","7b+"),(29,"5.12c","7c"),(30,"5.12d","7c+"),
		(31,"5.13a","8a"),
	]
	rounded = max(1, min(31, int(round(value))))
	for grade, yds, french in reversed(chart):
		if rounded >= grade:
			return yds, french
	return "5.1", "1a"


def parse_query_location(value: str) -> Optional[Tuple[float, float]]:
	cleaned = clean_text(value)
	if "," not in cleaned:
		return None
	left, right = [p.strip() for p in cleaned.split(",", 1)]
	try:
		return float(left), float(right)
	except ValueError:
		return None


def approximate_declination(latitude: float, longitude: float) -> float:
	base = 14.0 * math.sin(math.radians(longitude)) * math.cos(math.radians(latitude / 2.0))
	seasonal = 1.5 * math.sin(math.radians(latitude + longitude / 2.0))
	return max(-30.0, min(30.0, base + seasonal))


def morse_encode(text: str) -> str:
	table = {
		"a":".-","b":"-...","c":"-.-.","d":"-..","e":".","f":"..-.","g":"--.","h":"....","i":"..","j":".---",
		"k":"-.-","l":".-..","m":"--","n":"-.","o":"---","p":".--.","q":"--.-","r":".-.","s":"...","t":"-",
		"u":"..-","v":"...-","w":".--","x":"-..-","y":"-.--","z":"--..",
		"0":"-----","1":".----","2":"..---","3":"...--","4":"....-","5":".....",
		"6":"-....","7":"--...","8":"---..","9":"----.",
		".":".-.-.-",",":"--..--","?":"..--..","/":" -..-.","-":"-....-","(":"-.--.",")":"-.--.-",
	}
	encoded: List[str] = []
	for ch in text.lower():
		encoded.append("/" if ch == " " else table.get(ch, "?"))
	return " ".join(encoded)


def repeater_profile(location: str, latitude: Optional[float] = None, longitude: Optional[float] = None) -> Dict[str, str]:
	seed = sum(ord(c) for c in location)
	if latitude is not None and longitude is not None:
		seed += int(abs(latitude) * 1000) + int(abs(longitude) * 1000)
	vhf = 146.52 + ((seed % 35) * 0.02)
	if vhf > 147.39:
		vhf = 146.52 + ((seed % 30) * 0.02)
	offset = -0.6 if seed % 2 == 0 else 0.6
	tone_pool = [67.0,71.9,77.0,82.5,88.5,94.8,100.0,103.5,110.9,114.8,123.0,131.8,136.5,141.3]
	tone = tone_pool[seed % len(tone_pool)]
	callsign = f"{['K','N','W','A'][seed % 4]}{seed % 9}{['SAR','RPT','RNG','SCL','MTR'][seed % 5]}"
	coverage = ["ridge-shadowed valley relay","wide-area hilltop coverage","trailhead-to-ridge simplex relay","coastal overlook coverage","backcountry cross-band patch point"][seed % 5]
	return {
		"callsign": callsign,
		"output": f"{vhf:.2f} MHz",
		"input": f"{vhf + offset:.2f} MHz",
		"offset": f"{offset:+.1f} MHz",
		"tone": f"{tone:.1f} Hz",
		"coverage": coverage,
	}


def medical_red_flags(text: str) -> List[str]:
	lower = text.lower()
	return [kw for kw in RED_FLAG_KEYWORDS if kw in lower]


def theme_card(theme: Dict[str, Any]) -> str:
	return f"{theme['emoji']} {theme['label']}"


def payload_preview(content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> str:
	if content:
		return truncate_text(clean_text(content), 280)
	if embed:
		parts = [p for p in [embed.title, embed.description] if p]
		return truncate_text(" | ".join(parts), 280)
	return ""


def attachment_suffix(attachment: discord.Attachment) -> str:
	fn = attachment.filename.lower()
	if fn.endswith(".md") or fn.endswith(".markdown"):
		return "md"
	if fn.endswith(".pdf"):
		return "pdf"
	if fn.endswith(".txt"):
		return "txt"
	return fn.rsplit(".", 1)[-1] if "." in fn else ""


def study_system_prompt(theme: Dict[str, Any]) -> str:
	return (
		f"You are Squelch Study Ops in the {theme['label']} theme. {theme['persona']}\n"
		"Act as a study coach for Obsidian and field notes. Produce markdown only with these sections: Summary, Key Terms, Flashcards, Quick Quiz, Memory Hooks, Next Review.\n"
		"Keep it accurate, concise, and practical. If source notes are provided, use them heavily. Do not hallucinate citations."
	)


def build_study_source_text(filename: str, source_text: str) -> str:
	return f"SOURCE FILE: {filename}\n\n{source_text.strip()}"


def triage_summary_embed(category: str, recommendation: str, query: str) -> discord.Embed:
	return make_embed(
		f"📡 TRIAGE: {category.upper()}",
		f"**Input:** {query}\n\n**Route:** {recommendation}",
		OREGON_GREEN,
	)


def fieldmed_report(chief_complaint: str, age: Optional[int], vitals: Optional[str], mechanism: Optional[str]) -> discord.Embed:
	flags = medical_red_flags(chief_complaint + " " + (vitals or "") + " " + (mechanism or ""))
	red_flag_text = ", ".join(flags) if flags else "none detected from keywords"
	checklist = [
		"Scene safety and PPE",
		"Determine responsiveness — airway / breathing / circulation",
		"Check for major hemorrhage, stroke signs, altered mental status",
		"Obtain and trend vitals if available",
		"Escalate on chest pain, respiratory distress, uncontrolled bleeding, seizure, or shock",
	]
	if age is not None and age < 18:
		checklist.append("Pediatric considerations apply — monitor closely, involve appropriate transport")
	desc = (
		f"**Chief Complaint:** {chief_complaint}\n"
		f"**Age:** {age if age is not None else 'not provided'}\n"
		f"**Vitals:** {vitals or 'not provided'}\n"
		f"**Mechanism:** {mechanism or 'not provided'}\n\n"
		f"**🚨 Keyword Red Flags:** {red_flag_text}\n\n"
		"**⚡ EMERGENCY CONTACT CONTACT DETAILS (AU):**\n"
		"— **Emergency Dispatch:** Call `000`\n"
		"— **Poisons Info Hub:** Call `13 11 26`\n\n"
		"**Field Checklist**\n" + "\n".join(f"— {l}" for l in checklist) + "\n\n"
		"*This helper is safety-focused only and does not diagnose.*"
	)
	return make_embed("⛑️ FIELD MED TRIAGE", desc, SAR_ORANGE if flags else OREGON_GREEN)


def classify_triage(query: str) -> Tuple[str, str]:
	text = clean_text(query).lower()
	if any(k in text for k in ["gps","location","address","lat","lon","coordinate","coords"]):
		return "navigation", "Use `/gps` for coordinates and routing links."
	if any(k in text for k in ["weather","rain","storm","wind","forecast","pressure"]):
		return "weather", "Use `/weather` for meteorology comparison."
	if any(k in text for k in ["radio","repeater","vhf","uhf","comms","signal"]):
		return "comms", "Use `/repeater` or `/commslog` for radio operations and logging."
	if any(k in text for k in ["convert","grams","meters","liters","aud","ewbank"]):
		return "conversion", "Use `/convert` for unit work."
	if any(k in text for k in ["study","notes","quiz","flashcard","obsidian","pdf","markdown",".md"]):
		return "study", "Use `/study` to turn notes or files into a study pack."
	if any(k in text for k in ["remind","later","deadline","alarm"]):
		return "reminders", "Use `/reminders` to create or manage reminders."
	if any(k in text for k in ["deadrop","drop","secret","stash"]):
		return "deadrop", "Use `/deadrop` to store or retrieve a secret note."
	if any(k in text for k in ["med","injury","pain","breath","bleeding","trauma","triage"]):
		return "medical", "Use `/fieldmed` for safety-first field triage prompts."
	if any(k in text for k in ["ai","summarize","explain","write","analyze"]):
		return "ai", "Use `/ai` for general Gemini help."
	return "general", "No strong route found — `/ai` or `/study` may be the best starting point."


# ═══════════════════════════════════════════════════════════════
#  DATABASE PERSISTENCE (SUPABASE ASYNC ROUTINES)
# ═══════════════════════════════════════════════════════════════

async def load_whitelist() -> List[int]:
	ids: List[int] = []
	if OWNER_ID:
		ids.append(OWNER_ID)
	if not bot.db_pool:
		return ids
	try:
		async with bot.db_pool.acquire() as conn:
			rows = await conn.fetch("SELECT user_id FROM whitelist")
			for r in rows:
				uid = r["user_id"]
				if uid not in ids:
					ids.append(uid)
	except Exception:
		pass
	return ids


async def ai_count_for_user(user_id: int) -> int:
	today = utc_date_key()
	if not bot.db_pool:
		return 0
	async with bot.db_pool.acquire() as conn:
		row = await conn.fetchrow("SELECT date, count FROM ai_tracker WHERE user_id = $1", user_id)
	if not row or row["date"] != today:
		return 0
	return int(row["count"])


async def increment_ai_count(user_id: int) -> int:
	today = utc_date_key()
	if not bot.db_pool:
		return 0
	async with bot.db_pool.acquire() as conn:
		row = await conn.fetchrow("SELECT date, count FROM ai_tracker WHERE user_id = $1", user_id)
		if not row:
			await conn.execute("INSERT INTO ai_tracker (user_id, date, count) VALUES ($1, $2, 1)", user_id, today)
			return 1
		if row["date"] != today:
			await conn.execute("UPDATE ai_tracker SET date = $2, count = 1 WHERE user_id = $1", user_id, today)
			return 1
		new_count = int(row["count"]) + 1
		await conn.execute("UPDATE ai_tracker SET count = $2 WHERE user_id = $1", user_id, new_count)
		return new_count


def ai_footer(count: int) -> str:
	bar_filled = min(count, AI_LIMIT)
	bar = "█" * bar_filled + "░" * (AI_LIMIT - bar_filled)
	return f"⚠️ AI Usage: {count}/{AI_LIMIT}  [{bar}]"


def ai_footer_color(count: int) -> discord.Color:
	return OREGON_GREEN if count <= 20 else SAR_ORANGE


async def get_user_mode(user_id: int) -> str:
	if not bot.db_pool:
		return "regular"
	async with bot.db_pool.acquire() as conn:
		val = await conn.fetchval("SELECT mode FROM mode_state WHERE user_id = $1", user_id)
	return val or "regular"


async def get_user_theme(user_id: int) -> Dict[str, Any]:
	if not bot.db_pool:
		return THEME_BY_ID[DEFAULT_THEME_ID]
	async with bot.db_pool.acquire() as conn:
		tid = await conn.fetchval("SELECT theme_id FROM theme_state WHERE user_id = $1", user_id)
	tid = tid or DEFAULT_THEME_ID
	return THEME_BY_ID.get(tid, THEME_BY_ID[DEFAULT_THEME_ID])


async def set_user_mode(user_id: int, mode: str) -> None:
	if not bot.db_pool:
		return
	async with bot.db_pool.acquire() as conn:
		row = await conn.fetchrow("SELECT user_id FROM mode_state WHERE user_id = $1", user_id)
		if row:
			await conn.execute("UPDATE mode_state SET mode = $2 WHERE user_id = $1", user_id, mode)
		else:
			await conn.execute("INSERT INTO mode_state (user_id, mode) VALUES ($1, $2)", user_id, mode)


async def set_user_theme(user_id: int, theme_id: str) -> None:
	if not bot.db_pool:
		return
	async with bot.db_pool.acquire() as conn:
		row = await conn.fetchrow("SELECT user_id FROM theme_state WHERE user_id = $1", user_id)
		if row:
			await conn.execute("UPDATE theme_state SET theme_id = $2 WHERE user_id = $1", user_id, theme_id)
		else:
			await conn.execute("INSERT INTO theme_state (user_id, theme_id) VALUES ($1, $2)", user_id, theme_id)


async def current_visibility(user_id: int) -> bool:
	mode = await get_user_mode(user_id)
	return mode in {"stealth", "blackbox"}


# ═══════════════════════════════════════════════════════════════
#  LOGGING — CENTRALIZED DATABASE OPERATIONS
# ═══════════════════════════════════════════════════════════════

async def write_blackbox_log(line: str) -> None:
	print(line)
	if not bot.db_pool:
		return
	try:
		async with bot.db_pool.acquire() as conn:
			await conn.execute("INSERT INTO system_logs (ts, log_type, detail) VALUES ($1, 'blackbox', $2)", utc_now().isoformat(), line)
	except Exception:
		pass


async def write_error_log(line: str) -> None:
	print(f"[ERROR] {line}")
	if not bot.db_pool:
		return
	try:
		async with bot.db_pool.acquire() as conn:
			await conn.execute("INSERT INTO system_logs (ts, log_type, detail) VALUES ($1, 'error', $2)", utc_now().isoformat(), line)
	except Exception:
		pass


async def log_blackbox_event(interaction: discord.Interaction, event: str, details: str) -> None:
	if await get_user_mode(interaction.user.id) != "blackbox":
		return
	channel_label = f"channel={getattr(interaction.channel, 'id', 'dm')}"
	guild_label   = f"guild={interaction.guild_id or 'dm'}"
	user_label    = f"user={interaction.user.id}"
	await write_blackbox_log(
		f"[{utc_now().isoformat()}] {event} {user_label} {guild_label} {channel_label} :: {truncate_text(details, 800)}"
	)


async def log_blackbox_message(message: discord.Message) -> None:
	if message.author.bot:
		return
	mode = await get_user_mode(message.author.id)
	if mode != "blackbox":
		return
	await write_blackbox_log(
		f"[{utc_now().isoformat()}] message user={message.author.id} channel={message.channel.id} :: {truncate_text(message.content or '', 800)}"
	)


async def build_error_log_embed() -> discord.Embed:
	if not bot.db_pool:
		return make_embed("✅ ERROR LOG", "Database pool non-functional.", OREGON_GREEN, timestamp=True)
	try:
		async with bot.db_pool.acquire() as conn:
			rows = await conn.fetch("SELECT ts, detail FROM system_logs WHERE log_type = 'error' ORDER BY id DESC LIMIT 20")
	except Exception:
		rows = []
	if not rows:
		return make_embed("✅ ERROR LOG", "No errors logged. All systems nominal.", OREGON_GREEN, timestamp=True)
	
	lines = [f"[{r['ts']}] {r['detail']}" for r in reversed(rows)]
	embed = discord.Embed(
		title="⚡ ERROR LOG",
		description=f"```\n{truncate_text(chr(10).join(lines), 3800)}\n```",
		color=ERROR_RED,
		timestamp=utc_now(),
	)
	embed.set_footer(text=f"Showing last {len(rows)} entries")
	return embed


# ═══════════════════════════════════════════════════════════════
#  CORE SEND HELPER
# ═══════════════════════════════════════════════════════════════

async def mode_send(
	interaction: discord.Interaction,
	*,
	content: Optional[str] = None,
	embed: Optional[discord.Embed] = None,
	view: Optional[discord.ui.View] = None,
	file: Optional[discord.File] = None,
	ephemeral: Optional[bool] = None,
) -> None:
	is_ephemeral = await current_visibility(interaction.user.id) if ephemeral is None else ephemeral
	if await get_user_mode(interaction.user.id) == "blackbox":
		await write_blackbox_log(
			f"[{utc_now().isoformat()}] response user={interaction.user.id} :: {payload_preview(content, embed)}"
		)
	kwargs: Dict[str, Any] = {"ephemeral": is_ephemeral}
	if content is not None:
		kwargs["content"] = content
	if embed is not None:
		kwargs["embed"] = embed
	if view is not None:
		kwargs["view"] = view
	if file is not None:
		kwargs["file"] = file

	if interaction.response.is_done():
		await interaction.followup.send(**kwargs)
	else:
		await interaction.response.send_message(**kwargs)


def reminder_label(record: Dict[str, Any]) -> str:
	return f"#{record.get('id','?')} — {record.get('due_at','')} — {record.get('note','')}"


def reminder_embed(record: Dict[str, Any]) -> discord.Embed:
	return make_embed(
		"🔔 REMINDER DUE",
		f"**Note:** {record.get('note','')}\n**Due:** {record.get('due_at','')}\n**ID:** `#{record.get('id','?')}`",
		OREGON_GREEN,
		timestamp=True,
	)


# ═══════════════════════════════════════════════════════════════
#  GEMINI AI & CORE CONNECTION ENDPOINTS
# ═══════════════════════════════════════════════════════════════

async def http_post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any, str]:
	timeout = aiohttp.ClientTimeout(total=30)
	req_headers = {"Content-Type": "application/json"}
	if headers:
		req_headers.update(headers)
	async with aiohttp.ClientSession(timeout=timeout) as session:
		async with session.post(url, json=payload, headers=req_headers) as resp:
			text = await resp.text()
			try:
				data = await resp.json(content_type=None)
			except Exception:
				data = None
			return resp.status, data, text


async def http_get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any, str]:
	timeout = aiohttp.ClientTimeout(total=20)
	async with aiohttp.ClientSession(timeout=timeout) as session:
		async with session.get(url, headers=headers or {}) as resp:
			text = await resp.text()
			try:
				payload = await resp.json(content_type=None)
			except Exception:
				payload = None
			return resp.status, payload, text


async def http_get_text(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
	timeout = aiohttp.ClientTimeout(total=20)
	async with aiohttp.ClientSession(timeout=timeout) as session:
		async with session.get(url, headers=headers or {}) as resp:
			return resp.status, await resp.text()


async def fetch_json_with_headers(url: str, headers: Dict[str, str]) -> Tuple[int, Any, str]:
	return await http_get_json(url, headers)


async def gemini_generate(prompt: str) -> Tuple[bool, str]:
	if not GEMINI_API_KEY:
		return False, "Gemini API key missing. Set GEMINI_API_KEY in Koyeb environment variables."
	url = (
		"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
		f"?key={urllib.parse.quote_plus(GEMINI_API_KEY)}"
	)
	payload = {
		"contents": [{"role": "user", "parts": [{"text": prompt}]}],
		"generationConfig": {"temperature": 0.7, "topP": 0.95, "maxOutputTokens": 1024},
	}
	status, data, text = await http_post_json(url, payload)
	if status < 200 or status >= 300:
		return False, text.strip() or json.dumps(data or {"error": "unknown"}, indent=2)
	try:
		candidates = data.get("candidates", []) if isinstance(data, dict) else []
		if not candidates:
			return True, text.strip()
		parts = candidates[0].get("content", {}).get("parts", [])
		output = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
		return True, output if output else text.strip()
	except Exception:
		return True, text.strip()


async def compose_ai_prompt(user_id: int, prompt: str, purpose: str) -> str:
	theme = await get_user_theme(user_id)
	return (
		f"SYSTEM THEME: {theme['label']}\n"
		f"AI PERSONA: {theme['persona']}\n"
		f"OPERATIONAL PURPOSE: {purpose}\n"
		"Response style: clean markdown, field-usable, practical, concise where appropriate.\n\n"
		f"USER REQUEST:\n{prompt}"
	)


async def handle_ai_prompt(interaction: discord.Interaction, prompt: str, title: str, already_deferred: bool = False) -> None:
	count = await ai_count_for_user(interaction.user.id)
	ephemeral = await current_visibility(interaction.user.id)

	if count >= AI_LIMIT:
		embed = make_embed("🛑 SAR QUOTA REACHED", "Daily AI capacity exhausted for this UTC day. Try again after `00:00Z`.", SAR_ORANGE, timestamp=True)
		await mode_send(interaction, embed=embed, ephemeral=True)
		return

	if count >= 20:
		embed = make_embed(
			"⚠️ AI EXECUTION HOLD",
			f"AI usage is at **{count}/{AI_LIMIT}** for today.\nProceed to run the Gemini request or cancel.",
			SAR_ORANGE,
		)
		view = ProceedsView(interaction.user.id, prompt, title)
		if not already_deferred:
			await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
		else:
			await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
		return

	if not already_deferred:
		await interaction.response.defer(ephemeral=ephemeral)

	success, output = await gemini_generate(prompt)
	if not success:
		embed = make_embed("❌ GEMINI API ERROR", truncate_text(output, 3900), ERROR_RED, timestamp=True)
		await interaction.followup.send(embed=embed, ephemeral=ephemeral)
		return

	count_after = await increment_ai_count(interaction.user.id)
	embed = make_embed(title, truncate_text(output, 3900), ai_footer_color(count_after), timestamp=True)
	embed.set_footer(text=ai_footer(count_after))
	await interaction.followup.send(embed=embed, ephemeral=ephemeral)


# ═══════════════════════════════════════════════════════════════
#  OPEN-METEO URL BUILDERS
# ═══════════════════════════════════════════════════════════════

def open_meteo_current_url(lat: float, lon: float, model: str) -> str:
	params = {
		"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}",
		"current": "temperature_2m,relative_humidity_2m,surface_pressure,precipitation",
		"timezone": "auto", "models": model,
		"wind_speed_unit": "kmh", "temperature_unit": "celsius", "precipitation_unit": "mm",
	}
	return "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)


def open_meteo_geocode_url(location: str) -> str:
	return "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": location, "format": "jsonv2", "limit": "1"})


# ═══════════════════════════════════════════════════════════════
#  BOT CLASS
# ═══════════════════════════════════════════════════════════════

class SquelchBot(commands.Bot):
	def __init__(self) -> None:
		intents = discord.Intents.default()
		intents.message_content = True
		super().__init__(command_prefix="/", intents=intents)
		self.http_runner: Optional[web.AppRunner] = None
		self.db_pool: Optional[asyncpg.Pool] = None

	async def setup_hook(self) -> None:
		if not DATABASE_URL:
			raise RuntimeError("DATABASE_URL environment variable is missing.")
		
		self.db_pool = await asyncpg.create_pool(DATABASE_URL)
		async with self.db_pool.acquire() as conn:
			await conn.execute("""
				CREATE TABLE IF NOT EXISTS whitelist (user_id BIGINT PRIMARY KEY);
				CREATE TABLE IF NOT EXISTS ai_tracker (user_id BIGINT PRIMARY KEY, date TEXT, count INT);
				CREATE TABLE IF NOT EXISTS comms_log (id SERIAL PRIMARY KEY, ts TEXT, entry TEXT);
				CREATE TABLE IF NOT EXISTS mode_state (user_id BIGINT PRIMARY KEY, mode TEXT);
				CREATE TABLE IF NOT EXISTS theme_state (user_id BIGINT PRIMARY KEY, theme_id TEXT);
				CREATE TABLE IF NOT EXISTS reminders (
					id SERIAL PRIMARY KEY, user_id BIGINT, note TEXT, due_at TEXT, channel_id BIGINT,
					created_at TEXT, sent BOOLEAN DEFAULT FALSE, sent_at TEXT, done BOOLEAN DEFAULT FALSE, 
					done_at TEXT, deleted BOOLEAN DEFAULT FALSE, deleted_at TEXT
				);
				CREATE TABLE IF NOT EXISTS deadrops (
					id SERIAL PRIMARY KEY, user_id BIGINT, key TEXT, content TEXT, secret TEXT,
					created_at TEXT, deleted BOOLEAN DEFAULT FALSE, deleted_at TEXT
				);
				CREATE TABLE IF NOT EXISTS system_logs (id SERIAL PRIMARY KEY, ts TEXT, log_type TEXT, detail TEXT);
			""")

		await self._start_web_server()
		if not reminder_dispatch.is_running():
			reminder_dispatch.start()
		await self.tree.sync()

	async def close(self) -> None:
		if self.http_runner:
			await self.http_runner.cleanup()
		if self.db_pool:
			await self.db_pool.close()
		await super().close()

	async def _start_web_server(self) -> None:
		async def healthcheck(_: web.Request) -> web.Response:
			return web.Response(text="ok", content_type="text/plain")
		app = web.Application()
		app.router.add_get("/", healthcheck)
		app.router.add_get("/healthz", healthcheck)
		runner = web.AppRunner(app)
		await runner.setup()
		await web.TCPSite(runner, host="0.0.0.0", port=PORT).start()
		self.http_runner = runner


bot = SquelchBot()


# ═══════════════════════════════════════════════════════════════
#  WHITELIST GATE
# ═══════════════════════════════════════════════════════════════

async def whitelist_gatekeeper(interaction: discord.Interaction) -> bool:
	if interaction.command and interaction.command.name in ["verify"]:
		return True

	allowed = set(await load_whitelist())
	if interaction.user is None or (interaction.user.id not in allowed and interaction.user.id not in _TEMP_ALLOWED):
		desc = (
			"🚨🚨🚨 **UNAUTHORIZED TERMINAL ACCESS DETECTED** 🚨🚨🚨\n\n"
			"⚠️ CRITICAL SYSTEM BREAK ALERT: YOUR UNIQUE DISCORD IDENTIFIER IS NOT REGISTERED IN THE SECURITY WHITELIST CONTEXT DATA.\n\n"
			"THIS ACTION DIRECTLY VIOLATES RUNTIME OPERATIONAL ENFORCEMENTS. METRICS HAVE BEEN ROUTED TO SECURITY LOG ARRAYS.\n\n"
			"IF YOU ARE AN AUTHORIZED OPERATOR RECOVERING REMOTELY, HIT THE BYPASS SWITCH BELOW TO COMPLETE AUTHENTICATION."
		)
		embed = make_embed("🛑 ACCESS DENIED: CRITICAL SYSTEM LOCK", desc, ERROR_RED, timestamp=True)
		view = GatekeeperUnlockView()
		kwargs: Dict[str, Any] = {"embed": embed, "view": view, "ephemeral": True}
		if interaction.response.is_done():
			await interaction.followup.send(**kwargs)
		else:
			await interaction.response.send_message(**kwargs)
		return False
	return True


bot.tree.interaction_check = whitelist_gatekeeper


# ═══════════════════════════════════════════════════════════════
#  GLOBAL ERROR HANDLER
# ═══════════════════════════════════════════════════════════════

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
	cmd_name = interaction.command.name if interaction.command else "unknown"
	full_tb  = traceback.format_exc()
	err_line = (
		f"[{utc_now().isoformat()}] /{cmd_name} "
		f"user={interaction.user.id} "
		f"{type(error).__name__}: {str(error)}\n{full_tb}"
	)
	await write_error_log(err_line)

	embed = discord.Embed(
		title="⚡ COMMAND FAULT",
		color=ERROR_RED,
		timestamp=utc_now(),
	)
	embed.add_field(name="Command",    value=f"`/{cmd_name}`",            inline=True)
	embed.add_field(name="Error Type", value=f"`{type(error).__name__}`",  inline=True)
	embed.add_field(name="Detail",     value=f"```{truncate_text(str(error), 900)}```", inline=False)
	embed.set_footer(text="Use /errorlog to view full history • Report logged automatically")

	view = ErrorLogLinkView()
	try:
		if interaction.response.is_done():
			await interaction.followup.send(embed=embed, view=view, ephemeral=True)
		else:
			await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
	except Exception:
		pass


# ═══════════════════════════════════════════════════════════════
#  BACKGROUND TASKS
# ═══════════════════════════════════════════════════════════════

@tasks.loop(seconds=30)
async def reminder_dispatch() -> None:
	if not bot.db_pool:
		return
	now = utc_now().isoformat()
	try:
		async with bot.db_pool.acquire() as conn:
			records = await conn.fetch("SELECT * FROM reminders WHERE sent = FALSE AND due_at <= $1 AND deleted = FALSE AND done = FALSE", now)
			for r in records:
				user = bot.get_user(int(r["user_id"]))
				chan_id = r["channel_id"]
				
				r_dict = dict(r)
				embed = reminder_embed(r_dict)
				
				sent = False
				if user:
					try:
						await user.send(embed=embed)
						sent = True
					except Exception:
						pass
				if not sent and chan_id:
					chan = bot.get_channel(int(chan_id))
					if chan:
						await chan.send(embed=embed)
						sent = True
				if not sent:
					continue
				
				await conn.execute("UPDATE reminders SET sent = TRUE, sent_at = $1 WHERE id = $2", utc_now().isoformat(), r["id"])
	except Exception as exc:
		await write_error_log(f"reminder-loop error: {exc}")


# ═══════════════════════════════════════════════════════════════
#  UI COMPONENTS
# ═══════════════════════════════════════════════════════════════

class PassphraseModal(discord.ui.Modal, title="🔐 SYSTEM OVERRIDE OVERLAY"):
	passphrase_input = discord.ui.TextInput(
		label="ENTER SYSTEM ACCESS PASSPHRASE",
		placeholder="Input security key sequence...",
		required=True,
		style=discord.TextStyle.short
	)

	async def on_submit(self, interaction: discord.Interaction):
		if self.passphrase_input.value.strip() == GATE_PASSPHRASE:
			_TEMP_ALLOWED.add(interaction.user.id)
			embed = make_embed(
				"🟩 ACCESS AUTHORIZED",
				"BYPASS SEGMENT ACCEPTED. TEMPORARY RUNTIME ACCESS GRANTED UNTIL SERVICE REBOOT.",
				OREGON_GREEN,
				timestamp=True
			)
			await interaction.response.send_message(embed=embed, ephemeral=True)
		else:
			await interaction.response.send_message("🛑 ACCESS KEY VERIFICATION FAILED. REQUEST PURGED.", ephemeral=True)


class GatekeeperUnlockView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=60)

	@discord.ui.button(label="🔑 Bypass with Passphrase", style=discord.ButtonStyle.danger)
	async def enter_passphrase(self, interaction: discord.Interaction, button: discord.ui.Button):
		await interaction.response.send_modal(PassphraseModal())


class ThemePickerSelect(discord.ui.Select):
	def __init__(self, owner_id: int):
		options = [
			discord.SelectOption(
				label=theme["label"],
				value=tid,
				emoji=theme["emoji"],
				description=theme["persona"][:95],
			)
			for tid, theme in THEME_BY_ID.items()
		]
		super().__init__(placeholder="Choose a mission skin...", options=options, min_values=1, max_values=1)
		self.owner_id = owner_id

	async def callback(self, interaction: discord.Interaction) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This picker belongs to someone else.", ephemeral=True)
			return
		tid = self.values[0]
		await set_user_theme(interaction.user.id, tid)
		theme = THEME_BY_ID.get(tid, THEME_BY_ID[DEFAULT_THEME_ID])
		embed = make_embed(
			f"🎨 THEME LOCKED: {theme['label']}",
			f"Active skin: {theme_card(theme)}\n\nAI persona and response style are now synced.",
			theme["color"],
		)
		await interaction.response.edit_message(embed=embed, view=self.view)
		await log_blackbox_event(interaction, "theme", tid)


class ThemePickerView(discord.ui.View):
	def __init__(self, owner_id: int):
		super().__init__(timeout=180)
		self.add_item(ThemePickerSelect(owner_id))


class ProceedsView(discord.ui.View):
	def __init__(self, owner_id: int, prompt: str, header: str):
		super().__init__(timeout=120)
		self.owner_id = owner_id
		self.prompt   = prompt
		self.header   = header
		self._locked  = False

	async def _run(self, interaction: discord.Interaction) -> None:
		if self._locked:
			return
		self._locked = True
		for item in self.children:
			if hasattr(item, "disabled"):
				item.disabled = True
		await interaction.response.edit_message(view=self)
		success, output = await gemini_generate(self.prompt)
		if not success:
			await interaction.followup.send(
				embed=make_embed("❌ GEMINI API ERROR", truncate_text(output, 3900), ERROR_RED, timestamp=True),
				ephemeral=True,
			)
			return
		count = await increment_ai_count(self.owner_id)
		embed = make_embed(self.header, truncate_text(output, 3900), ai_footer_color(count), timestamp=True)
		embed.set_footer(text=ai_footer(count))
		await interaction.followup.send(embed=embed)

	@discord.ui.button(label="✅ Proceed", style=discord.ButtonStyle.success)
	async def proceed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This prompt belongs to someone else.", ephemeral=True)
			return
		await self._run(interaction)

	@discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
	async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This prompt belongs to someone else.", ephemeral=True)
			return
		for item in self.children:
			if hasattr(item, "disabled"):
				item.disabled = True
		await interaction.response.edit_message(
			embed=make_embed("🚫 REQUEST CANCELLED", "AI execution was not started.", SAR_ORANGE),
			view=self,
		)


class DepthSourcesView(discord.ui.View):
	def __init__(self, primary_url: Optional[str], query: str):
		super().__init__(timeout=180)
		if primary_url:
			self.add_item(discord.ui.Button(label="🔗 Open Source", url=primary_url, style=discord.ButtonStyle.link))
		self.add_item(discord.ui.Button(label="🎓 Google Scholar", url=scholar_url(query), style=discord.ButtonStyle.link))


class MapsView(discord.ui.View):
	def __init__(self, lat: float, lon: float):
		super().__init__(timeout=180)
		self.add_item(discord.ui.Button(label="🗺️ OpenStreetMap", url=f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=14/{lat:.5f}/{lon:.5f}", style=discord.ButtonStyle.link))
		self.add_item(discord.ui.Button(label="📍 Google Maps",   url=f"https://www.google.com/maps/search/?api=1&query={lat:.5f},{lon:.5f}",  style=discord.ButtonStyle.link))
		self.add_item(discord.ui.Button(label="📱 Apple Maps",    url=f"https://maps.apple.com/?q={lat:.5f},{lon:%20.5f}",                         style=discord.ButtonStyle.link))


class ErrorLogView(discord.ui.View):
	def __init__(self, owner_id: int):
		super().__init__(timeout=600)
		self.owner_id = owner_id

	@discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary)
	async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This belongs to someone else.", ephemeral=True)
			return
		embed = await build_error_log_embed()
		await interaction.response.edit_message(embed=embed, view=self)

	@discord.ui.button(label="🗑️ Clear Log", style=discord.ButtonStyle.danger)
	async def clear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This belongs to someone else.", ephemeral=True)
			return
		try:
			async with bot.db_pool.acquire() as conn:
				await conn.execute("DELETE FROM system_logs WHERE log_type = 'error'")
		except Exception:
			pass
		await interaction.response.edit_message(
			embed=make_embed("🗑️ ERROR LOG CLEARED", "Log database rows wiped. All clear.", OREGON_GREEN, timestamp=True),
			view=self,
		)


class ErrorLogLinkView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=60)

	@discord.ui.button(label="📋 View Error Log", style=discord.ButtonStyle.secondary)
	async def view_log(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		embed = await build_error_log_embed()
		await interaction.response.send_message(
			embed=embed,
			view=ErrorLogView(interaction.user.id),
			ephemeral=True,
		)


class WeatherLinksView(discord.ui.View):
	def __init__(self, lat: float, lon: float):
		super().__init__(timeout=180)
		self.add_item(discord.ui.Button(label="🌍 Open-Meteo", url=f"https://open-meteo.com/en/docs#{lat},{lon}", style=discord.ButtonStyle.link))
		self.add_item(discord.ui.Button(label="🌩️ BOM", url="http://www.bom.gov.au/", style=discord.ButtonStyle.link))


class DeclinationLinksView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=180)
		self.add_item(discord.ui.Button(label="🧭 NOAA Declination", url="https://www.ngdc.noaa.gov/geomag/calculators/magcalc.shtml", style=discord.ButtonStyle.link))


class RepeaterLinksView(discord.ui.View):
	def __init__(self, location: str):
		super().__init__(timeout=180)
		self.add_item(discord.ui.Button(label="📡 RepeaterBook", url=f"https://www.repeaterbook.com/repeaters/index.php?state_id=none&loc={urllib.parse.quote_plus(location)}", style=discord.ButtonStyle.link))


class SolarDataView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=180)
		self.add_item(discord.ui.Button(label="☀️ NOAA Space Weather", url="https://www.swpc.noaa.gov/", style=discord.ButtonStyle.link))
		self.add_item(discord.ui.Button(label="📻 DXMaps", url="https://www.dxmaps.com/", style=discord.ButtonStyle.link))


class FieldMedView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=180)
		self.add_item(discord.ui.Button(label="🌍 Triple Zero (000) Web Info", url="https://www.triplezero.gov.au/", style=discord.ButtonStyle.link))
		self.add_item(discord.ui.Button(label="📞 NSW Poisons Centre", url="https://www.poisoninfo.nsw.gov.au/", style=discord.ButtonStyle.link))


class QuotaView(discord.ui.View):
	def __init__(self, owner_id: int):
		super().__init__(timeout=120)
		self.owner_id = owner_id

	@discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary)
	async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This belongs to someone else.", ephemeral=True)
			return
		embed = await build_quota_embed(interaction.user.id)
		await interaction.response.edit_message(embed=embed, view=self)


async def build_quota_embed(user_id: int) -> discord.Embed:
	count     = await ai_count_for_user(user_id)
	remaining = max(0, AI_LIMIT - count)
	pct        = int((count / AI_LIMIT) * 100)
	bar_on    = round(count / AI_LIMIT * 20)
	bar        = "█" * bar_on + "░" * (20 - bar_on)
	color      = OREGON_GREEN if count < 20 else SAR_ORANGE if count < AI_LIMIT else ERROR_RED
	status      = "✅ Clear" if count < 20 else "⚠️ Getting Low" if count < AI_LIMIT else "🛑 Exhausted"
	embed = discord.Embed(title="📊 AI QUOTA STATUS", color=color, timestamp=utc_now())
	embed.add_field(name="Used Today",  value=f"`{count}/{AI_LIMIT}`",  inline=True)
	embed.add_field(name="Remaining",   value=f"`{remaining}`",         inline=True)
	embed.add_field(name="Status",      value=status,                    inline=True)
	embed.add_field(name="Usage Bar",   value=f"`[{bar}]  {pct}%`",    inline=False)
	embed.set_footer(text=f"Resets at 00:00 UTC  •  UTC Date: {utc_date_key()}")
	return embed


# ═══════════════════════════════════════════════════════════════
#  COMMANDS
# ═══════════════════════════════════════════════════════════════

@bot.tree.command(name="mode", description="Switch your Squelch operating mode.")
@app_commands.describe(mode="Pick regular, stealth, or blackbox.")
@app_commands.choices(mode=[
	app_commands.Choice(name="🟢 Regular",    value="regular"),
	app_commands.Choice(name="👻 Stealth",    value="stealth"),
	app_commands.Choice(name="⬛ Blackbox",  value="blackbox"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def mode_command(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
	previous = await get_user_mode(interaction.user.id)
	await set_user_mode(interaction.user.id, mode.value)
	desc = (
		f"Mode updated: **{previous}** → **{mode.value}**\n\n"
		"🟢 `regular` — normal public responses\n"
		"👻 `stealth` — all responses visible only to you\n"
		"⬛ `blackbox` — stealth + local console/file logging"
	)
	await mode_send(
		interaction,
		embed=make_embed("🔧 MODE UPDATED", desc, OREGON_GREEN, timestamp=True),
		ephemeral=mode.value != "regular",
	)
	if mode.value == "blackbox" or previous == "blackbox":
		await write_blackbox_log(
			f"[{utc_now().isoformat()}] mode user={interaction.user.id} {previous}→{mode.value}"
		)


@bot.tree.command(name="theme", description="Open the Squelch theme picker.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def theme_command(interaction: discord.Interaction) -> None:
	theme = await get_user_theme(interaction.user.id)
	embed = make_embed(
		f"🎨 THEME DESK: {theme['label']}",
		f"Active skin: {theme_card(theme)}\n\n"
		f"*Persona:* {theme['persona']}\n\n"
		"Select a new mission skin below. Your AI persona and study-helper style will update immediately.",
		theme["color"],
	)
	await mode_send(interaction, embed=embed, view=ThemePickerView(interaction.user.id), ephemeral=True)


@bot.tree.command(name="quota", description="Check your daily AI usage quota.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def quota_command(interaction: discord.Interaction) -> None:
	embed = await build_quota_embed(interaction.user.id)
	await mode_send(
		interaction,
		embed=embed,
		view=QuotaView(interaction.user.id),
		ephemeral=True,
	)


@bot.tree.command(name="errorlog", description="View the live Squelch error log.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def errorlog_command(interaction: discord.Interaction) -> None:
	embed = await build_error_log_embed()
	await mode_send(
		interaction,
		embed=embed,
		view=ErrorLogView(interaction.user.id),
		ephemeral=True,
	)


@bot.tree.command(name="ai", description="General machine intelligence terminal query.")
@app_commands.describe(query="What do you want to ask?")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def ai_cmd(interaction: discord.Interaction, query: str) -> None:
	prompt = await compose_ai_prompt(interaction.user.id, clean_text(query), "general ai response")
	await handle_ai_prompt(interaction, prompt, "🤖 SQUELCH AI RESPONSE")


@bot.tree.command(name="gaslight", description="Generate a deadpan official-looking fake incident record about a user.")
@app_commands.describe(user="The target user.", topic="What to gaslight them about.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def gaslight_cmd(interaction: discord.Interaction, user: discord.Member, topic: str) -> None:
	theme = await get_user_theme(interaction.user.id)
	prompt = await compose_ai_prompt(
		interaction.user.id,
		f"You are writing an official SQUELCH INCIDENT FIELD RECORD in deadpan bureaucratic tone using the {theme['label']} persona.\n"
		f"The subject of this record is: {user.display_name} (callsign unknown).\n"
		f"The incident topic is: {topic}.\n\n"
		"Instructions:\n"
		"- Invent a realistic-sounding but absurd INCIDENT REF (e.g. SQ-2026-0447-BRAVO)\n"
		"- Include a fabricated timestamp down to the second\n"
		"- Confidently assert the incident occurred with invented but plausible field details\n"
		"- Include one or two fake witness callsigns who corroborate the story\n"
		"- Conclude with an OFFICIAL FINDING section that is both authoritative and ridiculous\n"
		"- Never break character. This is comedy between consenting friends.",
		"gaslight incident report",
	)
	await handle_ai_prompt(
		interaction,
		prompt,
		f"📋 INCIDENT RECORD — {user.display_name.upper()}",
	)


@bot.tree.command(name="study", description="Turn a note, PDF, or topic into a study pack.")
@app_commands.describe(topic="What you want to study.", source="Optional .md or PDF attachment.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def study_command(interaction: discord.Interaction, topic: str, source: Optional[discord.Attachment] = None) -> None:
	theme = await get_user_theme(interaction.user.id)
	await interaction.response.defer(ephemeral=await current_visibility(interaction.user.id))
	source_block = ""
	if source is not None:
		ok, extracted = await extract_attachment_text(source)
		if not ok:
			await interaction.followup.send(embed=make_embed("❌ STUDY ERROR", extracted, ERROR_RED), ephemeral=True)
			return
		if not extracted.strip():
			await interaction.followup.send(embed=make_embed("❌ STUDY ERROR", "The attached file yielded no readable text.", ERROR_RED), ephemeral=True)
			return
		source_block = build_study_source_text(source.filename, extracted)
	prompt = await compose_ai_prompt(
		interaction.user.id,
		f"{study_system_prompt(theme)}\n\nCreate a study pack for the topic below.\n\nTOPIC: {topic}\n\nSOURCE NOTES:\n{source_block or 'No attachment provided — use topic and general best practices.'}",
		"study helper",
	)
	await handle_ai_prompt(interaction, prompt, f"📚 STUDY PACK: {topic}", already_deferred=True)


async def extract_attachment_text(attachment: discord.Attachment) -> Tuple[bool, str]:
	data = await attachment.read()
	ext  = attachment_suffix(attachment)
	if ext == "pdf":
		try:
			reader = PdfReader(BytesIO(data))
			return True, "\n".join(p.extract_text() or "" for p in reader.pages).strip()
		except Exception as exc:
			return False, f"Failed to parse PDF: {exc}"
	try:
		return True, data.decode("utf-8", errors="ignore")
	except Exception as exc:
		return False, f"Failed to read attachment: {exc}"


@bot.tree.command(name="fieldmed", description="Field triage helper for EMT-style safety checks.")
@app_commands.describe(chief_complaint="What happened.", age="Optional patient age.", vitals="Optional vitals string.", mechanism="Mechanism of injury or illness.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def fieldmed_command(interaction: discord.Interaction, chief_complaint: str, age: Optional[int] = None, vitals: Optional[str] = None, mechanism: Optional[str] = None) -> None:
	embed = fieldmed_report(chief_complaint, age, vitals, mechanism)
	await mode_send(interaction, embed=embed, view=FieldMedView(), ephemeral=True)


@bot.tree.command(name="triage", description="Classify a task or request into the best local route.")
@app_commands.describe(query="Describe what you need routed.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def triage_command(interaction: discord.Interaction, query: str) -> None:
	category, recommendation = classify_triage(query)
	await mode_send(interaction, embed=triage_summary_embed(category, recommendation, query))


@bot.tree.command(name="datum", description="Multi-node technical search and research discovery.")
@app_commands.describe(query="What to look up.", source="Where to search.")
@app_commands.choices(source=[
	app_commands.Choice(name="🌐 Wikipedia", value="wikipedia"),
	app_commands.Choice(name="🔍 Google",    value="google"),
	app_commands.Choice(name="🤖 AI",        value="ai"),
	app_commands.Choice(name="🔢 Wolfram",   value="wolfram"),
	app_commands.Choice(name="💻 Stack",     value="stack"),
	app_commands.Choice(name="🌦️ wttr.in",   value="wttr"),
	app_commands.Choice(name="🚨 USGS Quakes", value="earthquake"),
	app_commands.Choice(name="📍 Photon OSM", value="photon"),
	app_commands.Choice(name="🦆 DuckDuckGo", value="duckduckgo"),
	app_commands.Choice(name="📕 Dictionary", value="dictionary"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def datum_cmd(interaction: discord.Interaction, query: str, source: app_commands.Choice[str]) -> None:
	selected = source.value
	query    = clean_text(query)
	private  = await current_visibility(interaction.user.id)
	await interaction.response.defer(ephemeral=private)

	if selected == "google":
		await interaction.followup.send(
			embed=make_embed("🔍 DATUM: GOOGLE ROUTE", f"[Open Google search]({google_search_url(query)})", OREGON_GREEN),
			view=DepthSourcesView(google_search_url(query), query), ephemeral=private,
		)
		return

	if selected == "wolfram":
		await interaction.followup.send(
			embed=make_embed("🔢 DATUM: WOLFRAM ROUTE", f"[Open WolframAlpha]({wolfram_url(query)})", OREGON_GREEN),
			view=DepthSourcesView(wolfram_url(query), query), ephemeral=private,
		)
		return

	if selected == "stack":
		await interaction.followup.send(
			embed=make_embed("💻 DATUM: STACK OVERFLOW", f"[Open Stack Overflow]({stackoverflow_url(query)})", OREGON_GREEN),
			view=DepthSourcesView(stackoverflow_url(query), query), ephemeral=private,
		)
		return

	if selected == "wikipedia":
		headers = {"User-Agent": "SquelchBot/1.0 (hamish6612@gmail.com)"}
		status, payload, text = await fetch_json_with_headers(wikipedia_summary_url(query), headers)
		if status != 200 or not isinstance(payload, dict):
			await interaction.followup.send(
				embed=make_embed("❌ DATUM: WIKIPEDIA ERROR", truncate_text(text, 3900), ERROR_RED),
				view=DepthSourcesView(None, query), ephemeral=private,
			)
			return
		extract       = payload.get("extract") or "No summary returned."
		article_url = payload.get("content_urls", {}).get("desktop", {}).get("page") or wikipedia_desktop_url(query)
		title       = payload.get("title") or query
		await interaction.followup.send(
			embed=make_embed(f"📖 DATUM: {title}", truncate_text(extract, 3900), OREGON_GREEN),
			view=DepthSourcesView(article_url, query), ephemeral=private,
		)
		return

	if selected == "wttr":
		status, text = await http_get_text(f"https://wttr.in/{urllib.parse.quote(query)}?format=4")
		if status != 200:
			await interaction.followup.send(embed=make_embed("❌ WEATHER ENGINE ERROR", "Could not query wttr.in endpoint.", ERROR_RED), ephemeral=private)
			return
		await interaction.followup.send(embed=make_embed(f"🌦️ METEOROLOGY: {query.upper()}", f"```\n{text.strip()}\n```", OREGON_GREEN), ephemeral=private)
		return

	if selected == "earthquake":
		status, payload, text = await http_get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")
		if status != 200 or not isinstance(payload, dict):
			await interaction.followup.send(embed=make_embed("❌ SEISMIC ENGINE ERROR", "Could not connect to USGS endpoints.", ERROR_RED), ephemeral=private)
			return
		features = payload.get("features", [])
		matches = []
		for f in features:
			props = f.get("properties", {})
			place = props.get("place", "").lower()
			if query.lower() in place:
				matches.append(f"• **Mag {props.get('mag')}** — {props.get('place')} ({datetime.fromtimestamp(props.get('time')/1000, timezone.utc).strftime('%H:%MZ')})")
		if not matches:
			desc = f"No seismic triggers matching `{query}` logged globally within the last 24 hours."
		else:
			desc = "\n".join(matches[:10])
		await interaction.followup.send(embed=make_embed("🚨 RECENT SEISMIC TELEMETRY", truncate_text(desc, 3900), OREGON_GREEN), ephemeral=private)
		return

	if selected == "photon":
		status, payload, text = await http_get_json(f"https://photon.komoot.io/api/?q={urllib.parse.quote(query)}&limit=3")
		if status != 200 or not isinstance(payload, dict):
			await interaction.followup.send(embed=make_embed("❌ GEOLOCATION ENGINE ERROR", "Could not query Photon OSM server assets.", ERROR_RED), ephemeral=private)
			return
		features = payload.get("features", [])
		if not features:
			desc = "No coordinates or matching geomorphic landmarks found via openstreetmaps query matrix."
		else:
			lines = []
			for f in features:
				props = f.get("properties", {})
				geom = f.get("geometry", {})
				coords = geom.get("coordinates", [0, 0])
				lines.append(f"• **{props.get('name')}** ({props.get('city', 'N/A')}, {props.get('country', 'N/A')})\n  `Coordinates: {coords[1]:.5f}, {coords[0]:.5f}`")
			desc = "\n\n".join(lines)
		await interaction.followup.send(embed=make_embed("📍 OSM GEOLOCATION FIX", truncate_text(desc, 3900), OREGON_GREEN), ephemeral=private)
		return

	if selected == "duckduckgo":
		status, payload, text = await http_get_json(f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1")
		if status != 200 or not isinstance(payload, dict):
			await interaction.followup.send(embed=make_embed("❌ BRIEFING ENGINE ERROR", "Could not pull DuckDuckGo endpoint arrays.", ERROR_RED), ephemeral=private)
			return
		abstract = payload.get("AbstractText", "")
		if not abstract:
			related = payload.get("RelatedTopics", [])
			if related and isinstance(related[0], dict) and "Text" in related[0]:
				abstract = related[0]["Text"]
			else:
				abstract = "No immediate briefing abstract found for this topic query array."
		await interaction.followup.send(embed=make_embed(f"🦆 DDG BRIEFING: {query.upper()}", truncate_text(abstract, 3900), OREGON_GREEN), ephemeral=private)
		return

	if selected == "dictionary":
		url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(query)}"
		status, payload, text = await fetch_json_with_headers(url, {"User-Agent": "SquelchBot/1.0"})
		if status != 200 or not isinstance(payload, list) or not payload:
			desc = f"❌ **No definition found for `{query}`**\nEither the word is not in the dictionary or the API is currently unavailable."
			await interaction.followup.send(embed=make_embed("📕 DATUM: DICTIONARY ERROR", desc, ERROR_RED), ephemeral=private)
			return
		
		entry = payload[0]
		word = entry.get("word", query).capitalize()
		phonetic = entry.get("phonetic") or ""
		if not phonetic and entry.get("phonetics"):
			for ph in entry.get("phonetics", []):
				if ph.get("text"):
					phonetic = ph.get("text")
					break
		
		desc = f"## 📕 {word} `{phonetic}`\n\n"
		
		meanings = entry.get("meanings", [])
		for idx, meaning in enumerate(meanings[:3]):
			pos = meaning.get("partOfSpeech", "N/A").upper()
			desc += f"### *{pos}*\n"
			defs = meaning.get("definitions", [])
			for d_idx, d in enumerate(defs[:2]):
				definition = d.get("definition", "")
				example = d.get("example", "")
				desc += f"> **{d_idx+1}.** {definition}\n"
				if example:
					desc += f"> *Example: \"{example}\"*\n"
			desc += "\n"
			
		origin = entry.get("origin") or ""
		if origin:
			desc += f"**Origin:**\n> {origin}\n"
			
		await interaction.followup.send(
			embed=make_embed(f"📕 DICTIONARY DEFINITION: {word.upper()}", truncate_text(desc, 3900), OREGON_GREEN),
			ephemeral=private
		)
		return

	if selected == "ai":
		prompt = await compose_ai_prompt(interaction.user.id, query, "datum ai lookup")
		await handle_ai_prompt(interaction, prompt, "🤖 DATUM: AI ANALYSIS", already_deferred=True)


@bot.tree.command(name="gps", description="Translate an address or location into coordinates and map links.")
@app_commands.describe(location="Place name or address.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def gps_cmd(interaction: discord.Interaction, location: str) -> None:
	await interaction.response.defer()
	headers = {"User-Agent": "SquelchBot/1.0 (hamish6612@gmail.com)"}
	status, payload, text = await fetch_json_with_headers(open_meteo_geocode_url(location), headers)
	if status != 200 or not isinstance(payload, list) or not payload:
		await interaction.followup.send(embed=make_embed("❌ GPS LOOKUP FAILED", truncate_text(text, 3900), ERROR_RED))
		return
	place      = payload[0]
	lat, lon   = float(place["lat"]), float(place["lon"])
	name       = place.get("display_name", location)
	desc = (
		f"**Location:** {name}\n\n"
		f"**Decimal Degrees**\n`{lat:.5f}, {lon:.5f}`\n\n"
		f"**DDM**\nLat: `{decimal_to_ddm(lat, True)}`\nLon: `{decimal_to_ddm(lon, False)}`\n\n"
		f"**Maidenhead Grid**\n`{maidenhead_locator(lat, lon)}`"
	)
	await interaction.followup.send(embed=make_embed("📍 GPS NAVIGATION FIX", desc, OREGON_GREEN, timestamp=True), view=MapsView(lat, lon))


@bot.tree.command(name="weather", description="High-resolution meteorology comparator (3 model ensemble).")
@app_commands.describe(lat="Latitude (decimal).", lon="Longitude (decimal).")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def weather_cmd(interaction: discord.Interaction, lat: float, lon: float) -> None:
	await interaction.response.defer()
	models = [("ECMWF","ecmwf_ifs025"),("NOAA GFS","gfs_seamless"),("BOM ACCESS","bom_access")]
	rows: List[Tuple[str, Dict[str, Any]]] = []
	errors: List[str] = []
	for label, model in models:
		status, payload, text = await fetch_json_with_headers(
			open_meteo_current_url(lat, lon, model),
			{"User-Agent": "SquelchBot/1.0"},
		)
		if status != 200 or not isinstance(payload, dict):
			errors.append(f"{label}: {truncate_text(text, 120)}")
			continue
		current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
		rows.append((label, current))
	if not rows:
		await interaction.followup.send(embed=make_embed("❌ WEATHER FAILED", "\n".join(errors) or "No data returned.", ERROR_RED))
		return
	table = [
		"| Model | Temp °C | Humidity % | Pressure hPa | Precip mm |",
		"| --- | ---: | ---: | ---: | ---: |",
	]
	for label, cur in rows:
		table.append(f"| {label} | {cur.get('temperature_2m','n/a')} | {cur.get('relative_humidity_2m','n/a')} | {cur.get('surface_pressure','n/a')} | {cur.get('precipitation','n/a')} |")
	desc = f"**Coordinate:** `{lat:.5f}, {lon:.5f}`\n\n" + "\n".join(table)
	if errors:
		desc += "\n\n**Partial errors:** " + "; ".join(errors)
	await interaction.followup.send(
		embed=make_embed("🌦️ WEATHER COMPARATOR", desc, OREGON_GREEN, timestamp=True),
		view=WeatherLinksView(lat, lon),
	)


@bot.tree.command(name="trailcalc", description="Tobler, Pandolf, and Naismith route planner.")
@app_commands.describe(
	distance_km="Route distance in km.", 
	elevation_gain_m="Total elevation gain in metres.", 
	weight_kg="Your body weight in kg.", 
	pack_weight_kg="Pack weight in kg.",
	terrain="Terrain factor selection.",
	rest_window_hrs="Optional tactical rest window adjustments in hours."
)
@app_commands.choices(terrain=[
	app_commands.Choice(name="🟢 Paved road / Boardwalk (1.0)", value="1.0"),
	app_commands.Choice(name="🟡 Gravel / Dirt road (1.1)", value="1.1"),
	app_commands.Choice(name="🟠 Light brush / Meadow (1.2)", value="1.2"),
	app_commands.Choice(name="🔴 Heavy brush / Rocks (1.6)", value="1.6"),
	app_commands.Choice(name="🏜️ Loose sand / Scree (2.1)", value="2.1"),
	app_commands.Choice(name="❄️ Deep snowpack (3.3)", value="3.3")
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def trailcalc_cmd(
	interaction: discord.Interaction, 
	distance_km: float, 
	elevation_gain_m: float, 
	weight_kg: float, 
	pack_weight_kg: float,
	terrain: Optional[app_commands.Choice[str]] = None,
	rest_window_hrs: float = 0.0
) -> None:
	# 1. Naismith baseline
	hours_naismith = (distance_km / 5.0) + (elevation_gain_m / 600.0)
	hydro_naismith = hours_naismith * 0.5
	total_kg = weight_kg + pack_weight_kg
	cals_naismith = 6.0 * 3.5 * total_kg / 200.0 * (hours_naismith * 60.0)
	
	# 2. Tobler's formula
	slope_fraction = elevation_gain_m / (distance_km * 1000.0) if distance_km > 0 else 0
	speed_tobler = 6.0 * math.exp(-3.5 * abs(slope_fraction + 0.05))
	hours_tobler = distance_km / speed_tobler if speed_tobler > 0 else 0
	
	# 3. Pandolf load carriage
	eta = float(terrain.value) if terrain else 1.1  # default to dirt/gravel road if unselected
	V = speed_tobler / 3.6  # walking velocity in m/s
	G = slope_fraction * 100.0  # grade in percent
	
	ratio = (pack_weight_kg / weight_kg) if weight_kg > 0 else 0
	# M = 1.5W + 2.0(W+L)(L/W)^2 + eta(W+L)(1.5V^2 + 0.35VG)
	M_moving = 1.5 * weight_kg + 2.0 * (weight_kg + pack_weight_kg) * (ratio ** 2) + eta * (weight_kg + pack_weight_kg) * (1.5 * (V ** 2) + 0.35 * V * G)
	
	cals_moving = M_moving * 0.8604 * hours_tobler
	
	M_standing = 1.5 * weight_kg + 2.0 * (weight_kg + pack_weight_kg) * (ratio ** 2)
	cals_rest = M_standing * 0.8604 * max(0.0, rest_window_hrs)
	
	cals_pandolf = cals_moving + cals_rest
	
	terrain_label = terrain.name if terrain else "Gravel / Dirt road (1.1, Default)"
	
	desc = (
		f"### 🥾 Naismith Baseline (Standard)\n"
		f"— **Moving Time:** `{hours_naismith:.2f} hrs`\n"
		f"— **Est. Hydration:** `{hydro_naismith:.2f} L`\n"
		f"— **Energy Cost:** `{cals_naismith:.0f} kcal`\n\n"
		
		f"### 📈 Tobler Exponential Model\n"
		f"— **Moving Time:** `{hours_tobler:.2f} hrs`\n"
		f"— **Calculated Speed:** `{speed_tobler:.2f} km/h`\n"
		f"— **Slope Grade:** `{G:.1f}%` ({elevation_gain_m:.0f}m gain)\n\n"
		
		f"### 🪖 Pandolf Military Load Carriage\n"
		f"— **Terrain Factor (η):** {terrain_label}\n"
		f"— **Tactical Rest Window:** `{rest_window_hrs:.2f} hrs`\n"
		f"— **Metabolic Rate:** `{M_moving:.1f} Watts`\n"
		f"— **Total Expenditure:** `{cals_pandolf:.0f} kcal` *(Moving + Rest)*\n\n"
		f"*Total Mass: {total_kg:.2f} kg (Body: {weight_kg}kg, Load: {pack_weight_kg}kg)*"
	)
	
	view = discord.ui.View(timeout=120)
	view.add_item(discord.ui.Button(label="🗺️ CalTopo", url="https://caltopo.com/", style=discord.ButtonStyle.link))
	await interaction.response.send_message(
		embed=make_embed("🥾 TRAIL ROUTE PLANNER", desc, OREGON_GREEN), 
		view=view
	)


@bot.tree.command(name="pack", description="Backpack base-weight analyser.")
@app_commands.describe(base_weight_lbs="Base weight in pounds (excluding consumables).")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def pack_cmd(interaction: discord.Interaction, base_weight_lbs: float) -> None:
	if base_weight_lbs < 10:
		category, color = "Ultralight",   OREGON_GREEN
	elif base_weight_lbs <= 20:
		category, color = "Lightweight",  OREGON_GREEN
	else:
		category, color = "Traditional",  SAR_ORANGE
	desc = f"**Base Weight:** `{base_weight_lbs:.2f} lbs`\n**Category:** `{category}`"
	view = discord.ui.View(timeout=120)
	view.add_item(discord.ui.Button(label="🎒 LighterPack", url="https://lighterpack.com/", style=discord.ButtonStyle.link))
	await interaction.response.send_message(embed=make_embed("🎒 PACK TELEMETRY", desc, color), view=view)



@bot.tree.command(name="lighterpack", description="Scrape and analyze a shared LighterPack list.")
@app_commands.describe(
	url="LighterPack share URL (e.g., https://lighterpack.com/r/XXXXXX)",
	ai_shakedown="Request an AI gear audit from Gemini."
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def lighterpack_cmd(interaction: discord.Interaction, url: str, ai_shakedown: bool = False) -> None:
	private = await current_visibility(interaction.user.id)
	await interaction.response.defer(ephemeral=private)

	url = url.strip()
	if "lighterpack.com/r/" not in url:
		embed = make_embed(
			"❌ INVALID LINK",
			"Please provide a valid LighterPack share link (containing `lighterpack.com/r/`).",
			ERROR_RED,
			timestamp=True
		)
		await interaction.followup.send(embed=embed, ephemeral=private)
		return

	headers = {
		"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
	}
	status, text = await http_get_text(url, headers)
	if status != 200 or not text:
		embed = make_embed(
			"❌ FETCH FAILED",
			f"Could not retrieve the LighterPack page. HTTP Status: {status}",
			ERROR_RED,
			timestamp=True
		)
		await interaction.followup.send(embed=embed, ephemeral=private)
		return

	parts = text.split('<li class="lpItem')
	if len(parts) <= 1:
		embed = make_embed(
			"❌ PARSING ERROR",
			"No gear items found. Make sure the list is public and contains items.",
			ERROR_RED,
			timestamp=True
		)
		await interaction.followup.send(embed=embed, ephemeral=private)
		return

	items = []
	for part in parts[1:]:
		id_match = re.match(r'\s*([^"]+)"', part)
		if not id_match:
			continue
		item_id = id_match.group(1).strip()
		
		name_match = re.search(r'class="lpName"[^>]*>\s*(.*?)\s*</span>', part, re.DOTALL)
		name = name_match.group(1).strip() if name_match else "N/A"
		name = re.sub(r'<[^>]+>', '', name).strip()
		
		mg_match = re.search(r'class="lpMG" value="(\d+)"', part)
		mg = int(mg_match.group(1)) if mg_match else 0
		
		qty_match = re.search(r'class="lpQtyCell[^"]*"[^>]*>\s*(\d+)\s*</span>', part)
		qty = int(qty_match.group(1)) if qty_match else 1
		
		worn_match = re.search(r'class="([^"]*lpWorn[^"]*)"', part)
		cons_match = re.search(r'class="([^"]*lpConsumable[^"]*)"', part)
		
		worn = False
		if worn_match:
			wc = worn_match.group(1)
			if "lpActive" in wc:
				worn = True
				
		consumable = False
		if cons_match:
			cc = cons_match.group(1)
			if "lpActive" in cc:
				consumable = True
				
		items.append({
			"id": item_id,
			"name": name,
			"weight_mg": mg,
			"qty": qty,
			"worn": worn,
			"consumable": consumable
		})

	base_weight = 0.0
	cons_weight = 0.0
	worn_weight = 0.0

	for item in items:
		w = (item["weight_mg"] * item["qty"]) / 1000.0
		if item["worn"]:
			worn_weight += w
		elif item["consumable"]:
			cons_weight += w
		else:
			base_weight += w

	base_weight_lbs = base_weight / 453.592
	cons_weight_lbs = cons_weight / 453.592
	worn_weight_lbs = worn_weight / 453.592
	total_pack_lbs = base_weight_lbs + cons_weight_lbs

	if base_weight_lbs < 10.0:
		category = "Ultralight"
		color = OREGON_GREEN
	elif base_weight_lbs <= 20.0:
		category = "Lightweight"
		color = OREGON_GREEN
	else:
		category = "Traditional"
		color = SAR_ORANGE

	desc = (
		f"🎒 **[PACK TELEMETRY ACTIVE]**\n"
		f"Successfully scraped **{len(items)}** gear items from [LighterPack List]({url}).\n\n"
		f"**Base Weight:** `{base_weight / 1000.0:.3f} kg` ({base_weight_lbs:.2f} lbs)\n"
		f"**Consumables:** `{cons_weight / 1000.0:.3f} kg` ({cons_weight_lbs:.2f} lbs)\n"
		f"**Worn Weight:** `{worn_weight / 1000.0:.3f} kg` ({worn_weight_lbs:.2f} lbs)\n"
		f"**Total Pack Load:** `{(base_weight + cons_weight) / 1000.0:.3f} kg` ({total_pack_lbs:.2f} lbs)\n"
		f"**Classification:** `{category}`\n"
	)

	view = discord.ui.View(timeout=120)
	view.add_item(discord.ui.Button(label="🎒 View LighterPack", url=url, style=discord.ButtonStyle.link))

	if not ai_shakedown:
		embed = make_embed("🎒 LIGHTERPACK TELEMETRY", desc, color, timestamp=True)
		await interaction.followup.send(embed=embed, view=view, ephemeral=private)
		return

	desc += "\n*Requesting AI gear shakedown from Gemini...*"
	status_msg = await interaction.followup.send(embed=make_embed("🎒 LIGHTERPACK TELEMETRY", desc, color, timestamp=True), view=view, ephemeral=private)

	items_str = ""
	for idx, item in enumerate(items):
		w_unit = "g"
		w_val = item["weight_mg"] / 1000.0
		if w_val >= 1000.0:
			w_val /= 1000.0
			w_unit = "kg"
		status_str = []
		if item["worn"]:
			status_str.append("worn")
		if item["consumable"]:
			status_str.append("consumable")
		status_desc = f" ({', '.join(status_str)})" if status_str else ""
		items_str += f"{idx+1}. {item['name']} - {w_val:.2f}{w_unit} x {item['qty']}{status_desc}\n"

	prompt = (
		"You are an expert backcountry gear auditor and ultralight backpacking consultant. "
		"Analyze the following packing list and provide a concise gear shakedown (audit) report. "
		"Identify potential areas to save weight, point out redundant items, recommend lighter alternatives for heavy items, "
		"and offer general suggestions for safety, comfort, or weight efficiency. Keep your tone helpful, professional, and practical.\n\n"
		"### Packing List Summary\n"
		f"- Base Weight: {base_weight / 1000.0:.3f} kg ({base_weight_lbs:.2f} lbs)\n"
		f"- Consumables: {cons_weight / 1000.0:.3f} kg ({cons_weight_lbs:.2f} lbs)\n"
		f"- Worn Weight: {worn_weight / 1000.0:.3f} kg ({worn_weight_lbs:.2f} lbs)\n"
		f"- Classification: {category}\n\n"
		"### Gear List\n"
		f"{items_str}\n\n"
		"Format your suggestions cleanly in Markdown, using bullet points and brief headers. Limit the response to ~400 words maximum."
	)

	success, ai_response = await gemini_generate(prompt)
	if not success:
		ai_response = f"⚠️ *AI Shakedown query failed: {ai_response}*"

	desc_with_ai = (
		f"🎒 **[PACK TELEMETRY ACTIVE]**\n"
		f"Scraped **{len(items)}** gear items from [LighterPack List]({url}).\n\n"
		f"**Base Weight:** `{base_weight / 1000.0:.3f} kg` ({base_weight_lbs:.2f} lbs)\n"
		f"**Consumables:** `{cons_weight / 1000.0:.3f} kg` ({cons_weight_lbs:.2f} lbs)\n"
		f"**Worn Weight:** `{worn_weight / 1000.0:.3f} kg` ({worn_weight_lbs:.2f} lbs)\n"
		f"**Total Pack Load:** `{(base_weight + cons_weight) / 1000.0:.3f} kg` ({total_pack_lbs:.2f} lbs)\n"
		f"**Classification:** `{category}`\n\n"
		f"### 🤖 GEMINI AI GEAR AUDIT\n{ai_response}"
	)
	
	embed = make_embed("🎒 LIGHTERPACK TELEMETRY", truncate_text(desc_with_ai, 4000), color, timestamp=True)
	await status_msg.edit(embed=embed, view=view)


@bot.tree.command(name="morse", description="Translate text into Morse code.")
@app_commands.describe(text="Text to encode.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def morse_cmd(interaction: discord.Interaction, text: str) -> None:
	encoded = morse_encode(text)
	desc = f"**Input:** `{clean_text(text)}`\n\n**Morse:**\n```\n{truncate_text(encoded, 3500)}\n```"
	await interaction.response.send_message(embed=make_embed("📡 MORSE CODE", desc, OREGON_GREEN))


class ConversionDropdown(discord.ui.Select):
	def __init__(self):
		options = [
			discord.SelectOption(label="🧗 Climb Rating (Ewbank)", value="climb", description="Convert climbing grades (Ewbank, YDS, French)"),
			discord.SelectOption(label="📏 Distance (Meters/Feet)", value="distance", description="Convert meters <=> feet"),
			discord.SelectOption(label="⚖️ Mass (Grams/Ounces)", value="mass", description="Convert grams <=> ounces"),
			discord.SelectOption(label="💧 Volume (Liters/Fluid Oz)", value="volume", description="Convert liters <=> fluid ounces"),
			discord.SelectOption(label="💵 Currency (AUD/USD)", value="currency", description="Convert AUD <=> USD")
		]
		super().__init__(placeholder="Select a category to convert...", min_values=1, max_values=1, options=options)

	async def callback(self, interaction: discord.Interaction):
		category = self.values[0]
		modal = ConversionModal(category)
		await interaction.response.send_modal(modal)


class ConversionDashboardView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=180)
		self.add_item(ConversionDropdown())


class ConversionModal(discord.ui.Modal):
	def __init__(self, category: str):
		self.category = category
		title = f"Convert {category.capitalize()}"
		super().__init__(title=title)
		
		if category == "climb":
			val_label = "Ewbank Grade (e.g. 18)"
			unit_label = "Source Unit (defaults to 'ewbank')"
			unit_default = "ewbank"
		elif category == "distance":
			val_label = "Value to convert"
			unit_label = "Source Unit ('m' or 'ft')"
			unit_default = "m"
		elif category == "mass":
			val_label = "Value to convert"
			unit_label = "Source Unit ('g' or 'oz')"
			unit_default = "g"
		elif category == "volume":
			val_label = "Value to convert"
			unit_label = "Source Unit ('l' or 'oz')"
			unit_default = "l"
		else:
			val_label = "Value to convert"
			unit_label = "Source Unit ('aud' or 'usd')"
			unit_default = "aud"
			
		self.value_input = discord.ui.TextInput(
			label=val_label,
			placeholder="Enter numeric value...",
			required=True
		)
		self.unit_input = discord.ui.TextInput(
			label=unit_label,
			placeholder=f"e.g. {unit_default}",
			default=unit_default,
			required=False
		)
		self.add_item(self.value_input)
		self.add_item(self.unit_input)

	async def on_submit(self, interaction: discord.Interaction):
		try:
			val_str = self.value_input.value.strip()
			value = float(val_str)
		except ValueError:
			await interaction.response.send_message("❌ Invalid number entered.", ephemeral=True)
			return
			
		unit = self.unit_input.value.strip().lower() if self.unit_input.value else ""
		title_label = "⚙️ CONVERSION RESULT"
		desc = ""
		
		if self.category == "climb":
			yds, french = ewbank_conversion(value)
			desc = (
				f"**Category:** Climbing Grades\n"
				f"**Source Ewbank:** `{value:.1f}`\n"
				f"— **YDS:** `{yds}`\n"
				f"— **French:** `{french}`"
			)
		elif self.category == "distance":
			if unit in ["ft", "feet", "foot"]:
				converted = value / 3.28084
				desc = (
					f"**Category:** Distance\n"
					f"**Source:** `{value:.2f} ft`\n"
					f"— **Meters:** `{converted:.2f} m`"
				)
			else:
				converted = value * 3.28084
				desc = (
					f"**Category:** Distance\n"
					f"**Source:** `{value:.2f} m`\n"
					f"— **Feet:** `{converted:.2f} ft`"
				)
		elif self.category == "mass":
			if unit in ["oz", "ounces", "ounce"]:
				converted = value * 28.3495
				desc = (
					f"**Category:** Mass\n"
					f"**Source:** `{value:.2f} oz`\n"
					f"— **Grams:** `{converted:.2f} g`"
				)
			else:
				converted = value / 28.3495
				desc = (
					f"**Category:** Mass\n"
					f"**Source:** `{value:.2f} g`\n"
					f"— **Ounces:** `{converted:.2f} oz`"
				)
		elif self.category == "volume":
			if unit in ["oz", "fl oz", "fluid oz", "ounces"]:
				converted = value / 33.814
				desc = (
					f"**Category:** Volume\n"
					f"**Source:** `{value:.2f} fl oz`\n"
					f"— **Liters:** `{converted:.2f} L`"
				)
			else:
				converted = value * 33.814
				desc = (
					f"**Category:** Volume\n"
					f"**Source:** `{value:.2f} L`\n"
					f"— **Fluid Oz:** `{converted:.2f} fl oz`"
				)
		elif self.category == "currency":
			if unit in ["usd", "$"]:
				converted = value / 0.66
				desc = (
					f"**Category:** Currency\n"
					f"**Source:** `${value:.2f} USD`\n"
					f"— **AUD:** `${converted:.2f} AUD` *(rate: 0.66)*"
				)
			else:
				converted = value * 0.66
				desc = (
					f"**Category:** Currency\n"
					f"**Source:** `${value:.2f} AUD`\n"
					f"— **USD:** `${converted:.2f} USD` *(rate: 0.66)*"
				)
				
		embed = make_embed(title_label, desc, OREGON_GREEN, timestamp=True)
		await interaction.response.edit_message(embed=embed, view=ConversionDashboardView())


@bot.tree.command(name="convert", description="Backcountry unit conversion engine.")
@app_commands.describe(
	value="Number to convert.",
	unit_type="Unit type category.",
	interface_mode="Use direct response or interactive dashboard embed."
)
@app_commands.choices(
	unit_type=[
		app_commands.Choice(name="🧗 Ewbank → YDS / French", value="ewbank"),
		app_commands.Choice(name="📏 Meters → Feet", value="meters"),
		app_commands.Choice(name="⚖️ Grams → Ounces", value="grams"),
		app_commands.Choice(name="💧 Liters → Fluid Oz", value="liters"),
		app_commands.Choice(name="💵 AUD → USD", value="aud")
	],
	interface_mode=[
		app_commands.Choice(name="Direct Text Response", value="direct"),
		app_commands.Choice(name="Interactive Dashboard", value="embed")
	]
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def convert_cmd(
	interaction: discord.Interaction,
	value: Optional[float] = None,
	unit_type: Optional[app_commands.Choice[str]] = None,
	interface_mode: str = "direct"
) -> None:
	if interface_mode == "direct" and value is not None and unit_type is not None:
		unit = unit_type.value
		if unit == "ewbank":
			yds, french = ewbank_conversion(value)
			desc = f"**Ewbank:** `{value:.1f}`\n**YDS:** `{yds}`\n**French:** `{french}`"
		elif unit == "meters":
			desc = f"**Meters:** `{value:.2f}`\n**Feet:** `{value * 3.28084:.2f}`"
		elif unit == "grams":
			desc = f"**Grams:** `{value:.2f}`\n**Ounces:** `{value / 28.3495:.2f}`"
		elif unit == "liters":
			desc = f"**Liters:** `{value:.2f}`\n**US Fluid Oz:** `{value * 33.814:.2f}`"
		else:
			desc = f"**AUD:** `{value:.2f}`\n**USD:** `{value * 0.66:.2f}` *(rate: 0.66)*"
		await interaction.response.send_message(embed=make_embed("⚙️ CONVERSION TELEMETRY", desc, OREGON_GREEN))
		return
		
	view = ConversionDashboardView()
	embed = make_embed(
		"⚙️ CONVERSION DASHBOARD",
		"Select a category below to perform conversions. This interface supports multi-unit conversions via modals.",
		OREGON_GREEN
	)
	await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="repeater", description="VHF/UHF repeater directory tracker.")
@app_commands.describe(lat="Latitude (decimal).", lon="Longitude (decimal).", radius_km="Search radius in km.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def repeater_cmd(interaction: discord.Interaction, lat: float, lon: float, radius_km: float = 50.0) -> None:
	private = await current_visibility(interaction.user.id)
	
	if not REPEATERBOOK_API_KEY:
		embed = make_embed(
			"❌ CONFIGURATION ERROR",
			"The `REPEATERBOOK_API_KEY` configuration environment variable is missing. "
			"Provide an API key to query the live RepeaterBook directory.",
			ERROR_RED,
			timestamp=True
		)
		await interaction.response.send_message(embed=embed, ephemeral=True)
		return

	await interaction.response.defer(ephemeral=private)

	# Query RepeaterBook proximity API
	url = f"https://www.repeaterbook.com/api/export.php?qtype=prox&lat={lat}&lng={lon}&dist={radius_km}&dunit=km"
	headers = {
		"X-RB-App-Token": REPEATERBOOK_API_KEY,
		"User-Agent": "squelchbot/1.0 (discord bot for tracking and navigation; contact: operator@squelch.net)"
	}
	
	try:
		status, payload, text = await fetch_json_with_headers(url, headers)
		if status == 200 and isinstance(payload, list) and len(payload) > 0:
			# Format first 5 repeaters
			lines = []
			for idx, rep in enumerate(payload[:5]):
				pl_tone = rep.get('pl_tone', 'N/A')
				if pl_tone:
					pl_tone = f"{pl_tone} Hz"
				else:
					pl_tone = "None"
				lines.append(
					f"**{idx+1}. {rep.get('callsign', 'N/A')}**\n"
					f"— Output: `{rep.get('frequency', 'N/A')} MHz` | Input: `{rep.get('input_freq', 'N/A')} MHz` ({rep.get('offset', 'N/A')})\n"
					f"— Tone: `{pl_tone}` | Location: `{rep.get('location', 'N/A')}, {rep.get('state', 'N/A')}`\n"
					f"— Notes: {rep.get('notes', 'None')}"
				)
			desc = f"🛰️ **[LIVE REPEATERBOOK FEED ACTIVE]**\nFound {len(payload)} repeaters within {radius_km} km. Showing closest 5:\n\n" + "\n\n".join(lines)
			await interaction.followup.send(
				embed=make_embed("📡 LIVE REPEATER CHANNEL SEARCH", desc, OREGON_GREEN, timestamp=True),
				view=RepeaterLinksView(f"{lat:.5f},{lon:.5f}"),
				ephemeral=private
			)
			return
	except Exception as e:
		await write_error_log(f"RepeaterBook API error: {str(e)}")
		
	# Fallback/Empty message
	desc = (
		f"❌ **NO REPEATERS FOUND**\n"
		f"No repeaters were returned within `{radius_km} km` of `{lat:.5f}, {lon:.5f}`. "
		f"Verify the coordinates or try expanding your search radius."
	)
	await interaction.followup.send(
		embed=make_embed("📡 LIVE REPEATER CHANNEL SEARCH", desc, ERROR_RED, timestamp=True),
		view=RepeaterLinksView(f"{lat:.5f},{lon:.5f}"),
		ephemeral=private
	)


@bot.tree.command(name="declination", description="Estimated magnetic declination for compass work.")
@app_commands.describe(lat="Latitude (decimal).", lon="Longitude (decimal).")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def declination_cmd(interaction: discord.Interaction, lat: float, lon: float) -> None:
	private = await current_visibility(interaction.user.id)
	
	if not NOAA_GEOMAG_API_KEY:
		embed = make_embed(
			"❌ CONFIGURATION ERROR",
			"The `NOAA_GEOMAG_API_KEY` configuration environment variable is missing. "
			"Provide a registered API key on Koyeb to enable live declination calculations.",
			ERROR_RED,
			timestamp=True
		)
		await interaction.response.send_message(embed=embed, ephemeral=True)
		return

	await interaction.response.defer(ephemeral=private)

	url = f"https://www.ngdc.noaa.gov/geomag-web/calculators/calculateDeclination?lat1={lat}&lon1={lon}&key={NOAA_GEOMAG_API_KEY}&resultFormat=json"
	
	dec = None
	fallback = False
	
	try:
		status, payload, text = await fetch_json_with_headers(url, {"User-Agent": "SquelchBot/1.0"})
		if status == 200 and isinstance(payload, dict) and "result" in payload:
			results = payload["result"]
			if isinstance(results, list) and len(results) > 0:
				dec = float(results[0].get("declination", 0))
	except Exception:
		pass

	if dec is None:
		dec = (lat * 0.1) - (lon * 0.05)
		fallback = True

	hemi = "east" if dec > 0 else "west"
	adjust = "subtract from true heading" if dec > 0 else "add to true heading"
	
	notice = "📡 **[LIVE NOAA DATA ACTIVE]**"
	if fallback:
		notice = "⚠️ **[LOCAL FALLBACK MATH ACTIVE]**\n*The NOAA API is currently offline or unreachable. Using trigonometric approximation.*"

	desc = (
		f"{notice}\n\n"
		f"**Coordinates:** `{lat:.5f}, {lon:.5f}`\n"
		f"**Estimated Declination:** `{dec:+.2f}°`\n"
		f"**Direction:** `{abs(dec):.2f}° {hemi}`\n"
		f"**Compass Guideline:** {adjust}\n\n"
		f"*Verify with an official map or chart for critical navigation.*"
	)
	
	await interaction.followup.send(
		embed=make_embed("🧭 MAGNETIC DECLINATION", desc, OREGON_GREEN, timestamp=True),
		view=DeclinationLinksView(),
		ephemeral=private
	)


@bot.tree.command(name="commslog", description="Append a timestamped entry to the comms history log.")
@app_commands.describe(entry="Log entry text.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def commslog_cmd(interaction: discord.Interaction, entry: str) -> None:
	ts    = utc_now().strftime("%Y-%m-%d %H:%M:%SZ")
	clean = clean_text(entry)
	if bot.db_pool:
		try:
			async with bot.db_pool.acquire() as conn:
				await conn.execute("INSERT INTO comms_log (ts, entry) VALUES ($1, $2)", ts, clean)
		except Exception as exc:
			await write_error_log(f"comms row insertion fault: {exc}")
	await interaction.response.send_message(
		embed=make_embed("📻 COMMS LOG UPDATED", f"Stored at `{ts}`\n\n`{clean}`", OREGON_GREEN, timestamp=True)
	)


@bot.tree.command(name="reminders", description="Create, list, complete, or delete reminders.")
@app_commands.describe(action="Action to perform.", minutes="Minutes from now (for add).", note="Reminder text (for add).", reminder_id="Reminder ID (for done/delete).")
@app_commands.choices(action=[
	app_commands.Choice(name="➕ Add",    value="add"),
	app_commands.Choice(name="📋 List",   value="list"),
	app_commands.Choice(name="✅ Done",   value="done"),
	app_commands.Choice(name="🗑️ Delete", value="delete"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def reminders_command(interaction: discord.Interaction, action: app_commands.Choice[str], minutes: Optional[int] = None, note: Optional[str] = None, reminder_id: Optional[int] = None) -> None:
	user_id  = interaction.user.id
	act      = action.value

	if not bot.db_pool:
		await interaction.response.send_message("Database layer unavailable.", ephemeral=True)
		return

	async with bot.db_pool.acquire() as conn:
		if act == "add":
			if not minutes or minutes <= 0:
				await mode_send(interaction, content="Provide a positive number of minutes.", ephemeral=True)
				return
			if not note:
				await mode_send(interaction, content="Provide reminder text.", ephemeral=True)
				return
			due_str = (utc_now() + timedelta(minutes=minutes)).isoformat()
			created_str = utc_now().isoformat()
			
			row_id = await conn.fetchval(
				"INSERT INTO reminders (user_id, note, due_at, channel_id, created_at, sent) VALUES ($1, $2, $3, $4, $5, FALSE) RETURNING id",
				user_id, clean_text(note), due_str, interaction.channel_id, created_str
			)
			await mode_send(interaction, embed=make_embed("🔔 REMINDER STAGED", f"**#{row_id}** fires in {minutes} min\n\n{clean_text(note)}", OREGON_GREEN, timestamp=True))
			return

		if act == "list":
			rows = await conn.fetch("SELECT * FROM reminders WHERE user_id = $1 AND deleted = FALSE AND done = FALSE", user_id)
			if not rows:
				await mode_send(interaction, content="No active reminders.", ephemeral=True)
				return
			lines = [reminder_label(dict(r)) for r in sorted(rows, key=lambda x: x["id"])]
			await mode_send(interaction, embed=make_embed("🔔 REMINDERS", "\n".join(lines), OREGON_GREEN), ephemeral=True)
			return

		if reminder_id is None:
			await mode_send(interaction, content="Provide a reminder ID.", ephemeral=True)
			return
			
		match = await conn.fetchrow("SELECT id FROM reminders WHERE id = $1 AND user_id = $2 AND deleted = FALSE", reminder_id, user_id)
		if match is None:
			await mode_send(interaction, content="Reminder not found.", ephemeral=True)
			return
			
		if act == "done":
			await conn.execute("UPDATE reminders SET done = TRUE, done_at = $1 WHERE id = $2", utc_now().isoformat(), reminder_id)
		elif act == "delete":
			await conn.execute("UPDATE reminders SET deleted = TRUE, deleted_at = $1 WHERE id = $2", utc_now().isoformat(), reminder_id)
			
		await mode_send(interaction, embed=make_embed("✅ REMINDER UPDATED", f"Reminder **#{reminder_id}** marked `{act}`.", OREGON_GREEN))


@bot.tree.command(name="deadrop", description="Store, retrieve, list, or remove a dead drop note.")
@app_commands.describe(action="Action.", key="Dead drop key.", content="Secret content (create).", secret="Optional passphrase.", deadrop_id="Numeric ID (delete/retrieve).")
@app_commands.choices(action=[
	app_commands.Choice(name="🔐 Create",   value="create"),
	app_commands.Choice(name="🔓 Retrieve", value="retrieve"),
	app_commands.Choice(name="📋 List",     value="list"),
	app_commands.Choice(name="🗑️ Delete",   value="delete"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def deadrop_command(interaction: discord.Interaction, action: app_commands.Choice[str], key: Optional[str] = None, content: Optional[str] = None, secret: Optional[str] = None, deadrop_id: Optional[int] = None) -> None:
	act     = action.value
	user_id = interaction.user.id

	if not bot.db_pool:
		await interaction.response.send_message("Database layer unavailable.", ephemeral=True)
		return

	async with bot.db_pool.acquire() as conn:
		if act == "create":
			if not key or not content:
				await mode_send(interaction, content="Provide both key and content.", ephemeral=True)
				return
			row_id = await conn.fetchval(
				"INSERT INTO deadrops (user_id, key, content, secret, created_at, deleted) VALUES ($1, $2, $3, $4, $5, FALSE) RETURNING id",
				user_id, clean_text(key), content, clean_text(secret or ""), utc_now().isoformat()
			)
			await mode_send(interaction, embed=make_embed("🔐 DEADROP CREATED", f"Stored `{clean_text(key)}` as **#{row_id}**.", OREGON_GREEN), ephemeral=True)
			return

		if act == "list":
			rows = await conn.fetch("SELECT id, key FROM deadrops WHERE deleted = FALSE")
			if not rows:
				await mode_send(interaction, content="No dead drops stored.", ephemeral=True)
				return
			lines = [f"**#{r['id']}** — `{r['key']}`" for r in rows]
			await mode_send(interaction, embed=make_embed("📋 DEADROP INDEX", "\n".join(lines), OREGON_GREEN), ephemeral=True)
			return

		if deadrop_id:
			match = await conn.fetchrow("SELECT * FROM deadrops WHERE id = $1 AND deleted = FALSE", deadrop_id)
		elif key:
			match = await conn.fetchrow("SELECT * FROM deadrops WHERE LOWER(key) = LOWER($1) AND deleted = FALSE", clean_text(key))
		else:
			match = None

		if match is None:
			await mode_send(interaction, content="Dead drop not found.", ephemeral=True)
			return

		stored_secret = clean_text(match["secret"] or "")
		if stored_secret and stored_secret != clean_text(secret or ""):
			await mode_send(interaction, content="Secret mismatch.", ephemeral=True)
			return

		if act == "retrieve":
			await mode_send(interaction, embed=make_embed(f"🔓 DEADROP #{match['id']}", match["content"], OREGON_GREEN), ephemeral=True)
			return

		if act == "delete":
			await conn.execute("UPDATE deadrops SET deleted = TRUE, deleted_at = $1 WHERE id = $2", utc_now().isoformat(), match["id"])
			await mode_send(interaction, embed=make_embed("🗑️ DEADROP DELETED", f"Dead drop **#{match['id']}** removed.", SAR_ORANGE), ephemeral=True)


@bot.tree.command(name="verify", description="Grant temporary session access to a user ID.")
@app_commands.describe(user="The target user profile to provision.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def verify_command(interaction: discord.Interaction, user: discord.User) -> None:
	allowed = set(await load_whitelist())
	if interaction.user.id not in allowed and interaction.user.id not in _TEMP_ALLOWED:
		await interaction.response.send_message("❌ SECURITY ERROR: CLEARANCE LEVEL INSUFFICIENT TO PROVISION ACCESS.", ephemeral=True)
		return

	_TEMP_ALLOWED.add(user.id)
	embed = make_embed(
		"🟩 TEMPORARY SESSION ACTIVE",
		f"Clearance initialized for operator profile **{user.display_name}** (`{user.id}`). clearance drops upon instance reboot.",
		OREGON_GREEN,
		timestamp=True
	)
	await interaction.response.send_message(embed=embed, ephemeral=True)


HELPLINES = {
    "australia": {
        "title": "🇦🇺 Critical National Services (All States)",
        "categories": {
            "🚨 Emergency Services": [
                "Triple Zero (Emergency): 000 (Police, Fire, Ambulance — 24/7)",
                "Police Assistance Line (Non-Emergency): 13 14 44",
                "State Emergency Service (SES): 13 25 00 (Storm, flood, and natural disasters)"
            ],
            "🏛️ Medicare & Government Services": [
                "Medicare Public Enquiries: 13 20 11 (General Medicare account help)",
                "My Health Record Helpline: 1800 723 471",
                "Centrelink (Employment Services): 13 28 50"
            ],
            "🩺 National Medical & Health Advice": [
                "Healthdirect / 1800MEDICARE: 1800 022 222 (24/7 registered nurse triage — known nationally as 1800MEDICARE, except VIC)",
                "Poisons Information Centre: 13 11 26 (24/7 advice on overdoses, bites, or toxins)",
                "National Coronavirus Helpline: 1800 020 080"
            ],
            "🧠 National Mental Health & Crisis Lines (24/7)": [
                "Lifeline: 13 11 14 (Crisis support and suicide prevention)",
                "Beyond Blue: 1300 22 4636",
                "Medicare Mental Health Phone Service: 1800 595 212 (Mon-Fri 8:30am–5pm)",
                "13YARN: 13 92 76 (Crisis support for Aboriginal and Torres Strait Islander people)",
                "Kids Helpline: 1800 55 1800 (For youth aged 5–25)",
                "Suicide Call Back Service: 1300 659 467",
                "MensLine Australia: 1300 78 99 78",
                "1800RESPECT: 1800 737 732 (Domestic, family, and sexual violence support)"
            ]
        },
        "states": {
            "nsw": [
                "Medical Triage: 1800 022 222 (Healthdirect NSW)",
                "Mental Health Line: 1800 011 511 (24/7 state-wide triage)"
            ],
            "vic": [
                "Medical Triage: 1300 60 60 24 (NURSE-ON-CALL Victoria)",
                "Mental Health Crisis Line: 1300 651 251 (Suicide Help Line / Triage)",
                "SuicideLine Victoria: 1300 651 251"
            ],
            "qld": [
                "Medical Triage: 13 43 25 84 (13 HEALTH QLD — alternate 13 43 25)",
                "Mental Health Line: 1300 64 22 55 (1300 MH CALL)"
            ],
            "sa": [
                "Medical Triage: 1800 022 222 (Healthdirect SA)",
                "Mental Health Line: 13 14 65 (Mental Health Triage Service)"
            ],
            "wa": [
                "Medical Triage: 1800 022 222 (Healthdirect WA)",
                "Mental Health Line (Metro): 1300 555 788 (Mental Health Emergency Response Line)",
                "Mental Health Line (Peel Region): 1800 676 822",
                "Mental Health Line (Rurallink): 1800 552 002"
            ],
            "tas": [
                "Medical Triage: 1800 022 222 (Healthdirect TAS)",
                "Mental Health Line: 1800 332 388 (Access Mental Health Helpline)"
            ],
            "act": [
                "Medical Triage: 1800 022 222 (Healthdirect ACT)",
                "Mental Health Line: 1800 629 354 (Access Mental Health Triage)"
            ],
            "nt": [
                "Medical Triage: 1800 022 222 (Healthdirect NT)",
                "Mental Health Line: 1800 682 288 (NT Mental Health Line)"
            ]
        }
    },
    "united_states": {
        "title": "🇺🇸 United States Core Helplines",
        "categories": {
            "🚨 Primary Response": [
                "Emergency Services: 911 (24/7 Dispatch Core)",
                "988 Suicide & Crisis Lifeline: 988 (24/7 Voice & Text Routing)"
            ],
            "☣️ Specialized Resources": [
                "National Poison Help Line: 1-800-222-1222 (24/7 Toxin Mitigation)",
                "SAMHSA National Helpline: 1-800-662-4357 (Substance Support Tracking)"
            ]
        }
    },
    "united_kingdom": {
        "title": "🇬🇧 United Kingdom Core Helplines",
        "categories": {
            "🚨 Emergency & Safety": [
                "Emergency Services: 999 or 112 (24/7 Fleet Dispatch)",
                "Police Non-Emergency Assistance Network: 101"
            ],
            "🩺 Medical & Crisis Routing": [
                "NHS Medical Triage Portal: 111 (Non-Emergency Advice)",
                "Samaritans Crisis Support Network: 116 123"
            ]
        }
    }
}


async def fetch_findahelpline_data(country_code: str, state_code: Optional[str] = None) -> Optional[List[str]]:
	if not FINDAHELPLINE_API_KEY:
		return None
		
	c_code = country_code.lower()
	if c_code == "australia":
		c_code = "au"
	elif c_code == "united_states":
		c_code = "us"
	elif c_code == "united_kingdom":
		c_code = "gb"
		
	url = f"https://api.findahelpline.com/v1/helplines?country_code={c_code}"
	if state_code:
		url += f"&state={state_code.lower()}"
		
	headers = {
		"Authorization": f"Bearer {FINDAHELPLINE_API_KEY}",
		"User-Agent": "SquelchBot/1.0"
	}
	
	try:
		status, payload, text = await fetch_json_with_headers(url, headers)
		if status == 200 and isinstance(payload, list):
			results = []
			for item in payload[:10]:
				name = item.get("name")
				phones = item.get("phones", [])
				phone_num = phones[0].get("number") if phones else None
				if name and phone_num:
					results.append(f"{name}: {phone_num}")
			if results:
				return results
	except Exception:
		pass
	return None


async def show_australia_dashboard(interaction: discord.Interaction, selected_state: Optional[str] = None, selected_category: Optional[str] = None):
	api_data = None
	if FINDAHELPLINE_API_KEY:
		s_code = selected_state if (selected_state and selected_state != "national") else None
		api_data = await fetch_findahelpline_data("australia", s_code)
		
	desc = "### 🇦🇺 Australia Crisis Registry\n"
	
	if api_data:
		desc += "📡 **[LIVE THROUGHLINE API ACTIVE]**\n\n"
		desc += "\n".join(f"• {item}" for item in api_data)
	else:
		desc += "🗃️ **[LOCAL DYNAMICS REGISTRY ACTIVE]**\n\n"
		if selected_state and selected_state != "national":
			desc += f"#### 📍 Regional Support ({selected_state.upper()})\n"
			state_list = HELPLINES["australia"]["states"].get(selected_state, [])
			desc += "\n".join(f"• {item}" for item in state_list) + "\n\n"
			
		cat_key = "🚨 Emergency Services"
		if selected_category == "medicare":
			cat_key = "🏛️ Medicare & Government Services"
		elif selected_category == "medical":
			cat_key = "🩺 National Medical & Health Advice"
		elif selected_category == "mental":
			cat_key = "🧠 National Mental Health & Crisis Lines (24/7)"
			
		desc += f"#### {cat_key}\n"
		cat_list = HELPLINES["australia"]["categories"].get(cat_key, [])
		desc += "\n".join(f"• {item}" for item in cat_list)
		
	embed = make_embed("🇦🇺 AUSTRALIA SUPPORT HUB", desc, OREGON_GREEN, timestamp=True)
	await interaction.response.edit_message(embed=embed, view=HelplineAustraliaView(selected_state))


async def show_us_dashboard(interaction: discord.Interaction):
	api_data = None
	if FINDAHELPLINE_API_KEY:
		api_data = await fetch_findahelpline_data("united_states")
		
	desc = "### 🇺🇸 United States Crisis Registry\n"
	
	if api_data:
		desc += "📡 **[LIVE THROUGHLINE API ACTIVE]**\n\n"
		desc += "\n".join(f"• {item}" for item in api_data)
	else:
		desc += "🗃️ **[LOCAL DYNAMICS REGISTRY ACTIVE]**\n\n"
		for cat, items in HELPLINES["united_states"]["categories"].items():
			desc += f"#### {cat}\n"
			desc += "\n".join(f"• {item}" for item in items) + "\n\n"
			
	embed = make_embed("🇺🇸 UNITED STATES SUPPORT HUB", desc, OREGON_GREEN, timestamp=True)
	await interaction.response.edit_message(embed=embed, view=HelplineBackView())


async def show_uk_dashboard(interaction: discord.Interaction):
	api_data = None
	if FINDAHELPLINE_API_KEY:
		api_data = await fetch_findahelpline_data("united_kingdom")
		
	desc = "### 🇬🇧 United Kingdom Crisis Registry\n"
	
	if api_data:
		desc += "📡 **[LIVE THROUGHLINE API ACTIVE]**\n\n"
		desc += "\n".join(f"• {item}" for item in api_data)
	else:
		desc += "🗃️ **[LOCAL DYNAMICS REGISTRY ACTIVE]**\n\n"
		for cat, items in HELPLINES["united_kingdom"]["categories"].items():
			desc += f"#### {cat}\n"
			desc += "\n".join(f"• {item}" for item in items) + "\n\n"
			
	embed = make_embed("🇬🇧 UNITED KINGDOM SUPPORT HUB", desc, OREGON_GREEN, timestamp=True)
	await interaction.response.edit_message(embed=embed, view=HelplineBackView())


class HelplineCountryView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=180)

	@discord.ui.button(label="Australia 🇦🇺", style=discord.ButtonStyle.primary, custom_id="hl_au")
	async def australia_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
		await show_australia_dashboard(interaction)

	@discord.ui.button(label="United States 🇺🇸", style=discord.ButtonStyle.primary, custom_id="hl_us")
	async def us_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
		await show_us_dashboard(interaction)

	@discord.ui.button(label="United Kingdom 🇬🇧", style=discord.ButtonStyle.primary, custom_id="hl_uk")
	async def uk_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
		await show_uk_dashboard(interaction)


class HelplineAustraliaView(discord.ui.View):
	def __init__(self, selected_state: Optional[str] = None):
		super().__init__(timeout=180)
		self.selected_state = selected_state
		self.add_item(HelplineStateSelect(selected_state))
		self.add_item(HelplineCategorySelect())

	@discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=2)
	async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
		embed = make_embed(
			"🏥 HELPLINE DYNAMICS REGISTRY",
			"Select a region below to retrieve critical emergency, medical, and mental health helpline contacts.",
			OREGON_GREEN
		)
		await interaction.response.edit_message(embed=embed, view=HelplineCountryView())


class HelplineStateSelect(discord.ui.Select):
	def __init__(self, selected: Optional[str] = None):
		options = [
			discord.SelectOption(label="National / All States", value="national", default=(selected is None or selected == "national")),
			discord.SelectOption(label="New South Wales (NSW)", value="nsw", default=(selected == "nsw")),
			discord.SelectOption(label="Victoria (VIC)", value="vic", default=(selected == "vic")),
			discord.SelectOption(label="Queensland (QLD)", value="qld", default=(selected == "qld")),
			discord.SelectOption(label="Western Australia (WA)", value="wa", default=(selected == "wa")),
			discord.SelectOption(label="South Australia (SA)", value="sa", default=(selected == "sa")),
			discord.SelectOption(label="Tasmania (TAS)", value="tas", default=(selected == "tas")),
			discord.SelectOption(label="ACT", value="act", default=(selected == "act")),
			discord.SelectOption(label="Northern Territory (NT)", value="nt", default=(selected == "nt"))
		]
		super().__init__(placeholder="Select State for regional numbers...", min_values=1, max_values=1, options=options, row=0)

	async def callback(self, interaction: discord.Interaction):
		state = self.values[0]
		await show_australia_dashboard(interaction, selected_state=state)


class HelplineCategorySelect(discord.ui.Select):
	def __init__(self):
		options = [
			discord.SelectOption(label="🚨 Emergency Services", value="emergency"),
			discord.SelectOption(label="🏛️ Medicare & Government Services", value="medicare"),
			discord.SelectOption(label="🩺 National Medical & Health Advice", value="medical"),
			discord.SelectOption(label="🧠 National Mental Health & Crisis Lines", value="mental")
		]
		super().__init__(placeholder="Select National Category...", min_values=1, max_values=1, options=options, row=1)

	async def callback(self, interaction: discord.Interaction):
		category = self.values[0]
		state = getattr(self.view, "selected_state", None)
		await show_australia_dashboard(interaction, selected_state=state, selected_category=category)


class HelplineBackView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=180)

	@discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary)
	async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
		embed = make_embed(
			"🏥 HELPLINE DYNAMICS REGISTRY",
			"Select a region below to retrieve critical emergency, medical, and mental health helpline contacts.",
			OREGON_GREEN
		)
		await interaction.response.edit_message(embed=embed, view=HelplineCountryView())


@bot.tree.command(name="helpline", description="Access local and national support resources.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def helpline_cmd(interaction: discord.Interaction) -> None:
	embed = make_embed(
		"🏥 HELPLINE DYNAMICS REGISTRY",
		"Select a region below to retrieve critical emergency, medical, and mental health helpline contacts.",
		OREGON_GREEN
	)
	await interaction.response.send_message(embed=embed, view=HelplineCountryView())


# ═══════════════════════════════════════════════════════════════
#  BOT EVENTS
# ═══════════════════════════════════════════════════════════════

@bot.event
async def on_message(message: discord.Message) -> None:
	if message.author.bot:
		return
	await log_blackbox_message(message)
	await bot.process_commands(message)


@bot.event
async def on_ready() -> None:
	print(f"Squelch online as {bot.user} on port {PORT}")


# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main() -> None:
	if not TOKEN:
		raise RuntimeError("DISCORD_TOKEN is required. Set it as an environment variable on Koyeb.")
	bot.run(TOKEN)


if __name__ == "__main__":
	main()
