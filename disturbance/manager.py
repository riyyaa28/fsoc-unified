from disturbance.effects import add_gaussian_noise, add_fog, add_jitter, add_rain


class DisturbanceManager:
    LEVELS = {
        "fog": {0: 0.0, 1: 0.15, 2: 0.30, 3: 0.50},
        "noise": {0: 0, 1: 8, 2: 18, 3: 35},
        "jitter": {0: 0, 1: 2, 2: 5, 3: 10},
        "rain": {0: 0.0, 1: 0.2, 2: 0.4, 3: 0.6},
    }

    def __init__(self):
        self.state = {k: 0 for k in self.LEVELS}

    def set_level(self, category, level):
        self.state[category] = level

    def apply_to_frame(self, frame):
        out = frame
        if self.state["noise"] > 0:
            out = add_gaussian_noise(
                out, sigma=self.LEVELS["noise"][self.state["noise"]]
            )
        if self.state["fog"] > 0:
            out = add_fog(out, intensity=self.LEVELS["fog"][self.state["fog"]])
        if self.state["rain"] > 0:
            out = add_rain(out, intensity=self.LEVELS["rain"][self.state["rain"]])
        if self.state["jitter"] > 0:
            out = add_jitter(out, max_shift=self.LEVELS["jitter"][self.state["jitter"]])
        return out
