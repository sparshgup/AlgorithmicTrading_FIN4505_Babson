# hedge_manager.py

class HedgeManager:
    def __init__(self, session):
        self.session = session
        self.active_hedges = {}
        self.last_tick = -1

    def manage(self, tick, period, prices):
        self.last_tick = tick

        if period == 1 and tick >= 595:
            self.rollover_cl1f_to_cl2f()

        # Step 2: (later) Rebalance hedges dynamically if needed

    def rollover_cl1f_to_cl2f(self):
        positions = self.session.session.get('http://localhost:9999/v1/securities').json()

        cl1f_pos = 0
        for sec in positions:
            if sec['ticker'] == 'CL-1F':
                cl1f_pos = sec['position']
                break

        if cl1f_pos != 0:
            print(f"[tick {self.last_tick}] ROLLING {cl1f_pos} CL-1F --> CL-2F")

            new_side = 'SELL' if cl1f_pos < 0 else 'BUY'
            self.session.place_order('CL-2F', new_side, abs(cl1f_pos))

            side = 'BUY' if cl1f_pos < 0 else 'SELL'
            self.session.place_order('CL-1F', side, abs(cl1f_pos))

    def hedge_position(self, ticker, quantity, certainty=1.0):
        hedge_strength = self.calculate_hedge_strength(certainty)
        hedge_qty = int(abs(quantity) * hedge_strength)

        if hedge_qty == 0:
            print(f"[tick {self.last_tick}] Skipping hedge due to high certainty ({certainty:.2f})")
            return

        hedge_ticker = 'CL-1F' if self.last_tick < 580 else 'CL-2F'
        action = 'SELL' if quantity > 0 else 'BUY'

        print(f"[tick {self.last_tick}] Hedging {hedge_qty} contracts on {hedge_ticker} (certainty={certainty:.2f})")

        self.session.place_order(hedge_ticker, action, hedge_qty)

    def calculate_hedge_strength(self, certainty):
        """
        Given a certainty, return hedge % to apply.
        """

        if certainty >= 0.95:
            return 0.1  # EIA surprise level certainty
        elif certainty >= 0.8:
            return 0.6
        elif certainty >= 0.6:
            return 0.8
        else:
            return 1.0  # Fully hedge uncertain positions
