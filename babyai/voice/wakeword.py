class WakeWordDetector:
    def __init__(self, wakeword: str = "baby"):
        self.wakeword = wakeword
        self.available = False

    def is_available(self) -> bool:
        return self.available

    def listen(self) -> bool:
        return False
