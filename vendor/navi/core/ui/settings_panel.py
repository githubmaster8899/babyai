from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QApplication, QFrame
)
from PyQt6.QtGui import QPixmap, QPainter, QFont, QFontDatabase, QColor, QPalette, QBrush
from PyQt6.QtCore import Qt, QRect, pyqtSignal
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent.parent
FONT_PATH   = BASE_DIR / "assets/fonts/pixelFont-7-8x14-sproutLands.ttf"
PANEL_SHEET = BASE_DIR / "assets/ui/Setting_menu.png"
BTN_SHEET   = BASE_DIR / "assets/ui/UI_Big_Play_Button.png"
PANEL_W, PANEL_H = 128, 144
BTN_CW, BTN_CH   = 96, 32

def _crop(path, x, y, w, h) -> QPixmap:
    return QPixmap(str(path)).copy(QRect(x, y, w, h))

def _load_font(size=7):
    font_id  = QFontDatabase.addApplicationFont(str(FONT_PATH))
    families = QFontDatabase.applicationFontFamilies(font_id)
    return QFont(families[0] if families else "Courier New", size)

class SaveButton(QPushButton):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label      = label
        self._font       = _load_font(8)
        self._px_normal  = _crop(BTN_SHEET, 0, 0, BTN_CW, BTN_CH)
        self._px_pressed = _crop(BTN_SHEET, BTN_CW, 0, BTN_CW, BTN_CH)
        self._pressed    = False
        self.setFixedSize(120, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._px_pressed if self._pressed else self._px_normal)
        p.setFont(self._font)
        p.setPen(QColor("#3d2b1f"))
        p.drawText(self.rect().adjusted(0, 1 if self._pressed else 0, 0, 0),
                   Qt.AlignmentFlag.AlignCenter, self._label)
        p.end()

    def mousePressEvent(self, e):
        self._pressed = True; self.update(); super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._pressed = False; self.update(); super().mouseReleaseEvent(e)

class BgPanel(QFrame):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self.setFrameShape(QFrame.Shape.NoFrame)  

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawPixmap(self.rect(), self._pixmap)
        p.end()

class SettingsPanel(QWidget):

    saved = pyqtSignal(dict)

    def __init__(self, brain, parent=None):
        super().__init__(parent)
        self.brain = brain

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent; border: none;")

        QApplication.instance().installEventFilter(self)

        font       = _load_font(7)
        font_label = _load_font(6)

        LABEL_S = "background: transparent; color: #3d2b1f;"
        INPUT_S = ("background: rgba(255,255,255,0.35); color: #3d2b1f;"
                   "border: 1px solid #9d7f5a; border-radius: 3px; padding: 2px 5px;")
        COMBO_S = ("background: rgba(255,255,255,0.35); color: #3d2b1f;"
                   "border: 1px solid #9d7f5a; border-radius: 3px; padding: 2px 5px;"
                   "selection-background-color: #c8a87a;")

        self._panel = BgPanel(_crop(PANEL_SHEET, PANEL_W, 0, PANEL_W, PANEL_H), self)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(32, 36, 32, 26)
        layout.setSpacing(8)

        def row(lbl_text, widget):
            lbl = QLabel(lbl_text)
            lbl.setFont(font_label)
            lbl.setStyleSheet(LABEL_S)
            widget.setFont(font)
            widget.setFixedHeight(22)
            widget.setStyleSheet(INPUT_S if isinstance(widget, QLineEdit) else COMBO_S)
            r = QVBoxLayout()
            r.setSpacing(2)
            r.addWidget(lbl)
            r.addWidget(widget)
            layout.addLayout(r)

        self._pet_name  = QLineEdit()
        self._user_name = QLineEdit()
        self._model    = QComboBox()
        self._language  = QComboBox()

        self._model.addItems(["llama3.2", "llama3.1", "mistral", "gemma2"])
        self._language.addItems(["AUTO", "INDONESIA", "ENGLISH"])

        # header SETTINGS manual
        header = QLabel("SETTINGS")
        header.setFont(_load_font(9))
        header.setStyleSheet("background: transparent; color: #3d2b1f;")
        header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(header)
        layout.addSpacing(4)

        row("PET NAME",  self._pet_name)
        row("YOUR NAME", self._user_name)
        row("LLM MODEL", self._model)
        row("LANGUAGE",  self._language)

        layout.addSpacing(6)
        btn = SaveButton("SAVE", self._panel)
        btn.clicked.connect(self._on_save)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(self._panel)

        self.setMinimumWidth(300)
        self.adjustSize()
        self._load_config()

    def _load_config(self):
        cfg = self.brain.config
        self._pet_name.setText(cfg.get("pet_name", ""))
        self._user_name.setText(cfg.get("user_name", ""))
        model = cfg.get("model", "llama3.2")
        if self._model.findText(model) >= 0:
            self._model.setCurrentText(model)
        lang = cfg.get("language", "AUTO").upper()
        if self._language.findText(lang) >= 0:
            self._language.setCurrentText(lang)

    def _on_save(self):
        cfg = {
            "pet_name":  self._pet_name.text().strip() or "Xander",
            "user_name": self._user_name.text().strip(),
            "model":     self._model.currentText(),
            "language":  self._language.currentText(),
        }
        self.brain.update_config(**cfg)
        self.saved.emit(cfg)
        self.hide()

    def eventFilter(self, obj, event):
        if self.isVisible() and event.type() == event.Type.MouseButtonPress:
            if not self.geometry().contains(event.globalPosition().toPoint()):
                self.hide()
        return False

    def show_centered(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2
        )
        self._load_config()
        self.show()