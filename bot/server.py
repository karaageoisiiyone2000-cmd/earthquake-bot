"""
デプロイ用エントリーポイント。
Replitのヘルスチェック用HTTPサーバーをバックグラウンドで起動しつつ、
Discordボットをメインプロセスとして実行する。
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import earthquake_bot


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok","service":"earthquake-bot"}')

    def log_message(self, *args):
        pass  # アクセスログを抑制


def start_health_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


if __name__ == "__main__":
    # ヘルスチェックサーバーをデーモンスレッドで起動
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()

    # Discordボットをメインスレッドで実行（ブロッキング）
    earthquake_bot.main()
