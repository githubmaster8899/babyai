import argparse
import os
import signal
import sys
from pathlib import Path

from babyai.agent.core import BabyAI


def _portable_config(args) -> str:
    """Resolve --config, and turn --portable into BABYAI_HOME for the rest of the run."""
    config = args.config
    if args.portable:
        path = Path(config or "config.toml").expanduser()
        root = path.resolve().parent if path.exists() else Path.cwd()
        os.environ["BABYAI_HOME"] = str(root)
        if not path.exists() and (root / "config.toml").is_file():
            path = root / "config.toml"
        config = str(path)
    return config or "config.toml"


def main(argv=None):
    parser = argparse.ArgumentParser(description="BabyAI - local-first voice-driven AI assistant")
    parser.add_argument("--config", default=None, help="Path to config.toml (default: config.toml in the current folder)")
    parser.add_argument("--portable", action="store_true", help="Run from a portable copy on a USB drive: keep all data under <config folder>/data")
    parser.add_argument("--voice", action="store_true", help="Enable voice mode if available")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice mode")
    parser.add_argument("--desktop", action="store_true", help="Launch the floating desktop overlay (needs Tk)")
    summon = parser.add_mutually_exclusive_group()
    summon.add_argument("--toggle", dest="summon", action="store_const", const="toggle", help="Show the running overlay if she is hidden, hide her if she is showing (bind this to a keyboard shortcut)")
    summon.add_argument("--show", dest="summon", action="store_const", const="show", help="Bring the running overlay up")
    summon.add_argument("--hide", dest="summon", action="store_const", const="hide", help="Send the running overlay away")
    parser.add_argument("--capri", action="store_true", help="Optional: launch the separate C# Capri app built from capri/ and exit (not needed for --desktop, which has Capri built in); if it can't start, carry on as usual")
    parser.add_argument("command", nargs="?", help="Run a single text command instead of interactive mode")
    args = parser.parse_args(argv)

    if args.summon:  # talks to the overlay that is already running; starts nothing
        from babyai.ui.summon import send

        answer = send(args.summon)
        if answer == "ok":
            return 0
        print("He isn't running. Start him with: python3 -m babyai --desktop" if answer is None else f"He didn't understand '{args.summon}' ({answer}).")
        return 1

    if args.capri:
        from babyai.capri_launcher import launch_capri

        result = launch_capri()
        print(result.message)
        if result.ok:
            return 0
        print("Continuing without Capri...")

    config_path = _portable_config(args)
    voice_enabled = bool(args.voice) and not bool(args.no_voice)
    assistant = BabyAI(config_path=config_path, voice_enabled=voice_enabled)

    if args.portable or os.environ.get("BABYAI_HOME"):
        def _stop(*_args):  # the USB launcher asks politely; let the cleanup below run
            raise SystemExit(0)

        for name in (signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(name, _stop)
            except (ValueError, OSError):
                pass

    try:
        if args.desktop or not args.command:
            assistant.start_idea_checks()  # in the background, at most once a day; never in a one-shot command

        if args.desktop:
            try:
                import tkinter
                from babyai.ui.app import launch_desktop_ui
            except Exception as exc:
                print(f"Desktop UI unavailable: {exc}")
                print("Falling back to CLI...")
                assistant.run_cli()
                return 0
            try:
                launch_desktop_ui(assistant)
            except tkinter.TclError as exc:  # Tk is installed but there is no usable display
                print(f"Desktop UI could not start: {exc}")
                print("Falling back to CLI...")
                assistant.run_cli()
            return 0

        if args.command:
            if sys.stdin.isatty():  # someone is there to answer a "may I run this?" question
                assistant.confirm = assistant.cli_confirm
            response = assistant.process_user_input(args.command)
            print(response)
            return 0

        assistant.run_cli()
        return 0
    finally:
        assistant.memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
