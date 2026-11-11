# Setting up Blendbook

There are three ways to get Blendbook running. All of them need **rekordbox 6 or 7 with at least one set in its
History**, and **Chrome or Edge** for every feature (the controller view, Listen and the mini view need them).

| | Best for | Time |
|---|---|---|
| [Download the app](#1-download-the-app) | Most people | 2 minutes |
| [Run from source](#2-run-from-source) | Seeing what you run, or changing it | 5 minutes |
| [Let an AI set it up](#3-let-an-ai-set-it-up) | Running from source without typing the commands | 5 minutes |

## 1. Download the app

Python and everything else is built in.

1. Get the installer from the [latest release](https://github.com/ujjaidasani/blendbook/releases/latest):
   - **Mac, Apple Silicon** (M1 and later): `Blendbook-…-mac-arm64.dmg`
   - **Mac, Intel**: `Blendbook-…-mac-x86_64.dmg`
   - **Windows 10 or 11**: `Blendbook-…-windows-setup.exe`
2. Install it. On a Mac, open the `.dmg` and drag Blendbook to Applications. On Windows, run the installer; it needs
   no admin rights.
3. Confirm it once. Blendbook isn't signed with a paid Apple or Microsoft certificate yet, so the first open asks:
   - **Mac:** open Blendbook and dismiss the warning. Then go to **System Settings → Privacy & Security**, scroll
     down, and press **Open Anyway** next to Blendbook.
   - **Windows:** on the "Windows protected your PC" screen, press **More info**, then **Run anyway**.
4. Blendbook opens in your browser and stays running as a small icon in the menu bar (Mac) or system tray (Windows).
   Use that icon to reopen it or quit.

## 2. Run from source

You need **Python 3.12 or newer** and **git**.

**Mac**

```bash
git clone https://github.com/ujjaidasani/blendbook.git
cd blendbook
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

After the first time you can double-click `Start Blendbook.command` instead.

**Windows (PowerShell)**

```powershell
git clone https://github.com/ujjaidasani/blendbook.git
cd blendbook
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Then check three things:

1. **The address.** It prints `Blendbook … running at http://localhost:8080` and opens your browser. If another app
   has port 8080, Blendbook takes the next free one, so read the address it prints.
2. **The badge.** The top right of the page should read "rekordbox · N sets". If it says rekordbox wasn't found, see
   below.
3. **The tests**, if you want to be sure of the install: `.venv/bin/python -m unittest discover -s tests`
   (Windows: `.venv\Scripts\python -m unittest discover -s tests`).

To stop Blendbook, press Ctrl+C in the terminal, or use **Settings → Quit Blendbook** in the page.

### Optional extras

- **ffmpeg**, for analysing mix recordings. Mac: `brew install ffmpeg`. Windows: `winget install ffmpeg`. Restart
  Blendbook afterwards.
- **An Anthropic API key**, for free-form coaching. Add it in **Settings** in the page. The built-in coach works
  without one.

## 3. Let an AI set it up

Paste the prompt below into a coding assistant that can run commands on your computer, such as Claude Code, Cursor or
Codex. It checks what you have, installs Blendbook from source, and confirms it reads your library. In a plain chat
app it works as a step-by-step guide instead.

```text
Help me set up Blendbook on this computer. It's a free, open-source companion app for rekordbox DJs: https://github.com/ujjaidasani/blendbook

Work through these steps one at a time. Run the commands yourself if you can, otherwise tell me exactly what to type. Wait for me whenever a step needs my input.

1. Check what I have: my operating system (macOS or Windows), Python 3.12 or newer (python3 --version, or py --version on Windows), git, and rekordbox 6 or 7 with at least one set in its History. If Python or git is missing, tell me the simplest way to install it, then wait.

2. Get the code: git clone https://github.com/ujjaidasani/blendbook.git, then work inside the blendbook folder.

3. Create a virtual environment in .venv and install requirements.txt into it.
   macOS: python3 -m venv .venv, then .venv/bin/pip install -r requirements.txt
   Windows: py -m venv .venv, then .venv\Scripts\pip install -r requirements.txt

4. Confirm the install by running the tests with the virtual environment's Python: python -m unittest discover -s tests. They should all pass.

5. Start Blendbook with the virtual environment's Python: python app.py. It prints "Blendbook ... running at http://localhost:8080" and opens my browser. If port 8080 is busy it uses the next free one, so read the address it prints.

6. Check it worked: the badge at the top right of the page should say "rekordbox · N sets". If it says rekordbox wasn't found, help me find the folder that holds master.db (macOS: ~/Library/Pioneer/rekordbox, Windows: %APPDATA%\Pioneer\rekordbox) and set it in Settings, Choose folder.

7. Optional extras. Ask me before each one:
   - ffmpeg, for analysing mix recordings (macOS: brew install ffmpeg, Windows: winget install ffmpeg).
   - An Anthropic API key, for free-form coaching. Don't ask me to paste a key into this chat. I'll type it into Blendbook's Settings myself.

Rules:
- Blendbook only reads a copy of my rekordbox library. Don't change, move or delete anything in the rekordbox folder.
- Don't use sudo or administrator rights, and don't install anything system-wide without asking me first.
- I should use Chrome or Edge for the Blendbook page.
- If a step fails, show me the exact error and fix that before moving on.

Finish by telling me how to start Blendbook next time, and how to stop it.
```

## If something goes wrong

- **"rekordbox not found"**: open rekordbox once so it creates its library. If your library is somewhere unusual, go
  to **Settings → Choose folder…** and pick the folder that holds `master.db`. The usual places are
  `~/Library/Pioneer/rekordbox` on a Mac and `%APPDATA%\Pioneer\rekordbox` on Windows.
- **No sets show up**: Blendbook reads rekordbox's History. Play a few tracks in rekordbox, close it, and reload.
- **`python3: command not found`, or Python is older than 3.12**: install a current Python from
  [python.org](https://www.python.org/downloads/), then start again from `python3 -m venv .venv`.
- **`pip install` fails**: check you're online, then run the same command again. If it still fails, copy the last
  20 lines into [a new issue](https://github.com/ujjaidasani/blendbook/issues).
- **The page didn't open**: go to the address Blendbook printed, in Chrome or Edge.
- **The controller doesn't show up**: use Chrome or Edge, and allow MIDI access when the browser asks.
- **Anything else**: press **Settings → Copy diagnostics** and paste it into
  [a new issue](https://github.com/ujjaidasani/blendbook/issues). Read it first: it hides your home folder, but log
  lines can mention file names.

## Where your data lives

| Where | What's there |
|---|---|
| Mac: `~/Library/Application Support/Blendbook`, Windows: `%APPDATA%\Blendbook` | Plans, goals, mix log, settings, a working copy of your rekordbox library, backups |
| `Blendbook Recordings` in your Music folder | Any mix recordings you drop in |

To remove Blendbook completely, delete the app (or the `blendbook` folder you cloned) and those two folders.
