"""Start the app: a local API server on a background thread and a native macOS window in front of it."""

from __future__ import annotations

import argparse
import logging
import secrets
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from . import DISPLAY_NAME, config
from .agent.session import AgentService
from .server import create_app
from .store import Store


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _self_check() -> int:
    import json

    import anthropic
    import keyring

    from .server import STATIC_DIR

    report = {
        "anthropic": anthropic.__version__,
        "keyring_backend": type(keyring.get_keyring()).__name__,
        "static_index": (STATIC_DIR / "index.html").is_file(),
        "data_dir": str(config.data_dir()),
    }
    print(json.dumps(report, ensure_ascii=False))
    ok = report["static_index"] and "fail" not in report["keyring_backend"].lower()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentence-agent", description=DISPLAY_NAME)
    parser.add_argument("--server-only", action="store_true", help="只启动本地服务，不打开窗口（开发用）")
    parser.add_argument("--browser", action="store_true", help="用默认浏览器打开，而不是独立窗口")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default="", help="固定访问口令（开发用）")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--self-check", action="store_true", help="检查运行环境后退出")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)

    if args.self_check:
        return _self_check()

    store = Store(config.db_path())
    agent = AgentService(store)
    token = args.token or secrets.token_urlsafe(24)
    port = args.port or _free_port()
    app = create_app(store, agent, token)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info" if args.debug else "warning")
    )
    url = f"http://127.0.0.1:{port}/#t={token}"

    if args.server_only:
        print(f"{DISPLAY_NAME} 服务已启动：{url}", flush=True)
        server.run()
        return 0

    thread = threading.Thread(target=server.run, name="api-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            print("本地服务没能启动", file=sys.stderr)
            return 1
        time.sleep(0.05)

    if args.browser:
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            pass
        return 0

    import webview

    webview.create_window(
        DISPLAY_NAME,
        url,
        width=1200,
        height=820,
        min_size=(780, 560),
        text_select=True,
    )
    webview.start(debug=args.debug, private_mode=False)

    server.should_exit = True
    thread.join(timeout=5)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
