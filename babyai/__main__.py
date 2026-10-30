import argparse

from babyai.agent.core import BabyAI


def main(argv=None):
    parser = argparse.ArgumentParser(description="BabyAI - local-first voice-driven AI assistant")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--voice", action="store_true", help="Enable voice mode if available")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice mode")
    parser.add_argument("--desktop", action="store_true", help="Launch the floating desktop overlay (needs Tk)")
    parser.add_argument("--capri", action="store_true", help="Launch the built Capri companion app from capri/ and exit; if it can't start, carry on as usual")
    parser.add_argument("command", nargs="?", help="Run a single text command instead of interactive mode")
    args = parser.parse_args(argv)

    if args.capri:
        from babyai.capri_launcher import launch_capri

        result = launch_capri()
        print(result.message)
        if result.ok:
            return 0
        print("Continuing without Capri...")

    voice_enabled = bool(args.voice) and not bool(args.no_voice)
    assistant = BabyAI(config_path=args.config, voice_enabled=voice_enabled)

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
        response = assistant.process_user_input(args.command)
        print(response)
        return 0

    assistant.run_cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
