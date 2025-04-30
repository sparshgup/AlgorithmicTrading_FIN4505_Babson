class StorageModel:
    def __init__(self, session, lease_manager, fundamental_model=None):
        self.session = session
        self.lease_manager = lease_manager
        self.signals = []
        self.active_arb = None
        self.pending_release_check = False
        self.tank_capacity = 10000
        self.storage_cost_per_tick = 0.05 / 30  # $0.05/day

    def update(self, tick, period):
        self.tick = tick
        self.period = period

        if self.pending_release_check:
            self.check_and_release_leases()

        if self.active_arb:
            self.check_exit()
        else:
            self.check_entry()

    def check_and_release_leases(self):
        position = self.session.get_position('CL')
        leases = self.session.session.get('http://localhost:9999/v1/leases').json()
        if position == 0:
            for lease in leases:
                if lease['ticker'] == 'CL-STORAGE' and lease['containment_usage'] == 0:
                    self.session.release_lease(lease['id'])
            self.pending_release_check = False

    def theoretical_future_price(self, cl_price, current_tick, expiry_tick):
        ticks_left = expiry_tick - current_tick
        days = ticks_left / 30
        return cl_price + days * 0.05

    def estimate_expected_pnl_spot_fut(self, cl, fut_price, fut_expiry_tick, direction):
        fair_value = self.theoretical_future_price(cl, self.tick, fut_expiry_tick)
        spread = fut_price - fair_value
        if direction == "long_CL":
            return spread * 1000 * 10 - 20
        else:
            return -spread * 1000 * 10 - 20

    def check_entry(self):
        prices = self.session.get_prices()
        cl = prices.get('CL')
        cl1f = prices.get('CL-1F')
        cl2f = prices.get('CL-2F')

        if not cl or not cl1f or not cl2f:
            return

        fut = 'CL-1F' if self.period == 1 else 'CL-2F'
        fut_price = cl1f if fut == 'CL-1F' else cl2f
        expiry_tick = 600 if fut == 'CL-1F' else 1200

        expected_pnl = self.estimate_expected_pnl_spot_fut(cl, fut_price, expiry_tick, "long_CL")
        if expected_pnl > 100:
            self.lease_manager.request_storage('CL-STORAGE', 1)
            self.signals.append({'ticker': 'CL', 'action': 'BUY', 'qty': 10})
            self.signals.append({'ticker': fut, 'action': 'SELL', 'qty': 10})
            self.active_arb = {
                'type': 'spot_fut',
                'long': 'CL',
                'short': fut,
                'long_entry': cl,
                'short_entry': fut_price,
                'tick_entered': self.tick,
                'expiry_tick': expiry_tick,
                'storage_leased': 1
            }

    def check_exit(self):
        prices = self.session.get_prices()
        if not prices or not self.active_arb:
            return

        long_price = prices.get(self.active_arb['long'])
        short_price = prices.get(self.active_arb['short'])
        if long_price is None or short_price is None:
            return

        spread_change = (long_price - short_price) - (self.active_arb['long_entry'] - self.active_arb['short_entry'])
        pnl = spread_change * 1000 * 10
        hold_ticks = self.tick - self.active_arb['tick_entered']

        if self.tick >= self.active_arb['expiry_tick'] - 20 or pnl >= 150 or hold_ticks >= 80:
            self.exit_positions()

    def exit_positions(self):
        self.signals.append({'ticker': self.active_arb['long'], 'action': 'SELL', 'qty': 10})
        self.signals.append({'ticker': self.active_arb['short'], 'action': 'BUY', 'qty': 10})
        if self.active_arb.get('storage_leased'):
            self.pending_release_check = True
        self.active_arb = None

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)

    def expected_profit(self):
        if not self.active_arb:
            return 0, 0

        prices = self.session.get_prices()
        long_price = prices.get(self.active_arb['long'])
        short_price = prices.get(self.active_arb['short'])
        if not long_price or not short_price:
            return 0, 0

        delta = (long_price - short_price) - (self.active_arb['long_entry'] - self.active_arb['short_entry'])
        return delta * 1000 * 10, 0.5
