import asyncio
import os
import logging
import random
import traceback
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

# ──────────────────────────────────────────────────────────────────────────────
# ロギング設定：24/7安定運用向け
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# discord.py の冗長なログを抑制（安定性向上）
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

# ロール名（サーバー内のロール名と一致させること）
EARTHQUAKE_ROLE_NAME = "地震速報"

# メンション条件（更新）
#   震度3以上      → @地震速報 ロールをメンション
#   津波注意報以上 → @everyone
SCALE_ROLE_MIN   = 30   # 震度3
TSUNAMI_EVERYONE_CODES = {"Watch", "Warning", "MajorWarning"}

# 10秒ごとにポーリング（1分間6リクエスト）
# 2秒はAPIへの負荷が高くIPブロックのリスクあり。
# 気象庁の地震情報自体が発生から約30〜60秒後の配信のため、10秒で実質最速。
POLL_INTERVAL_SECONDS = 10

# API設定
P2P_INFO_URL = "https://api.p2pquake.net/v2/history?codes=551&limit=5"
P2P_HISTORY_URL = "https://api.p2pquake.net/v2/history?codes=551&limit={limit}"

# タイムアウト設定（macOS 24/7運用向け）
API_TIMEOUT_SECONDS = 15
API_CONNECT_TIMEOUT_SECONDS = 10

FOOTER_TEXT = "データ提供: 気象庁 (JMA)"

seen_event_ids: set[str] = set()

