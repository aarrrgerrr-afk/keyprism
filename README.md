# 🔮 KeyPrism

A dark, synthwave desktop app that turns MIDI files into Roblox piano keystrokes — with a real falling-notes visualizer, a sidebar music hub, and an optional cloud backend for syncing your library and stats.

The brand mark is a violet↔cyan crystal prism, and the whole UI is built around that duotone.

---

## ✨ Features

- **Load any MIDI** (`.mid` / `.midi`) from a file picker or the sidebar library
- **Hub sidebar** — a permanent music-app-style rail:
  - **LIBRARY** with live search, one-click PLAY, note count + duration
  - **RECENT** songs (last 6), persisted across launches
  - **SONG / TRACKS / SETTINGS** cards
- **Synthesia-style falling notes** — velocity-colored (cyan = loud, violet = soft), land on the key exactly when it's pressed, freeze on pause
- **61-key & 88-key** Roblox piano layouts with octave wrapping
- **Speed** (0.25x–2.0x), **transpose** (−12…+12), **AUTO transpose** (centers the song on the keyboard)
- **Loop**, **track selector**, **focus delay** with a big countdown numeral
- **Status machine** — IDLE → READY → COUNTDOWN → PLAYING → PAUSED → DONE, with a pulsing status dot
- **Keyboard**: `Space` = Play/Pause, `F6` = Play/Pause, `F7` = Stop
- **Cloud sync** (optional, see below) — stats + MIDI library backup
- **Reduced-motion aware** — all animation honors the OS "Show animations" setting

---

## 🚀 Run from source

```bash
pip install -r requirements.txt   # mido, pynput, pyautogui (customtkinter optional)
python app.py
```

The `run.py` launcher auto-installs missing deps. The app falls back to a themed classic-tkinter UI if `customtkinter` isn't installed.

Drop your own MIDIs into `midi_files/` — the library picks them up on startup.

---

## ☁️ Cloud (Railway) — sync library + stats

KeyPrism's cloud is a **zero-dependency** stdlib Python server. Host it on [Railway](https://railway.app) in ~2 minutes:

### 1. Deploy this repo to Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select `aarrrgerrr-afk/keyprism` (this repo)
3. Railway reads `railway.toml` automatically — it runs `python server/main.py` and health-checks `/api/health`. No build settings needed.

> Alternatively, with the [Railway CLI](https://docs.railway.app/cli): `railway init` → `railway up` from this folder.

### 2. Copy your service URL

Once deployed, copy the generated URL — it looks like:

```
https://keyprism-production.up.railway.app
```

### 3. Connect the app

In KeyPrism, open the **CLOUD** card in the sidebar, paste the URL, and hit **CONNECT**. The status dot turns **ONLINE**, and you can:

- **SYNC** — merge lifetime stats (`songs_played`, `notes_played`) with the server
- **SYNC LIBRARY** — two-way MIDI backup (uploads your local songs, downloads any remote ones)
- Stats also **auto-push** to the cloud every time a song finishes

### Server endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | health check |
| GET / POST | `/api/stats` | read / merge stats (max-wins) |
| GET / POST | `/api/library` | list / upsert library metadata |
| POST | `/api/songs` | upload a MIDI (multipart) |
| GET | `/api/songs/{id}` | download a MIDI |

Data is stored in SQLite (`server/keyprism.db`, created automatically). The server binds `0.0.0.0:$PORT`, which is exactly what Railway provides.

### Run the server locally (for testing)

```bash
python server/main.py          # listens on http://localhost:8000
```

Then point the app's CLOUD box at `http://localhost:8000`.

---

## 🛠 Project structure

```
├── app.py                 # KeyPrism GUI (hub sidebar, visualizer, transport)
├── engine.py              # MIDI parser + threaded player
├── mapping.py             # note → Roblox key mapping + shift handling
├── cloud.py               # stdlib-only API client for the cloud backend
├── cli_player.py          # headless CLI player
├── run.py                 # launcher with dependency auto-install
├── server/main.py         # zero-dependency cloud backend (Railway-ready)
├── railway.toml           # Railway deploy config
├── requirements.txt       # Python deps (cloud is stdlib-only)
└── midi_files/            # demo pack + your own MIDIs
```

---

## 📦 Build to .EXE (Windows)

```bash
pyinstaller --onefile --noconsole --name KeyPrism --icon=app.ico app.py \
  --hidden-import mido --hidden-import pynput --collect-all customtkinter
```

(`BUILD_WINDOWS_EXE.bat` runs the exact command.) The exe lands in `dist/KeyPrism.exe` with the crystal icon and bundled logo.

---

## ⚠️ Note

For **educational and entertainment purposes only**. Automating input may be against some Roblox game rules — use responsibly.
