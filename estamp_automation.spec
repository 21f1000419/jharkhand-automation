# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
scipy_array_api_hiddenimports = collect_submodules("scipy._external.array_api_compat.numpy")
test_assets = [
    ("test-gemini-ocr.html", "."),
    ("test-images", "test-images"),
]

a = Analysis(
    ["src/app.py"],
    pathex=["src"],
    binaries=playwright_binaries,
    datas=playwright_datas + test_assets,
    hiddenimports=playwright_hiddenimports + scipy_array_api_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["fastapi", "uvicorn"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Compitcom-eStamp-Automation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
