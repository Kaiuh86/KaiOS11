KAIOS 11
=========

KaiOS 11 is a Windows desktop simulator built with Python and PySide6.
It includes a desktop, start menu, browser, store, wallpapers, games,
photos app with editor, and a simple multi-account sign-in system.

FEATURES
--------
- Multi-account sign in (up to 5 accounts; you can also delete the final account)
- Desktop with wallpapers, app tiles, and Start menu
- Kai's Browser, KaiOS Store, Kai's Settings, and Kai's File Manager
- Kai's Photos with photo editing tools
- Document-style demo apps (Word, Excel, PowerPoint, Outlook, Notepad)
- Kai's ChatGPT web shortcut
- Built-in games: Space Explorer, Adventure Quest, Snake, Kai Racing, Crystal Match
- Fullscreen mode with F11
- Universal KaiOS logo across the project

REQUIREMENTS
------------
- Windows
- Python 3.10+ recommended
- Packages from requirements.txt

RUN
---
Option 1 (easy):
1. Extract the ZIP.
2. Double-click START_KAIOS_HERE.bat.

Option 2 (manual):
1. Open the KaiOS11 folder in VS Code or a terminal.
2. Install dependencies if needed:
       python -m pip install -r requirements.txt
3. Run KaiOS:
       python kaios.py

FILES
-----
User account data is stored in your Windows user profile under the KaiOS11
folder, so it is kept separate from this project folder.

NOTES
-----
- This project is a Windows application, not a real operating system.
- KaiOS account passwords protect access inside the app, but do not replace
  Windows security or encrypt files on disk.
- If an older setup used the app name "ChatGPT", this build automatically
  migrates it to "Kai's ChatGPT".
- Delete Account requires the account password and confirmation, even for the
  only remaining account. By default its files are moved to DeletedAccounts.
  If no accounts remain, KaiOS offers Create Account the next time you start it.
- GitHub source files do not include your local account registry or private files.
  Do not upload files from your Windows profile's KaiOS11 data folder.

PROJECT STRUCTURE
-----------------
- kaios.py              Main KaiOS application
- kaios_accounts.py     Account storage and login support
- wallpapers/           Built-in wallpaper assets
- START_KAIOS.bat       Inner launcher
- requirements.txt      Python dependencies

