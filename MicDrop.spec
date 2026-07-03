# Build a standalone one-folder app:  pyinstaller MicDrop.spec  ->  dist/MicDrop/MicDrop.exe
# PortAudio (sounddevice) and libsndfile (soundfile) are collected explicitly.

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

binaries = collect_dynamic_libs("sounddevice") + collect_dynamic_libs("soundfile")
datas = collect_data_files("soundfile") + [("assets/icon.ico", "assets")]

a = Analysis(
    ["run_micdrop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["sounddevice", "soundfile", "soxr", "numpy", "keyboard"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "unittest", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MicDrop",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # windowed app, no console (matches run.bat / pythonw)
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MicDrop",
)
