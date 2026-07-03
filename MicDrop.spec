# MicDrop.spec — build a standalone Windows app (one-folder).
#
#   pyinstaller MicDrop.spec
#
# Produces dist/MicDrop/MicDrop.exe  (zip the whole dist/MicDrop folder to share).
# The native audio libraries (PortAudio via sounddevice, libsndfile via soundfile)
# are collected explicitly so a Python-free machine has everything it needs.

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
