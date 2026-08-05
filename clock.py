"""
GameClock: in-game day-of-week progression, cycling MON..SUN forever.
No dependencies other than itself; drawing its widget lives in ui.py.
"""


class GameClock:
    """One full lap of `day_progress` (0.0 -> 1.0) is one in-game day.
    Update logic only — rendering is a separate, swappable concern."""

    DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
    DAY_DURATION_SECONDS = 10.0

    def __init__(self):
        self.day_index = 0
        self.day_progress = 0.0  # 0.0 (start of day) .. 1.0 (end of day)

    def update(self, dt):
        self.day_progress += dt / self.DAY_DURATION_SECONDS
        while self.day_progress >= 1.0:
            self.day_progress -= 1.0
            self.day_index = (self.day_index + 1) % len(self.DAYS)

    @property
    def day_label(self):
        return self.DAYS[self.day_index]
