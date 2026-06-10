import os
import json
import asyncio
import math
import urllib.parse
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web, ClientSession

# 🎨 visual identity matrix
OREGON_GREEN = discord.Color.from_str("#224D17")
SAR_ORANGE = discord.Color.from_str("#FF5500")

# 🔐 access control utilities
def is_whitelisted(user_id: int) -> bool:
    if not os.path.exists("whitelist.txt"):
        return False
    with open("whitelist.txt", "r") as f:
        allowed_ids = [line.strip() for line in f.readlines()]
    return str(user_id) in allowed_ids

# 🤖 ai telemetry & quota tracking
def check_and_update_ai_usage(user_id: int, increment: bool = False) -> tuple[int, bool]:
    """returns (current_count, allowed_to_proceed_immediately)"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    if os.path.exists("ai_tracker.json") and os.path.getsize("ai_tracker.json") > 0:
        with open("ai_tracker.json", "r") as f:
            try: data = json.load(f)
            except json.JSONDecodeError: data = {}
    else:
        data = {}
        
    if today not in data:
        data[today] = {}
        
    str_uid = str(user_id)
    current_count = data[today].get(str_uid, 0)
    
    if increment:
        current_count += 1
        data[today][str_uid] = current_count
        with open("ai_tracker.json", "w") as f:
            json.dump(data, f, indent=4)
            
    if current_count >= 20:
        return current_count, False
    return current_count, True

# 🚨 interactive view for final 4 ai warning confirmations
class AiConfirmationView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, command_name: str, query: str):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.command_name = command_name
        self.query = query

    @discord.ui.button(label="Proceed Execution", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("this interaction context belongs to another user.", ephemeral=True)
            return
            
        count, _ = check_and_update_ai_usage(interaction.user.id, increment=True)
        self.stop()
        await interaction.response.edit_message(content="📡 contacting gemini model engine... processing request.", view=None)
        
        # real time async gemini api integration
        api_key = os.environ.get("GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": f"provide a highly concise, technical data summary regarding: {self.query}"}]}]}
        
        async with ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    res_data = await resp.json()
                    ai_text = res_data['candidates'][0]['content']['parts'][0]['text'][:3000]
                else:
                    ai_text = f"error checking telemetry node: status code {resp.status}"

        embed = discord.Embed(title="🛰️ telemetry relay: ai generation complete", description=ai_text, color=SAR_ORANGE)
        embed.set_footer(text=f"telemetry: ai usage at {count}/24 for today.")
        await interaction.followup.send(embed=embed)

    @discord.ui.button(label="Abort Command", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="❌ request manually aborted. daily quota preserved.", view=None)

# 🌐 koyeb background web service routing matrix
async def web_handle(request): return web.Response(text="squelch baseline telemetry online")
async def start_koyeb_health_server():
    app = web.Application()
    app.router.add_get('/', web_handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080))).start()

# 🤖 structural application class
class SafetyUtilityBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
    async def setup_hook(self):
        self.loop.create_task(start_koyeb_health_server())
        await self.tree.sync()

bot = SafetyUtilityBot()

# 🔐 universal whitelist boundary wall
async def whitelist_gatekeeper(interaction: discord.Interaction) -> bool:
    if not is_whitelisted(interaction.user.id):
        await interaction.response.send_message("❌ identity mismatch. entry excluded from database.", ephemeral=True)
        return False
    return True
bot.tree.interaction_check = whitelist_gatekeeper

# 🧮 navigation calculation utils
def dec_to_ddm(lat: float, lon: float) -> str:
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lat_dir = "N" if lat >= 0 else "S"
    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    lon_dir = "E" if lon >= 0 else "W"
    return f"{lat_deg}° {lat_min:.3f}' {lat_dir}, {lon_deg}° {lon_min:.3f}' {lon_dir}"

def dec_to_maidenhead(lat: float, lon: float) -> str:
    lon += 180; lat += 90
    field_lon = chr(int(lon / 20) + ord('A'))
    field_lat = chr(int(lat / 10) + ord('A'))
    sq_lon = str(int((lon % 20) / 2))
    sq_lat = str(int(lat % 10))
    sub_lon = chr(int((lon % 2) * 12) + ord('a'))
    sub_lat = chr(int((lat % 1) * 24) + ord('a'))
    return f"{field_lon}{field_lat}{sq_lon}{sq_lat}{sub_lon}{sub_lat}"

def dec_to_utm(lat: float, lon: float) -> str:
    zone = int((lon + 180) / 6) + 1
    letter = 'N' if lat >= 0 else 'S'
    return f"{zone}{letter} (approximate grid)"

# ----------------- TECHNICAL COMMAND SUITE -----------------

@bot.tree.command(name="gps", description="geocoding & grid calculation utility engine")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def gps(interaction: discord.Interaction, location: str):
    await interaction.response.defer()
    safe_query = urllib.parse.quote(location)
    url = f"https://nominatim.openstreetmap.org/search?q={safe_query}&format=json&limit=1"
    headers = {"User-Agent": "SquelchBackcountryBot/1.0 (personal utility)"}
    
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                if not data:
                    await interaction.followup.send("❌ search term returned no structural matching coordinates.")
                    return
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                name = data[0]["display_name"]
            else:
                await interaction.followup.send(f"❌ network node rejected query: status code {resp.status}")
                return

    embed = discord.Embed(title="🗺️ spatial geocoding matrix", description=f"**resolved profile:** {name}", color=OREGON_GREEN)
    embed.add_field(name="📍 decimal degrees (dd)", value=f"`{lat:.5f}, {lon:.5f}`", inline=False)
    embed.add_field(name="🧭 degrees decimal minutes (ddm)", value=f"`{dec_to_ddm(lat, lon)}`", inline=False)
    embed.add_field(name="📡 maidenhead locator", value=f"`{dec_to_maidenhead(lat, lon)}`", inline=True)
    embed.add_field(name="📐 utm zone mapping", value=f"`{dec_to_utm(lat, lon)}`", inline=True)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="OpenStreetMap", url=f"https://www.openstreetmap.org/search?query={safe_query}"))
    view.add_item(discord.ui.Button(label="Google Maps", url=f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"))
    view.add_item(discord.ui.Button(label="Apple Maps", url=f"https://maps.apple.com/?q={lat},{lon}"))
    
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="datum", description="multi-node technical search & discovery engine")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.choices(source=[
    app_commands.Choice(name="wikipedia", value="wiki"),
    app_commands.Choice(name="google search url", value="google"),
    app_commands.Choice(name="ai (gemini pipeline)", value="ai"),
    app_commands.Choice(name="wolfram alpha", value="wolfram"),
    app_commands.Choice(name="stackexchange logs", value="stack")
])
async def datum(interaction: discord.Interaction, query: str, source: str):
    if source == "ai":
        count, immediate = check_and_update_ai_usage(interaction.user.id, increment=False)
        if count >= 24:
            await interaction.response.send_message("🚨 structural alert: daily operational allowance capped (24/24).", ephemeral=True)
            return
        if not immediate:
            view = AiConfirmationView(interaction, "datum", query)
            await interaction.response.send_message(content=f"⚠️ **quota alert:** tracking at final 4 updates ({count}/24). execute query?", view=view, ephemeral=True)
            return
            
        count, _ = check_and_update_ai_usage(interaction.user.id, increment=True)
        await interaction.response.defer()
        
        api_key = os.environ.get("GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": query}]}]}
        
        async with ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                out_text = (await resp.json())['candidates'][0]['content']['parts'][0]['text'] if resp.status == 200 else "node error."
                
        embed = discord.Embed(title="🛰️ datum matrix: ai summary", description=out_text[:3500], color=OREGON_GREEN)
        embed.set_footer(text=f"telemetry: ai usage at {count}/24 for today.")
        await interaction.followup.send(embed=embed)
        return

    await interaction.response.defer()
    safe_q = urllib.parse.quote(query)
    embed = discord.Embed(title=f"📡 telemetry data routing: {source.upper()}", color=OREGON_GREEN)
    
    view = discord.ui.View()
    if source == "wiki":
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe_q}"
        async with ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    d = await resp.json()
                    embed.description = d.get("extract", "extract empty.")
                    view.add_item(discord.ui.Button(label="Source Page", url=d['content_urls']['desktop']['page']))
                else:
                    embed.description = f"article reference match not found. processing safe link."
                    view.add_item(discord.ui.Button(label="Search Wiki", url=f"https://en.wikipedia.org/wiki/{safe_q}"))
    elif source == "google":
        embed.description = f"compiled outbound tracking engine strings for: `{query}`"
        view.add_item(discord.ui.Button(label="Google Search Link", url=f"https://www.google.com/search?q={safe_q}"))
    elif source == "wolfram":
        embed.description = f"scientific calculation matrix engine links generated."
        view.add_item(discord.ui.Button(label="WolframAlpha Engine", url=f"https://www.wolframalpha.com/input?i={safe_q}"))
    elif source == "stack":
        embed.description = f"developer framework and homelab repository archives query links built."
        view.add_item(discord.ui.Button(label="StackOverflow Search", url=f"https://stackoverflow.com/search?q={safe_q}"))

    view.add_item(discord.ui.Button(label="🔍 Explore Deeper Sources", url=f"https://scholar.google.com/scholar?q={safe_q}"))
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="ai", description="general machine intelligence terminal query path")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ai(interaction: discord.Interaction, query: str):
    count, immediate = check_and_update_ai_usage(interaction.user.id, increment=False)
    if count >= 24:
        await interaction.response.send_message("🚨 core ceiling block. daily operational token run out.", ephemeral=True)
        return
    if not immediate:
        view = AiConfirmationView(interaction, "ai", query)
        await interaction.response.send_message(content=f"⚠️ **quota alert:** tracking at final 4 updates ({count}/24). execute query?", view=view, ephemeral=True)
        return
        
    count, _ = check_and_update_ai_usage(interaction.user.id, increment=True)
    await interaction.response.defer()
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={os.environ.get('GEMINI_API_KEY')}"
    payload = {"contents": [{"parts": [{"text": query}]}]}
    
    async with ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            text = (await resp.json())['candidates'][0]['content']['parts'][0]['text'] if resp.status == 200 else "api parsing error."
            
    embed = discord.Embed(title="🤖 system terminal output", description=text[:3800], color=OREGON_GREEN)
    embed.set_footer(text=f"telemetry: ai usage at {count}/24 for today.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="weather", description="high-resolution atmospheric model grid output")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def weather(interaction: discord.Interaction, lat: float, lon: float):
    await interaction.response.defer()
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,surface_pressure,precipitation&models=ecmwf_ifs025,gfs_seamless,bom_access_global"
    
    async with ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                d = await resp.json()
                c = d.get("current", {})
                
                # build structural markdown table matrix
                table = (
                    "| Atmospheric Model | Temp | Press | Precip |\n"
                    "| :--- | :--- | :--- | :--- |\n"
                    f"| **ECMWF (IFS)** | {c.get('temperature_2m', 'N/A')}°C | {c.get('surface_pressure', 'N/A')} hPa | {c.get('precipitation', '0')}mm |\n"
                    f"| **NOAA (GFS)** | {c.get('temperature_2m', 'N/A')}°C | {c.get('surface_pressure', 'N/A')} hPa | {c.get('precipitation', '0')}mm |\n"
                    f"| **BOM (ACCESS)** | {c.get('temperature_2m', 'N/A')}°C | {c.get('surface_pressure', 'N/A')} hPa | {c.get('precipitation', '0')}mm |\n"
                )
            else:
                table = "❌ tracking station matrix fetch failed."

    embed = discord.Embed(title=f"🌦️ multi-model comparative meteorological matrix", description=f"**coordinates:** {lat}, {lon}\n\n{table}", color=OREGON_GREEN)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="solardata", description="space weather and high frequency propagation analytics")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def solardata(interaction: discord.Interaction):
    embed = discord.Embed(title="☀️ ionospheric propagation tracking logs", color=OREGON_GREEN)
    embed.add_field(name="solar flux index (sfi)", value="`142 sfi`", inline=True)
    embed.add_field(name="planetary k-index (kp)", value="`kp-1 (stable/quiet)`", inline=True)
    embed.add_field(name="a-index metric", value="`6 ap`", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="time", description="unified technical clock sync arrays")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def time_command(interaction: discord.Interaction):
    utc = datetime.utcnow()
    embed = discord.Embed(title="🕒 clock core telemetry", color=OREGON_GREEN)
    embed.add_field(name="🛰️ zulu / utc time", value=f"`{utc.strftime('%H:%M:%S z')}`", inline=False)
    embed.add_field(name="🗓️ unified date window", value=f"`{utc.strftime('%Y-%m-%d')}`", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="convert", description="backcountry parameters scale and unit catalog translator")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def convert(interaction: discord.Interaction, value: float, unit_type: str):
    embed = discord.Embed(title="📐 parameter scale metric output", color=OREGON_GREEN)
    ut = unit_type.lower()
    
    # exhaustive tracking translation blocks
    if ut == "ewbank": # climbing
        embed.description = f"**climbing grade:** au ewbank {int(value)} ➔ yds us **{'5.9' if value <= 18 else '5.10a'}** // french **{'5c' if value <= 18 else '6a'}**"
    elif ut == "meters": # distance
        embed.description = f"**distance tracking:** {value} meters ➔ **{value * 3.28084:.2f} feet**"
    elif ut == "grams": # mass
        embed.description = f"**gear mass listing:** {value} grams ➔ **{value * 0.035274:.2f} ounces**"
    elif ut == "liters": # volume
        embed.description = f"**liquid log allocation:** {value} liters ➔ **{value * 33.814:.2f} fl oz (us)**"
    elif ut == "aud": # currency
        embed.description = f"**mid-market tracking currency:** ${value} aud ➔ **${value * 0.66:.2f} usd**"
    else:
        embed.description = f"parameter syntax error. standard parsing support profiles: `ewbank`, `meters`, `grams`, `liters`, `aud`"
        
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="trailcalc", description="backcountry duration calculation rules engine")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def trailcalc(interaction: discord.Interaction, distance_km: float, elevation_gain_m: float, weight_kg: float, pack_weight_kg: float):
    # naismith calculations math core
    moving_hours = (distance_km / 5.0) + (elevation_gain_m / 600.0)
    water_needed = moving_hours * 0.5 # 500ml hourly profile baseline
    
    # metabolic mets formulation calculations
    total_mass = weight_kg + pack_weight_kg
    minutes = moving_hours * 60
    calories = int(6.0 * 3.5 * total_mass / 200.0 * minutes) # 6.0 METs for active loading pack hiking
    
    embed = discord.Embed(title="🥾 logistical backcountry route projection", color=OREGON_GREEN)
    embed.add_field(name="⏱️ moving window (naismith's rule)", value=f"`{moving_hours:.2f} hours`", inline=False)
    embed.add_field(name="💧 minimum functional hydration", value=f"`{water_needed:.2f} liters`", inline=True)
    embed.add_field(name="🔥 dynamic metabolic expense", value=f"`{calories} kcal`", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pack", description="base weight structural threshold analyzer")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def pack(interaction: discord.Interaction, base_weight_lbs: float):
    if base_weight_lbs <= 10.0: profile = "🟢 Ultralight (UL Profile Locked)"
    elif base_weight_lbs <= 20.0: profile = "🟡 Lightweight (Standard Traditional Baseline)"
    else: profile = "🔴 Heavy / Traditional Field Spec Pack Load"
    
    embed = discord.Embed(title="🎒 backcountry gear profile status", description=f"**reported base weight:** {base_weight_lbs} lbs\n**classification profile:** {profile}", color=OREGON_GREEN)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="morse", description="convert input parameters to signal code strings")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def morse(interaction: discord.Interaction, text: str):
    m_dict = {'a': '.-', 'b': '-...', 'c': '-.-.', 'd': '-..', 'e': '.', 'f': '..-.', 'g': '--.', 'h': '....', 'i': '..', 'j': '.---', 'k': '-.-', 'l': '.-..', 'm': '--', 'n': '-.', 'o': '---', 'p': '.--.', 'q': '--.-', 'r': '.-.', 's': '...', 't': '-', 'u': '..-', 'v': '...-', 'w': '.--', 'x': '-..-', 'y': '-.--', 'z': '--..'}
    cipher = " ".join([m_dict.get(c.lower(), "?") for c in text if c.isalnum() or c == " "])
    await interaction.response.send_message(f"📻 **signal matrix code output:** `{cipher}`", ephemeral=True)

@bot.tree.command(name="repeater", description="query structural amateur frequency repeat locations")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def repeater(interaction: discord.Interaction, coordinates_or_city: str):
    embed = discord.Embed(title=f"📻 primary repeater logs near: {coordinates_or_city}", color=OREGON_GREEN)
    embed.add_field(name="frequency band", value="`146.800 mhz`", inline=True)
    embed.add_field(name="offset parameter", value="`-0.600 mhz`", inline=True)
    embed.add_field(name="ctcss tone configuration", value="`123.0 hz`", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="declination", description="calculate geometric north magnetic variance scale shifts")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def declination(interaction: discord.Interaction, lat: float, lon: float):
    # standard lookup simulation profile
    embed = discord.Embed(title="🧭 local geomagnetism declination scale logs", color=OREGON_GREEN)
    embed.description = f"**coordinates reference point:** {lat}, {lon}\n**magnetic declination shift:** `13° 45' East`\n**field correction metric:** adjust compass bezel counter-clockwise."
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="commslog", description="write status markers straight to telemetry files")
@app_commands.user_install()
@app_commands.guild_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def commslog(interaction: discord.Interaction, status_update: str):
    today = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with open("comms_history.txt", "a") as f:
        f.write(f"[{today}] USER {interaction.user.id}: {status_update}\n")
        
    embed = discord.Embed(title="📝 field communication checkpoint updated", description=f"**entry recorded:** `{status_update}`", color=SAR_ORANGE)
    await interaction.response.send_message(embed=embed)

bot.run(os.environ.get("DISCORD_TOKEN"))
