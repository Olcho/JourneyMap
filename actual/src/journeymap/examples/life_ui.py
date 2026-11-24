"""Local-only live dashboard. Run with python -m journeymap.examples.life_ui."""

import argparse
import json
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import monotonic
from typing import cast
from urllib.parse import parse_qs, urlsplit

from journeymap.adapters.life_llm import LifeLLMConfig
from journeymap.experiments.checkpoint import load_checkpoint
from journeymap.experiments.life import LifeSession
from journeymap.experiments.live import LiveLife
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import LifeConfig


def create_server(live: LiveLife, port: int = 8765) -> HTTPServer:
    """Local researcher tool, not a multi-user authorization server."""
    token = secrets.token_hex(24)
    page = Path(__file__).with_name("life_ui.html").read_text(encoding="utf-8")
    page = page.replace("__CONTROL_TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:",
            )
            self.end_headers()
            self.wfile.write(body)

        def local_host(self) -> bool:
            port = cast(HTTPServer, self.server).server_port
            return self.headers.get("Host") == f"127.0.0.1:{port}"

        def do_GET(self) -> None:
            if not self.local_host():
                self.reply(403, b"Local host required", "text/plain")
                return
            url = urlsplit(self.path)
            if url.path == "/":
                self.reply(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/state":
                try:
                    actor = parse_qs(url.query).get("actor", [None])[0]
                    body = json.dumps(live.view(actor), ensure_ascii=False).encode("utf-8")
                    self.reply(200, body, "application/json; charset=utf-8")
                except ValueError:
                    self.reply(400, b"Invalid actor", "text/plain")
            else:
                self.reply(404, b"Not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local_host() or self.headers.get("X-Control-Token") != token:
                self.reply(403, b"Local control token required", "text/plain")
                return
            if self.path != "/control":
                self.reply(404, b"Not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("invalid command size")
                command = json.loads(self.rfile.read(length))
                if type(command) is not dict:
                    raise ValueError("expected command object")
                live.command(command, monotonic())
                self.reply(200, b'{"ok":true}', "application/json")
            except (ValueError, OSError):
                live.running = False
                self.reply(400, b'{"error":"Command failed; session paused"}', "application/json")

    return HTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("artifacts/phase4/live-checkpoint.json")
    )
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--closure", action="store_true")
    parser.add_argument("--controller", choices=("rule", "offline"), default="rule")
    parser.add_argument(
        "--hold-responses",
        action="store_true",
        help="Manual offline response gate for UI verification",
    )
    args = parser.parse_args()
    if args.resume:
        session = load_checkpoint(args.resume)
    elif args.controller == "offline":
        session = MixedLifeSession(
            LifeConfig(horizon=24, closure_tick=2 if args.closure else None),
            LifeLLMConfig(
                hold_responses=args.hold_responses,
                timeout_seconds=300.0 if args.hold_responses else 5.0,
            ),
        )
    else:
        session = LifeSession(LifeConfig(closure_tick=2 if args.closure else None))
    live = LiveLife(session, args.checkpoint)
    server = create_server(live, args.port)
    server.timeout = 0.05
    print(f"JourneyMap LIVE_SESSION http://127.0.0.1:{server.server_port}", flush=True)
    try:
        while True:
            server.handle_request()
            live.pump(monotonic())
    except KeyboardInterrupt:
        pass
    finally:
        live.session.close()
        server.server_close()


if __name__ == "__main__":
    main()
