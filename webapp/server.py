from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, render_template


CHINA_TZ = ZoneInfo("Asia/Shanghai")
UPDATED_AT = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")


def build_app() -> Flask:
    app = Flask(__name__, template_folder="templates")

    @app.get("/")
    def index():
        return redirect("/push", code=302)

    @app.get("/push")
    def push():
        return render_template("push.html")

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "updated_at": UPDATED_AT})

    @app.get("/api/public/overview")
    def public_overview():
        return jsonify(
            {
                "ok": True,
                "updated_at": UPDATED_AT,
                "summary": {
                    "source_count": 3,
                    "modules_count": 4,
                    "status": "public-demo",
                },
                "sources": [
                    {
                        "name": "Source A",
                        "role": "Primary market feed",
                        "status": "demo-connected",
                    },
                    {
                        "name": "Source B",
                        "role": "Validation feed",
                        "status": "demo-connected",
                    },
                    {
                        "name": "Source C",
                        "role": "Backup feed",
                        "status": "demo-standby",
                    },
                ],
                "modules": [
                    {
                        "title": "Multi-source input",
                        "detail": "Public demo for unified market-data ingestion.",
                    },
                    {
                        "title": "Dashboard",
                        "detail": "Web monitoring page with generic runtime cards.",
                    },
                    {
                        "title": "Stock paper trading",
                        "detail": "Execution bridge shown as a concept only.",
                    },
                    {
                        "title": "Futures paper trading",
                        "detail": "Execution workflow shown as a concept only.",
                    },
                ],
                "notes": [
                    "Production strategies have been removed from this public snapshot.",
                    "Private APIs, runtime files, and server-specific integrations are not included.",
                    "The page is kept as a demo shell for architecture sharing and UI preview.",
                ],
            }
        )

    return app


app = build_app()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Public demo website server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
