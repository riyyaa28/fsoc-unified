# PyInstaller build for the standalone FSOC app.
#   pip install pyinstaller
#   pyinstaller fsoc_app.spec
# Output: dist/FSOC_Tracker/FSOC_Tracker(.exe)  - ship the whole folder.
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("uvicorn") + collect_submodules("websockets") + ["server.app", "server.__main__"]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    datas=[("web/dist", "web/dist"), ("beacon_yolo.pt", ".")],
    hiddenimports=hidden,
    # torch/ultralytics are optional (YOLO); excluding them keeps the app small.
    excludes=["torch", "torchvision", "ultralytics", "matplotlib", "PyQt5", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="FSOC_Tracker", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="FSOC_Tracker")
