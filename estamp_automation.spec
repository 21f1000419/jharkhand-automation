# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
paddle_datas, paddle_binaries, paddle_hiddenimports = collect_all("paddle")
paddleocr_datas, paddleocr_binaries, paddleocr_hiddenimports = collect_all("paddleocr")
paddlex_datas, paddlex_binaries, paddlex_hiddenimports = collect_all("paddlex")
torch_datas, torch_binaries, torch_hiddenimports = collect_all("torch")
torchvision_datas, torchvision_binaries, torchvision_hiddenimports = collect_all("torchvision")
easyocr_datas, easyocr_binaries, easyocr_hiddenimports = collect_all("easyocr")
backports_datas, backports_binaries, backports_hiddenimports = collect_all("backports")
# PaddleX checks this dependency through importlib.metadata at runtime.  The
# module is collected via EasyOCR, but its distribution metadata is not.
python_bidi_metadata = copy_metadata("python-bidi")
# PaddleX tests this optional OCR dependency using importlib.metadata rather
# than by importing ``cv2``.  The OpenCV module is bundled, but its metadata
# is not discovered automatically by PyInstaller.
opencv_contrib_metadata = copy_metadata("opencv-contrib-python")
# PaddleOCR installs PaddleX's ``ocr-core`` extra. PaddleX discovers these
# packages through ``importlib.metadata`` while registering OCR readers, so
# bundle both the packages and their metadata rather than relying on static
# import discovery.
imagesize_datas, imagesize_binaries, imagesize_hiddenimports = collect_all("imagesize")
pyclipper_datas, pyclipper_binaries, pyclipper_hiddenimports = collect_all("pyclipper")
pypdfium2_datas, pypdfium2_binaries, pypdfium2_hiddenimports = collect_all("pypdfium2")
shapely_datas, shapely_binaries, shapely_hiddenimports = collect_all("shapely")
paddlex_ocr_core_metadata = (
    copy_metadata("imagesize")
    + copy_metadata("pyclipper")
    + copy_metadata("pypdfium2")
    + copy_metadata("shapely")
)
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
        + imagesize_binaries
        + pyclipper_binaries
        + pypdfium2_binaries
        + shapely_binaries
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
        + python_bidi_metadata
        + opencv_contrib_metadata
        + imagesize_datas
        + pyclipper_datas
        + pypdfium2_datas
        + shapely_datas
        + paddlex_ocr_core_metadata
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
        + imagesize_hiddenimports
        + pyclipper_hiddenimports
        + pypdfium2_hiddenimports
        + shapely_hiddenimports
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
    name="jharni",
)
