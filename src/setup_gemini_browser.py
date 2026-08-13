"""Open the persistent Chrome profile so the user can sign in to Gemini.

Run this once before using the OCR API:
    .venv\\Scripts\\python.exe src\\setup_gemini_browser.py
"""

from __future__ import annotations

import asyncio

from gemini_ocr_server import GeminiOcrBrowser


async def main() -> None:
    browser = GeminiOcrBrowser()
    try:
        page = await browser.get_gemini_page()
        print("Chrome is open at Gemini. Sign in to the intended Google account in that window.")
        print("Complete any verification prompts, then press Enter here to keep the saved profile and exit.")
        await asyncio.to_thread(input)
        print("Login setup complete. You can now start the Gemini OCR API.")
    finally:
        # The persistent Chrome process must remain open so its profile is not interrupted.
        await browser.detach()


if __name__ == "__main__":
    asyncio.run(main())
