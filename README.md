# Gemini OCR local API

This Python/FastAPI service sends an image to Gemini with the prompt `OCR this` and returns Gemini's completed response. The source files are:

- `src/gemini_ocr_server.py` - the local OCR API
- `src/setup_gemini_browser.py` - opens the persistent Chrome profile for interactive Google/Gemini login

By default both scripts share `D:\Projects\agent-orchestrator\.playwright-chrome-profile`, the `Default` Chrome profile, and the local Chrome installation. Set `AGENT_ORCHESTRATOR_ROOT`, `CHROME_USER_DATA_DIR`, `CHROME_PROFILE_DIRECTORY`, or `CHROME_EXECUTABLE_PATH` to override those defaults.

## Setup and login

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\src\setup_gemini_browser.py
```

Chrome opens visibly at Gemini. Sign in to the intended Google account, complete any verification, and press Enter in the terminal. The script leaves Chrome and its persistent login profile available for the OCR service.

## Run the API

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.gemini_ocr_server:app --host 127.0.0.1 --port 4318
```

The API listens only on `http://127.0.0.1:4318` by default. Change the port with `PORT`, or pass a different Uvicorn port.

## Request

```powershell
curl.exe -X POST http://127.0.0.1:4318/gemini/ocr `
  -F "image=@D:\Projects\compitcom\automation\screenshot.png"
```

The request must be `multipart/form-data` with one `image` file field (maximum 25 MB). A successful response is:

```json
{ "text": "Recognized text...", "url": "https://gemini.google.com/app/..." }
```
