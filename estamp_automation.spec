# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
paddle_datas, paddle_binaries, paddle_hiddenimports = collect_all("paddle")
paddleocr_datas, paddleocr_binaries, paddleocr_hiddenimports = collect_all("paddleocr")
paddlex_datas, paddlex_binaries, paddlex_hiddenimports = collect_all("paddlex")
torch_datas, torch_binaries, torch_hiddenimports = collect_all("torch")
test_assets = [
    ("test-gemini-ocr.html", "."),
    ("test-images", "test-images"),
    ("assets/paddleocr", "assets/paddleocr"),
]

a = Analysis(
    ["src/app.py"],
    pathex=["src"],
    binaries=(
        playwright_binaries
        + paddle_binaries
        + paddleocr_binaries
        + paddlex_binaries
        + torch_binaries
    ),
    datas=playwright_datas + paddle_datas + paddleocr_datas + paddlex_datas + torch_datas + test_assets,
    hiddenimports=(
        playwright_hiddenimports
        + paddle_hiddenimports
        + paddleocr_hiddenimports
        + paddlex_hiddenimports
        + torch_hiddenimports
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["easyocr", "torchvision", "fastapi", "uvicorn"],
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
