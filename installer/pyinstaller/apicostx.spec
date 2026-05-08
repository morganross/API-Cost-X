# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


ROOT = Path(SPECPATH).parents[1]


def collect_tree(source: Path, destination: str):
    rows = []
    if not source.exists():
        return rows
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        rows.append((str(path), str(Path(destination) / path.relative_to(source).parent)))
    return rows


web_dist = ROOT / "assets" / "react-build"
if not (web_dist / "index.html").is_file():
    raise SystemExit("Missing assets/react-build/index.html. Run scripts/build-windows.ps1.")

datas = [
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "api" / "app" / "config" / "models.yaml"), "app/config"),
    (str(ROOT / "api" / "app" / "seed" / "api-cost-x.seed.db"), "app/seed"),
]
datas += collect_tree(ROOT / "api" / "app" / "seed" / "artifacts", "app/seed/artifacts")
datas += collect_tree(ROOT / "assets" / "react-build", "assets/react-build")
datas += collect_tree(ROOT / "packages" / "FilePromptForge", "packages/FilePromptForge")

hiddenimports = [
    "app.main",
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [str(ROOT / "api" / "app" / "desktop.py")],
    pathex=[str(ROOT / "api"), str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="APICostX",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
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
    name="APICostX",
)
