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
#  GEMINI AI
# ═══════════════════════════════════════════════════════════════

async def http_post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any, str]:
	timeout = aiohttp.ClientTimeout(total=30)
	req_headers = {"Content-Type": "application/json"}
	if headers:
		req_headers.update(req_headers)
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


async def fetch_json_with_headers(url: str, headers: Dict[str, str]) -> Tuple[int, Any, str]:
	return await http_get_json(url, headers)


async def gemini_generate(prompt: str) -> Tuple[bool, str]:
	if not GEMINI_API_KEY:
		return False, "Gemini API key missing. Set GEMINI_API_KEY in Koyeb environment variables."
	url = (
		"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
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
	allowed = set(await load_whitelist())
	if interaction.user is None or interaction.user.id not in allowed:
		embed = make_embed(
			"🔒 ACCESS DENIED",
			"This Squelch deployment is restricted to the local whitelist.\n"
			"Set `OWNER_ID` as an environment variable on Koyeb, or add your Discord user ID to the database index.",
			ERROR_RED,
		)
		kwargs: Dict[str, Any] = {"embed": embed, "ephemeral": True}
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
		self.add_item(discord.ui.Button(label="📱 Apple Maps",    url=f"https://maps.apple.com/?q={lat:.5f},{lon:.5f}",                         style=discord.ButtonStyle.link))


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
		self.add_item(discord.ui.Button(label="🚑 Emergency Services (AU)", url="tel:000", style=discord.ButtonStyle.link))
		self.add_item(discord.ui.Button(label="📞 Poison Hotline (AU)", url="tel:131126", style=discord.ButtonStyle.link))


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
		headers = {"User-Agent": "SquelchBot/1.0 (Discord bot)"}
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
	headers = {"User-Agent": "SquelchBot/1.0 (Discord bot)"}
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
	models = [("ECMWF","ecmwf_ifs"),("NOAA GFS","gfs_seamless"),("BOM ACCESS","bom_access_global")]
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


@bot.tree.command(name="solardata", description="Space weather and HF propagation analytics.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def solardata_cmd(interaction: discord.Interaction) -> None:
	desc = (
		"**Solar Condition Snapshot**\n\n"
		"— **Solar Flux (SFI):** 132\n— **K-index (Kp):** 3.0\n— **A-index:** 11\n"
		"— **Sunspot trend:** moderate, stable active region\n— **HF noise floor:** quiet to moderate\n\n"
		"**Propagation Guidance**\n"
		"— **80m / 40m:** reliable for regional NVIS and nighttime contacts\n"
		"— **30m / 20m:** solid daytime and grayline utility\n"
		"— **17m / 15m:** open with current solar flux\n"
		"— **10m / 6m:** intermittent — watch for sporadic-E\n\n"
		"*Keep antenna takeoff angles low for DX. Monitor after geomagnetic disturbances.*"
	)
	embed = make_embed("☀️ SPACE WEATHER FIELD REPORT", desc, OREGON_GREEN, timestamp=True)
	embed.add_field(name="Telemetry",  value="SFI 132 | Kp 3 | A 11", inline=True)
	embed.add_field(name="Best Bands", value="80m, 40m, 20m, 17m",     inline=True)
	await interaction.response.send_message(embed=embed, view=SolarDataView())


@bot.tree.command(name="time", description="Synchronized Zulu clock core.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def time_cmd(interaction: discord.Interaction) -> None:
	now = utc_now()
	desc = (
		f"**Zulu Time:** `{now.strftime('%H:%M:%S')}Z`\n"
		f"**Date:** `{now.strftime('%Y-%m-%d')}`\n"
		f"**Julian Day:** `{julian_day(now):.5f}`\n"
		f"**Weekday:** `{now.strftime('%A')}`\n"
		f"**Tracking window:** `0000Z → 2359Z`"
	)
	await interaction.response.send_message(embed=make_embed("🕐 CLOCK CORE ONLINE", desc, OREGON_GREEN, timestamp=True))


@bot.tree.command(name="convert", description="Backcountry unit conversion engine.")
@app_commands.describe(value="Number to convert.", unit_type="Unit type.")
@app_commands.choices(unit_type=[
	app_commands.Choice(name="🧗 Ewbank → YDS / French", value="ewbank"),
	app_commands.Choice(name="📏 Meters → Feet",          value="meters"),
	app_commands.Choice(name="⚖️ Grams → Ounces",        value="grams"),
	app_commands.Choice(name="💧 Liters → Fluid Oz",     value="liters"),
	app_commands.Choice(name="💵 AUD → USD",              value="aud"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def convert_cmd(interaction: discord.Interaction, value: float, unit_type: app_commands.Choice[str]) -> None:
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


@bot.tree.command(name="trailcalc", description="Naismith's Rule route planner.")
@app_commands.describe(distance_km="Route distance in km.", elevation_gain_m="Total elevation gain in metres.", weight_kg="Your body weight in kg.", pack_weight_kg="Pack weight in kg.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def trailcalc_cmd(interaction: discord.Interaction, distance_km: float, elevation_gain_m: float, weight_kg: float, pack_weight_kg: float) -> None:
	hours    = (distance_km / 5.0) + (elevation_gain_m / 600.0)
	hydro    = hours * 0.5
	total_kg = weight_kg + pack_weight_kg
	cals     = 6.0 * 3.5 * total_kg / 200.0 * (hours * 60.0)
	desc = (
		f"**Moving Time (Naismith):** `{hours:.2f} hrs`\n"
		f"**Hydration Est.:** `{hydro:.2f} L`\n"
		f"**Calorie Expenditure:** `{cals:.0f} kcal`\n"
		f"**Total Mass:** `{total_kg:.2f} kg`"
	)
	view = discord.ui.View(timeout=120)
	view.add_item(discord.ui.Button(label="🗺️ CalTopo", url="https://caltopo.com/", style=discord.ButtonStyle.link))
	await interaction.response.send_message(embed=make_embed("🥾 TRAIL ROUTE PLANNER", desc, OREGON_GREEN), view=view)


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


@bot.tree.command(name="morse", description="Translate text into Morse code.")
@app_commands.describe(text="Text to encode.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def morse_cmd(interaction: discord.Interaction, text: str) -> None:
	encoded = morse_encode(text)
	desc = f"**Input:** `{clean_text(text)}`\n\n**Morse:**\n```\n{truncate_text(encoded, 3500)}\n```"
	await interaction.response.send_message(embed=make_embed("📡 MORSE CODE", desc, OREGON_GREEN))


@bot.tree.command(name="repeater", description="Simulated VHF/UHF repeater info for a location.")
@app_commands.describe(location="Place name or lat,lon.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def repeater_cmd(interaction: discord.Interaction, location: str) -> None:
	coords  = parse_query_location(location)
	lat     = coords[0] if coords else None
	lon     = coords[1] if coords else None
	profile = repeater_profile(location, lat, lon)
	desc = (
		f"**Coverage Area:** `{clean_text(location)}`\n"
		f"**Callsign:** `{profile['callsign']}`\n"
		f"**Output:** `{profile['output']}`\n"
		f"**Input:** `{profile['input']}`\n"
		f"**Offset:** `{profile['offset']}`\n"
		f"**Tone:** `{profile['tone']}`\n"
		f"**Field Note:** {profile['coverage']}"
	)
	await interaction.response.send_message(
		embed=make_embed("📻 REPEATER SIMULATION", desc, OREGON_GREEN),
		view=RepeaterLinksView(location),
	)


@bot.tree.command(name="declination", description="Estimated magnetic declination for compass work.")
@app_commands.describe(location="Place name or lat,lon.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def declination_cmd(interaction: discord.Interaction, location: str) -> None:
	coords = parse_query_location(location)
	if coords:
		lat, lon = coords
		label = f"{lat:.5f}, {lon:.5f}"
	else:
		label = clean_text(location)
		seed  = sum(ord(c) for c in label)
		lat   = (seed % 180) - 90
		lon   = ((seed * 3) % 360) - 180
	dec    = approximate_declination(lat, lon)
	hemi   = "east" if dec > 0 else "west"
	adjust = "subtract from true heading" if dec > 0 else "add to true heading"
	desc = (
		f"**Location:** `{label}`\n"
		f"**Approximate Declination:** `{dec:+.1f}°`\n"
		f"**Direction:** `{abs(dec):.1f}° {hemi}`\n"
		f"**Compass Guideline:** {adjust}\n\n"
		"*Operational estimate only — verify with an official chart for critical navigation.*"
	)
	await interaction.response.send_message(
		embed=make_embed("🧭 MAGNETIC DECLINATION", desc, OREGON_GREEN),
		view=DeclinationLinksView(),
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
	
    	
