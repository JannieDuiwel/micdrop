# Build a standalone single-file app:  pyinstaller MicDrop.spec  ->  dist/MicDrop.exe
# Everything (Python, PortAudio via sounddevice, libsndfile via soundfile, the icon)
# is packed into one .exe. PortAudio/libsndfile binaries are collected explicitly.

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
    a.binaries,
    a.datas,
    [],
    name="MicDrop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # windowed app, no console (matches run.bat / pythonw)
    icon="assets/icon.ico",
)
