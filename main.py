import os
import sys
import re
import json
import threading
# PyInstaller collects another copy of these DLLs in Qt's bin folder. Load
# the application-root copies first so the loader cannot pick Qt's copy when
# resolving Torch's native dependencies.
_vc_runtime_handles = []
if getattr(sys, "frozen", False):
    import ctypes

    _bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    for _runtime_name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        _runtime_path = os.path.join(_bundle_root, _runtime_name)
        if os.path.isfile(_runtime_path):
            _vc_runtime_handles.append(ctypes.WinDLL(_runtime_path))

import torch  # noqa: F401  (load torch DLLs before Qt; the reverse order fails on Windows)
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import unquote, urlsplit


from PyQt5.QtCore import QUrl, QTimer
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget
from PyQt5.QtWebEngineWidgets import (
    QWebEngineDownloadItem, QWebEnginePage, QWebEngineSettings, QWebEngineView
)

# Import must happen before we build the tabs, same ordering FSOC_FINAL relied on.
from ui.dashboard import Dashboard  # noqa: E402  (2D BORE-SIGHT, unmodified)

APP_TITLE = "FSOC Coarse Alignment Control Center"
# PyInstaller extracts bundled web assets under _MEIPASS. In source mode,
# keep resolving them beside main.py.
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
WEB3D_INDEX = os.path.join(BASE_DIR, "ui", "web3d", "index.html")


