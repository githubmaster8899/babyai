import sys
import os
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QFontDatabase

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
    os.chdir(BASE_DIR)
else:
    BASE_DIR = Path(__file__).parent

app = QApplication(sys.argv)

font_path = BASE_DIR / "assets/fonts/pixelFont-7-8x14-sproutLands.ttf"
font_id   = QFontDatabase.addApplicationFont(str(font_path))
families  = QFontDatabase.applicationFontFamilies(font_id)
app.setFont(QFont(families[0] if families else "Courier New", 10))

from core.desktop_pet import DesktopPet
from PyQt6.QtGui import QGuiApplication

pet = DesktopPet()

screen = QGuiApplication.primaryScreen().availableGeometry()
pet.move(
    screen.center().x() - pet.width() // 2,
    screen.bottom() - pet.height() + pet.floor_offset
)
pet.show()

sys.exit(app.exec())