SAMPLE_LOCATIONS = [
    "東京都", "大阪府", "神奈川県", "愛知県", "宮城県",
    "北海道", "福岡県", "静岡県", "熊本県", "新潟県",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def tsunami_color(code: str) -> discord.Color:
    if code == "MajorWarning":
        return discord.Color.from_rgb(180, 0, 0)
    if code == "Warning":
        return discord.Color.from_rgb(230, 50, 0)
    if code == "Watch":
        return discord.Color.from_rgb(0, 100, 200)
    return discord.Color.blurple()


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
    return mapping.get(scale, "不明")


def tsunami_label(code: str) -> str:
    mapping = {
        "None":         "なし",
        "Unknown":      "不明",
        "Checking":     "調査中",
        "NonEffective": "若干の海面変動あり（被害の心配なし）",
        "Watch":        "⚠️ 津波注意報",
        "Warning":      "🚨 津波警報",
        "MajorWarning": "🚨🚨 大津波警報",
    }
    return mapping.get(code, code)


def should_mention_everyone(max_scale: int, tsunami: str) -> tuple[bool, str]:
    """
    @everyone をメンションするかを判定し、理由を返す。
    津波注意報以上のときのみ @everyone をメンション。
    
    返り値: (メンション判定, 理由テキスト)
    """
    if tsunami == "MajorWarning":
        return True, "大津波警報"
    if tsunami == "Warning":
        return True, "津波警報"
    if tsunami == "Watch":
        return True, "津波注意報"
    return False, ""


def should_mention_role(max_scale: int, tsunami: str) -> tuple[bool, str]:
    """
    @地震速報 ロールをメンションするかを判定し、理由を返す。
    震度3以上のとき、かつ津波注意報未満のときにメンション。
    
    返り値: (メンション判定, 理由テキスト)
    """
    # 津波注意報以上の場合は役職ロールをメンションしない（@everyoneで対応）
    if tsunami in TSUNAMI_EVERYONE_CODES:
        return False, ""
    
    # 震度3以上で役職ロールをメンション
    if max_scale >= SCALE_ROLE_MIN:
        return True, f"震度3以上（{scale_label(max_scale)}）"
    
    return False, ""


def alert_title(mag: float, tsunami: str, test: bool = False) -> str:
    prefix = "【テスト】" if test else ""
    # 津波系タイトルを優先
    if tsunami == "MajorWarning":
        return f"{prefix}🚨 大津波警報 🚨"
    if tsunami in ("Watch", "Warning"):
        return f"{prefix}🌊 津波情報 🌊"
    # 地震規模ベース
    if mag >= 7.0:
        return f"{prefix}🚨 緊急地震速報 🚨"
    if mag >= 6.0:
        return f"{prefix}⚠️ 強い地震が発生しました — M{mag:.1f}"
    if mag >= 5.0:
        return f"{prefix}⚠️ 地震が発生しました — M{mag:.1f}"
    return f"{prefix}🇯🇵 地震情報 — M{mag:.1f}"


def alert_content(
    max_scale: int,
    tsunami: str,
    role: Optional[discord.Role] = None,
    test: bool = False,
) -> Optional[str]:
    """メンション付きメッセージ本文を返す。不要なら None。"""
    suffix = " *(テスト)*" if test else ""

    # @everyone 条件: 津波注意報以上
    should_everyone, _ = should_mention_everyone(max_scale, tsunami)
    if should_everyone:
        if tsunami == "MajorWarning":
            return f"🚨 大津波警報 🚨\n@everyone{suffix}"
        if tsunami in ("Watch", "Warning"):
            return f"🌊 津波情報 🌊\n@everyone{suffix}"
        return f"🚨 緊急地震速報 🚨\n@everyone{suffix}"

    # 役職ロール条件: 震度3以上（かつ津波注意報未満）
    should_role, _ = should_mention_role(max_scale, tsunami)
    if should_role:
        mention = role.mention if role else ""
        if mention:
            return f"{mention}{suffix}"

    return None


def quake_to_embed(
    mag: float,
    location: str,
    depth: int,
    max_scale: int,
    tsunami: str,
    occurred_at: str,
    test: bool = False,
) -> discord.Embed:
    title = alert_title(mag, tsunami, test=test)

    description_parts = []
    if tsunami == "MajorWarning" and not test:
        description_parts.append("**直ちに高台へ避難してください。**\n海岸・河口付近には絶対に近づかないでください。")
    elif tsunami in ("Watch", "Warning") and not test:
        description_parts.append("津波に関する情報に注意し、海岸付近には近づかないでください。")
    elif mag >= 7.0 and not test:
        description_parts.append(f"**マグニチュード {mag:.1f}** の大規模地震が発生しました。\n最新情報に注意してください。")
    if test:
        description_parts.append("⚠️ **これはテスト配信です。実際の地震ではありません。**")

    # 色: 津波警報がある場合は津波色を優先
    if tsunami in TSUNAMI_EVERYONE_CODES:
        color = tsunami_color(tsunami)
    else:
        color = magnitude_color(mag)

    embed = discord.Embed(
        title=title,
        description="\n".join(description_parts) if description_parts else None,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    try:
        dt = datetime.fromisoformat(occurred_at.replace(" ", "T"))
        dt = dt.replace(tzinfo=timezone.utc)
        time_display = f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        time_display = occurred_at or "不明"

    embed.add_field(name="🕐 発生時刻", value=time_display, inline=True)
    embed.add_field(name="📍 震源地", value=location or "不明", inline=True)
    embed.add_field(name="💥 マグニチュード", value=f"**M{mag:.1f}**", inline=True)
    embed.add_field(
        name="🕳️ 震源の深さ",
        value=f"約 {depth} km" if depth >= 0 else "不明",
        inline=True,
    )
    embed.add_field(
        name="📊 最大震度",
        value=scale_label(max_scale) if max_scale > 0 else "不明",
        inline=True,
    )
    embed.add_field(name="🌊 津波情報", value=tsunami_label(tsunami), inline=True)
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def parse_quake(quake: dict) -> dict:
    eq = quake.get("earthquake", {})
    hypo = eq.get("hypocenter", {})
    return {
        "mag":       hypo.get("magnitude", 0.0),
        "depth":     hypo.get("depth", -1),
        "location":  hypo.get("name", "不明"),
        "max_scale": eq.get("maxScale", -1),
        "tsunami":   eq.get("domesticTsunami", "Unknown"),
        "time":      eq.get("time", ""),
    }


def build_alert(quake: dict, role: Optional[discord.Role] = None) -> tuple[discord.Embed, Optional[str]]:
    p = parse_quake(quake)
    embed = quake_to_embed(**p)
    content = alert_content(p["max_scale"], p["tsunami"], role=role)
    return embed, content


def build_test_alert(mag: float, role: Optional[discord.Role] = None) -> tuple[discord.Embed, Optional[str]]:
    depth = random.randint(5, 60)
    location = random.choice(SAMPLE_LOCATIONS)
    scale_opts = [10, 20, 30, 40, 45, 50, 55, 60, 70]
    max_scale = scale_opts[min(int(mag) - 1, len(scale_opts) - 1)]
    tsunami = "MajorWarning" if mag >= 8.0 else "Warning" if mag >= 7.0 else "Watch" if mag >= 6.0 else "None"
    time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    embed = quake_to_embed(mag, location, depth, max_scale, tsunami, time_str, test=True)
    content = alert_content(max_scale, tsunami, role=role, test=True)
    return embed, content


def build_startup_embed(channel: discord.TextChannel) -> discord.Embed:
    embed = discord.Embed(
        title="✅ 地震監視システムを開始しました",
        description="気象庁（JMA）の地震情報をリアルタイムで監視します。",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="🟢 監視状態", value="稼働中", inline=True)
    embed.add_field(name="🔁 監視頻度", value=f"{POLL_INTERVAL_SECONDS}秒ごと", inline=True)
    embed.add_field(name="📢 通知チャンネル", value=channel.mention, inline=True)
    embed.add_field(
        name="📣 通知ルール",
        value=(
            f"🔔 **@{EARTHQUAKE_ROLE_NAME}**: 震度3以上\n"
            f"📢 **@everyone**: 津波注意報以上\n"
            f"🌍 24/7 連続監視中（安定性向上済み）"
        ),
        inline=False
    )
    embed.add_field(name="🌐 データソース", value="P2P地震情報 / 気象庁（JMA）", inline=True)
    embed.add_field(
        name="💬 利用可能なコマンド",
        value="`/ping`　`/status`　`/test`　`/history`　`/help`",
        inline=False,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class EarthquakeBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.alert_channel: Optional[discord.TextChannel] = None
        self._initial_poll_done = False
        self._monitor_start_time: Optional[datetime] = None
        self._consecutive_api_failures = 0
        self._max_api_failures = 10  # 100秒（10回 × 10秒）以上失敗したらアラート

        # ── /ping ─────────────────────────────────────────────────────────
        @self.tree.command(name="ping", description="ボットの応答速度を確認します")
        async def ping(interaction: discord.Interaction):
            ms = round(self.latency * 1000)
            embed = discord.Embed(
                title="🏓 Pong!",
                description=f"ゲートウェイレイテンシ: **{ms} ms**",
                color=discord.Color.green() if ms < 200 else discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text=FOOTER_TEXT)
            await interaction.response.send_message(embed=embed)

        # ── /status ────────────────────────────────────────────────────────
        @self.tree.command(name="status", description="地震監視ボットの稼働状況を表示します")
        async def status(interaction: discord.Interaction):
            uptime = "不明"
            if self._monitor_start_time:
                delta = datetime.now(timezone.utc) - self._monitor_start_time
                h, rem = divmod(int(delta.total_seconds()), 3600)
                m, s = divmod(rem, 60)
                uptime = f"{h}時間 {m}分 {s}秒"

            ch = self.alert_channel.mention if self.alert_channel else f"<#{DISCORD_CHANNEL_ID}>（未検出）"
            is_ready = "🟢 稼働中" if self._initial_poll_done else "🟡 初期化中"

            embed = discord.Embed(
                title="📡 地震監視ボット — 稼働状況",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="監視状態", value=is_ready, inline=True)
            embed.add_field(name="⏱️ 稼働時間", value=uptime, inline=True)
            embed.add_field(name="🔁 監視頻度", value=f"{POLL_INTERVAL_SECONDS}秒ごと", inline=True)
            embed.add_field(name="📢 通知チャンネル", value=ch, inline=True)
            embed.add_field(name="📊 検知済みイベント数", value=str(len(seen_event_ids)), inline=True)
            embed.add_field(name="🌐 データソース", value="P2P地震情報 / 気象庁（JMA）", inline=True)
            embed.add_field(
                name="📣 通知ルール",
                value=(
                    f"🔔 **@{EARTHQUAKE_ROLE_NAME}**: 震度3以上\n"
                    f"📢 **@everyone**: 津波注意報、津波警報、大津波警報、海外の大規模地震"
                ),
                inline=False,
            )
            embed.set_footer(text=FOOTER_TEXT)
            await interaction.response.send_message(embed=embed)

        # ── /test ─────────────────────────────────────────────────────────
        @self.tree.command(name="test", description="テスト用の地震アラートを送信します")
        @app_commands.describe(magnitude="マグニチュード（1.0〜9.0、デフォルト: 6.5）")
        async def test(
            interaction: discord.Interaction,
            magnitude: app_commands.Range[float, 1.0, 9.0] = 6.5,
        ):
            if self.alert_channel is None:
                await interaction.response.send_message(
                    "❌ 通知チャンネルが見つかりません。`DISCORD_CHANNEL_ID` を確認してください。",
                    ephemeral=True,
                )
                logger.error("テスト実行失敗: alert_channel が None")
                return
            try:
                role = discord.utils.get(self.alert_channel.guild.roles, name=EARTHQUAKE_ROLE_NAME)
                embed, content = build_test_alert(magnitude, role=role)
                await self.alert_channel.send(content=content, embed=embed)
                await interaction.response.send_message(
                    f"✅ テストアラート（M{magnitude:.1f}）を {self.alert_channel.mention} に送信しました。",
                    ephemeral=True,
                )
                logger.info("テストアラート送信成功: ユーザー=%s マグニチュード=%.1f", interaction.user, magnitude)
            except Exception as exc:
                logger.error("テストアラート送信失敗: %s\n%s", exc, traceback.format_exc())
                await interaction.response.send_message(
                    f"❌ テストアラート送信失敗: {exc}",
                    ephemeral=True,
                )

        # ── /history ───────────────────────────────────────────────────────
        @self.tree.command(name="history", description="最近の地震履歴を表示します")
        @app_commands.describe(件数="表示する件数（1〜20、デフォルト: 5）")
        async def history(
            interaction: discord.Interaction,
            件数: app_commands.Range[int, 1, 20] = 5,
        ):
            await interaction.response.defer(thinking=True)
            try:
                async with aiohttp.ClientSession() as session:
                    url = P2P_HISTORY_URL.format(limit=件数)
                    timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS, connect=API_CONNECT_TIMEOUT_SECONDS)
                    async with session.get(url, timeout=timeout) as resp:
                        if resp.status != 200:
                            logger.warning("履歴取得API失敗: HTTP %d", resp.status)
                            await interaction.followup.send(
                                f"❌ データの取得に失敗しました（HTTP {resp.status}）。", ephemeral=True
                            )
                            return
                        data = await resp.json()
                        logger.info("履歴取得成功: %d件", len(data))
            except asyncio.TimeoutError:
                logger.error("履歴取得タイムアウト")
                await interaction.followup.send("❌ データ取得がタイムアウトしました。", ephemeral=True)
                return
            except Exception as exc:
                logger.error("履歴取得失敗: %s\n%s", exc, traceback.format_exc())
                await interaction.followup.send("❌ データの取得中にエラーが発生しました。", ephemeral=True)
                return

            if not data:
                logger.info("履歴取得: データなし")
                await interaction.followup.send("📭 地震データが見つかりませんでした。", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"📋 最近の地震情報（直近 {len(data)} 件）",
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc),
            )

            for i, quake in enumerate(data, start=1):
                p = parse_quake(quake)
                mag = p["mag"]
                location = p["location"]
                depth = p["depth"]
                max_scale = p["max_scale"]
                tsunami = p["tsunami"]
                time_str = p["time"]

                try:
                    dt = datetime.fromisoformat(time_str.replace(" ", "T"))
                    dt = dt.replace(tzinfo=timezone.utc)
                    time_display = f"<t:{int(dt.timestamp())}:R>"
                except Exception:
                    time_display = time_str or "不明"

                depth_str = f"約{depth}km" if depth >= 0 else "不明"
                scale_str = scale_label(max_scale) if max_scale > 0 else "不明"
                mag_icon = "🔴" if mag >= 6.0 else "🟠" if mag >= 5.0 else "🟡" if mag >= 4.0 else "🔵"

                value = (
                    f"**{mag_icon} M{mag:.1f}** — {location}\n"
                    f"深さ: {depth_str}　最大震度: {scale_str}\n"
                    f"津波: {tsunami_label(tsunami)}　{time_display}"
                )
                embed.add_field(name=f"第{i}件", value=value, inline=False)

            embed.set_footer(text=FOOTER_TEXT)
            await interaction.followup.send(embed=embed)

        # ── /help ─────────────────────────────────────────────────────────
        COMMAND_META: dict[str, dict] = {
            "ping":    {"category": "🔧 基本コマンド", "params": "", "detail": "ボットのレスポンス速度を確認"},
            "status":  {"category": "🔧 基本コマンド", "params": "", "detail": "ボットの稼働状況と統計情報を表示"},
            "test":    {"category": "🧪 テスト",       "params": " [マグニチュード]", "detail": "テスト地震アラートを送信"},
            "history": {"category": "📜 履歴",         "params": " [件数]", "detail": "最近の地震履歴を表示"},
            "help":    {"category": "🔧 基本コマンド", "params": "", "detail": "このヘルプを表示"},
        }
        CATEGORY_ORDER = ["🔧 基本コマンド", "🧪 テスト", "📜 履歴", "その他"]

        @self.tree.command(name="help", description="利用可能なコマンドと通知ルールを表示します")
        async def help(interaction: discord.Interaction):
            commands = sorted(self.tree.get_commands(), key=lambda c: c.name)

            # カテゴリごとにグループ化
            groups: dict[str, list[str]] = {cat: [] for cat in CATEGORY_ORDER}
            for cmd in commands:
                meta = COMMAND_META.get(cmd.name, {})
                category = meta.get("category", "その他")
                params = meta.get("params", "")
                detail = meta.get("detail", cmd.description)
                groups.setdefault(category, [])
                groups[category].append(f"`/{cmd.name}{params}`\n{detail}")

            embed = discord.Embed(
                title="📖 地震速報Bot — コマンド＆通知ルール",
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc),
            )

            # コマンド一覧
            for category in CATEGORY_ORDER:
                entries = groups.get(category, [])
                if entries:
                    embed.add_field(
                        name=category,
                        value="\n\n".join(entries),
                        inline=False,
                    )

            # 通知ルール（詳細版）
            embed.add_field(
                name="📣 通知ルール — 詳細",
                value=(
                    f"**🔔 @{EARTHQUAKE_ROLE_NAME} ロール:**\n"
                    f"　• 震度3以上のすべての地震\n\n"
                    f"**📢 @everyone 全員:**\n"
                    f"　• 津波注意報（Watch）が発表されたとき\n"
                    f"　• 津波警報（Warning）が発表されたとき\n"
                    f"　• 大津波警報（MajorWarning）が発表されたとき\n"
                    f"　• 海外の大規模地震で日本への影響がありうるとき\n\n"
                    f"**⚪ メンションなし:**\n"
                    f"　• 震度2以下の地震\n"
                    f"　• 津波注意報未満の地震\n\n"
                    f"**📡 監視頻度:** {POLL_INTERVAL_SECONDS}秒ごと\n"
                    f"**⏰ 運用:** 24/7 連続監視（macOS安定化済み）\n"
                    f"**🌐 情報源:** P2P地震情報 API / 気象庁（JMA）"
                ),
                inline=False,
            )

            embed.add_field(
                name="ℹ️ 補足",
                value=(
                    f"・テストアラートは実際の地震ではありません\n"
                    f"・最大20件の地震履歴が利用可能です\n"
                    f"・ロール `@{EARTHQUAKE_ROLE_NAME}` は手動で作成してください\n"
                    f"・日本の地震情報をメインに監視しています\n"
                    f"・地震による @everyone メンションは廃止（津波のみ）"
                ),
                inline=False,
            )

            embed.set_footer(text=f"登録コマンド数: {len(commands)}　|　地震速報Bot v1.1　|　{FOOTER_TEXT}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("✅ グローバルスラッシュコマンドを同期しました")

    async def on_ready(self):
        logger.info("🔗 ログイン完了: %s (ID: %s)", self.user, self.user.id)
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if channel is None:
            logger.error("❌ チャンネル %d が見つかりません — DISCORD_CHANNEL_ID を確認してください", DISCORD_CHANNEL_ID)
        else:
            self.alert_channel = channel
            logger.info("✅ 通知チャンネル設定完了: #%s (Guild: %s)", channel.name, channel.guild.name)
            
            # ギルド限定の即時同期
            guild = channel.guild
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
                logger.info("✅ ギルド '%s' にスラッシュコマンドを即時同期しました: %s", guild.name, [c.name for c in synced])
            except Exception as exc:
                logger.error("❌ コマンド同期失敗: %s\n%s", exc, traceback.format_exc())
        
        self.check_earthquakes.start()
        logger.info("🚀 地震監視タスクを開始しました（間隔: %d秒）", POLL_INTERVAL_SECONDS)

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def check_earthquakes(self):
        if self.alert_channel is None:
            return

        try:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS, connect=API_CONNECT_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(P2P_INFO_URL, timeout=timeout) as resp:
                        if resp.status != 200:
                            self._consecutive_api_failures += 1
                            logger.warning("⚠️  API呼び出し失敗: HTTP %d (失敗回数: %d/%d)", resp.status, self._consecutive_api_failures, self._max_api_failures)
                            if self._consecutive_api_failures >= self._max_api_failures:
                                logger.error("❌ API連続失敗が閾値に達しました: %d回", self._consecutive_api_failures)
                            return
                        
                        data = await resp.json()
                        self._consecutive_api_failures = 0  # 成功時はカウンター初期化
                        
                except asyncio.TimeoutError:
                    self._consecutive_api_failures += 1
                    logger.warning("⚠️  API呼び出しタイムアウト (失敗回数: %d/%d)", self._consecutive_api_failures, self._max_api_failures)
                    return
                except Exception as exc:
                    self._consecutive_api_failures += 1
                    logger.error("⚠️  API呼び出し中にエラー: %s (失敗回数: %d/%d)\n%s", exc, self._consecutive_api_failures, self._max_api_failures, traceback.format_exc())
                    return
        except Exception as exc:
            self._consecutive_api_failures += 1
            logger.error("❌ 予期しないエラー (地震監視): %s\n%s", exc, traceback.format_exc())
            return

        new_quakes = []
        for quake in data:
            event_id = quake.get("id") or quake.get("_id") or str(quake.get("time", ""))
            if event_id:
                if event_id not in seen_event_ids:
                    seen_event_ids.add(event_id)
                    if not self._initial_poll_done:
                        logger.debug("初回ポール中にイベントをスキップ: %s", event_id)
                        continue
                    new_quakes.append(quake)
                else:
                    logger.debug("重複イベントをスキップ: %s", event_id)

        if not self._initial_poll_done:
            self._initial_poll_done = True
            self._monitor_start_time = datetime.now(timezone.utc)
            logger.info("✅ 初回ポール完了 — %d 件のイベントを読み込みました。新しい地震を監視中。", len(seen_event_ids))
            try:
                await self.alert_channel.send(embed=build_startup_embed(self.alert_channel))
                logger.info("✅ スタートアップメッセージを送信しました")
            except Exception as exc:
                logger.error("❌ スタートアップメッセージ送信失敗: %s\n%s", exc, traceback.format_exc())
            return

        if new_quakes:
            logger.info("🔔 新しい地震イベント検出: %d件", len(new_quakes))

        role = discord.utils.get(self.alert_channel.guild.roles, name=EARTHQUAKE_ROLE_NAME)
        if role is None:
            logger.warning("⚠️  ロール '%s' が見つかりません。震度3以上の通知はメンションなしで送信されます。", EARTHQUAKE_ROLE_NAME)

        for quake in new_quakes:
            try:
                p = parse_quake(quake)
                event_id = quake.get("id") or quake.get("_id") or str(quake.get("time", ""))
                
                # メンション判定と理由を取得
                should_everyone, everyone_reason = should_mention_everyone(p["max_scale"], p["tsunami"])
                should_role, role_reason = should_mention_role(p["max_scale"], p["tsunami"])
                
                # ログ出力（詳細）
                if should_everyone:
                    logger.info(
                        "📢 @everyone メンション判定: M%.1f 最大震度=%s 津波=%s | 理由: %s",
                        p["mag"], p["max_scale"], p["tsunami"], everyone_reason
                    )
                elif should_role:
                    logger.info(
                        "🔔 ロールメンション判定: M%.1f 最大震度=%s 津波=%s | 理由: %s | ロール: %s",
                        p["mag"], p["max_scale"], p["tsunami"], role_reason, role.name if role else "未検出"
                    )
                else:
                    logger.info(
                        "🔵 メンションなし: M%.1f 最大震度=%s 津波=%s",
                        p["mag"], p["max_scale"], p["tsunami"]
                    )
                
                embed, content = build_alert(quake, role=role)
                await self.alert_channel.send(content=content, embed=embed)
                logger.info(
                    "✅ アラート送信成功: イベントID=%s M=%.1f 震度=%s 津波=%s 場所=%s",
                    event_id, p["mag"], scale_label(p["max_scale"]), p["tsunami"], p["location"]
                )
            except Exception as exc:
                logger.error("❌ アラート送信失敗: イベントID=%s\nエラー: %s\n%s", event_id, exc, traceback.format_exc())

    @check_earthquakes.before_loop
    async def before_check(self):
        await self.wait_until_ready()
        logger.info("✅ ボット準備完了。地震監視を開始します。")


def main():
    try:
        bot = EarthquakeBot()
        logger.info("🚀 ボットを起動しています...")
        bot.run(DISCORD_BOT_TOKEN, log_handler=None)
    except KeyboardInterrupt:
        logger.info("⏹️  キーボード割り込みで停止されました")
    except Exception as exc:
        logger.error("❌ ボット実行中に予期しないエラーが発生しました: %s\n%s", exc, traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
