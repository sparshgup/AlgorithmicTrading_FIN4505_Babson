EIA_TICK_RANGES = [
    (89, 92),
    (239, 242),
    (389, 392),
    (539, 542)
]

class EventScheduler:
    def __init__(self):
        self.eia_windows = list(EIA_TICK_RANGES)
        self.aggression_mode = False
        self.current_window = None
        self.eia_tick_log = []

    def update(self, tick, period):
        for window in self.eia_windows:
            if window[0] <= tick <= window[1]:
                if self.current_window != window:
                    print(f"[p{period}][tick {tick}] Aggression Mode ACTIVATED (EIA window {window})")
                    self.aggression_mode = True
                    self.current_window = window
                    self.eia_tick_log.append((period, tick))
                return

        if self.aggression_mode and self.current_window:
            print(f"[p{period}][tick {tick}] Aggression Mode DEACTIVATED")
            self.aggression_mode = False
            self.current_window = None

    def aggression_mode_active(self):
        return self.aggression_mode

    def is_eia_tick(self, tick):
        return any(start <= tick <= end for start, end in self.eia_windows)

    def last_eia_tick(self):
        return self.eia_tick_log[-1] if self.eia_tick_log else (None, None)
