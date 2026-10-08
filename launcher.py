"""Standalone launcher: starts the FSOC server and shows the app in its own window.

Used as the entry point for the PyInstaller build (see fsoc_app.spec) and can
also be run directly:  python launcher.py
Falls back to the default browser if a native window cannot be opened, or when
started with --browser.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

TITLE = "FSOC Coarse Alignment - Virtual Camera Tracking System"


def free_port(preferred: int = 8011) -> int:
    for port in [preferred, *range(8012, 8040)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free local port between 8011 and 8039.")


def redirect_output() -> None:
    """A windowed build has no console (sys.stdout is None), which breaks uvicorn's logging."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    log_dir = base / "FSOC_data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def open_window(url: str) -> bool:
    try:
        import webview
    except ImportError:
        return False
    try:
        webview.create_window(TITLE, url, width=1440, height=900, min_size=(1024, 680), maximized=True)
        webview.start()
        return True
    except Exception as error:  # e.g. WebView2 runtime missing
        print(f"Native window unavailable ({error}); using the browser instead.", flush=True)
        return False


def main() -> None:
    redirect_output()
    import uvicorn

    from server.app import app

    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info" if os.getenv("FSOC_DEBUG") else "warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(150):  # wait up to ~15 s for the server to answer
        try:
            urllib.request.urlopen(url + "api/health", timeout=0.5)
            break
        except OSError:
            time.sleep(0.1)
    print(f"{TITLE}\nRunning at {url}", flush=True)

    if "--no-browser" in sys.argv:
        pass
    elif "--browser" not in sys.argv and open_window(url):
        server.should_exit = True  # the window was closed: shut down
        thread.join(5)
        os._exit(0)
    else:
        webbrowser.open(url)
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    main()
