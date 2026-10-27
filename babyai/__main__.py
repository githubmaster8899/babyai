import argparse

from babyai.agent.core import BabyAI
from babyai.ui.app import launch_desktop_ui


def main(argv=None):
    parser = argparse.ArgumentParser(description="BabyAI - local-first voice-driven AI assistant")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--voice", action="store_true", help="Enable voice mode if available")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice mode")
    parser.add_argument("--desktop", action="store_true", help="Launch the desktop companion UI")
    parser.add_argument("command", nargs="?", help="Run a single text command instead of interactive mode")
    args = parser.parse_args(argv)

    voice_enabled = bool(args.voice) and not bool(args.no_voice)
    assistant = BabyAI(config_path=args.config, voice_enabled=voice_enabled)

    if args.desktop:
        launch_desktop_ui(assistant)
        return 0

    if args.command:
        print(assistant.process_user_input(args.command))
        return 0

    assistant.run_cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
