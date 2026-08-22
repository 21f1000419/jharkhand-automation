# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
paddle_datas, paddle_binaries, paddle_hiddenimports = collect_all("paddle")
paddleocr_datas, paddleocr_binaries, paddleocr_hiddenimports = collect_all("paddleocr")
paddlex_datas, paddlex_binaries, paddlex_hiddenimports = collect_all("paddlex")
torch_datas, torch_binaries, torch_hiddenimports = collect_all("torch")
torchvision_datas, torchvision_binaries, torchvision_hiddenimports = collect_all("torchvision")
easyocr_datas, easyocr_binaries, easyocr_hiddenimports = collect_all("easyocr")
backports_datas, backports_binaries, backports_hiddenimports = collect_all("backports")
scipy_array_api_hiddenimports = collect_submodules("scipy._external.array_api_compat.numpy")
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
        + torchvision_binaries
        + easyocr_binaries
        + backports_binaries
    ),
    datas=(
        playwright_datas
        + paddle_datas
        + paddleocr_datas
        + paddlex_datas
        + torch_datas
        + torchvision_datas
        + easyocr_datas
        + backports_datas
        + test_assets
    ),
    hiddenimports=(
        playwright_hiddenimports
        + paddle_hiddenimports
        + paddleocr_hiddenimports
        + paddlex_hiddenimports
        + torch_hiddenimports
        + torchvision_hiddenimports
        + easyocr_hiddenimports
        + backports_hiddenimports
        + scipy_array_api_hiddenimports
    ),
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
    [],
    exclude_binaries=True,
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Compitcom-eStamp-Automation",
)
