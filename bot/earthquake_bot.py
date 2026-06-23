import asyncio
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord.ext import tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

# Threshold magnitude to mention @everyone
EVERYONE_MENTION_THRESHOLD = 5.0

# P2P earthquake info API (free, no key required, returns latest JMA data)
P2P_INFO_URL = "https://api.p2pquake.net/v2/history?codes=551&limit=5"

# Store IDs of alerts already sent so we don't double-post
seen_event_ids: set[str] = set()


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

    title = f"🇯🇵 Earthquake Detected — M{mag:.1f}"
    if mag >= 7.0:
        title = f"🚨 MAJOR EARTHQUAKE — M{mag:.1f}"
    elif mag >= 6.0:
        title = f"⚠️ Strong Earthquake — M{mag:.1f}"
    elif mag >= 5.0:
        title = f"⚠️ Moderate Earthquake — M{mag:.1f}"

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


class EarthquakeBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.channel: Optional[discord.TextChannel] = None

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if channel is None:
            logger.error("Channel %d not found — check DISCORD_CHANNEL_ID", DISCORD_CHANNEL_ID)
        else:
            self.channel = channel
            logger.info("Alert channel: #%s", channel.name)
        self.check_earthquakes.start()

    @tasks.loop(seconds=60)
    async def check_earthquakes(self):
        if self.channel is None:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(P2P_INFO_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning("API returned status %d", resp.status)
                        return
                    data = await resp.json()
        except Exception as exc:
            logger.error("Failed to fetch earthquake data: %s", exc)
            return

        new_quakes = []
        for quake in data:
            event_id = quake.get("id") or quake.get("_id") or str(quake.get("time", ""))
            if event_id and event_id not in seen_event_ids:
                seen_event_ids.add(event_id)
                # Skip if this is the very first poll (populate seen set without alerting)
                if not hasattr(self, "_initial_poll_done"):
                    continue
                new_quakes.append(quake)

        if not hasattr(self, "_initial_poll_done"):
            self._initial_poll_done = True
            logger.info("Initial poll complete — %d recent events loaded, monitoring for new ones.", len(seen_event_ids))
            await self.channel.send(
                embed=discord.Embed(
                    title="🟢 Earthquake Monitor Active",
                    description=(
                        "Monitoring Japan earthquake data every minute.\n"
                        f"Alerts will appear here. M{EVERYONE_MENTION_THRESHOLD}+ events will mention @everyone."
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
                await self.channel.send(content=content, embed=embed)
                mag = quake.get("earthquake", {}).get("hypocenter", {}).get("magnitude", "?")
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