class WebHandler(SimpleHTTPRequestHandler):
    _qt_compatible_three_core = None

    def do_GET(self):
        # QtWebEngine bundled with PyQt5 is based on an older Chromium that
        # cannot parse ES2022 class static blocks. Three.js uses these blocks
        # only to set type flags on prototypes, so express those initializers
        # as equivalent public static fields for this served copy.
        if urlsplit(self.path).path == "/node_modules/three/build/three.core.js":
            if WebHandler._qt_compatible_three_core is None:
                source_path = os.path.join(
                    BASE_DIR, "node_modules", "three", "build", "three.core.js"
                )
                with open(source_path, "r", encoding="utf-8") as source_file:
                    source = source_file.read()
                pattern = re.compile(
                    r"\tstatic \{\s*(?:/\*\*.*?\*/\s*)?"
                    r"([A-Za-z_$][\w$]*)\.prototype\.([A-Za-z_$][\w$]*)"
                    r"\s*=\s*true;\s*\}",
                    re.DOTALL,
                )
                source, _ = pattern.subn(
                    lambda match: (
                        f"\tstatic __qtCompat_{match.group(2)} = "
                        f"{match.group(1)}.prototype.{match.group(2)} = true;"
                    ),
                    source,
                )
                WebHandler._qt_compatible_three_core = source.encode("utf-8")

            body = WebHandler._qt_compatible_three_core
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        super().do_GET()

    def translate_path(self, path):
        # Serve the page and its local Three.js module from narrowly scoped
        # paths so the relative import works in QtWebEngine without import maps.
        request_path = unquote(urlsplit(path).path)
        if request_path.startswith("/node_modules/"):
            root = os.path.join(BASE_DIR, "node_modules")
            relative_path = request_path[len("/node_modules/"):]
        elif request_path.startswith("/ui/web3d/"):
            root = os.path.join(BASE_DIR, "ui", "web3d")
            relative_path = request_path[len("/ui/web3d/"):]
        else:
            return os.path.join(BASE_DIR, "__not_found__")

        resolved_root = os.path.realpath(root)
        resolved_path = os.path.realpath(os.path.join(resolved_root, relative_path))
        if os.path.commonpath([resolved_root, resolved_path]) == resolved_root:
            return resolved_path
        return os.path.join(BASE_DIR, "__not_found__")

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".html": "text/html",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setStyleSheet("""
            QMainWindow, QTabWidget, QTabWidget::pane, QStackedWidget,
            QWidget#qt_tabwidget_stackedwidget {
                background-color: #071426;
                color: #c7e5ef;
            }
        """)

        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(
                max(1200, available.width() - 40), max(800, available.height() - 40)
            )
        else:
            self.resize(1400, 900)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                background: #050b12;
                border: 1px solid #24566a;
                border-top: 2px solid #18d8f2;
                margin-top: -1px;
            }
            QTabBar::tab {
                background: #09131f;
                color: #789bb0;
                border: 1px solid #1c3546;
                border-bottom: 2px solid #153344;
                padding: 10px 22px;
                min-width: 150px;
                margin-right: 4px;
                font-family: Consolas, monospace;
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 1px;
            }
            QTabBar::tab:selected {
                color: #68f4ff;
                background: #102535;
                border-color: #18a9c4;
                border-bottom: 2px solid #55f5ff;
            }
            QTabBar::tab:hover:!selected {
                color: #c3faff;
                background: #0d1c29;
            }
        """)
        self.setCentralWidget(self.tabs)

        # --- Tab 1: 2D BORE-SIGHT (scenario engine + video benchmark) ---
        self.dashboard = Dashboard()
        self.tabs.addTab(self.dashboard, "2D BORE-SIGHT")

        # --- Tab 2: 3D AIRSPACE (Three.js scene) ---
        self.airspace_view = QWebEngineView()
        self.airspace_view.settings().setAttribute(
            QWebEngineSettings.WebGLEnabled, True
        )

        # QWebEngineView drops downloads unless something accepts them; save
        # reports straight to the user's Downloads folder.
        self._active_downloads = []
        self.airspace_view.page().profile().downloadRequested.connect(
            self._on_download_requested
        )

        handler = partial(WebHandler, directory=BASE_DIR)

        self.web_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)

        self.web_server_thread = threading.Thread(
            target=self.web_server.serve_forever, daemon=True
        )

        self.web_server_thread.start()

        port = self.web_server.server_address[1]

        self._loaded_web3d_signature = self._web3d_asset_signature()
        self.airspace_view.setUrl(QUrl(f"http://127.0.0.1:{port}/ui/web3d/index.html"))
        self.airspace_view.loadFinished.connect(self._on_airspace_loaded)

        # Added directly: embedding it via a native window container drew the
        # 3D page over the tab bar, hiding the 2D/3D switch.
        self.airspace_tab = self.airspace_view
        self.tabs.addTab(self.airspace_tab, "3D AIRSPACE")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.tabs.setCurrentIndex(1)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self._refresh_airspace_view)

    def _on_tab_changed(self, index):
        # While the 2D tab is shown, freeze the 3D page (its animation and
        # simulation loop otherwise compete with the 2D tracker for the CPU).
        # It resumes where it left off when its tab is shown again.
        page = self.airspace_view.page()
        if self.tabs.widget(index) is not self.airspace_tab:
            page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)
            return
        page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        current_signature = self._web3d_asset_signature()
        if current_signature != self._loaded_web3d_signature:
            self.airspace_view.reload()
        else:
            QTimer.singleShot(100, self._refresh_airspace_view)

    @staticmethod
    def _web3d_asset_signature():
        assets = (WEB3D_INDEX, os.path.join(BASE_DIR, "ui", "web3d", "app.js"),
                  os.path.join(BASE_DIR, "ui", "web3d", "style.css"))
        try:
            return tuple(os.stat(path).st_mtime_ns for path in assets)
        except OSError:
            return None

    def _on_airspace_loaded(self, success):
        self._loaded_web3d_signature = self._web3d_asset_signature()
        if success:
            self._refresh_airspace_view()

    def _on_download_requested(self, item):
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        suggested = os.path.basename(item.path()) or "fsoc-technical-report.pdf"
        stem, ext = os.path.splitext(suggested)
        target = os.path.join(downloads_dir, suggested)
        counter = 1
        while os.path.exists(target):
            target = os.path.join(downloads_dir, f"{stem} ({counter}){ext}")
            counter += 1
        item.setPath(target)
        self._active_downloads.append(item)
        item.finished.connect(lambda: self._on_download_finished(item, target))
        item.accept()

    def _on_download_finished(self, item, target):
        if item in self._active_downloads:
            self._active_downloads.remove(item)
        ok = item.state() == QWebEngineDownloadItem.DownloadCompleted
        self.airspace_view.page().runJavaScript(
            "window.fsocReportDownloaded && "
            f"window.fsocReportDownloaded({json.dumps(target)}, {json.dumps(ok)})"
        )
        if ok:
            # Open the report that was just written, so an older file in
            # Downloads is never mistaken for it.
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _refresh_airspace_view(self):
        if self.tabs.currentWidget() is self.airspace_tab:
            self.airspace_view.page().runJavaScript(
                "window.fsocRefreshViewport && window.fsocRefreshViewport()"
            )

    def closeEvent(self, event):
        # The dashboard is a child widget, so its own closeEvent() never fires:
        # stop its video decoder thread before the interpreter shuts down.
        media = getattr(self.dashboard, "media_capture", None)
        if media is not None:
            self.dashboard.timer.stop()
            self.dashboard.running = False
            media.release()
            self.dashboard.media_capture = None
        logger = getattr(self.dashboard, "logger", None)
        if logger is not None:
            try:
                logger.close()
            except Exception:
                pass
        if hasattr(self, "web_server"):
            self.web_server.shutdown()
            self.web_server.server_close()
        event.accept()


def _self_test(win, png_path):
    """Smoke test for packaged builds (FSOC_SELFTEST=<png path>): run a short
    scenario in the 2D tab, save a screenshot of the window and exit."""
    def start():
        win.tabs.setCurrentIndex(0)
        win.dashboard._start_simulation()
        QTimer.singleShot(6000, finish)

    def finish():
        screen = QApplication.primaryScreen()
        screen.grabWindow(int(win.winId())).save(png_path)
        win.close()

    QTimer.singleShot(8000, start)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    if os.environ.get("FSOC_SELFTEST"):
        _self_test(win, os.environ["FSOC_SELFTEST"])
    sys.exit(app.exec_())
