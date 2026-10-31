import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "AIPetDesktop.spec"

APP_NAME    = "AIPetDesktop"
ENTRY_POINT = str(ROOT / "main.py")
ICON_PATH   = ROOT / "assets" / "ui" / "logo.png"

ADD_DATA = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "pets"),   "pets"),
    (str(ROOT / "core"),   "core"),
    (str(ROOT / "data"),   "data"),
]

# add config.json only if it exists
if (ROOT / "config.json").exists():
    ADD_DATA.append((str(ROOT / "config.json"), "."))

HIDDEN_IMPORTS = [
    "win32gui",
    "win32process",
    "win32api",
    "win32con",
    "pywintypes",
    "psutil",
    "ollama",
    "sqlite3",
    "core.ai.brain",
    "core.ai.memory",
    "core.ui.chat_bubble",
    "core.ui.pet_menu",
    "core.ui.settings_panel",
    "core.ui.checkers",
    "core.animation",
    "core.behaviour",
    "core.physics",
    "core.state",
    "core.desktop_pet",
]

def check_pyinstaller():
    try:
        import PyInstaller
        print(f"[OK] PyInstaller {PyInstaller.__version__} is already installed.")
    except ImportError:
        print("[...] Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("[OK] PyInstaller installed successfully.")

def convert_icon():
    ico_path = ROOT / "assets" / "ui" / "logo.ico"
    if ico_path.exists():
        print(f"[OK] Icon found: {ico_path}")
        return ico_path
    if not ICON_PATH.exists():
        print("[!!] logo.png not found, building without icon.")
        return None
    try:
        from PIL import Image
        img = Image.open(ICON_PATH).convert("RGBA")
        img.save(ico_path, format="ICO", sizes=[(16,16),(32,32),(48,48),(256,256)])
        print(f"[OK] Icon converted: {ico_path}")
        return ico_path
    except Exception as ex:
        print(f"[!!] Failed to convert icon: {ex}")
        return None

def clean():
    for folder in [DIST, BUILD]:
        if folder.exists():
            shutil.rmtree(folder)
            print(f"[..] Removed {folder.name}/")
    if SPEC.exists():
        SPEC.unlink()
        print(f"[..] Removed {SPEC.name}")

def build():
    print("\n=== Starting AIPetDesktop build ===\n")

    ico_path = convert_icon()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name",     APP_NAME,
        "--noconsole",
        "--onedir",
        "--clean",
        "--noconfirm",
    ]

    if ico_path:
        cmd += ["--icon", str(ico_path)]

    sep = ";" if sys.platform == "win32" else ":"
    for src, dest in ADD_DATA:
        if not Path(src).exists():
            print(f"[!!] Not found, skipped: {src}")
            continue
        cmd += ["--add-data", f"{src}{sep}{dest}"]

    for imp in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", imp]

    cmd.append(ENTRY_POINT)

    print("Command:", " ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode == 0:
        exe_path = DIST / APP_NAME / f"{APP_NAME}.exe"
        print(f"\n{'='*50}")
        print(f"[OK] Build completed!")
        print(f"     Exe: {exe_path}")
        print(f"     Distribute folder: dist\\{APP_NAME}\\")
        print(f"{'='*50}")
        print("\nNotes:")
        print("  - Users must install Ollama separately.")
        print("  - config.json & memory.db are inside dist\\AIPetDesktop\\")
    else:
        print(f"\n[ERROR] Build failed (exit code {result.returncode})")
        sys.exit(result.returncode)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    check_pyinstaller()
    if args.clean:
        clean()
    build()