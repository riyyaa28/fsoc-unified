"""Standalone launcher: starts the FSOC server and opens the app in the browser.

Used as the entry point for the PyInstaller build (see fsoc_app.spec) and can
also be run directly:  python launcher.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
import webbrowser


def free_port(preferred: int = 8011) -> int:
    for port in [preferred, *range(8012, 8040)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free local port between 8011 and 8039.")


def main() -> None:
    import uvicorn

    from server.app import app

    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):  # wait up to ~10 s for the server to answer
        try:
            urllib.request.urlopen(url + "api/health", timeout=0.5)
            break
        except OSError:
            time.sleep(0.1)
    print("=" * 64, flush=True)
    print(" FSOC Coarse Alignment - Virtual Camera Tracking System")
    print(f" Running at {url}")
    print(" The app opens in your browser. Close this window to exit.", flush=True)
    print("=" * 64, flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    main()
