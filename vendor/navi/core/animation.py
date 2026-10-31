import os
import logging
from PyQt6.QtGui import QPixmap, QTransform

log = logging.getLogger(__name__)

class AnimationManager:
    def __init__(self, label, pet_name="otter"):
        self.label      = label
        self.pet_name   = pet_name
        self.frames     = []
        self.frame_index = 0
        self.direction  = 1
        self.project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )

    def play(self, animation_name):
        folder = os.path.join(
            self.project_root, "pets", self.pet_name, animation_name
        )
        if not os.path.exists(folder):
            log.warning("Animation folder not found: %s", folder)
            return

        files = sorted(f for f in os.listdir(folder) if f.endswith(".png"))
        self.frames = [os.path.join(folder, f) for f in files]
        self.frame_index = 0
        log.debug("Loaded %d frames for '%s'", len(self.frames), animation_name)

    def next_frame(self):
        if not self.frames:
            return
        frame_path = self.frames[self.frame_index]
        pixmap = QPixmap(frame_path)
        if pixmap.isNull():
            log.warning("Failed to load frame: %s", frame_path)
            return
        if self.direction == -1:
            pixmap = pixmap.transformed(QTransform().scale(-1, 1))
        self.label.setPixmap(pixmap)
        self.frame_index = (self.frame_index + 1) % len(self.frames)