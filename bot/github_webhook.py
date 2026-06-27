"""
GitHub Webhook Server for Discord Bot
Handles GitHub push and release events, sends notifications to Discord
"""

import hmac
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload_body: bytes, signature: str, secret: str) -> bool:
    """
    GitHub webhook signature verification using HMAC-SHA256
    
    Args:
        payload_body: Raw request body (bytes)
        signature: X-Hub-Signature-256 header value (e.g., "sha256=abc123...")
        secret: GitHub webhook secret
    
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        if not signature or not signature.startswith("sha256="):
            logger.warning("⚠️  無効な署名形式: %s", signature)
            return False
        
        # Compute HMAC-SHA256
        expected_signature = "sha256=" + hmac.new(
            secret.encode(),
            payload_body,
            hashlib.sha256
        ).hexdigest()
        
        # Constant-time comparison
        is_valid = hmac.compare_digest(signature, expected_signature)
        if not is_valid:
            logger.warning("⚠️  Webhook署名検証失敗（一致しません）")
        return is_valid
    except Exception as exc:
        logger.error("❌ Webhook署名検証中にエラー: %s", exc)
        return False


def build_commit_embed(commit_data: Dict[str, Any], repo_name: str) -> Any:
    """
    Build a Discord Embed for a commit push event
    
    Args:
        commit_data: Commit information from GitHub webhook
        repo_name: Repository name
    
    Returns:
        discord.Embed object
    """
    import discord
    
    try:
        commit_id = commit_data.get("id", "unknown")[:7]
        message = commit_data.get("message", "No message").split("\n")[0]
        author = commit_data.get("author", {})
        author_name = author.get("name", "Unknown")
        author_email = author.get("email", "")
        url = commit_data.get("url", "")
        timestamp_str = commit_data.get("timestamp", "")
        
        embed = discord.Embed(
            title="📝 新しいコミット",
            description=f"**{message}**",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        
        embed.add_field(name="📦 リポジトリ", value=repo_name, inline=True)
        embed.add_field(name="👤 作成者", value=f"{author_name}", inline=True)
        embed.add_field(name="🔗 コミット", value=f"`{commit_id}`", inline=True)
        embed.add_field(name="⏰ タイムスタンプ", value=timestamp_str or "不明", inline=True)
        
        if url:
            embed.add_field(name="🌐 URL", value=f"[GitHub]{url}", inline=False)
        
        if author_email:
            embed.add_field(name="📧 メール", value=author_email, inline=True)
        
        embed.set_footer(text="GitHub Webhook | データ提供: GitHub")
        return embed
    except Exception as exc:
        logger.error("❌ コミットEmbed作成失敗: %s", exc)
        return None


def build_release_embed(release_data: Dict[str, Any], repo_name: str) -> Any:
    """
    Build a Discord Embed for a GitHub release event (Japanese)
    
    Args:
        release_data: Release information from GitHub webhook
        repo_name: Repository name
    
    Returns:
        discord.Embed object
    """
    import discord
    
    try:
        version = release_data.get("tag_name", "unknown")
        title = release_data.get("name", "")
        body = release_data.get("body", "変更内容なし")
        release_url = release_data.get("html_url", "")
        published_at = release_data.get("published_at", "")
        author = release_data.get("author", {})
        author_name = author.get("login", "Unknown")
        
        # Truncate body if too long
        if len(body) > 1024:
            body = body[:1021] + "..."
        
        embed = discord.Embed(
            title="🎉 新しいリリース",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        
        embed.add_field(name="🔖 バージョン", value=f"**{version}**", inline=True)
        embed.add_field(name="📦 リポジトリ", value=repo_name, inline=True)
        embed.add_field(name="👤 作成者", value=author_name, inline=True)
        
        if title:
            embed.add_field(name="📝 タイトル", value=title, inline=False)
        
        embed.add_field(name="【変更内容】", value=body, inline=False)
        
        if published_at:
            embed.add_field(name="📅 公開日時", value=published_at, inline=True)
        
        if release_url:
            embed.add_field(name="🌐 リリースURL", value=f"[GitHub]{release_url}", inline=False)
        
        embed.set_footer(text="GitHub Webhook | GitHub Release")
        return embed
    except Exception as exc:
        logger.error("❌ リリースEmbed作成失敗: %s", exc)
        return None


async def handle_github_webhook(
    bot_instance,
    alert_channel,
    event_type: str,
    payload: Dict[str, Any]
) -> bool:
    """
    Handle GitHub webhook events and send Discord notifications
    
    Args:
        bot_instance: Discord bot instance
        alert_channel: Discord channel to send notifications to
        event_type: GitHub event type (push, release, etc.)
        payload: GitHub webhook payload
    
    Returns:
        True if handled successfully, False otherwise
    """
    try:
        if event_type == "push":
            return await _handle_push_event(bot_instance, alert_channel, payload)
        elif event_type == "release":
            return await _handle_release_event(bot_instance, alert_channel, payload)
        else:
            logger.debug("⏭️  サポートされていないイベント: %s", event_type)
            return True
    except Exception as exc:
        logger.error("❌ Webhookイベント処理中にエラー (type=%s): %s", event_type, exc)
        return False


async def _handle_push_event(bot_instance, alert_channel, payload: Dict[str, Any]) -> bool:
    """Handle GitHub push events"""
    try:
        ref = payload.get("ref", "")
        # Only process main branch
        if not ref.endswith("/main"):
            logger.debug("⏭️  main以外のブランチをスキップ: %s", ref)
            return True
        
        repository = payload.get("repository", {})
        repo_name = repository.get("full_name", "unknown")
        commits = payload.get("commits", [])
        
        if not commits:
            logger.info("⏭️  コミットなし")
            return True
        
        logger.info("📝 %d個のコミットを処理中: %s", len(commits), repo_name)
        
        # Send notification for each commit (max 5 to avoid spam)
        for commit in commits[:5]:
            embed = build_commit_embed(commit, repo_name)
            if embed and alert_channel:
                try:
                    await alert_channel.send(embed=embed)
                    logger.info("✅ コミット通知送信: %s", commit.get("id", "unknown")[:7])
                except Exception as exc:
                    logger.error("❌ コミット通知送信失敗: %s", exc)
        
        if len(commits) > 5:
            logger.info("💬 5件以上のコミットがあるため、最初の5件のみ通知しました")
        
        return True
    except Exception as exc:
        logger.error("❌ Push イベント処理失敗: %s", exc)
        return False


async def _handle_release_event(bot_instance, alert_channel, payload: Dict[str, Any]) -> bool:
    """Handle GitHub release events"""
    try:
        action = payload.get("action", "")
        # Only process published releases
        if action != "published":
            logger.debug("⏭️  非公開リリース状態をスキップ: %s", action)
            return True
        
        repository = payload.get("repository", {})
        repo_name = repository.get("full_name", "unknown")
        release = payload.get("release", {})
        
        logger.info("🎉 新しいリリースを処理中: %s", release.get("tag_name", "unknown"))
        
        embed = build_release_embed(release, repo_name)
        if embed and alert_channel:
            try:
                await alert_channel.send(embed=embed)
                logger.info("✅ リリース通知送信: %s", release.get("tag_name", "unknown"))
            except Exception as exc:
                logger.error("❌ リリース通知送信失敗: %s", exc)
        
        return True
    except Exception as exc:
        logger.error("❌ Release イベント処理失敗: %s", exc)
        return False
