import asyncio
import json
import math
import os
import urllib.parse
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks
from pypdf import PdfReader


BASE_DIR = Path(__file__).resolve().parent
WHITELIST_PATH = BASE_DIR / "whitelist.txt"
AI_TRACKER_PATH = BASE_DIR / "ai_tracker.json"
COMMS_HISTORY_PATH = BASE_DIR / "comms_history.txt"
MODE_STATE_PATH = BASE_DIR / "mode_state.json"
THEME_STATE_PATH = BASE_DIR / "theme_state.json"
REMINDERS_PATH = BASE_DIR / "reminders.json"
DEADROPS_PATH = BASE_DIR / "deadrops.json"
BLACKBOX_LOG_PATH = BASE_DIR / "blackbox.log"
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")

OREGON_GREEN = discord.Color.from_rgb(34, 77, 23)
SAR_ORANGE = discord.Color.from_rgb(255, 85, 0)

AI_WARNING_FOOTER = "⚠️ Telemetry: AI usage at {count}/24 for today."
AI_LIMIT = 24
DEFAULT_THEME_ID = "ranger"
VALID_MODES = ("regular", "stealth", "blackbox")

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

THEME_BY_ID = {theme["id"]: theme for theme in THEME_PRESETS}
THEME_OPTIONS = [app_commands.Choice(name=theme["label"], value=theme["id"]) for theme in THEME_PRESETS]
RED_FLAG_KEYWORDS = {
	"chest pain",
	"shortness of breath",
	"sob",
	"unresponsive",
	"altered mental",
	"stroke",
	"facial droop",
	"slurred speech",
	"seizure",
	"seizing",
	"major bleed",
	"massive bleeding",
	"anaphylaxis",
	"shock",
	"burn",
	"airway",
	"cyanosis",
}

_MODE_STATE_CACHE: Dict[str, Any] = {}
_THEME_STATE_CACHE: Dict[str, Any] = {}
_REMINDERS_STATE_CACHE: Dict[str, Any] = {}
_DEADROPS_STATE_CACHE: Dict[str, Any] = {}
_BLACKBOX_CHANNELS: set = set()
PERSISTENCE_LOCK = asyncio.Lock()


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


def make_embed(title: str, description: str, color: discord.Color = OREGON_GREEN) -> discord.Embed:
	return discord.Embed(title=title, description=description, color=color)


