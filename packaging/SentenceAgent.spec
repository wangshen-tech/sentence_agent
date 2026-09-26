# PyInstaller spec for the macOS app bundle. Build with:  scripts/build_app.sh
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

datas = [(str(SRC / "sentence_agent" / "static"), "sentence_agent/static")]
datas += copy_metadata("keyring") + copy_metadata("anthropic") + copy_metadata("openai")

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + ["sentence_agent.agent.session", "sentence_agent.agent.transcript"]
)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SentenceAgent",
    console=False,
    argv_emulation=False,
    target_arch=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SentenceAgent")

app = BUNDLE(
    coll,
    name="地道句子本.app",
    icon=str(ROOT / "packaging" / "AppIcon.icns"),
    bundle_identifier="app.sentenceagent.desktop",
    info_plist={
        "CFBundleDisplayName": "地道句子本",
        "CFBundleName": "地道句子本",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        # The window talks to its own server on 127.0.0.1 over plain HTTP.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
