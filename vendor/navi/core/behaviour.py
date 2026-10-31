import random
import time
from PyQt6.QtCore import QTimer
from core.state import PetState

class BehaviourEngine:
    LOCKED_STATES = [
        PetState.SLEEP,
        PetState.JUMP,
        PetState.SPIN
    ]
    def __init__(self, pet):
        self.pet = pet
        self.behaviours = [
            (PetState.IDLE, 40),
            (PetState.IDLE_ALT, 20),
            (PetState.RUN, 20),
            (PetState.JUMP, 10),
            (PetState.SPIN, 5),
            (PetState.SLEEP, 5),
        ]

        self.last_action_time = 0

    def choose_behaviour(self):
        if self.pet.state in self.LOCKED_STATES:
            return

        # cooldown 
        if time.time() - self.last_action_time < 3:
            return

        self.last_action_time = time.time()

        choices, weights = zip(*self.behaviours)
        behaviour = random.choices(choices, weights=weights)[0]

        if behaviour == PetState.RUN:
            self.start_running()

        elif behaviour == PetState.JUMP:
            self.start_jump()

        elif behaviour == PetState.SPIN:
            self.start_spin()

        elif behaviour == PetState.IDLE_ALT:
            self.start_idle_alt()

        elif behaviour == PetState.SLEEP:
            self.start_sleep()

        else:
            self.start_idle()

    def safe_stop_timer(self, name):
        if hasattr(self.pet, name):
            timer = getattr(self.pet, name)
            if timer:
                timer.stop()
                timer.deleteLater()
                setattr(self.pet, name, None)

    def _make_timer(self, attr: str, callback, interval: int,
                    single_shot: bool = False) -> None:
        self.safe_stop_timer(attr)
        t = QTimer()
        t.setSingleShot(single_shot)
        t.timeout.connect(callback)
        t.start(interval)
        setattr(self.pet, attr, t)

    def start_idle(self):
        self.safe_stop_timer("run_timer")
        self.pet.is_running = False

        floor = self.pet.physics.get_floor()
        self.pet.move(self.pet.x(), floor)

        self.pet.set_state(PetState.IDLE)
        self.pet.animation.play("idle")

    def start_running(self):
        if self.pet.state in self.LOCKED_STATES:
            return

        self.pet.set_state(PetState.RUN)
        self.safe_stop_timer("idle_alt_timer")
        self.pet.is_running = True
        self.pet.direction  = random.choice([-1, 1])
        self.pet.animation.play("run")
        self._make_timer("run_timer", self.pet.move_pet, 30)

    def start_jump(self):
        if self.pet.state in self.LOCKED_STATES:
            return

        self.pet.set_state(PetState.JUMP)
        self.pet.physics.start_jump(-15)
        self.pet.animation.play("jump")
        self._make_timer("jump_timer", self.pet.physics.apply_gravity, 30)

    def start_spin(self):
        if self.pet.state in self.LOCKED_STATES:
            return

        self.pet.set_state(PetState.SPIN)
        self.pet.animation.play("spin")
        self.pet.spin_count = 0
        self._make_timer("spin_timer", self.pet.tick_spin, 150)

    def start_idle_alt(self):
        if self.pet.state in self.LOCKED_STATES:
            return

        self.pet.set_state(PetState.IDLE_ALT)
        self.pet.animation.play("idle_alt")
        self._make_timer("idle_alt_timer", self.pet.finish_idle_alt,
                         2400, single_shot=True)

    def start_sleep(self):
        if self.pet.state in self.LOCKED_STATES:
            return

        self.pet.set_state(PetState.SLEEP)
        self.pet.is_sleeping = True
        self.pet.animation.play("sleep")
        duration = random.randint(10000, 15000)
        self._make_timer("sleep_timer", self.pet.wake_up,
                         duration, single_shot=True)

    def start_manual_sleep(self):
        if self.pet.state in self.LOCKED_STATES:
            return

        self.pet._manual_sleeping = True
        self.pet.set_state(PetState.SLEEP)
        self.pet.is_sleeping = True
        self.pet.animation.play("sleep")
        self.pet._random_talk_timer.stop()
        self.pet._window_watch_timer.stop()
        self.pet.menu.set_sleep_mode(True)

    def wake_up_manual(self):
        self.pet._manual_sleeping = False
        self.pet.is_sleeping = False
        self.pet._random_talk_timer.start(30000)
        self.pet._window_watch_timer.start(1000)
        self.pet.menu.set_sleep_mode(False)
        self.start_idle()