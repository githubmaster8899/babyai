class SpeechToText:
    def __init__(self, model: str = "base"):
        self.model = model
        self.available = False

    def is_available(self) -> bool:
        return self.available

    def transcribe(self, audio_path: str | None = None) -> str:
        return "Speech-to-text is unavailable because the optional voice dependencies are not installed."