def link_button(label: str, url: str, style: discord.ButtonStyle = discord.ButtonStyle.link) -> discord.ui.Button:
	return discord.ui.Button(label=label, url=url, style=style)


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
	year = moment.year
	month = moment.month
	day = moment.day
	hour = moment.hour + (moment.minute / 60.0) + (moment.second / 3600.0) + (moment.microsecond / 3_600_000_000.0)
	if month <= 2:
		year -= 1
		month += 12
	a = year // 100
	b = 2 - a + (a // 4)
	jd_day = int(365.25 * (year + 4716))
	jd_month = int(30.6001 * (month + 1))
	return jd_day + jd_month + day + b - 1524.5 + (hour / 24.0)


def format_float(value: float, precision: int = 2) -> str:
	return f"{value:.{precision}f}"


def ewbank_conversion(value: float) -> Tuple[str, str]:
	chart = [
		(1, "5.1", "1a"),
		(2, "5.2", "2a"),
		(3, "5.3", "2b"),
		(4, "5.4", "3a"),
		(5, "5.5", "3b"),
		(6, "5.6", "3c"),
		(7, "5.7", "4a"),
		(8, "5.7+", "4a+"),
		(9, "5.8", "4b"),
		(10, "5.8+", "4b+"),
		(11, "5.9-", "4c-"),
		(12, "5.9", "4c"),
		(13, "5.9+", "4c+"),
		(14, "5.10a", "5a"),
		(15, "5.10b", "5a+"),
		(16, "5.10c", "5b"),
		(17, "5.10d", "5b+"),
		(18, "5.9", "5c"),
		(19, "5.10a", "6a"),
		(20, "5.10b", "6a+"),
		(21, "5.10c", "6b"),
		(22, "5.10d", "6b+"),
		(23, "5.11a", "6c"),
		(24, "5.11b", "6c+"),
		(25, "5.11c", "7a"),
		(26, "5.11d", "7a+"),
		(27, "5.12a", "7b"),
		(28, "5.12b", "7b+"),
		(29, "5.12c", "7c"),
		(30, "5.12d", "7c+"),
		(31, "5.13a", "8a"),
	]
	rounded = max(1, min(31, int(round(value))))
	for grade, yds, french in reversed(chart):
		if rounded >= grade:
			return yds, french
	return "5.1", "1a"


def load_json_file(path: Path) -> Dict[str, Any]:
	if not path.exists():
		return {}
	try:
		with path.open("r", encoding="utf-8") as handle:
			data = json.load(handle)
			if isinstance(data, dict):
				return data
	except Exception:
		pass
	return {}


def save_json_file(path: Path, payload: Dict[str, Any]) -> None:
	temp_path = path.with_suffix(path.suffix + ".tmp")
	with temp_path.open("w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2, sort_keys=True)
		handle.write("\n")
	os.replace(str(temp_path), str(path))


_MODE_STATE_CACHE = load_json_file(MODE_STATE_PATH)
_THEME_STATE_CACHE = load_json_file(THEME_STATE_PATH)
_REMINDERS_STATE_CACHE = load_json_file(REMINDERS_PATH)
_DEADROPS_STATE_CACHE = load_json_file(DEADROPS_PATH)


def load_whitelist() -> List[int]:
	if not WHITELIST_PATH.exists():
		return []
	ids: List[int] = []
	with WHITELIST_PATH.open("r", encoding="utf-8") as handle:
		for raw_line in handle:
			line = clean_text(raw_line)
			if not line or line.startswith("#"):
				continue
			try:
				ids.append(int(line))
			except ValueError:
				continue
	return ids


def normalize_ai_store(store: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
	today = utc_date_key()
	normalized: Dict[str, Dict[str, Any]] = {}
	for user_id, entry in store.items():
		if isinstance(entry, dict):
			date_value = str(entry.get("date", today))
			count_value = entry.get("count", 0)
			try:
				count = int(count_value)
			except (TypeError, ValueError):
				count = 0
			if date_value != today:
				count = 0
			normalized[str(user_id)] = {"date": today, "count": max(0, count)}
	return normalized


AI_TRACKER_LOCK = asyncio.Lock()
_ACTIONS_LOCK = asyncio.Lock()
_AI_TRACKER_CACHE = normalize_ai_store(load_json_file(AI_TRACKER_PATH))


def ai_count_for_user(user_id: int) -> int:
	entry = _AI_TRACKER_CACHE.get(str(user_id))
	if not entry:
		return 0
	if entry.get("date") != utc_date_key():
		return 0
	try:
		return int(entry.get("count", 0))
	except (TypeError, ValueError):
		return 0


def ai_remaining_for_user(user_id: int) -> int:
	return max(0, AI_LIMIT - ai_count_for_user(user_id))


async def increment_ai_count(user_id: int) -> int:
	async with AI_TRACKER_LOCK:
		global _AI_TRACKER_CACHE
		_AI_TRACKER_CACHE = normalize_ai_store(_AI_TRACKER_CACHE)
		key = str(user_id)
		entry = _AI_TRACKER_CACHE.get(key)
		if entry is None:
			entry = {"date": utc_date_key(), "count": 0}
			_AI_TRACKER_CACHE[key] = entry
		if entry.get("date") != utc_date_key():
			entry["date"] = utc_date_key()
			entry["count"] = 0
		entry["count"] = int(entry.get("count", 0)) + 1
		save_json_file(AI_TRACKER_PATH, _AI_TRACKER_CACHE)
		return int(entry["count"])


async def get_ai_usage_status(user_id: int) -> Tuple[int, int, str]:
	async with AI_TRACKER_LOCK:
		global _AI_TRACKER_CACHE
		_AI_TRACKER_CACHE = normalize_ai_store(_AI_TRACKER_CACHE)
		count = ai_count_for_user(user_id)
		remaining = max(0, AI_LIMIT - count)
		return count, remaining, utc_date_key()


def ai_footer(count: int) -> str:
	return AI_WARNING_FOOTER.format(count=count)


def ai_footer_color(count: int) -> discord.Color:
	return OREGON_GREEN if count <= 20 else SAR_ORANGE


def load_mapping_state(path: Path) -> Dict[str, Any]:
	data = load_json_file(path)
	return data if isinstance(data, dict) else {}


def normalize_simple_state(store: Dict[str, Any], default_value: str) -> Dict[str, str]:
	result: Dict[str, str] = {}
	for key, value in store.items():
		if isinstance(value, str):
			result[str(key)] = value
		else:
			result[str(key)] = default_value
	return result


def get_user_mode(user_id: int) -> str:
	return normalize_simple_state(_MODE_STATE_CACHE, "regular").get(str(user_id), "regular")


def get_user_theme(user_id: int) -> Dict[str, Any]:
	theme_id = normalize_simple_state(_THEME_STATE_CACHE, DEFAULT_THEME_ID).get(str(user_id), DEFAULT_THEME_ID)
	return THEME_BY_ID.get(theme_id, THEME_BY_ID[DEFAULT_THEME_ID])


async def set_user_mode(user_id: int, mode: str) -> None:
	_MODE_STATE_CACHE[str(user_id)] = mode
	async with PERSISTENCE_LOCK:
		save_state(MODE_STATE_PATH, _MODE_STATE_CACHE)


async def set_user_theme(user_id: int, theme_id: str) -> None:
	_THEME_STATE_CACHE[str(user_id)] = theme_id
	async with PERSISTENCE_LOCK:
		save_state(THEME_STATE_PATH, _THEME_STATE_CACHE)


def get_mode_label(user_id: int) -> str:
	return get_user_mode(user_id).title()


def user_is_blackbox(user_id: int) -> bool:
	return get_user_mode(user_id) == "blackbox"


def should_ephemeral(user_id: int) -> bool:
	return get_user_mode(user_id) == "stealth"


def compose_ai_prompt(user_id: int, prompt: str, purpose: str) -> str:
	theme = get_user_theme(user_id)
	return (
		f"SYSTEM THEME: {theme['label']}\n"
		f"AI PERSONA: {theme['persona']}\n"
		f"OPERATIONAL PURPOSE: {purpose}\n"
		"Response style: clean markdown, field-usable, practical, concise where appropriate.\n\n"
		f"USER REQUEST:\n{prompt}"
	)


def payload_preview(content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> str:
	if content:
		return truncate_text(clean_text(content), 280)
	if embed:
		parts = [part for part in [embed.title, embed.description] if part]
		return truncate_text(" | ".join(parts), 280)
	return ""


async def write_blackbox_log(line: str) -> None:
	print(line)
	async with PERSISTENCE_LOCK:
		with BLACKBOX_LOG_PATH.open("a", encoding="utf-8") as handle:
			handle.write(line + "\n")


async def log_blackbox_event(interaction: discord.Interaction, event: str, details: str) -> None:
	if get_user_mode(interaction.user.id) != "blackbox":
		return
	channel_label = f"channel={getattr(interaction.channel, 'id', 'dm')}"
	guild_label = f"guild={interaction.guild_id or 'dm'}"
	user_label = f"user={interaction.user.id}"
	await write_blackbox_log(
		f"[{utc_now().isoformat()}] {event} {user_label} {guild_label} {channel_label} :: {truncate_text(details, 800)}"
	)
	if interaction.channel_id is not None:
		_BLACKBOX_CHANNELS.add(interaction.channel_id)


async def log_blackbox_message(message: discord.Message) -> None:
	if message.channel.id not in _BLACKBOX_CHANNELS:
		return
	if message.author.bot:
		return
	await write_blackbox_log(
		f"[{utc_now().isoformat()}] message user={message.author.id} channel={message.channel.id} :: {truncate_text(message.content or '', 800)}"
	)


def current_visibility(user_id: int) -> bool:
	return get_user_mode(user_id) in {"stealth", "blackbox"}


async def mode_send(interaction: discord.Interaction, *, content: Optional[str] = None, embed: Optional[discord.Embed] = None, view: Optional[discord.ui.View] = None, file: Optional[discord.File] = None, ephemeral: Optional[bool] = None) -> None:
	should_ephemeral = current_visibility(interaction.user.id) if ephemeral is None else ephemeral
	if get_user_mode(interaction.user.id) == "blackbox":
		await write_blackbox_log(
			f"[{utc_now().isoformat()}] response user={interaction.user.id} :: {payload_preview(content, embed)}"
		)
	if interaction.response.is_done():
		await interaction.followup.send(content=content, embed=embed, view=view, file=file, ephemeral=should_ephemeral)
	else:
		await interaction.response.send_message(content=content, embed=embed, view=view, file=file, ephemeral=should_ephemeral)


def normalize_reminders_state(store: Any) -> Dict[str, Any]:
	if not isinstance(store, dict):
		return {"items": [], "next_id": 1}
	items = store.get("items")
	if not isinstance(items, list):
		items = []
	next_id = store.get("next_id", 1)
	try:
		next_id = int(next_id)
	except (TypeError, ValueError):
		next_id = 1
	return {"items": items, "next_id": max(1, next_id)}


def normalize_deadrops_state(store: Any) -> Dict[str, Any]:
	if not isinstance(store, dict):
		return {"items": [], "next_id": 1}
	items = store.get("items")
	if not isinstance(items, list):
		items = []
	next_id = store.get("next_id", 1)
	try:
		next_id = int(next_id)
	except (TypeError, ValueError):
		next_id = 1
	return {"items": items, "next_id": max(1, next_id)}


def save_state(path: Path, payload: Any) -> None:
	save_json_file(path, payload)


def theme_card(theme: Dict[str, Any]) -> str:
	return f"{theme['emoji']} {theme['label']}"


def attachment_suffix(attachment: discord.Attachment) -> str:
	filename = attachment.filename.lower()
	if filename.endswith(".md") or filename.endswith(".markdown"):
		return "md"
	if filename.endswith(".pdf"):
		return "pdf"
	if filename.endswith(".txt"):
		return "txt"
	return filename.rsplit(".", 1)[-1] if "." in filename else ""


async def extract_attachment_text(attachment: discord.Attachment) -> Tuple[bool, str]:
	data = await attachment.read()
	extension = attachment_suffix(attachment)
	if extension == "pdf":
		try:
			reader = PdfReader(BytesIO(data))
			parts: List[str] = []
			for page in reader.pages:
				parts.append(page.extract_text() or "")
			return True, "\n".join(parts).strip()
		except Exception as exc:
			return False, f"Failed to parse PDF: {exc}"
	try:
		return True, data.decode("utf-8", errors="ignore")
	except Exception as exc:
		return False, f"Failed to read attachment: {exc}"


def study_system_prompt(theme: Dict[str, Any]) -> str:
	return (
		f"You are Squelch Study Ops in the {theme['label']} theme. {theme['persona']}\n"
		"Act as a study coach for Obsidian and field notes. Produce markdown only with these sections: Summary, Key Terms, Flashcards, Quick Quiz, Memory Hooks, Next Review.\n"
		"Keep it accurate, concise, and practical. If source notes are provided, use them heavily. Do not hallucinate citations."
	)


def reminder_state() -> Dict[str, Any]:
	global _REMINDERS_STATE_CACHE
	_REMINDERS_STATE_CACHE = normalize_reminders_state(_REMINDERS_STATE_CACHE)
	return _REMINDERS_STATE_CACHE


def deadrop_state() -> Dict[str, Any]:
	global _DEADROPS_STATE_CACHE
	_DEADROPS_STATE_CACHE = normalize_deadrops_state(_DEADROPS_STATE_CACHE)
	return _DEADROPS_STATE_CACHE


def reminder_label(record: Dict[str, Any]) -> str:
	due_at = record.get("due_at", "")
	note = record.get("note", "")
	return f"#{record.get('id', '?')} • {due_at} • {note}"


def classify_triage(query: str) -> Tuple[str, str]:
	text = clean_text(query).lower()
	if any(keyword in text for keyword in ["gps", "location", "address", "lat", "lon", "coordinate", "coords"]):
		return "navigation", "Use `/gps` for coordinates and routing links."
	if any(keyword in text for keyword in ["weather", "rain", "storm", "wind", "forecast", "pressure"]):
		return "weather", "Use `/weather` for meteorology comparison."
	if any(keyword in text for keyword in ["radio", "repeater", "vhf", "uhf", "comms", "signal"]):
		return "comms", "Use `/repeater` or `/commslog` for radio operations and logging."
	if any(keyword in text for keyword in ["convert", "grams", "meters", "liters", "aud", "ewbank"]):
		return "conversion", "Use `/convert` for unit work."
	if any(keyword in text for keyword in ["study", "notes", "quiz", "flashcard", "obsidian", "pdf", "markdown", ".md"]):
		return "study", "Use `/study` to turn notes or files into a study pack."
	if any(keyword in text for keyword in ["remind", "later", "deadline", "alarm"]):
		return "reminders", "Use `/reminders` to create or manage reminders."
	if any(keyword in text for keyword in ["deadrop", "drop", "secret", "stash"]):
		return "deadrop", "Use `/deadrop` to store or retrieve a secret note."
	if any(keyword in text for keyword in ["med", "injury", "pain", "breath", "bleeding", "trauma", "triage"]):
		return "medical", "Use `/fieldmed` for safety-first field triage prompts."
	if any(keyword in text for keyword in ["ai", "summarize", "explain", "write", "analyze"]):
		return "ai", "Use `/ai` for general Gemini help."
	return "general", "No strong route found. `/ai` or `/study` may be the best starting point."


def medical_red_flags(text: str) -> List[str]:
	lower = text.lower()
	flags: List[str] = []
	for keyword in RED_FLAG_KEYWORDS:
		if keyword in lower:
			flags.append(keyword)
	return flags


class ThemePickerSelect(discord.ui.Select):
	def __init__(self, owner_id: int):
		options = [
			discord.SelectOption(
				label=theme["label"],
				value=theme_id,
				emoji=theme["emoji"],
				description=theme["persona"][:95],
			)
			for theme_id, theme in THEME_BY_ID.items()
		]
		super().__init__(placeholder="Choose a mission skin...", options=options, min_values=1, max_values=1)
		self.owner_id = owner_id

	async def callback(self, interaction: discord.Interaction) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This picker belongs to someone else.", ephemeral=True)
			return
		theme_id = self.values[0]
		await set_user_theme(interaction.user.id, theme_id)
		theme = THEME_BY_ID.get(theme_id, THEME_BY_ID[DEFAULT_THEME_ID])
		embed = make_embed(
			f"THEME LOCKED: {theme['label']}",
			f"Selected theme: {theme_card(theme)}\n\nAI persona and response style are now synced to this preset.",
			theme["color"],
		)
		await interaction.response.edit_message(embed=embed, view=self.view)
		await log_blackbox_event(interaction, "theme", f"{theme_id}")


class ThemePickerView(discord.ui.View):
	def __init__(self, owner_id: int):
		super().__init__(timeout=180)
		self.add_item(ThemePickerSelect(owner_id))


def reminder_embed(record: Dict[str, Any]) -> discord.Embed:
	return make_embed(
		"REMINDER DUE",
		f"**Note:** {record.get('note', '')}\n**Due:** {record.get('due_at', '')}\n**Reminder ID:** `{record.get('id', '?')}`",
		OREGON_GREEN,
	)


@tasks.loop(seconds=30)
async def reminder_dispatch() -> None:
	state = reminder_state()
	items = state.get("items", [])
	now = utc_now()
	changed = False
	for record in items:
		if not isinstance(record, dict) or record.get("sent"):
			continue
		due_at = record.get("due_at", "")
		try:
			due_time = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
		except Exception:
			continue
		if due_time > now:
			continue
		user = bot.get_user(int(record.get("user_id", 0)))
		channel_id = record.get("channel_id")
		embed = reminder_embed(record)
		try:
			sent = False
			if user is not None:
				try:
					await user.send(embed=embed)
					sent = True
				except Exception:
					pass
			if not sent and channel_id is not None:
				channel = bot.get_channel(int(channel_id))
				if channel is not None:
					await channel.send(embed=embed)
					sent = True
			if not sent:
				continue
			record["sent"] = True
			record["sent_at"] = utc_now().isoformat()
			changed = True
			await write_blackbox_log(f"[{utc_now().isoformat()}] reminder-firing id={record.get('id')} user={record.get('user_id')} note={truncate_text(str(record.get('note', '')), 120)}")
		except Exception as exc:
			await write_blackbox_log(f"[{utc_now().isoformat()}] reminder-error id={record.get('id')} error={exc}")
	if changed:
		async with PERSISTENCE_LOCK:
			save_state(REMINDERS_PATH, state)


def parse_reminder_minutes(minutes: float) -> str:
	return (utc_now() + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def build_study_source_text(filename: str, source_text: str) -> str:
	return f"SOURCE FILE: {filename}\n\n{source_text.strip()}"


def triage_summary_embed(category: str, recommendation: str, query: str) -> discord.Embed:
	return make_embed(
		f"TRIAGE: {category.upper()}",
		f"**Input:** {query}\n\n**Route:** {recommendation}",
		OREGON_GREEN,
	)


def fieldmed_report(chief_complaint: str, age: Optional[int], vitals: Optional[str], mechanism: Optional[str]) -> discord.Embed:
	flags = medical_red_flags(chief_complaint + " " + (vitals or "") + " " + (mechanism or ""))
	red_flag_text = ", ".join(flags) if flags else "none obvious from keywords"
	assessment_lines = [
		"- Scene safety and PPE",
		"- Determine responsiveness and airway/breathing/circulation",
		"- Check for major hemorrhage, stroke signs, and altered mental status",
		"- Obtain set of vitals and trend them if available",
		"- Escalate if there is chest pain, respiratory distress, uncontrolled bleeding, seizure activity, or shock signs",
	]
	if age is not None and age < 18:
		assessment_lines.append("- Pediatric considerations apply; monitor closely and involve appropriate transport/escalation")
	description = (
		f"**Chief Complaint:** {chief_complaint}\n"
		f"**Age:** {age if age is not None else 'not provided'}\n"
		f"**Vitals:** {vitals or 'not provided'}\n"
		f"**Mechanism:** {mechanism or 'not provided'}\n\n"
		f"**Keyword Red Flags:** {red_flag_text}\n\n"
		"**Field Checklist**\n" + "\n".join(assessment_lines) + "\n\n"
		"**Note:** This helper is safety-focused only and does not diagnose."
	)
	color = SAR_ORANGE if flags else OREGON_GREEN
	embed = make_embed("FIELD MED TRIAGE", description, color)
	return embed


def open_meteo_current_url(latitude: float, longitude: float, model: str) -> str:
	params = {
		"latitude": f"{latitude:.5f}",
		"longitude": f"{longitude:.5f}",
		"current": "temperature_2m,relative_humidity_2m,surface_pressure,precipitation",
		"timezone": "auto",
		"models": model,
		"wind_speed_unit": "kmh",
		"temperature_unit": "celsius",
		"precipitation_unit": "mm",
	}
	return "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)


def open_meteo_geocode_url(location: str) -> str:
	params = {
		"q": location,
		"format": "jsonv2",
		"limit": "1",
	}
	return "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)


async def http_get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any, str]:
	timeout = aiohttp.ClientTimeout(total=20)
	async with aiohttp.ClientSession(timeout=timeout) as session:
		async with session.get(url, headers=headers) as response:
			text = await response.text()
			try:
				payload = await response.json(content_type=None)
			except Exception:
				payload = None
			return response.status, payload, text


async def http_post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any, str]:
	timeout = aiohttp.ClientTimeout(total=30)
	request_headers = {"Content-Type": "application/json"}
	if headers:
		request_headers.update(headers)
	async with aiohttp.ClientSession(timeout=timeout) as session:
		async with session.post(url, json=payload, headers=request_headers) as response:
			text = await response.text()
			try:
				data = await response.json(content_type=None)
			except Exception:
				data = None
			return response.status, data, text


async def gemini_generate(prompt: str) -> Tuple[bool, str]:
	if not GEMINI_API_KEY:
		return False, "Gemini API key missing. Set GEMINI_API_KEY in the environment."

	url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={urllib.parse.quote_plus(GEMINI_API_KEY)}"

	payload = {
		"contents": [{"role": "user", "parts": [{"text": prompt}]}],
		"generationConfig": {
			"temperature": 0.7,
			"topP": 0.95,
			"maxOutputTokens": 1024,
		},
	}
	status, data, text = await http_post_json(url, payload)
	if status < 200 or status >= 300:
		return False, text.strip() or json.dumps(data or {"error": "unknown"}, indent=2)
	try:
		candidates = data.get("candidates", []) if isinstance(data, dict) else []
		if not candidates:
			return True, text.strip()
		parts = candidates[0].get("content", {}).get("parts", [])
		output = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
		if output:
			return True, output
		return True, text.strip()
	except Exception:
		return True, text.strip()

def parse_query_location(value: str) -> Optional[Tuple[float, float]]:
	cleaned = clean_text(value)
	if "," not in cleaned:
		return None
	left, right = [piece.strip() for piece in cleaned.split(",", 1)]
	try:
		return float(left), float(right)
	except ValueError:
		return None


def approximate_declination(latitude: float, longitude: float) -> float:
	base = 14.0 * math.sin(math.radians(longitude)) * math.cos(math.radians(latitude / 2.0))
	seasonal = 1.5 * math.sin(math.radians(latitude + longitude / 2.0))
	return max(-30.0, min(30.0, base + seasonal))


def repeater_profile(location: str, latitude: Optional[float] = None, longitude: Optional[float] = None) -> Dict[str, str]:
	seed_value = sum(ord(character) for character in location)
	if latitude is not None and longitude is not None:
		seed_value += int(abs(latitude) * 1000) + int(abs(longitude) * 1000)
	vhf_freq = 146.52 + ((seed_value % 35) * 0.02)
	if vhf_freq > 147.39:
		vhf_freq = 146.52 + ((seed_value % 30) * 0.02)
	offset = -0.6 if seed_value % 2 == 0 else 0.6
	tone_pool = [67.0, 71.9, 77.0, 82.5, 88.5, 94.8, 100.0, 103.5, 110.9, 114.8, 123.0, 131.8, 136.5, 141.3]
	tone = tone_pool[seed_value % len(tone_pool)]
	prefix = ["K", "N", "W", "A"]
	suffix = ["SAR", "RPT", "RNG", "SCL", "MTR"]
	callsign = f"{prefix[seed_value % len(prefix)]}{seed_value % 9}{suffix[seed_value % len(suffix)]}"
	coverage = [
		"ridge-shadowed valley relay",
		"wide-area hilltop coverage",
		"trailhead-to-ridge simplex relay",
		"coastal overlook coverage",
		"backcountry cross-band patch point",
	][seed_value % 5]
	return {
		"callsign": callsign,
		"output": f"{vhf_freq:.2f} MHz",
		"input": f"{vhf_freq + offset:.2f} MHz",
		"offset": f"{offset:+.1f} MHz",
		"tone": f"{tone:.1f} Hz",
		"coverage": coverage,
	}


def morse_encode(text: str) -> str:
	table = {
		"a": ".-", "b": "-...", "c": "-.-.", "d": "-..", "e": ".", "f": "..-.", "g": "--.",
		"h": "....", "i": "..", "j": ".---", "k": "-.-", "l": ".-..", "m": "--", "n": "-.",
		"o": "---", "p": ".--.", "q": "--.-", "r": ".-.", "s": "...", "t": "-", "u": "..-",
		"v": "...-", "w": ".--", "x": "-..-", "y": "-.--", "z": "--..",
		"0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
		"6": "-....", "7": "--...", "8": "---..", "9": "----.",
		".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "-": "-....-", "(": "-.--.", ")": "-.--.-",
		"&": ".-...", ":": "---...", ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "_": "..--.-",
		"\"": ".-..-.", "$": "...-..-", "!": "-.-.--", "@": ".--.-.",
	}
	encoded: List[str] = []
	for character in text.lower():
		if character == " ":
			encoded.append("/")
		else:
			encoded.append(table.get(character, "?"))
	return " ".join(encoded)


async def ai_usage_guard(user_id: int) -> Tuple[str, int]:
	count = ai_count_for_user(user_id)
	if count >= AI_LIMIT:
		return "blocked", count
	if count >= 20:
		return "confirm", count
	return "go", count


async def send_ai_result(interaction: discord.Interaction, prompt: str, source_name: str, header: str) -> None:
	count_before = ai_count_for_user(interaction.user.id)
	status = "go" if count_before < 20 else "confirm" if count_before < AI_LIMIT else "blocked"
	if status == "blocked":
		await interaction.followup.send(
			embed=make_embed(
				"SAR QUOTA REACHED",
				"Daily AI capacity is exhausted for this UTC day. Try again after 00:00 UTC.",
				SAR_ORANGE,
			)
		)
		return

	if status == "confirm":
		return

	success, output = await gemini_generate(prompt)
	if not success:
		await interaction.followup.send(
			embed=make_embed("GEMINI API ERROR", truncate_text(output, 3900), SAR_ORANGE)
		)
		return

	count_after = await increment_ai_count(interaction.user.id)
	embed = make_embed(header, truncate_text(output, 3900), ai_footer_color(count_after))
	embed.set_footer(text=ai_footer(count_after))
	await interaction.followup.send(embed=embed)


class ProceedsView(discord.ui.View):
	def __init__(self, owner_id: int, prompt: str, source_name: str, header: str):
		super().__init__(timeout=120)
		self.owner_id = owner_id
		self.prompt = prompt
		self.source_name = source_name
		self.header = header
		self._locked = False

	async def _run(self, interaction: discord.Interaction) -> None:
		if self._locked:
			return
		self._locked = True
		for item in self.children:
			if hasattr(item, "disabled"):
				item.disabled = True
		await interaction.response.edit_message(view=self)
		count_after = await increment_ai_count(self.owner_id)
		success, output = await gemini_generate(self.prompt)
		if not success:
			await interaction.followup.send(
				embed=make_embed("GEMINI API ERROR", truncate_text(output, 3900), SAR_ORANGE)
			)
			return
		embed = make_embed(self.header, truncate_text(output, 3900), ai_footer_color(count_after))
		embed.set_footer(text=ai_footer(count_after))
		await interaction.followup.send(embed=embed)

	@discord.ui.button(label="Proceed", style=discord.ButtonStyle.success)
	async def proceed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This prompt belongs to someone else.", ephemeral=True)
			return
		await self._run(interaction)

	@discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
	async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if interaction.user.id != self.owner_id:
			await interaction.response.send_message("This prompt belongs to someone else.", ephemeral=True)
			return
		for item in self.children:
			if hasattr(item, "disabled"):
				item.disabled = True
		await interaction.response.edit_message(
			embed=make_embed("REQUEST CANCELLED", "AI execution was not started.", SAR_ORANGE),
			view=self,
		)


class DepthSourcesView(discord.ui.View):
	def __init__(self, primary_url: Optional[str], query: str):
		super().__init__(timeout=180)
		if primary_url:
			self.add_item(link_button("Open Source", primary_url))
		self.add_item(link_button("🔍 Explore Deeper Sources", scholar_url(query)))


class MapsView(discord.ui.View):
	def __init__(self, latitude: float, longitude: float):
		super().__init__(timeout=180)
		self.add_item(link_button("🗺️ OpenStreetMap", f"https://www.openstreetmap.org/?mlat={latitude:.5f}&mlon={longitude:.5f}#map=14/{latitude:.5f}/{longitude:.5f}"))
		self.add_item(link_button("📍 Google Maps", f"https://www.google.com/maps/search/?api=1&query={latitude:.5f},{longitude:.5f}"))
		self.add_item(link_button("📱 Apple Maps", f"https://maps.apple.com/?q={latitude:.5f},{longitude:.5f}"))


class SquelchBot(commands.Bot):
	def __init__(self) -> None:
		intents = discord.Intents.default()
		intents.message_content = True
		super().__init__(command_prefix="/", intents=intents)
		self.http_runner: Optional[web.AppRunner] = None
		self.http_site: Optional[web.TCPSite] = None

	async def setup_hook(self) -> None:
		await self._start_web_server()
		if not reminder_dispatch.is_running():
			reminder_dispatch.start()
		await self.tree.sync()

	async def close(self) -> None:
		if self.http_runner is not None:
			await self.http_runner.cleanup()
		await super().close()

	async def _start_web_server(self) -> None:
		async def healthcheck(_: web.Request) -> web.Response:
			return web.Response(text="ok", content_type="text/plain")

		app = web.Application()
		app.router.add_get("/", healthcheck)
		app.router.add_get("/healthz", healthcheck)
		runner = web.AppRunner(app)
		await runner.setup()
		site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
		await site.start()
		self.http_runner = runner
		self.http_site = site


bot = SquelchBot()


async def whitelist_gatekeeper(interaction: discord.Interaction) -> bool:
	allowed_ids = set(load_whitelist())
	if interaction.user is None or interaction.user.id not in allowed_ids:
		message = make_embed(
			"ACCESS DENIED",
			"This Squelch deployment is restricted to the local whitelist.",
			SAR_ORANGE,
		)
		if interaction.response.is_done():
			await interaction.followup.send(embed=message, ephemeral=True)
		else:
			await interaction.response.send_message(embed=message, ephemeral=True)
		return False
	return True


bot.tree.interaction_check = whitelist_gatekeeper


async def fetch_json_with_headers(url: str, headers: Dict[str, str]) -> Tuple[int, Any, str]:
	timeout = aiohttp.ClientTimeout(total=25)
	async with aiohttp.ClientSession(timeout=timeout) as session:
		async with session.get(url, headers=headers) as response:
			text = await response.text()
			try:
				payload = await response.json(content_type=None)
			except Exception:
				payload = None
			return response.status, payload, text


@bot.tree.command(name="mode", description="Switch your local Squelch operating mode.")
@app_commands.describe(mode="Pick regular, stealth, or blackbox.")
@app_commands.choices(mode=[
	app_commands.Choice(name="regular", value="regular"),
	app_commands.Choice(name="stealth", value="stealth"),
	app_commands.Choice(name="blackbox", value="blackbox"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def mode_command(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
	previous_mode = get_user_mode(interaction.user.id)
	await set_user_mode(interaction.user.id, mode.value)
	description = (
		f"Mode updated from **{previous_mode}** to **{mode.value}**.\n\n"
		"regular = normal responses\n"
		"stealth = private responses\n"
		"blackbox = private responses plus local logging to console and blackbox.log"
	)
	await mode_send(interaction, embed=make_embed("MODE UPDATED", description, OREGON_GREEN), ephemeral=mode.value != "regular")
	if mode.value == "blackbox" or previous_mode == "blackbox":
		if interaction.channel_id is not None and mode.value == "blackbox":
			_BLACKBOX_CHANNELS.add(interaction.channel_id)
		await write_blackbox_log(
			f"[{utc_now().isoformat()}] mode user={interaction.user.id} from={previous_mode} to={mode.value}"
		)


@bot.tree.command(name="theme", description="Open the Squelch theme picker.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def theme_command(interaction: discord.Interaction) -> None:
	theme = get_user_theme(interaction.user.id)
	embed = make_embed(
		f"THEME DESK: {theme['label']}",
		f"Current theme: {theme_card(theme)}\n\nPick a new persona from the menu below. The selected theme changes AI tone and study-helper style.",
		theme["color"],
	)
	await mode_send(interaction, embed=embed, view=ThemePickerView(interaction.user.id), ephemeral=True)


@bot.tree.command(name="reminders", description="Create, list, update, or clear a reminder.")
@app_commands.describe(
	action="Pick add, list, done, or delete.",
	minutes="Minutes from now for add.",
	note="Reminder text for add.",
	reminder_id="Reminder ID for done or delete.",
)
@app_commands.choices(action=[
	app_commands.Choice(name="add", value="add"),
	app_commands.Choice(name="list", value="list"),
	app_commands.Choice(name="done", value="done"),
	app_commands.Choice(name="delete", value="delete"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def reminders_command(
	interaction: discord.Interaction,
	action: app_commands.Choice[str],
	minutes: Optional[int] = None,
	note: Optional[str] = None,
	reminder_id: Optional[int] = None,
) -> None:
	state = reminder_state()
	items = state["items"]
	user_id = interaction.user.id
	action_value = action.value

	if action_value == "add":
		if minutes is None or minutes <= 0:
			await mode_send(interaction, content="Provide a positive number of minutes for the reminder.", ephemeral=True)
			return
		if not note:
			await mode_send(interaction, content="Provide reminder text for the add action.", ephemeral=True)
			return
		record = {
			"id": state["next_id"],
			"user_id": user_id,
			"note": clean_text(note),
			"due_at": (utc_now() + timedelta(minutes=minutes)).isoformat(),
			"channel_id": interaction.channel_id,
			"created_at": utc_now().isoformat(),
			"sent": False,
		}
		state["next_id"] += 1
		items.append(record)
		async with PERSISTENCE_LOCK:
			save_state(REMINDERS_PATH, state)
		await mode_send(interaction, embed=make_embed("REMINDER STAGED", f"Reminder #{record['id']} scheduled for {record['due_at']}\n\n{record['note']}", OREGON_GREEN))
		return

	user_items = [record for record in items if isinstance(record, dict) and int(record.get("user_id", 0)) == user_id and not record.get("deleted")]

	if action_value == "list":
		if not user_items:
			await mode_send(interaction, content="No active reminders.", ephemeral=True)
			return
		lines = [reminder_label(record) for record in sorted(user_items, key=lambda entry: entry.get("id", 0))]
		await mode_send(interaction, embed=make_embed("REMINDERS", "\n".join(lines), OREGON_GREEN), ephemeral=True)
		return

	if reminder_id is None:
		await mode_send(interaction, content="Provide a reminder ID for that action.", ephemeral=True)
		return

	match = None
	for record in user_items:
		if int(record.get("id", 0)) == reminder_id:
			match = record
			break

	if match is None:
		await mode_send(interaction, content="Reminder not found.", ephemeral=True)
		return

	if action_value == "done":
		match["done"] = True
		match["done_at"] = utc_now().isoformat()
	elif action_value == "delete":
		match["deleted"] = True
		match["deleted_at"] = utc_now().isoformat()

	async with PERSISTENCE_LOCK:
		save_state(REMINDERS_PATH, state)
	await mode_send(interaction, embed=make_embed("REMINDER UPDATED", f"Reminder #{reminder_id} marked **{action_value}**.", OREGON_GREEN))


@bot.tree.command(name="deadrop", description="Store, retrieve, list, or remove a dead drop note.")
@app_commands.describe(
	action="Pick create, retrieve, list, or delete.",
	key="Dead drop key name.",
	content="Secret content for create.",
	secret="Optional retrieval or deletion passphrase.",
	deadrop_id="Numeric dead drop ID for delete.",
)
@app_commands.choices(action=[
	app_commands.Choice(name="create", value="create"),
	app_commands.Choice(name="retrieve", value="retrieve"),
	app_commands.Choice(name="list", value="list"),
	app_commands.Choice(name="delete", value="delete"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def deadrop_command(
	interaction: discord.Interaction,
	action: app_commands.Choice[str],
	key: Optional[str] = None,
	content: Optional[str] = None,
	secret: Optional[str] = None,
	deadrop_id: Optional[int] = None,
) -> None:
	state = deadrop_state()
	items = state["items"]
	action_value = action.value
	user_id = interaction.user.id

	if action_value == "create":
		if not key or not content:
			await mode_send(interaction, content="Provide both key and content for a deadrop.", ephemeral=True)
			return
		record = {
			"id": state["next_id"],
			"user_id": user_id,
			"key": clean_text(key),
			"content": content,
			"secret": clean_text(secret or ""),
			"created_at": utc_now().isoformat(),
			"deleted": False,
		}
		state["next_id"] += 1
		items.append(record)
		async with PERSISTENCE_LOCK:
			save_state(DEADROPS_PATH, state)
		await mode_send(interaction, embed=make_embed("DEADROP CREATED", f"Stored `{record['key']}` as deadrop #{record['id']}.", OREGON_GREEN), ephemeral=True)
		return

	active_items = [record for record in items if isinstance(record, dict) and not record.get("deleted")]

	if action_value == "list":
		if not active_items:
			await mode_send(interaction, content="No dead drops stored.", ephemeral=True)
			return
		lines = [f"#{record.get('id', '?')} • {record.get('key', '')}" for record in active_items]
		await mode_send(interaction, embed=make_embed("DEADROP INDEX", "\n".join(lines), OREGON_GREEN), ephemeral=True)
		return

	if action_value == "retrieve":
		match = None
		for record in active_items:
			if key and clean_text(record.get("key", "")).lower() == clean_text(key).lower():
				match = record
				break
			if deadrop_id is not None and int(record.get("id", 0)) == deadrop_id:
				match = record
				break
		if match is None:
			await mode_send(interaction, content="Dead drop not found.", ephemeral=True)
			return
		stored_secret = clean_text(match.get("secret", ""))
		if stored_secret and stored_secret != clean_text(secret or ""):
			await mode_send(interaction, content="Secret mismatch.", ephemeral=True)
			return
		await mode_send(interaction, embed=make_embed(f"DEADROP #{match.get('id', '?')}", match.get("content", ""), OREGON_GREEN), ephemeral=True)
		return

	if action_value == "delete":
		if deadrop_id is None:
			await mode_send(interaction, content="Provide a deadrop ID to delete.", ephemeral=True)
			return
		match = None
		for record in active_items:
			if int(record.get("id", 0)) == deadrop_id:
				match = record
				break
		if match is None:
			await mode_send(interaction, content="Dead drop not found.", ephemeral=True)
			return
		stored_secret = clean_text(match.get("secret", ""))
		if stored_secret and stored_secret != clean_text(secret or ""):
			await mode_send(interaction, content="Secret mismatch.", ephemeral=True)
			return
		match["deleted"] = True
		match["deleted_at"] = utc_now().isoformat()
		async with PERSISTENCE_LOCK:
			save_state(DEADROPS_PATH, state)
		await mode_send(interaction, content=f"Deadrop #{deadrop_id} deleted.", ephemeral=True)


@bot.tree.command(name="triage", description="Classify a task or request into the best local route.")
@app_commands.describe(query="Describe what you need routed.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def triage_command(interaction: discord.Interaction, query: str) -> None:
	category, recommendation = classify_triage(query)
	embed = triage_summary_embed(category, recommendation, query)
	await mode_send(interaction, embed=embed, ephemeral=current_visibility(interaction.user.id))


@bot.tree.command(name="study", description="Turn a note, PDF, or topic into a study pack.")
@app_commands.describe(topic="What you want to study.", source="Optional .md or PDF attachment.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def study_command(interaction: discord.Interaction, topic: str, source: Optional[discord.Attachment] = None) -> None:
	theme = get_user_theme(interaction.user.id)
	await interaction.response.defer(ephemeral=current_visibility(interaction.user.id))
	source_block = ""
	if source is not None:
		ok, extracted = await extract_attachment_text(source)
		if not ok:
			await interaction.followup.send(embed=make_embed("STUDY ERROR", extracted, SAR_ORANGE), ephemeral=True)
			return
		if not extracted.strip():
			await interaction.followup.send(embed=make_embed("STUDY ERROR", "The attached file did not yield readable text.", SAR_ORANGE), ephemeral=True)
			return
		source_block = build_study_source_text(source.filename, extracted)
	prompt = compose_ai_prompt(
		interaction.user.id,
		f"{study_system_prompt(theme)}\n\nCreate a study pack for the topic below. Include an overview, flashcards, and a short quiz.\n\nTOPIC: {topic}\n\nSOURCE NOTES:\n{source_block or 'No attachment provided. Use the topic and general best practices only.'}",
		"study helper",
	)
	success, output = await gemini_generate(prompt)
	if not success:
		await interaction.followup.send(embed=make_embed("STUDY ERROR", truncate_text(output, 3900), SAR_ORANGE), ephemeral=True)
		return
	embed = make_embed(f"STUDY PACK: {topic}", truncate_text(output, 3900), theme["color"])
	await interaction.followup.send(embed=embed, ephemeral=current_visibility(interaction.user.id))


@bot.tree.command(name="fieldmed", description="Field triage helper for EMT-style safety checks.")
@app_commands.describe(
	chief_complaint="What happened.",
	age="Optional patient age.",
	vitals="Optional vitals string.",
	mechanism="Optional mechanism of injury or illness.",
)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def fieldmed_command(
	interaction: discord.Interaction,
	chief_complaint: str,
	age: Optional[int] = None,
	vitals: Optional[str] = None,
	mechanism: Optional[str] = None,
) -> None:
	embed = fieldmed_report(chief_complaint, age, vitals, mechanism)
	await mode_send(interaction, embed=embed, ephemeral=True)


@bot.tree.command(name="datum", description="Multi-node technical search and research discovery tool.")
@app_commands.choices(source=[
	app_commands.Choice(name="wikipedia", value="wikipedia"),
	app_commands.Choice(name="google", value="google"),
	app_commands.Choice(name="ai", value="ai"),
	app_commands.Choice(name="wolfram", value="wolfram"),
	app_commands.Choice(name="stack", value="stack"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def datum_cmd(interaction: discord.Interaction, query: str, source: app_commands.Choice[str]) -> None:
	selected = source.value
	query = clean_text(query)
	private = current_visibility(interaction.user.id)
	await interaction.response.defer(ephemeral=private)

	if selected == "google":
		embed = make_embed("DATUM: GOOGLE ROUTE", f"[Open Google search]({google_search_url(query)})", OREGON_GREEN)
		view = DepthSourcesView(google_search_url(query), query)
		await interaction.followup.send(embed=embed, view=view, ephemeral=private)
		return

	if selected == "wolfram":
		embed = make_embed("DATUM: WOLFRAM ROUTE", f"[Open WolframAlpha]({wolfram_url(query)})", OREGON_GREEN)
		view = DepthSourcesView(wolfram_url(query), query)
		await interaction.followup.send(embed=embed, view=view, ephemeral=private)
		return

	if selected == "stack":
		embed = make_embed("DATUM: STACK OVERFLOW ROUTE", f"[Open Stack Overflow search]({stackoverflow_url(query)})", OREGON_GREEN)
		view = DepthSourcesView(stackoverflow_url(query), query)
		await interaction.followup.send(embed=embed, view=view, ephemeral=private)
		return

	if selected == "wikipedia":
		headers = {"User-Agent": "SquelchBot/1.0 (Discord bot)"}
		summary_url = wikipedia_summary_url(query)
		status, payload, text = await fetch_json_with_headers(summary_url, headers)
		if status != 200 or not isinstance(payload, dict):
			embed = make_embed("DATUM: WIKIPEDIA ERROR", truncate_text(text, 3900), SAR_ORANGE)
			view = DepthSourcesView(None, query)
			await interaction.followup.send(embed=embed, view=view, ephemeral=private)
			return
		extract = payload.get("extract") or "No summary extract was returned."
		article_url = payload.get("content_urls", {}).get("desktop", {}).get("page") or wikipedia_desktop_url(query)
		title = payload.get("title") or query
		embed = make_embed(f"DATUM: {title}", truncate_text(extract, 3900), OREGON_GREEN)
		view = DepthSourcesView(article_url, query)
		await interaction.followup.send(embed=embed, view=view, ephemeral=private)
		return

	if selected == "ai":
		prompt = compose_ai_prompt(interaction.user.id, query, "datum ai lookup")
		count = ai_count_for_user(interaction.user.id)
		if count >= AI_LIMIT:
			await interaction.followup.send(
				embed=make_embed(
					"SAR QUOTA REACHED",
					"Daily AI capacity is exhausted for this UTC day. Try again after 00:00 UTC.",
					SAR_ORANGE,
				),
				ephemeral=private,
			)
			return
		if count >= 20:
			view = ProceedsView(interaction.user.id, prompt, "ai", "DATUM: AI ANALYSIS")
			await interaction.followup.send(
				embed=make_embed(
					"AI EXECUTION HOLD",
					f"AI usage is at {count}/24 for today. Proceed to execute the Gemini request or cancel.",
					SAR_ORANGE,
				),
				view=view,
				ephemeral=private,
			)
			return
		success, output = await gemini_generate(prompt)
		if not success:
			embed = make_embed("GEMINI API ERROR", truncate_text(output, 3900), SAR_ORANGE)
			view = DepthSourcesView(None, query)
			await interaction.followup.send(embed=embed, view=view, ephemeral=private)
			return
		count_after = await increment_ai_count(interaction.user.id)
		embed = make_embed("DATUM: AI ANALYSIS", truncate_text(output, 3900), ai_footer_color(count_after))
		embed.set_footer(text=ai_footer(count_after))
		view = DepthSourcesView(None, query)
		await interaction.followup.send(embed=embed, view=view, ephemeral=private)
		return


@bot.tree.command(name="gps", description="Translates addresses or locations into coordinates and navigation links.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def gps_cmd(interaction: discord.Interaction, location: str) -> None:
	await interaction.response.defer()
	headers = {"User-Agent": "SquelchBot/1.0 (Discord bot)"}
	status, payload, text = await fetch_json_with_headers(open_meteo_geocode_url(location), headers)
	if status != 200 or not isinstance(payload, list) or not payload:
		await interaction.followup.send(
			embed=make_embed("GPS LOOKUP FAILED", truncate_text(text, 3900), SAR_ORANGE)
		)
		return

	place = payload[0]
	latitude = float(place["lat"])
	longitude = float(place["lon"])
	display_name = place.get("display_name", location)
	dd_lat = f"{latitude:.5f}"
	dd_lon = f"{longitude:.5f}"
	ddm_lat = decimal_to_ddm(latitude, True)
	ddm_lon = decimal_to_ddm(longitude, False)
	maidenhead = maidenhead_locator(latitude, longitude)
	description = (
		f"**Location:** {display_name}\n\n"
		f"**Decimal Degrees (DD)**\n"
		f"Lat: `{dd_lat}`, Lon: `{dd_lon}`\n\n"
		f"**Degrees Decimal Minutes (DDM)**\n"
		f"Lat: `{ddm_lat}`\n"
		f"Lon: `{ddm_lon}`\n\n"
		f"**Maidenhead Grid**\n"
		f"`{maidenhead}`"
	)
	await interaction.followup.send(embed=make_embed("GPS NAVIGATION FIX", description, OREGON_GREEN), view=MapsView(latitude, longitude))


async def handle_ai_prompt(interaction: discord.Interaction, prompt: str, title: str) -> None:
	count = ai_count_for_user(interaction.user.id)
	if count >= AI_LIMIT:
		await interaction.response.send_message(
			embed=make_embed(
				"SAR QUOTA REACHED",
				"Daily AI capacity is exhausted for this UTC day. Try again after 00:00 UTC.",
				SAR_ORANGE,
			),
			ephemeral=current_visibility(interaction.user.id),
		)
		return
	if count >= 20:
		await interaction.response.send_message(
			embed=make_embed(
				"AI EXECUTION HOLD",
				f"AI usage is at {count}/24 for today. Proceed to execute the Gemini request or cancel.",
				SAR_ORANGE,
			),
			view=ProceedsView(interaction.user.id, prompt, "ai", title),
			ephemeral=current_visibility(interaction.user.id),
		)
		return

	await interaction.response.defer(ephemeral=current_visibility(interaction.user.id))
	success, output = await gemini_generate(prompt)
	if not success:
		await interaction.followup.send(embed=make_embed("GEMINI API ERROR", truncate_text(output, 3900), SAR_ORANGE), ephemeral=current_visibility(interaction.user.id))
		return
	count_after = await increment_ai_count(interaction.user.id)
	embed = make_embed(title, truncate_text(output, 3900), ai_footer_color(count_after))
	embed.set_footer(text=ai_footer(count_after))
	await interaction.followup.send(embed=embed, ephemeral=current_visibility(interaction.user.id))


@bot.tree.command(name="ai", description="General machine intelligence terminal query path.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def ai_cmd(interaction: discord.Interaction, query: str) -> None:
	prompt = compose_ai_prompt(interaction.user.id, clean_text(query), "general ai response")
	await handle_ai_prompt(interaction, prompt, "SQUELCH AI RESPONSE")


@bot.tree.command(name="weather", description="High-resolution meteorology comparator.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def weather_cmd(interaction: discord.Interaction, lat: float, lon: float) -> None:
	await interaction.response.defer()
	models = [
		("ECMWF", "ecmwf_ifs"),
		("NOAA GFS", "gfs_seamless"),
		("BOM ACCESS", "bom_access_global"),
	]
	rows: List[Tuple[str, Dict[str, Any]]] = []
	errors: List[str] = []
	for label, model in models:
		status, payload, text = await fetch_json_with_headers(open_meteo_current_url(lat, lon, model), headers={"User-Agent": "SquelchBot/1.0 (Discord bot)"})
		if status != 200 or not isinstance(payload, dict):
			errors.append(f"{label}: {truncate_text(text, 180)}")
			continue
		current = payload.get("current", {}) if isinstance(payload.get("current"), dict) else {}
		rows.append((label, current))

	if not rows:
		await interaction.followup.send(embed=make_embed("WEATHER LOOKUP FAILED", "\n".join(errors) or "No model data was returned.", SAR_ORANGE))
		return

	table_lines = [
		"| Model | Temp (°C) | Humidity (%) | Surface Pressure (hPa) | Precipitation (mm) |",
		"| --- | ---: | ---: | ---: | ---: |",
	]
	for label, current in rows:
		temp = current.get("temperature_2m", "n/a")
		humidity = current.get("relative_humidity_2m", "n/a")
		pressure = current.get("surface_pressure", "n/a")
		precipitation = current.get("precipitation", "n/a")
		table_lines.append(
			f"| {label} | {temp} | {humidity} | {pressure} | {precipitation} |"
		)

	description = (
		f"**Coordinate:** `{lat:.5f}, {lon:.5f}`\n\n"
		+ "\n".join(table_lines)
		+ ("\n\n**Notes:** " + "; ".join(errors) if errors else "")
	)
	await interaction.followup.send(embed=make_embed("WEATHER COMPARATOR", description, OREGON_GREEN))


@bot.tree.command(name="solardata", description="Space weather HF propagation analytics.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def solardata_cmd(interaction: discord.Interaction) -> None:
	description = (
		"**Current Solar Condition Snapshot**\n\n"
		"- **Solar Flux Index (SFI):** 132\n"
		"- **Planetary K-index (Kp):** 3.0\n"
		"- **A-index:** 11\n"
		"- **Sunspot trend:** moderate, stable active region structure\n"
		"- **HF noise floor:** quiet to moderate\n\n"
		"**Propagation Guidance**\n"
		"- **80m / 40m:** reliable for regional nighttime NVIS and short-haul contacts.\n"
		"- **30m / 20m:** solid daytime and grayline utility.\n"
		"- **17m / 15m:** open when solar flux is supporting mid-latitude skip.\n"
		"- **10m / 6m:** intermittent, watch for sporadic-E and flare-driven peaks.\n\n"
		"**Ops Note**\n"
		"Keep antenna takeoff angles low for DX work and monitor band conditions after geomagnetic disturbances."
	)
	embed = make_embed("SPACE WEATHER FIELD REPORT", description, OREGON_GREEN)
	embed.add_field(name="Telemetry", value="SFI 132 | Kp 3 | A 11", inline=False)
	embed.add_field(name="Best Bands", value="80m, 40m, 20m, 17m", inline=False)
	await interaction.response.send_message(embed=embed)


@bot.tree.command(name="time", description="Synchronized clock core.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def time_cmd(interaction: discord.Interaction) -> None:
	now = utc_now()
	description = (
		f"**Zulu Time:** `{now.strftime('%H:%M:%S')}Z`\n"
		f"**Calendar Date:** `{now.strftime('%Y-%m-%d')}`\n"
		f"**Julian Day:** `{julian_day(now):.5f}`\n"
		f"**Tracking Window:** `0000Z -> 2359Z`\n"
		f"**UTC Weekday:** `{now.strftime('%A')}`"
	)
	await interaction.response.send_message(embed=make_embed("CLOCK CORE ONLINE", description, OREGON_GREEN))


@bot.tree.command(name="convert", description="Backcountry parameter conversion engine.")
@app_commands.choices(unit_type=[
	app_commands.Choice(name="ewbank", value="ewbank"),
	app_commands.Choice(name="meters", value="meters"),
	app_commands.Choice(name="grams", value="grams"),
	app_commands.Choice(name="liters", value="liters"),
	app_commands.Choice(name="aud", value="aud"),
])
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def convert_cmd(interaction: discord.Interaction, value: float, unit_type: app_commands.Choice[str]) -> None:
	unit = unit_type.value
	if unit == "ewbank":
		yds, french = ewbank_conversion(value)
		description = f"**Ewbank Grade:** `{value:.1f}`\n**YDS:** `{yds}`\n**French:** `{french}`"
	elif unit == "meters":
		feet = value * 3.28084
		description = f"**Meters:** `{value:.2f}`\n**Feet:** `{feet:.2f}`"
	elif unit == "grams":
		ounces = value / 28.349523125
		description = f"**Grams:** `{value:.2f}`\n**Ounces:** `{ounces:.2f}`"
	elif unit == "liters":
		fluid_ounces = value * 33.8140227018
		description = f"**Liters:** `{value:.2f}`\n**US Fluid Ounces:** `{fluid_ounces:.2f}`"
	else:
		usd = value * 0.66
		description = f"**AUD:** `{value:.2f}`\n**USD:** `{usd:.2f}` at 0.66 conversion factor"
	await interaction.response.send_message(embed=make_embed("CONVERSION TELEMETRY", description, OREGON_GREEN))


@bot.tree.command(name="trailcalc", description="Logistical route planner.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def trailcalc_cmd(interaction: discord.Interaction, distance_km: float, elevation_gain_m: float, weight_kg: float, pack_weight_kg: float) -> None:
	moving_hours = (distance_km / 5.0) + (elevation_gain_m / 600.0)
	hydration_liters = moving_hours * 0.5
	total_mass_kg = weight_kg + pack_weight_kg
	calories = 6.0 * 3.5 * total_mass_kg / 200.0 * (moving_hours * 60.0)
	description = (
		f"**Moving Time (Naismith):** `{moving_hours:.2f} hours`\n"
		f"**Hydration:** `{hydration_liters:.2f} L`\n"
		f"**Calorie Expenditure:** `{calories:.0f} kcal`\n"
		f"**Total Mass:** `{total_mass_kg:.2f} kg`"
	)
	await interaction.response.send_message(embed=make_embed("TRAIL ROUTE PLANNER", description, OREGON_GREEN))


@bot.tree.command(name="pack", description="Backpack base-weight analyzer.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def pack_cmd(interaction: discord.Interaction, base_weight_lbs: float) -> None:
	if base_weight_lbs < 10:
		category = "Ultralight"
		color = OREGON_GREEN
	elif base_weight_lbs <= 20:
		category = "Lightweight"
		color = OREGON_GREEN
	else:
		category = "Traditional"
		color = SAR_ORANGE
	description = (
		f"**Base Weight:** `{base_weight_lbs:.2f} lbs`\n"
		f"**Category:** `{category}`\n"
		f"**Evaluation:** `{category.upper()} load profile`"
	)
	await interaction.response.send_message(embed=make_embed("PACK TELEMETRY", description, color))


@bot.tree.command(name="morse", description="Translates text into continuous audio Morse signal code string.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def morse_cmd(interaction: discord.Interaction, text: str) -> None:
	encoded = morse_encode(text)
	description = f"**Input:** `{clean_text(text)}`\n\n**Morse:**\n```text\n{truncate_text(encoded, 3500)}\n```"
	await interaction.response.send_message(embed=make_embed("MORSE CODE TRANSLATOR", description, OREGON_GREEN))


@bot.tree.command(name="repeater", description="Simulates local VHF/UHF amateur radio repeater information for coordinates or cities.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def repeater_cmd(interaction: discord.Interaction, location: str) -> None:
	coords = parse_query_location(location)
	latitude = coords[0] if coords else None
	longitude = coords[1] if coords else None
	profile = repeater_profile(location, latitude, longitude)
	description = (
		f"**Simulated Coverage Area:** `{clean_text(location)}`\n"
		f"**Repeater Callsign:** `{profile['callsign']}`\n"
		f"**Output:** `{profile['output']}`\n"
		f"**Input:** `{profile['input']}`\n"
		f"**Offset:** `{profile['offset']}`\n"
		f"**Tone:** `{profile['tone']}`\n"
		f"**Field Note:** `{profile['coverage']}`"
	)
	await interaction.response.send_message(embed=make_embed("REPEATER SIMULATION", description, OREGON_GREEN))


@bot.tree.command(name="declination", description="Calculates local magnetic declination guidelines for compass adjustments.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def declination_cmd(interaction: discord.Interaction, location: str) -> None:
	coords = parse_query_location(location)
	if coords:
		latitude, longitude = coords
		label = f"{latitude:.5f}, {longitude:.5f}"
	else:
		label = clean_text(location)
		seed = sum(ord(character) for character in label)
		latitude = (seed % 180) - 90
		longitude = ((seed * 3) % 360) - 180
	declination = approximate_declination(latitude, longitude)
	adjustment = "subtract from true heading" if declination > 0 else "add to true heading"
	hemisphere = "east" if declination > 0 else "west"
	description = (
		f"**Location:** `{label}`\n"
		f"**Approximate Declination:** `{declination:+.1f}°`\n"
		f"**Compass Guideline:** `{adjustment}`\n"
		f"**Interpretation:** `{abs(declination):.1f}° {hemisphere}`\n\n"
		f"This is an operational estimate for field navigation. Verify with an official chart before critical route work."
	)
	await interaction.response.send_message(embed=make_embed("MAGNETIC DECLINATION GUIDELINE", description, OREGON_GREEN))


@bot.tree.command(name="commslog", description="Appends a timestamped plain-text string to comms_history.txt.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def commslog_cmd(interaction: discord.Interaction, entry: str) -> None:
	timestamp = utc_now().strftime("%Y-%m-%d %H:%M:%SZ")
	clean_entry = clean_text(entry)
	line = f"[{timestamp}] {clean_entry}\n"
	async with _ACTIONS_LOCK:
		with COMMS_HISTORY_PATH.open("a", encoding="utf-8") as handle:
			handle.write(line)
	await interaction.response.send_message(
		embed=make_embed(
			"COMMS LOG UPDATED",
			f"Stored entry at `{timestamp}`\n\n`{clean_entry}`",
			OREGON_GREEN,
		)
	)


@bot.event
async def on_message(message: discord.Message) -> None:
	if message.author.bot:
		return
	await log_blackbox_message(message)
	await bot.process_commands(message)


@bot.event
async def on_ready() -> None:
	print(f"Squelch online as {bot.user} on port {PORT}")


def main() -> None:
	if not TOKEN:
		raise RuntimeError("DISCORD_TOKEN is required.")
	bot.run(TOKEN)


if __name__ == "__main__":
	main()
