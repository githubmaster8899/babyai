from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QFrame, QApplication
from PyQt6.QtGui import QPixmap, QFontDatabase, QFont
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
FONT_PATH = BASE_DIR / "assets/fonts/pixelFont-7-8x14-sproutLands.ttf"
BUBBLE_PADDING = (22, 20, 22, 18)
LINE_SPACING   = 8  
MAX_BUBBLE_WIDTH = 320

class ChatBubble(QWidget):

    emotion_detected = pyqtSignal(str)

    def __init__(self, pet, brain):
        super().__init__()

        self.pet         = pet
        self.brain       = brain
        self._worker     = None
        self._full_reply = ""

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        QApplication.instance().installEventFilter(self)

        self.pixel_font = self._load_pixel_font()

        self.bg = QLabel(self)
        self.bg.setScaledContents(True)
        self.bg.lower()

        self.label = QLabel("...")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(MAX_BUBBLE_WIDTH - BUBBLE_PADDING[0] - BUBBLE_PADDING[2])
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.label.setFont(self.pixel_font)
        self.label.setStyleSheet("background: transparent; color: #3d2b1f;")

        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.Shape.HLine)
        self.divider.setStyleSheet("color: rgba(100,70,40,0.25); background: transparent;")
        self.divider.setFixedHeight(1)

        self.input = QLineEdit()
        self.input.setPlaceholderText("SAY SOMETHING...")
        self.input.setFont(self.pixel_font)
        self.input.setStyleSheet("background: transparent; border: none; color: #3d2b1f;")
        self.input.returnPressed.connect(self.handle_input)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*BUBBLE_PADDING)
        layout.setSpacing(6)
        layout.addWidget(self.label)
        layout.addWidget(self.divider)
        layout.addWidget(self.input)

        self.resize(MAX_BUBBLE_WIDTH, 100)
        self.update_style("...")

        self._dot_count    = 0
        self._typing_timer = QTimer()
        self._typing_timer.timeout.connect(self._tick_typing)

        self._follow_timer = QTimer()
        self._follow_timer.timeout.connect(self._follow_pet)
        self._follow_timer.start(30)

    # font 

    def _load_pixel_font(self):
        font_id = QFontDatabase.addApplicationFont(str(FONT_PATH))
        if font_id == -1:
            return QFont("Courier New", 8)
        families = QFontDatabase.applicationFontFamilies(font_id)
        return QFont(families[0], 9)

    # asset

    def get_bubble_asset(self, text):
        n = len(text)
        if n < 30:   asset = "assets/ui/dialog_small.png"
        elif n < 80: asset = "assets/ui/dialog_medium.png"
        else:        asset = "assets/ui/dialog_big.png"
        return BASE_DIR / asset

    def update_style(self, text, flipped: bool = False):
        path   = self.get_bubble_asset(text)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        if flipped:
            from PyQt6.QtGui import QTransform
            pixmap = pixmap.transformed(QTransform().scale(-1, 1))
        self.bg.setPixmap(pixmap)
        self.bg.resize(self.size())
        self.bg.lower()

    # events

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.bg.resize(self.size())

    def eventFilter(self, obj, event):
        if self.isVisible() and event.type() == event.Type.MouseButtonPress:
            if not self.geometry().contains(event.globalPosition().toPoint()):
                self.hide()
        return False

    # typing indicator

    def _start_typing(self):
        self._dot_count = 0
        self.label.setText(".")
        self.input.setEnabled(False)
        self.input.setPlaceholderText("...")
        self._typing_timer.start(400)

    def _tick_typing(self):
        self._dot_count = (self._dot_count + 1) % 4
        self.label.setText("." * max(self._dot_count, 1))

    def _stop_typing(self):
        self._typing_timer.stop()
        self.input.setEnabled(True)
        self.input.setPlaceholderText("SAY SOMETHING...")
        self.input.setFocus()

    # input streaming

    def handle_input(self):
        text = self.input.text().strip()
        if not text:
            return

        if not self.brain.ollama_ok:
            self._display("Ollama is not running!\nRun: ollama serve")
            return

        self.input.clear()
        self._full_reply = ""
        self._start_typing()

        self._worker = self.brain.create_stream_worker(text)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_chunk(self, piece: str):
        if self._typing_timer.isActive():
            self._stop_typing()
        self._full_reply += piece
        self._display(self._truncate(self._full_reply))

    def _on_finished(self, full_reply: str):
        self._stop_typing()
        self._display(self._truncate(full_reply))
        import threading
        threading.Thread(
            target=lambda: self.emotion_detected.emit(self.brain.extract_emotion(full_reply)),
            daemon=True
        ).start()

    def _on_error(self, fallback: str):
        self._stop_typing()
        self._display(fallback)

    # display helper

    def _truncate(self, text: str, max_words: int = 20) -> str:
        words = text.split()
        if len(words) <= max_words:
            return text
        return " ".join(words[:max_words]) + "..."

    def _display(self, text: str):
        self.label.setText(text)
        self.label.adjustSize()
        self.adjustSize()
        self.show_right_of_pet()  # handles flip + update_style internally

    # greeting, random talk

    def show_message(self, text: str):
        if not text:
            return
        self.input.hide()
        self.divider.hide()
        self._display(text)

    def restore_chat_mode(self):
        self.input.show()
        self.divider.show()
        self.label.setText("...")

    # position

    def _calc_position(self):
        screen   = self.pet.screen().availableGeometry()
        x_right  = self.pet.x() + self.pet.width() - 60
        flipped  = False

        if x_right + self.width() > screen.right():
            # bubble mirror, flip bubble tail
            x       = self.pet.x() - self.width() + 60
            flipped = True
        else:
            x = x_right

        y = self.pet.y() + (self.pet.height() // 2) - (self.height() // 2) - 20
        y = max(screen.top(), min(y, screen.bottom() - self.height()))
        return x, y, flipped

    def _follow_pet(self):
        if self.isVisible():
            x, y, flipped = self._calc_position()
            self.update_style(self.label.text(), flipped)
            self.move(x, y)

    def show_right_of_pet(self):
        self.adjustSize()
        x, y, flipped = self._calc_position()
        self.update_style(self.label.text(), flipped)
        self.bg.resize(self.size())
        self.bg.lower()
        self.move(x, y)
        self.show()