from PyQt6.QtWidgets import QApplication, QWidget, QLabel
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from core.animation import AnimationManager
from core.state import PetState
from core.physics import PhysicsEngine
from core.behaviour import BehaviourEngine
from core.ai.brain import AIBrain
from core.ui.chat_bubble import ChatBubble
from core.ui.pet_menu import PetMenu
from core.ui.settings_panel import SettingsPanel
from core.ui.checkers import CheckersWindow

import random
import threading

class DesktopPet(QWidget):
    _pet_says_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.pet = QLabel(self)
        self.pet.resize(200, 200)
        self.pet.setScaledContents(True)
        self.pet.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.animation = AnimationManager(self.pet)
        self.hide()  

        self.animation.play("idle")

        self.physics         = PhysicsEngine(self)
        self.behaviour       = BehaviourEngine(self)

        self.timer = QTimer()
        self.timer.timeout.connect(self.animate)
        self.timer.start(200)

        self.behaviour_timer = QTimer()
        self.behaviour_timer.timeout.connect(self.behaviour.choose_behaviour)
        self.behaviour_timer.start(5000)

        self.resize(200, 200)

        self.is_running  = False
        self.direction   = 1
        self.state       = PetState.IDLE
        self.is_jumping  = False
        self.is_sleeping = False
        self._manual_sleeping = False
        self.floor_offset = 40

        screen = QGuiApplication.primaryScreen()
        self.screen_geometry = screen.availableGeometry()

        self.passive_gravity_timer = QTimer()
        self.passive_gravity_timer.timeout.connect(self.physics.apply_passive_gravity)
        self.passive_gravity_timer.start(30)

        self.brain    = AIBrain()
        self.chat     = ChatBubble(self, self.brain)
        self.menu     = PetMenu()
        self.settings = SettingsPanel(self.brain)
        self.checkers = CheckersWindow(self.brain.config.get("pet_name", "Xander"))

        # connect signals
        self._pet_says_signal.connect(self._show_pet_says)
        self.menu.action_talk.connect(self._on_talk)
        self.menu.action_play.connect(self._on_play)
        self.menu.action_sleep.connect(self._on_sleep)
        self.menu.action_settings.connect(self._on_settings)
        self.menu.action_quit.connect(self.quit_app)
        self.settings.saved.connect(self._on_settings_saved)
        self.chat.emotion_detected.connect(self._on_emotion)

        # AI timers
        self._activity_timer = QTimer()
        self._activity_timer.timeout.connect(self.brain.refresh_activity)
        self._activity_timer.start(1000)

        self._idle_seconds = 0
        self._idle_timer   = QTimer()
        self._idle_timer.timeout.connect(self._tick_idle)
        self._idle_timer.start(1000)

        self._random_talk_timer = QTimer()
        self._random_talk_timer.timeout.connect(self._check_random_talk)
        self._random_talk_timer.start(30000)

        self._current_window  = None
        self._pending_window  = None
        self._window_stable_s = 0

        self._window_watch_timer = QTimer()
        self._window_watch_timer.timeout.connect(self._check_window_comment)
        self._window_watch_timer.start(1000)

        self._talk_cooldown  = False
        self._cooldown_timer = QTimer()
        self._cooldown_timer.setSingleShot(True)
        self._cooldown_timer.timeout.connect(self._reset_cooldown)

        self._retry_timer = QTimer()
        self._retry_timer.timeout.connect(self._retry_ollama)
        if not self.brain.ollama_ok:
            self._retry_timer.start(30000)

    # Tray
    def quit_app(self):
        self.brain.save_session_summary()
        self.brain.memory.close()
        QApplication.quit()

    def closeEvent(self, event):
        self.brain.save_session_summary()
        self.brain.memory.close()
        event.accept()

    # Ollama retry
    def _retry_ollama(self):
        if self.brain.ensure_ollama():
            self._retry_timer.stop()
            def run():
                text = self.brain.greeting()
                if text:
                    self._pet_says_signal.emit(text)
            threading.Thread(target=run, daemon=True).start()

    # Menu actions 
    def _on_talk(self):
        if self.is_sleeping:
            return
        self.reset_idle()
        self.chat.restore_chat_mode()
        self.chat.show_right_of_pet()
        self.chat.input.setFocus()

    def _on_sleep(self):
        if getattr(self, '_manual_sleeping', False):
            self.behaviour.wake_up_manual()
        else:
            self.behaviour.start_manual_sleep()

    def _on_settings(self):
        self.settings.show_centered()

    def _on_settings_saved(self, cfg):
        if "pet_size" in cfg:
            size = cfg["pet_size"]
            self.pet.resize(size, size)
            self.resize(size, size)

    def _on_emotion(self, emotion: str):
        emotion_map = {
            "happy":   "idle",
            "excited": "jump",
            "sad":     "idle",
            "sleepy":  "sleep",
            "angry":   "idle",
            "neutral": "idle",
        }
        self.animation.play(emotion_map.get(emotion, "idle"))

    # Cooldown 
    def _reset_cooldown(self):
        self._talk_cooldown = False

    def _can_talk(self) -> bool:
        return (
            not self._talk_cooldown and
            not self.chat.isVisible() and
            not self.is_sleeping
        )

    def _show_pet_says(self, text: str):
        if not text:
            return
        self.chat.show_message(text)
        self._talk_cooldown = True
        self._cooldown_timer.start(120_000)
        QTimer.singleShot(10000, self._auto_hide_bubble)

    def _auto_hide_bubble(self):
        self.chat.hide()
        self.chat.restore_chat_mode()
        self._talk_cooldown = False

    # Idle tracking 

    def _tick_idle(self):
        self._idle_seconds += 1

    def reset_idle(self):
        self._idle_seconds = 0

    # Random self-talk 

    def _check_random_talk(self):
        if self._idle_seconds < 180 or not self._can_talk():
            return
        def run():
            text = self.brain.random_thought()
            if text:
                self._pet_says_signal.emit(text)
        threading.Thread(target=run, daemon=True).start()
        self._idle_seconds = 0

    # Window comment 

    def _check_window_comment(self):
        self.brain.refresh_activity()
        activity = self.brain._get_activity_context()

        if activity is None:
            self._pending_window  = None
            self._window_stable_s = 0
            return

        if activity != self._pending_window:
            self._pending_window  = activity
            self._window_stable_s = 0
            if self._talk_cooldown and not self.chat.isVisible():
                self._talk_cooldown = False
                self._cooldown_timer.stop()
            return

        self._window_stable_s += 1

        if self._window_stable_s >= 10:
            self._window_stable_s = 0
            if activity != self._current_window and self._can_talk():
                self._current_window = activity
                def run():
                    text = self.brain.random_thought()
                    if text:
                        self._pet_says_signal.emit(text)
                threading.Thread(target=run, daemon=True).start()

    # Standard pet methods 

    def animate(self):
        self.animation.direction = self.direction
        self.animation.next_frame()

    def mousePressEvent(self, event):
        self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        self.reset_idle()
        event.accept()

    def mouseMoveEvent(self, event):
        self.move(event.globalPosition().toPoint() - self.drag_position)
        event.accept()

    def contextMenuEvent(self, event):
        self.menu.show_for_pet(self)

    def move_pet(self):
        geo          = QGuiApplication.primaryScreen().availableGeometry()
        self.screen_geometry = geo
        floor        = geo.bottom() - self.height() + self.floor_offset
        x            = self.x() + (5 * self.direction)
        y            = min(self.y(), floor)

        if x <= geo.left():
            self.direction = 1
        elif x + self.width() >= geo.right():
            self.direction = -1

        self.move(x, y)

        if random.random() < 0.02:
            self.run_timer.stop()
            self.is_running = False
            self.behaviour.start_idle()

    def finish_idle_alt(self):
        self.is_idle_alt = False
        self.set_state(PetState.IDLE)
        self.behaviour.start_idle()

    def start_land(self):
        self.animation.play("land")
        self.land_timer = QTimer()
        self.land_timer.setSingleShot(True)
        self.land_timer.timeout.connect(self.finish_land)
        self.land_timer.start(600)

    def finish_land(self):
        self.land_timer.stop()
        self.is_jumping = False
        self.set_state(PetState.IDLE)
        self.behaviour.start_idle()

    def tick_spin(self):
        self.spin_count += 1
        if self.spin_count >= len(self.animation.frames) * 2:
            self.spin_timer.stop()
            self.is_spinning = False
            self.set_state(PetState.IDLE)
            self.behaviour.start_idle()

    def wake_up(self):
        if self._manual_sleeping:
            return  
        self.sleep_timer.stop()
        self.is_sleeping = False
        self.set_state(PetState.IDLE)
        self.behaviour.start_idle()

    def set_state(self, new_state):
        self.state = new_state
        
    def _on_play(self):
        if self.is_sleeping:
            return
        self.checkers.show_centered()