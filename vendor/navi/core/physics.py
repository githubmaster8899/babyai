class PhysicsEngine:

    def __init__(self, pet):
        self.pet = pet

        self.velocity_y = 0
        self.gravity = 1

    def apply_passive_gravity(self):

        if self.pet.is_jumping:
            return

        from core.state import PetState
        if self.pet.state == PetState.RUN:
            return

        floor = self.get_floor()
        current_y = self.pet.y()

        if current_y < floor:
            new_y = min(current_y + 8, floor)
            self.pet.move(self.pet.x(), new_y)

        elif current_y > floor:
            self.pet.move(self.pet.x(), floor)

    def start_jump(self, velocity=-15):

        self.pet.is_jumping = True
        self.velocity_y = velocity

    def apply_gravity(self):

        self.velocity_y += self.gravity
        new_y = self.pet.y() + self.velocity_y
        floor = self.get_floor()

        if new_y < floor:
            self.pet.move(self.pet.x(), new_y)
        else:
            self.pet.move(self.pet.x(), floor)
            self.pet.jump_timer.stop()
            self.pet.start_land()
            
    def get_floor(self):
        screen = self.pet.screen_geometry
        return screen.bottom() - self.pet.height() + self.pet.floor_offset