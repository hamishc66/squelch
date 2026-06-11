import asyncio
import json
import math
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands


BASE_DIR = Path(__file__).resolve().parent
WHITELIST_PATH = BASE_DIR / "whitelist.txt"
AI_TRACKER_PATH = BASE_DIR / "ai_tracker.json"
COMMS_HISTORY_PATH = BASE_DIR / "comms_history.txt"
PORT = int(os.getenv("PORT", "8080"))
TOKEN = os.getenv("DISCORD_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")

OREGON_GREEN = discord.Color.from_rgb(34, 77, 23)
SAR_ORANGE = discord.Color.from_rgb(255, 85, 0)

AI_WARNING_FOOTER = "⚠️ Telemetry: AI usage at {count}/24 for today."
AI_LIMIT = 24


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
	url = (
		"https://generativelanguage.googleapis.com/v1beta/models/"
		"gemini-1.5-flash:generateContent?key=" + urllib.parse.quote_plus(GEMINI_API_KEY)
	)
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
		super().__init__(command_prefix="/", intents=intents)
		self.http_runner: Optional[web.AppRunner] = None
		self.http_site: Optional[web.TCPSite] = None

	async def setup_hook(self) -> None:
		await self._start_web_server()
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
	await interaction.response.defer()

	if selected == "google":
		embed = make_embed("DATUM: GOOGLE ROUTE", f"[Open Google search]({google_search_url(query)})", OREGON_GREEN)
		view = DepthSourcesView(google_search_url(query), query)
		await interaction.followup.send(embed=embed, view=view)
		return

	if selected == "wolfram":
		embed = make_embed("DATUM: WOLFRAM ROUTE", f"[Open WolframAlpha]({wolfram_url(query)})", OREGON_GREEN)
		view = DepthSourcesView(wolfram_url(query), query)
		await interaction.followup.send(embed=embed, view=view)
		return

	if selected == "stack":
		embed = make_embed("DATUM: STACK OVERFLOW ROUTE", f"[Open Stack Overflow search]({stackoverflow_url(query)})", OREGON_GREEN)
		view = DepthSourcesView(stackoverflow_url(query), query)
		await interaction.followup.send(embed=embed, view=view)
		return

	if selected == "wikipedia":
		headers = {"User-Agent": "SquelchBot/1.0 (Discord bot)"}
		summary_url = wikipedia_summary_url(query)
		status, payload, text = await fetch_json_with_headers(summary_url, headers)
		if status != 200 or not isinstance(payload, dict):
			embed = make_embed("DATUM: WIKIPEDIA ERROR", truncate_text(text, 3900), SAR_ORANGE)
			view = DepthSourcesView(None, query)
			await interaction.followup.send(embed=embed, view=view)
			return
		extract = payload.get("extract") or "No summary extract was returned."
		article_url = payload.get("content_urls", {}).get("desktop", {}).get("page") or wikipedia_desktop_url(query)
		title = payload.get("title") or query
		embed = make_embed(f"DATUM: {title}", truncate_text(extract, 3900), OREGON_GREEN)
		view = DepthSourcesView(article_url, query)
		await interaction.followup.send(embed=embed, view=view)
		return

	if selected == "ai":
		count = ai_count_for_user(interaction.user.id)
		if count >= AI_LIMIT:
			await interaction.followup.send(
				embed=make_embed(
					"SAR QUOTA REACHED",
					"Daily AI capacity is exhausted for this UTC day. Try again after 00:00 UTC.",
					SAR_ORANGE,
				)
			)
			return
		if count >= 20:
			view = ProceedsView(interaction.user.id, query, "ai", "DATUM: AI ANALYSIS")
			await interaction.followup.send(
				embed=make_embed(
					"AI EXECUTION HOLD",
					f"AI usage is at {count}/24 for today. Proceed to execute the Gemini request or cancel.",
					SAR_ORANGE,
				),
				view=view,
			)
			return
		success, output = await gemini_generate(query)
		if not success:
			embed = make_embed("GEMINI API ERROR", truncate_text(output, 3900), SAR_ORANGE)
			view = DepthSourcesView(None, query)
			await interaction.followup.send(embed=embed, view=view)
			return
		count_after = await increment_ai_count(interaction.user.id)
		embed = make_embed("DATUM: AI ANALYSIS", truncate_text(output, 3900), ai_footer_color(count_after))
		embed.set_footer(text=ai_footer(count_after))
		view = DepthSourcesView(None, query)
		await interaction.followup.send(embed=embed, view=view)
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
			)
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
		)
		return

	await interaction.response.defer()
	success, output = await gemini_generate(prompt)
	if not success:
		await interaction.followup.send(embed=make_embed("GEMINI API ERROR", truncate_text(output, 3900), SAR_ORANGE))
		return
	count_after = await increment_ai_count(interaction.user.id)
	embed = make_embed(title, truncate_text(output, 3900), ai_footer_color(count_after))
	embed.set_footer(text=ai_footer(count_after))
	await interaction.followup.send(embed=embed)


@bot.tree.command(name="ai", description="General machine intelligence terminal query path.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.guild_install()
@app_commands.user_install()
async def ai_cmd(interaction: discord.Interaction, query: str) -> None:
	prompt = clean_text(query)
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
async def on_ready() -> None:
	print(f"Squelch online as {bot.user} on port {PORT}")


def main() -> None:
	if not TOKEN:
		raise RuntimeError("DISCORD_TOKEN is required.")
	bot.run(TOKEN)


if __name__ == "__main__":
	main()
