# SyncBoard — Collaborative Real-Time Code Board

A beginner-friendly full-stack demo using Python, Flask, Flask-SocketIO, CodeMirror, HTML/CSS/JavaScript and SQLite.

## Features
- Shared workspaces (use the same workspace ID in two browser windows)
- Real-time code updates via Socket.IO/WebSockets where available
- Syntax-highlighted editor for Python, JavaScript, Java, HTML, CSS and SQL
- Review comments with Pending, Approved and Changes Requested statuses
- SQLite persistence for code and comments
- Dark responsive UI and code export

## Requirements
- Python 3.10+
- VS Code
- Internet connection for the CDN-hosted CodeMirror and Socket.IO client libraries

## Run locally (Windows PowerShell)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000 in your browser.

If PowerShell blocks activation, run this instead:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Test real-time collaboration
1. Open http://127.0.0.1:5000 in Chrome.
2. Join workspace `cse-team` with a display name.
3. Open a second browser window (or incognito window).
4. Join the same workspace ID with another name.
5. Edit code in one window; the other window should receive the update.
6. Add a review comment and change its status.

## Important limitations
- This demo broadcasts the complete editor contents after each edit. It demonstrates live collaboration but is not a conflict-free collaborative editor. Concurrent typing can overwrite changes.
- This demo has no authentication or authorization. Workspace IDs are not private. Do not use it for confidential code or deploy publicly as-is.
- For production, add authentication, workspace permissions, CSRF/input protections, rate limits, robust logging, and a conflict-free editing protocol such as Yjs.
- Code is edited and saved as text; this demo does not execute submitted code.
- CodeMirror and Socket.IO client are loaded from public CDNs.
