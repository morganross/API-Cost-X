# Windows Installer

APICostX can be packaged as a native Windows installer for nontechnical local use.

The installer target is:

- one `APICostX-Setup-<version>.exe` release asset;
- one installed `APICostX.exe` desktop launcher;
- no WSL, Node.js, npm, or Python requirement for end users;
- one desktop app window backed by Microsoft Edge WebView2, not the user's default browser;
- one local service bound to `127.0.0.1:8000` for bundled GUI and API traffic;
- one writable secrets file at `%LOCALAPPDATA%\APICostX\.env`;
- SQLite and generated files under `%LOCALAPPDATA%\APICostX\data`;
- logs, including `desktop-service.log`, under `%LOCALAPPDATA%\APICostX\logs`.

The installed app remains a single-user, local-only app. Do not bind it to a public or LAN address.

## Build Requirements

Building the installer requires a Windows machine or GitHub Actions Windows runner with:

- Python 3.11;
- Node.js 20 and npm;
- Inno Setup 6;
- .NET SDK 8 for publishing the desktop launcher;
- internet access for npm and Python package installation.

End users do not need those build tools.

## Build Command

From the repo root on Windows:

```powershell
.\scripts\build-windows.ps1 -Version 0.1.0
```

The script:

1. runs `npm ci` and `npm run build` in `web-gui`;
2. downloads the official Python embedded runtime matching the build Python version;
3. installs the API dependencies into the bundled Python runtime;
4. copies the API source, FilePromptForge, `.env.example`, and built web GUI into `dist\APICostX`;
5. builds the `APICostX.exe` desktop launcher that starts the bundled Python runtime and desktop shell;
6. builds `dist\installer\APICostX-Setup-<version>.exe` with Inno Setup;
7. writes `dist\installer\SHA256SUMS-windows.txt`.

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

On first launch, `APICostX.exe` copies the bundled `.env.example` to `%LOCALAPPDATA%\APICostX\.env`, initializes the SQLite database from the bundled sanitized seed DB, starts the local service on `127.0.0.1:8000`, and opens the GUI in its own WebView2 desktop window.

The installed app should be launched from the Start Menu. `127.0.0.1:8000` is a diagnostic endpoint for the local service, not a URL installer users need to browse to manually.

Closing the APICostX desktop window stops the local service.

## Troubleshooting

- If the desktop window does not open or closes immediately, inspect `%LOCALAPPDATA%\APICostX\logs\desktop-service.log` for the startup failure.
- If `desktop-service.log` reports that `127.0.0.1:8000` is already in use, close other APICostX windows, stop the process using port 8000, or set `API_COST_X_API_PORT` to another local port in `%LOCALAPPDATA%\APICostX\.env`, then launch APICostX again from the Start Menu.
- If Windows reports that WebView2 is missing, install the Microsoft Edge WebView2 Runtime, then relaunch APICostX.
- If provider calls fail after the desktop shell opens, edit `%LOCALAPPDATA%\APICostX\.env`, save the provider keys, and restart APICostX so the local service reloads configuration.
