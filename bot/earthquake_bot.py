import asyncio
import os
import logging
import random
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

EVERYONE_MENTION_THRESHOLD = 5.0
P2P_INFO_URL = "https://api.p2pquake.net/v2/history?codes=551&limit=5"

seen_event_ids: set[str] = set()

SAMPLE_LOCATIONS = [
    "東京都", "大阪府", "神奈川県", "愛知県", "宮城県",
    "北海道", "福岡県", "静岡県", "熊本県", "新潟県",
]


def magnitude_color(mag: float) -> discord.Color:
    if mag >= 7.0:
        return discord.Color.from_rgb(180, 0, 0)
    if mag >= 6.0:
        return discord.Color.from_rgb(230, 50, 0)
    if mag >= 5.0:
        return discord.Color.from_rgb(230, 140, 0)
    if mag >= 4.0:
        return discord.Color.from_rgb(230, 200, 0)
    return discord.Color.from_rgb(60, 150, 230)


def scale_label(scale: int) -> str:
    mapping = {
        10: "1",
        20: "2",
        30: "3",
        40: "4",
        45: "5-weak (震度5弱)",
        50: "5-strong (震度5強)",
        55: "6-weak (震度6弱)",
        60: "6-strong (震度6強)",
        70: "7",
    }
    return mapping.get(scale, f"unknown ({scale})")


def build_embed(quake: dict) -> tuple[discord.Embed, bool]:
    earthquake = quake.get("earthquake", {})
    hypocenter = earthquake.get("hypocenter", {})

    mag: float = hypocenter.get("magnitude", 0.0)
    depth: int = hypocenter.get("depth", -1)
    name: str = hypocenter.get("name", "Unknown location")
    max_scale: int = earthquake.get("maxScale", -1)
    domestic_tsunami: str = earthquake.get("domesticTsunami", "Unknown")

    time_str: str = earthquake.get("time", "")
    try:
        dt = datetime.fromisoformat(time_str.replace(" ", "T"))
        dt = dt.replace(tzinfo=timezone.utc)
        time_display = f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        time_display = time_str or "Unknown"

    tsunami_map = {
        "None": "None expected",
        "Unknown": "Unknown",
        "Checking": "Under investigation",
        "NonEffective": "Negligible",
        "Watch": "⚠️ Tsunami Watch issued",
        "Warning": "🚨 Tsunami Warning issued",
    }
    tsunami_text = tsunami_map.get(domestic_tsunami, domestic_tsunami)

    mention_everyone = mag >= EVERYONE_MENTION_THRESHOLD

    if mag >= 7.0:
        title = f"🚨 MAJOR EARTHQUAKE — M{mag:.1f}"
    elif mag >= 6.0:
        title = f"⚠️ Strong Earthquake — M{mag:.1f}"
    elif mag >= 5.0:
        title = f"⚠️ Moderate Earthquake — M{mag:.1f}"
    else:
        title = f"🇯🇵 Earthquake Detected — M{mag:.1f}"

    embed = discord.Embed(
        title=title,
        color=magnitude_color(mag),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="📍 Location", value=name, inline=True)
    embed.add_field(name="💥 Magnitude", value=f"M{mag:.1f}", inline=True)
    embed.add_field(
        name="🕳️ Depth",
        value=f"{depth} km" if depth >= 0 else "Unknown",
        inline=True,
    )
    if max_scale > 0:
        embed.add_field(
            name="📊 Max Seismic Intensity",
            value=scale_label(max_scale),
            inline=True,
        )
    embed.add_field(name="🌊 Tsunami", value=tsunami_text, inline=True)
    embed.add_field(name="🕐 Time (UTC)", value=time_display, inline=True)
    embed.set_footer(text="Source: P2P地震情報 / JMA data")

    return embed, mention_everyone


def build_test_embed(mag: float) -> tuple[discord.Embed, bool]:
    depth = random.randint(5, 60)
    location = random.choice(SAMPLE_LOCATIONS)
    scale_options = [10, 20, 30, 40, 45, 50, 55, 60, 70]
    max_scale = scale_options[min(int(mag) - 1, len(scale_options) - 1)]

    mention_everyone = mag >= EVERYONE_MENTION_THRESHOLD

    if mag >= 7.0:
        title = f"🚨 [TEST] MAJOR EARTHQUAKE — M{mag:.1f}"
        tsunami = "⚠️ Tsunami Watch issued"
    elif mag >= 6.0:
        title = f"⚠️ [TEST] Strong Earthquake — M{mag:.1f}"
        tsunami = "None expected"
    elif mag >= 5.0:
        title = f"⚠️ [TEST] Moderate Earthquake — M{mag:.1f}"
        tsunami = "None expected"
    else:
        title = f"🇯🇵 [TEST] Earthquake Detected — M{mag:.1f}"
        tsunami = "None expected"

    embed = discord.Embed(
        title=title,
        description="⚠️ **This is a TEST alert — not a real earthquake.**",
        color=magnitude_color(mag),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="📍 Location", value=location, inline=True)
    embed.add_field(name="💥 Magnitude", value=f"M{mag:.1f}", inline=True)
    embed.add_field(name="🕳️ Depth", value=f"{depth} km", inline=True)
    embed.add_field(
        name="📊 Max Seismic Intensity",
        value=scale_label(max_scale),
        inline=True,
    )
    embed.add_field(name="🌊 Tsunami", value=tsunami, inline=True)
    embed.add_field(
        name="🕐 Time (UTC)",
        value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>",
        inline=True,
    )
    embed.set_footer(text="[TEST] Source: P2P地震情報 / JMA data")

    return embed, mention_everyone


class EarthquakeBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.alert_channel: Optional[discord.TextChannel] = None
        self._initial_poll_done = False
        self._monitor_start_time: Optional[datetime] = None

        @self.tree.command(name="ping", description="Check the bot's latency")
        async def ping(interaction: discord.Interaction):
            latency_ms = round(self.latency * 1000)
            embed = discord.Embed(
                title="🏓 Pong!",
                description=f"Gateway latency: **{latency_ms} ms**",
                color=discord.Color.green() if latency_ms < 200 else discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="status", description="Show the earthquake monitor status")
        async def status(interaction: discord.Interaction):
            uptime_str = "Unknown"
            if self._monitor_start_time:
                delta = datetime.now(timezone.utc) - self._monitor_start_time
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                uptime_str = f"{hours}h {minutes}m {seconds}s"

            channel_mention = (
                self.alert_channel.mention
                if self.alert_channel
                else f"<#{DISCORD_CHANNEL_ID}> (not found)"
            )

            embed = discord.Embed(
                title="📡 Earthquake Monitor Status",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="🟢 Status", value="Online & monitoring", inline=True)
            embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=True)
            embed.add_field(name="🔁 Check interval", value="Every 60 seconds", inline=True)
            embed.add_field(name="📢 Alert channel", value=channel_mention, inline=True)
            embed.add_field(
                name="📣 @everyone threshold",
                value=f"M{EVERYONE_MENTION_THRESHOLD}+",
                inline=True,
            )
            embed.add_field(
                name="📊 Events tracked",
                value=str(len(seen_event_ids)),
                inline=True,
            )
            embed.add_field(name="🌐 Data source", value="P2P地震情報 / JMA", inline=False)
            embed.set_footer(text="Japan Earthquake Monitor")
            await interaction.response.send_message(embed=embed)

        @self.tree.command(
            name="test",
            description="Send a simulated earthquake alert to the alert channel",
        )
        @app_commands.describe(
            magnitude="Magnitude of the test earthquake (1.0–9.0, default: 6.5)"
        )
        async def test(
            interaction: discord.Interaction,
            magnitude: app_commands.Range[float, 1.0, 9.0] = 6.5,
        ):
            if self.alert_channel is None:
                await interaction.response.send_message(
                    "❌ Alert channel not found. Check `DISCORD_CHANNEL_ID`.",
                    ephemeral=True,
                )
                return

            embed, mention_everyone = build_test_embed(magnitude)
            content = "@everyone *(test)*" if mention_everyone else None

            await self.alert_channel.send(content=content, embed=embed)
            await interaction.response.send_message(
                f"✅ Test alert (M{magnitude:.1f}) sent to {self.alert_channel.mention}.",
                ephemeral=True,
            )
            logger.info(
                "Test alert triggered by %s — M%.1f", interaction.user, magnitude
            )

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Slash commands synced globally")

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if channel is None:
            logger.error(
                "Channel %d not found — check DISCORD_CHANNEL_ID", DISCORD_CHANNEL_ID
            )
        else:
            self.alert_channel = channel
            logger.info("Alert channel: #%s", channel.name)
        self.check_earthquakes.start()

    @tasks.loop(seconds=60)
    async def check_earthquakes(self):
        if self.alert_channel is None:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    P2P_INFO_URL, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("API returned status %d", resp.status)
                        return
                    data = await resp.json()
        except Exception as exc:
            logger.error("Failed to fetch earthquake data: %s", exc)
            return

        new_quakes = []
        for quake in data:
            event_id = (
                quake.get("id") or quake.get("_id") or str(quake.get("time", ""))
            )
            if event_id and event_id not in seen_event_ids:
                seen_event_ids.add(event_id)
                if not self._initial_poll_done:
                    continue
                new_quakes.append(quake)

        if not self._initial_poll_done:
            self._initial_poll_done = True
            self._monitor_start_time = datetime.now(timezone.utc)
            logger.info(
                "Initial poll complete — %d recent events loaded, monitoring for new ones.",
                len(seen_event_ids),
            )
            await self.alert_channel.send(
                embed=discord.Embed(
                    title="🟢 Earthquake Monitor Active",
                    description=(
                        "Monitoring Japan earthquake data every minute.\n"
                        f"Alerts will appear here. M{EVERYONE_MENTION_THRESHOLD}+"
                        " events will mention @everyone.\n\n"
                        "**Slash commands:** `/ping` `/status` `/test`"
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc),
                ).set_footer(text="Source: P2P地震情報 / JMA data")
            )
            return

        for quake in new_quakes:
            try:
                embed, mention_everyone = build_embed(quake)
                content = "@everyone" if mention_everyone else None
                await self.alert_channel.send(content=content, embed=embed)
                mag = (
                    quake.get("earthquake", {})
                    .get("hypocenter", {})
                    .get("magnitude", "?")
                )
                logger.info("Alert sent for M%s event", mag)
            except Exception as exc:
                logger.error("Failed to send alert: %s", exc)

    @check_earthquakes.before_loop
    async def before_check(self):
        await self.wait_until_ready()


def main():
    bot = EarthquakeBot()
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
