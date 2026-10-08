# PyInstaller build for the standalone FSOC app.
#   pip install pyinstaller pywebview
#   pyinstaller fsoc_app.spec
# Output: dist/FSOC_Tracker(.exe)  - a single self-contained file.
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    datas=[("web/dist", "web/dist"), ("beacon_yolo.pt", ".")],
    hiddenimports=[
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'engineio.async_drivers.asgi',
        'server.app',
        'server.__main__',
    ] + collect_submodules("websockets"),
    # torch/ultralytics are optional (YOLO); excluding them keeps the app small.
    excludes=["torch", "torchvision", "ultralytics", "matplotlib", "PyQt5", "tkinter"],
)
pyz = PYZ(a.pure)
# console=False: the app opens in its own window (pywebview); server output goes to FSOC_data/logs/app.log.
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FSOC_Tracker", console=False, icon="fsoc.ico")
