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
        10: "震度1",
        20: "震度2",
        30: "震度3",
        40: "震度4",
        45: "震度5弱",
        50: "震度5強",
        55: "震度6弱",
        60: "震度6強",
        70: "震度7",
    }
    return mapping.get(scale, f"不明 ({scale})")


def tsunami_label(code: str) -> str:
    mapping = {
        "None": "なし",
        "Unknown": "不明",
        "Checking": "調査中",
        "NonEffective": "若干の海面変動あり（被害の心配なし）",
        "Watch": "⚠️ 津波注意報",
        "Warning": "🚨 津波警報",
    }
    return mapping.get(code, code)


def build_embed(quake: dict) -> tuple[discord.Embed, bool]:
    earthquake = quake.get("earthquake", {})
    hypocenter = earthquake.get("hypocenter", {})

    mag: float = hypocenter.get("magnitude", 0.0)
    depth: int = hypocenter.get("depth", -1)
    name: str = hypocenter.get("name", "不明")
    max_scale: int = earthquake.get("maxScale", -1)
    domestic_tsunami: str = earthquake.get("domesticTsunami", "Unknown")

    time_str: str = earthquake.get("time", "")
    try:
        dt = datetime.fromisoformat(time_str.replace(" ", "T"))
        dt = dt.replace(tzinfo=timezone.utc)
        time_display = f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        time_display = time_str or "不明"

    mention_everyone = mag >= EVERYONE_MENTION_THRESHOLD

    if mag >= 7.0:
        title = f"🚨 大規模地震発生 — M{mag:.1f}"
    elif mag >= 6.0:
        title = f"⚠️ 強い地震発生 — M{mag:.1f}"
    elif mag >= 5.0:
        title = f"⚠️ 地震発生 — M{mag:.1f}"
    else:
        title = f"🇯🇵 地震発生 — M{mag:.1f}"

    embed = discord.Embed(
        title=title,
        color=magnitude_color(mag),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="震源地", value=name, inline=True)
    embed.add_field(name="マグニチュード", value=f"M{mag:.1f}", inline=True)
    embed.add_field(
        name="震源の深さ",
        value=f"約{depth}km" if depth >= 0 else "不明",
        inline=True,
    )
    if max_scale > 0:
        embed.add_field(name="最大震度", value=scale_label(max_scale), inline=True)
    embed.add_field(name="津波の有無", value=tsunami_label(domestic_tsunami), inline=True)
    embed.add_field(name="発生時刻", value=time_display, inline=True)
    embed.set_footer(text="情報源：P2P地震情報 / 気象庁（JMA）")

    return embed, mention_everyone


def build_test_embed(mag: float) -> tuple[discord.Embed, bool]:
    depth = random.randint(5, 60)
    location = random.choice(SAMPLE_LOCATIONS)
    scale_options = [10, 20, 30, 40, 45, 50, 55, 60, 70]
    max_scale = scale_options[min(int(mag) - 1, len(scale_options) - 1)]

    mention_everyone = mag >= EVERYONE_MENTION_THRESHOLD

    if mag >= 7.0:
        title = f"🚨 [テスト] 大規模地震発生 — M{mag:.1f}"
        tsunami = "Warning"
    elif mag >= 6.0:
        title = f"⚠️ [テスト] 強い地震発生 — M{mag:.1f}"
        tsunami = "None"
    elif mag >= 5.0:
        title = f"⚠️ [テスト] 地震発生 — M{mag:.1f}"
        tsunami = "None"
    else:
        title = f"🇯🇵 [テスト] 地震発生 — M{mag:.1f}"
        tsunami = "None"

    embed = discord.Embed(
        title=title,
        description="⚠️ **これはテスト配信です。実際の地震ではありません。**",
        color=magnitude_color(mag),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="震源地", value=location, inline=True)
    embed.add_field(name="マグニチュード", value=f"M{mag:.1f}", inline=True)
    embed.add_field(name="震源の深さ", value=f"約{depth}km", inline=True)
    embed.add_field(name="最大震度", value=scale_label(max_scale), inline=True)
    embed.add_field(name="津波の有無", value=tsunami_label(tsunami), inline=True)
    embed.add_field(
        name="発生時刻",
        value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>",
        inline=True,
    )
    embed.set_footer(text="[テスト] 情報源：P2P地震情報 / 気象庁（JMA）")

    return embed, mention_everyone


class EarthquakeBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.alert_channel: Optional[discord.TextChannel] = None
        self._initial_poll_done = False
        self._monitor_start_time: Optional[datetime] = None

        @self.tree.command(name="ping", description="ボットのレイテンシを確認します")
        async def ping(interaction: discord.Interaction):
            latency_ms = round(self.latency * 1000)
            embed = discord.Embed(
                title="🏓 Pong!",
                description=f"ゲートウェイレイテンシ: **{latency_ms} ms**",
                color=discord.Color.green() if latency_ms < 200 else discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="status", description="地震監視ボットの稼働状況を表示します")
        async def status(interaction: discord.Interaction):
            uptime_str = "不明"
            if self._monitor_start_time:
                delta = datetime.now(timezone.utc) - self._monitor_start_time
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                uptime_str = f"{hours}時間 {minutes}分 {seconds}秒"

            channel_mention = (
                self.alert_channel.mention
                if self.alert_channel
                else f"<#{DISCORD_CHANNEL_ID}>（未検出）"
            )

            embed = discord.Embed(
                title="📡 地震監視ボット — 稼働状況",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="🟢 状態", value="監視中", inline=True)
            embed.add_field(name="⏱️ 稼働時間", value=uptime_str, inline=True)
            embed.add_field(name="🔁 確認間隔", value="60秒ごと", inline=True)
            embed.add_field(name="📢 通知チャンネル", value=channel_mention, inline=True)
            embed.add_field(
                name="📣 @everyone 発動条件",
                value=f"M{EVERYONE_MENTION_THRESHOLD}以上",
                inline=True,
            )
            embed.add_field(
                name="📊 検知済みイベント数",
                value=str(len(seen_event_ids)),
                inline=True,
            )
            embed.add_field(name="🌐 情報源", value="P2P地震情報 / 気象庁（JMA）", inline=False)
            embed.set_footer(text="日本地震速報ボット")
            await interaction.response.send_message(embed=embed)

        @self.tree.command(
            name="test",
            description="テスト用地震アラートを通知チャンネルに送信します",
        )
        @app_commands.describe(
            magnitude="テスト地震のマグニチュード（1.0〜9.0、デフォルト: 6.5）"
        )
        async def test(
            interaction: discord.Interaction,
            magnitude: app_commands.Range[float, 1.0, 9.0] = 6.5,
        ):
            if self.alert_channel is None:
                await interaction.response.send_message(
                    "❌ 通知チャンネルが見つかりません。`DISCORD_CHANNEL_ID` を確認してください。",
                    ephemeral=True,
                )
                return

            embed, mention_everyone = build_test_embed(magnitude)
            if mention_everyone:
                content = "🚨 緊急地震速報 🚨\n@everyone *(テスト)*"
            else:
                content = None

            await self.alert_channel.send(content=content, embed=embed)
            await interaction.response.send_message(
                f"✅ テストアラート（M{magnitude:.1f}）を {self.alert_channel.mention} に送信しました。",
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
                    title="🟢 地震監視を開始しました",
                    description=(
                        "気象庁（JMA）の地震情報を60秒ごとに監視します。\n"
                        f"M{EVERYONE_MENTION_THRESHOLD}以上の地震は @everyone に通知されます。\n\n"
                        "**スラッシュコマンド:** `/ping` `/status` `/test`"
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc),
                ).set_footer(text="情報源：P2P地震情報 / 気象庁（JMA）")
            )
            return

        for quake in new_quakes:
            try:
                embed, mention_everyone = build_embed(quake)
                if mention_everyone:
                    content = "🚨 緊急地震速報 🚨\n@everyone"
                else:
                    content = None
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
