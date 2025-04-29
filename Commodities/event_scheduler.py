# event_scheduler.py

EIA_TICK_RANGES = [
    (89, 91),
    (239, 241),
    (389, 391),
    (539, 541)
]

class EventScheduler:
    def __init__(self):
        self.eia_windows = list(EIA_TICK_RANGES)
        self.aggression_mode = False
        self.current_window = None

    def update(self, tick, period):
        """
        Monitor important case events like EIA releases.
        Set 'aggression mode' True during EIA windows.
        """
        # Start Aggression Mode if entering a window
        for window in self.eia_windows:
            if window[0] <= tick <= window[1]:
                if self.current_window != window:
                    print(f"[p{period}][tick {tick}] Aggression Mode ACTIVATED (EIA window {window})")
                    self.aggression_mode = True
                    self.current_window = window
                return  # Don't check further windows

        # If not in any window, and we had been aggressive, turn it off
        if self.aggression_mode and self.current_window:
            print(f"[p{period}][tick {tick}] Aggression Mode DEACTIVATED")
            self.aggression_mode = False
            self.current_window = None

    def aggression_mode_active(self):
        return self.aggression_mode
