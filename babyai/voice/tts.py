class TTS:
    def __init__(self, voice: str = ""):
        self.voice = voice
        self.available = False

    def is_available(self) -> bool:
        return self.available

    def speak(self, text: str) -> str:
        return "Text-to-speech is unavailable because optional voice dependencies are not installed."
