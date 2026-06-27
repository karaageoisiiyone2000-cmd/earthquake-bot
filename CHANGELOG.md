# Changelog

All notable changes to the Earthquake Alert Bot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v1.2.0] - 2026-06-27

### Added
- ✅ `/version` コマンドを追加
  - ボットのバージョン情報を表示
  - Python バージョン、discord.py バージョン表示
  - 稼働時間、監視間隔を表示
- ✅ バージョンシステムの実装
  - `BOT_VERSION` 定数管理
  - `BUILD_DATE` ビルド日付追跡
- ✅ 自動アップデートアナウンス機能
  - 新しいバージョンで起動時に自動通知
  - `bot_version.json` で最後のアナウンス版を追跡
  - 同じバージョンでは重複して通知しない
- ✅ CHANGELOG.md を追加
  - 全バージョンの変更履歴を記録

### Improved
- ✅ `/help` コマンドに `/version` を追加
- ✅ ボット起動時にバージョン情報をログ出力
- ✅ スタートアップメッセージに `/version` コマンドを追加
- ✅ アップデートアナウンスは日本語の詳細情報を表示

### Technical
- 追加: `sys` モジュールで Python バージョン取得
- 追加: `json` モジュールでバージョン追跡データ管理
- 追加: `build_update_announcement_embed()` 関数
- 追加: `load_last_announced_version()` 関数
- 追加: `save_announced_version()` 関数

### Documentation
- CHANGELOG.md ファイルを作成・初期化

---

## [v1.1.0] - 2026-06-27

### Added
- ✅ 新しい通知ルール実装
  - 震度3以上のすべての地震 → @地震速報 ロール
  - 津波注意報・津波警報・大津波警報のみ → @everyone
  - 震度による @everyone メンションを廃止

### Improved
- ✅ `/help` コマンドを大幅改善
  - コマンド説明を詳細化
  - 通知ルールを明確に記載
  - カテゴリ分類を追加
- ✅ `/status` コマンドに通知ルールを表示
- ✅ ログ出力を強化
  - メンション判定理由をログに記録
  - エラーの詳細なスタックトレース表示
  - 連続API失敗を追跡
- ✅ macOS 24/7 安定性向上
  - 非同期タイムアウト処理改善
  - 例外処理の堅牢化
  - セッション管理の適切化
- ✅ verbose ログを抑制
  - discord.py ログを WARNING レベルに設定
  - aiohttp ログを抑制

### Technical
- 変更: `should_mention_everyone()` 関数を津波のみに変更
- 変更: `should_mention_role()` 関数に津波条件を追加
- 追加: API 失敗カウンター機構
- 追加: `API_TIMEOUT_SECONDS`, `API_CONNECT_TIMEOUT_SECONDS` 定数

---

## [v1.0.0] - 2026-06-24

### Initial Release

#### Features
- ✅ 地震情報のリアルタイム監視
  - P2P地震情報 API を使用
  - 10秒間隔でポーリング
  - イベントの自動重複排除
- ✅ Discord 通知機能
  - 震度に応じた自動メンション
  - 津波情報の即時通知
  - カラフルな Embed 形式での表示
- ✅ スラッシュコマンド実装
  - `/ping` - ボットの応答速度確認
  - `/status` - ボット稼働状況表示
  - `/test` - テスト地震アラート（マグニチュード指定可能）
  - `/history` - 最近の地震履歴表示（最大20件）
  - `/help` - コマンド一覧と通知ルール表示
- ✅ 24/7 安定運用
  - Heroku、Railway、Replit デプロイ対応
  - Docker コンテナ対応
  - HTTP ヘルスチェック機能
- ✅ 多言語対応
  - すべてのメッセージが日本語
  - 震度・津���情報の日本語ラベル
- ✅ 詳細なロギング
  - すべての操作を記録
  - エラー時の詳細情報出力

#### Technical Details
- 言語: Python 3.11+
- フレームワーク: discord.py 2.3.2+
- 非同期処理: asyncio, aiohttp
- API: P2P地震情報 REST API

#### Configuration
- 環境変数: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`
- オプション環境変数: `PORT` (デフォルト: 8080)

---

## Release Notes

### How to Update
1. Pull the latest code: `git pull`
2. Restart the bot process
3. The bot will automatically announce the new version on first startup

### Reporting Issues
Please report any bugs or feature requests through GitHub Issues.

### Acknowledgments
- Data Source: P2P地震情報 API、気象庁（JMA）
- Built with: discord.py, aiohttp, Python

---

**Last Updated**: 2026-06-27
