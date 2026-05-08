# Windows Installer

APICostX can be packaged as a native Windows installer for nontechnical local use.

The installer target is:

- one `APICostX-Setup-<version>.exe` release asset;
- one installed `APICostX.exe` launcher;
- no WSL, Node.js, npm, or Python requirement for end users;
- one local browser URL at `http://127.0.0.1:8000`;
- one writable secrets file at `%LOCALAPPDATA%\APICostX\.env`;
- SQLite and generated files under `%LOCALAPPDATA%\APICostX\data`;
- logs under `%LOCALAPPDATA%\APICostX\logs`.

The installed app remains a single-user, local-only app. Do not bind it to a public or LAN address.

## Build Requirements

Building the installer requires a Windows machine or GitHub Actions Windows runner with:

- Python 3.11;
- Node.js 20 and npm;
- Inno Setup 6;
- internet access for npm and Python package installation.

End users do not need those build tools.

## Build Command

From the repo root on Windows:

```powershell
.\scripts\build-windows.ps1 -Version 0.1.0
```

The script:

1. creates `.venv-windows-build`;
2. installs the API package and PyInstaller into that build venv;
3. runs `npm ci` and `npm run build` in `web-gui`;
4. builds `dist\APICostX\APICostX.exe` with PyInstaller;
5. builds `dist\installer\APICostX-Setup-<version>.exe` with Inno Setup;
6. writes `dist\installer\SHA256SUMS-windows.txt`.

## Runtime Layout

The installer installs immutable program files under:

```text
%LOCALAPPDATA%\Programs\APICostX
```

The app creates mutable user files under:

```text
%LOCALAPPDATA%\APICostX\.env
%LOCALAPPDATA%\APICostX\data\
%LOCALAPPDATA%\APICostX\logs\
```

Uninstalling APICostX removes the installed program files but intentionally preserves `%LOCALAPPDATA%\APICostX` so provider keys, SQLite data, presets, and history are not accidentally deleted.

## First Launch

On first launch, `APICostX.exe` copies the bundled `.env.example` to `%LOCALAPPDATA%\APICostX\.env`, initializes the SQLite database from the bundled sanitized seed DB, starts the API on `127.0.0.1:8000`, serves the built web GUI from the same process, and opens the default browser.

Keep the console window open while using APICostX. Closing the console stops the local API.
