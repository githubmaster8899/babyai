from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QApplication, QLabel
from PyQt6.QtGui import QPixmap, QPainter, QFont, QFontDatabase, QColor
from PyQt6.QtCore import Qt, QRect, pyqtSignal
from pathlib import Path

BASE_DIR     = Path(__file__).parent.parent.parent
FONT_PATH    = BASE_DIR / "assets/fonts/pixelFont-7-8x14-sproutLands.ttf"
BTN_SHEET    = BASE_DIR / "assets/ui/UI_Big_Play_Button.png"
PANEL_SHEET  = BASE_DIR / "assets/ui/Setting_menu.png"

CELL_W, CELL_H   = 96, 32
PANEL_W, PANEL_H = 128, 144

def _crop(path, x, y, w, h) -> QPixmap:
    return QPixmap(str(path)).copy(QRect(x, y, w, h))

class PixelButton(QPushButton):
    def __init__(self, label: str, font: QFont, parent=None):
        super().__init__(parent)
        self._label         = label
        self._font          = font
        self._px_normal     = _crop(BTN_SHEET, 0, 0, CELL_W, CELL_H)       # col 0 row 0 — normal
        self._px_pressed    = _crop(BTN_SHEET, CELL_W, 0, CELL_W, CELL_H)  # col 1 row 0 — pressed
        self._hovered       = False
        self._pressed       = False
        self._hover_enabled = False  

        self.setFixedSize(120, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        px = self._px_pressed if self._pressed else self._px_normal
        painter.drawPixmap(self.rect(), px)
        painter.setFont(self._font)
        painter.setPen(QColor("#3d2b1f"))
        offset = 1 if self._pressed else 0
        painter.drawText(
            self.rect().adjusted(0, offset, 0, offset),
            Qt.AlignmentFlag.AlignCenter,
            self._label
        )
        painter.end()

    def enterEvent(self, event):
        if self._hover_enabled:
            self._hovered = True
            self.update()

    def mouseMoveEvent(self, event):
        self._hover_enabled = True
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()

    def mousePressEvent(self, event):
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

class PetMenu(QWidget):

    action_talk     = pyqtSignal()
    action_play     = pyqtSignal()
    action_sleep    = pyqtSignal()
    action_settings = pyqtSignal()
    action_quit     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        QApplication.instance().installEventFilter(self)

        self._pet = None 

        # follow timer 
        from PyQt6.QtCore import QTimer
        self._follow_timer = QTimer()
        self._follow_timer.timeout.connect(self._follow_pet)
        self._follow_timer.start(30)

        # background panel
        self._bg = QLabel(self)
        self._bg.setScaledContents(True)
        self._bg.setPixmap(_crop(PANEL_SHEET, PANEL_W, 0, PANEL_W, PANEL_H))

        # font
        font_id  = QFontDatabase.addApplicationFont(str(FONT_PATH))
        families = QFontDatabase.applicationFontFamilies(font_id)
        font     = QFont(families[0] if families else "Courier New", 8)

        # layout 
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(2)

        for label, signal in [
            ("TALK",     self.action_talk),
            ("PLAY (CHECKERS)", self.action_play),
            ("SLEEP",    self.action_sleep),
            ("SETTINGS", self.action_settings),
            ("QUIT",     self.action_quit),
        ]:
            btn = PixelButton(label, font, self)
            btn.clicked.connect(lambda _, s=signal: (self.hide(), s.emit()))
            layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            if label == "SLEEP":
                self._btn_sleep = btn
            elif label == "PLAY (CHECKERS)":
                self._btn_play = btn
            elif label == "TALK":
                self._btn_talk = btn

        self.adjustSize()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg.setGeometry(0, 0, self.width(), self.height())
        self._bg.lower()

    def eventFilter(self, obj, event):
        if self.isVisible() and event.type() == event.Type.MouseButtonPress:
            if not self.geometry().contains(event.globalPosition().toPoint()):
                self.hide()
        return False

    def _calc_pos_near_pet(self):
        if self._pet is None:
            return None, None
        screen = QApplication.primaryScreen().availableGeometry()
        x = self._pet.x() + self._pet.width() - 60
        y = self._pet.y() + (self._pet.height() // 2) - (self.height() // 2) - 80
        if x + self.width() > screen.right():
            x = self._pet.x() - self.width() - 4
        y = max(screen.top(), min(y, screen.bottom() - self.height()))
        return x, y

    def _follow_pet(self):
        if self.isVisible() and self._pet:
            x, y = self._calc_pos_near_pet()
            if x is not None:
                self.move(x, y)

    def _reset_buttons(self):
        for btn in self.findChildren(PixelButton):
            btn._hovered       = False
            btn._pressed       = False
            btn._hover_enabled = False
            btn.update()

    def set_sleep_mode(self, sleeping: bool):
        if sleeping:
            self._btn_sleep._label = "WAKE UP"
            self._btn_talk.setEnabled(False)
            self._btn_talk.setStyleSheet("background: transparent; border: none; opacity: 0.4;")
            self._btn_play.setEnabled(False)
            self._btn_play.setStyleSheet("background: transparent; border: none; opacity: 0.4;")
        else:
            self._btn_sleep._label = "SLEEP"
            self._btn_talk.setEnabled(True)
            self._btn_talk.setStyleSheet("background: transparent; border: none;")
            self._btn_play.setEnabled(True)
            self._btn_play.setStyleSheet("background: transparent; border: none;")
        self._btn_sleep.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._reset_buttons()

    def show_for_pet(self, pet):
        self._pet = pet
        self._reset_buttons()
        x, y = self._calc_pos_near_pet()
        if x is not None:
            self.move(x, y)
        self.show()