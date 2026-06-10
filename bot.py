import os
import json
import asyncio
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web

# 🎨 theme configurations
OREGON_GREEN = discord.Color.from_str("#224D17")
SAR_ORANGE = discord.Color.from_str("#FF5500")

# 🔐 access control utilities
def is_whitelisted(user_id: int) -> bool:
    if not os.path.exists("whitelist.txt"):
        return False
    with open("whitelist.txt", "r") as f:
        allowed_ids = [line.strip() for line in f.readlines()]
    return str(user_id) in allowed_ids

# 🤖 ai usage tracking core
def check_and_update_ai_usage(user_id: int, increment: bool = False) -> tuple[int, bool]:
    """returns (current_count, allowed_to_proceed_immediately)"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    if os.path.exists("ai_tracker.json") and os.path.getsize("ai_tracker.json") > 0:
        with open("ai_tracker.json", "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
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
            
    # if they hit 20 or more requests, they need a hard confirmation prompt
    if current_count >= 20:
        return current_count, False
    return current_count, True

# 🚨 confirmation button view for the final 4 requests
class AiConfirmationView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, command_name: str, kwargs: dict):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.command_name = command_name
        self.kwargs = kwargs

    @discord.ui.button(label="Proceed", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("this prompt isn't yours.", ephemeral=True)
            return
            
        # increment counter and execute
        count, _ = check_and_update_ai_usage(interaction.user.id, increment=True)
        self.stop()
        
        # update message to process the logic
        await interaction.response.edit_message(content="📡 contacting data node... processing ai request.", view=None)
        embed = discord.Embed(
            title="🛰️ telemetry relay: ai processing complete",
            description=f"simulated ai response for your query.\ncurrent limit: **{count}/24**",
            color=SAR_ORANGE if count >= 20 else OREGON_GREEN
        )
        embed.set_footer(text=f"telemetry: ai usage at {count}/24 for today.")
        await interaction.followup.send(embed=embed)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="❌ request aborted. daily allowance preserved.", view=None)

# 🌐 koyeb micro-webserver for automated network health checks
async def web_handle(request):
    return web.Response(text="relay system online")

async def start_koyeb_health_server():
    app = web.Application()
    app.router.add_get('/', web_handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# 🤖 bot configuration
class SafetyUtilityBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # hook the background webserver right into discord's main event loop
        self.loop.create_task(start_koyeb_health_server())
        await self.tree.sync()

# 🎯 FIXED: instantiating the bot client engine here
bot = SafetyUtilityBot()

# 🔐 global command check to lock out unverified accounts
async def whitelist_gatekeeper(interaction: discord.Interaction) -> bool:
    if not is_whitelisted(interaction.user.id):
        await interaction.response.send_message(
            "❌ identification mismatch. user id not in telemetry database.", 
            ephemeral=True
        )
        return False
    return True

# assign it directly to the tree's global check attribute
bot.tree.interaction_check = whitelist_gatekeeper

# 🛰️ sample commands structure
@bot.tree.command(name="datum", description="query structural data parameters from baseline systems")
@app_commands.choices(source=[
    app_commands.Choice(name="wikipedia", value="wiki"),
    app_commands.Choice(name="google", value="google"),
    app_commands.Choice(name="ai (gemini)", value="ai"),
    app_commands.Choice(name="dictionary", value="dict")
])
async def datum(interaction: discord.Interaction, query: str, source: str):
    if source == "ai":
        count, immediate = check_and_update_ai_usage(interaction.user.id, increment=False)
        if count >= 24:
            await interaction.response.send_message("🚨 maximum daily operational ceiling reached (24/24). reset occurs at 00:00 utc.", ephemeral=True)
            return
            
        if not immediate:
            # intercept execution and supply confirmation buttons
            view = AiConfirmationView(interaction, "datum", {"query": query, "source": source})
            await interaction.response.send_message(
                content=f"⚠️ **alert:** you are within your final 4 daily ai allocations ({count}/24). confirm deployment?",
                view=view,
                ephemeral=True
            )
            return
            
        # normal increment if under 20 requests
        count, _ = check_and_update_ai_usage(interaction.user.id, increment=True)
        
        embed = discord.Embed(title="🛰️ datum matrix: ai analysis", description=f"query: {query}\n\n[insert gemini api response code here]", color=OREGON_GREEN)
        embed.set_footer(text=f"telemetry: ai usage at {count}/24 for today.")
        await interaction.response.send_message(embed=embed)
        return

    # non-ai sources handle directly
    embed = discord.Embed(title=f"🛰️ datum fetch: {source}", description=f"displaying parsed values for: {query}", color=OREGON_GREEN)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="gps", description="coordinating grid system translator")
async def gps(interaction: discord.Interaction, location: str):
    embed = discord.Embed(
        title="🗺️ geographic coordinate calculation",
        description=f"**target:** {location}\n**dd:** 44.5646, -123.2620\n**ddm:** 44° 33.876' N, 123° 15.720' W\n**maidenhead:** CN84in",
        color=OREGON_GREEN
    )
    
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="OpenStreetMap", url=f"https://www.openstreetmap.org/search?query={location}"))
    view.add_item(discord.ui.Button(label="Google Maps", url=f"https://www.google.com/maps/search/?api=1&query={location}"))
    
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="time", description="unified temporal telemetry overview")
async def time_command(interaction: discord.Interaction):
    utc_now = datetime.utcnow()
    embed = discord.Embed(title="🕒 synchronized temporal metrics", color=OREGON_GREEN)
    embed.add_field(name="🛰️ zulu / utc", value=f"`{utc_now.strftime('%H:%M:%S UTC')}`", inline=False)
    embed.add_field(name="📅 date matrix", value=f"`{utc_now.strftime('%Y-%m-%d')}`", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="convert", description="tactical backcountry unit and scale translator")
@app_commands.choices(category=[
    app_commands.Choice(name="climbing grades (ewbank to yds)", value="climb"),
    app_commands.Choice(name="elevation (meters to feet)", value="elevation"),
    app_commands.Choice(name="weight (grams to ounces)", value="weight")
])
async def convert(interaction: discord.Interaction, category: str, value: float):
    embed = discord.Embed(title="📐 physical unit conversion log", color=OREGON_GREEN)
    
    if category == "climb":
        yds_val = "5.9" if value <= 18 else "5.10a"
        embed.description = f"**input:** ewbank {int(value)}\n**output:** yosemite decimal system (yds) **{yds_val}**"
    elif category == "elevation":
        converted = value * 3.28084
        embed.description = f"**input:** {value} meters\n**output:** {converted:.2f} feet"
    elif category == "weight":
        converted = value * 0.035274
        embed.description = f"**input:** {value} grams\n**output:** {converted:.2f} ounces"
        
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="trailcalc", description="backcountry speed, metabolic, and hydration metrics")
async def trailcalc(interaction: discord.Interaction, distance_km: float, elevation_gain_m: float, pack_weight_kg: float):
    moving_hours = (distance_km / 5.0) + (elevation_gain_m / 600.0)
    water_liters = moving_hours * 0.5
    calories = int(moving_hours * 450)
    
    embed = discord.Embed(
        title="🥾 logistical route projection",
        description=f"**metrics calculated for a pack weight of {pack_weight_kg} kg:**",
        color=OREGON_GREEN
    )
    embed.add_field(name="⏱️ moving duration (naismith's rule)", value=f"`{moving_hours:.2f} hours`", inline=True)
    embed.add_field(name="💧 min hydration volume", value=f"`{water_liters:.2f} liters`", inline=True)
    embed.add_field(name="🔥 estimated caloric cost", value=f"`{calories} kcal`", inline=True)
    
    await interaction.response.send_message(embed=embed)

# trigger execution using environment keys
bot.run(os.environ.get("DISCORD_TOKEN"))
