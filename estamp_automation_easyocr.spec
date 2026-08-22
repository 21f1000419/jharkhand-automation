# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
ddddocr_datas, ddddocr_binaries, ddddocr_hiddenimports = collect_all("ddddocr")
onnxruntime_datas = collect_data_files("onnxruntime", include_py_files=False)
onnxruntime_binaries = collect_dynamic_libs("onnxruntime")
easyocr_datas, easyocr_binaries, easyocr_hiddenimports = collect_all("easyocr")
scipy_array_api_hiddenimports = collect_submodules("scipy._external.array_api_compat.numpy")
test_assets = [
    ("test-gemini-ocr.html", "."),
    ("test-images", "test-images"),
]

a = Analysis(
    ["src/app.py"],
    pathex=["src"],
    binaries=playwright_binaries + ddddocr_binaries + onnxruntime_binaries + easyocr_binaries,
    datas=playwright_datas + ddddocr_datas + onnxruntime_datas + easyocr_datas + test_assets,
    hiddenimports=(
        playwright_hiddenimports
        + ddddocr_hiddenimports
        + easyocr_hiddenimports
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
    a.binaries,
    a.datas,
    [],
    name="Compitcom-eStamp-Automation-EasyOCR",
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
