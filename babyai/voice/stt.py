class AudioIO:
    def __init__(self):
        self.available = False

    def is_available(self) -> bool:
        return self.available

    def start(self) -> str:
        return "Audio I/O is not available because optional voice dependencies are not installed."

    def stop(self) -> str:
        return "Audio I/O stopped."
