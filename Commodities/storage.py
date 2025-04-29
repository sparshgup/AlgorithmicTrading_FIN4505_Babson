# storage.py

class StorageModel:
    def __init__(self, session, lease_manager):
        self.session = session
        self.lease_manager = lease_manager
        self.signals = []
        self.active_arb = None
        self.pending_release_check = False

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

    def estimate_expected_pnl_spot_fut(self, cl, future_price, days, direction):
        storage_cost = days * 0.05
        spread = future_price - cl
        adj_spread = spread - storage_cost if direction == "long_CL" else -spread - storage_cost
        gross_profit = adj_spread * 1000 * 10
        net_profit = gross_profit - 20
        return net_profit

    def check_entry(self):
        prices = self.session.get_prices()
        cl = prices.get('CL')
        cl1f = prices.get('CL-1F')
        cl2f = prices.get('CL-2F')

        if cl is None or cl1f is None or cl2f is None:
            return

        future = 'CL-1F' if self.period == 1 else 'CL-2F'
        future_price = prices.get(future)
        end_tick = 600 if self.period == 1 else 1200
        days = (end_tick - self.tick) // 30

        if future_price - cl > 0:
            direction = "long_CL"
            expected_pnl = self.estimate_expected_pnl_spot_fut(cl, future_price, days, direction)
            if expected_pnl > 100:
                self.lease_manager.request_storage('CL-STORAGE', 1)
                self.signals.append({'ticker': 'CL', 'action': 'BUY', 'qty': 10})
                self.signals.append({'ticker': future, 'action': 'SELL', 'qty': 10})
                self.active_arb = {
                    'type': 'spot_fut',
                    'long': 'CL',
                    'short': future,
                    'long_entry': cl,
                    'short_entry': future_price,
                    'tick_entered': self.tick,
                    'storage_leased': 1
                }
                return

        elif cl - future_price > 0:
            direction = "short_CL"
            expected_pnl = self.estimate_expected_pnl_spot_fut(cl, future_price, days, direction)
            if expected_pnl > 100:
                self.signals.append({'ticker': 'CL', 'action': 'SELL', 'qty': 10})
                self.signals.append({'ticker': future, 'action': 'BUY', 'qty': 10})
                self.active_arb = {
                    'type': 'spot_fut',
                    'long': future,
                    'short': 'CL',
                    'long_entry': future_price,
                    'short_entry': cl,
                    'tick_entered': self.tick,
                    'storage_leased': 0
                }

    def check_exit(self):
        prices = self.session.get_prices()
        cl = prices.get('CL')
        cl1f = prices.get('CL-1F')
        cl2f = prices.get('CL-2F')

        if cl is None or cl1f is None or cl2f is None:
            return

        long_price = prices.get(self.active_arb['long'])
        short_price = prices.get(self.active_arb['short'])

        if long_price is None or short_price is None:
            return

        initial_spread = self.active_arb['long_entry'] - self.active_arb['short_entry']
        current_spread = long_price - short_price
        spread_change = current_spread - initial_spread
        pnl = spread_change * 1000 * 10

        hold_ticks = self.tick - self.active_arb['tick_entered']

        if self.period == 1 and self.tick >= 580 and 'CL-1F' in (self.active_arb['long'], self.active_arb['short']):
            self.exit_positions()
            return

        if pnl >= 200 or hold_ticks >= 60:
            self.exit_positions()

    def exit_positions(self):
        self.signals.append({'ticker': self.active_arb['long'], 'action': 'SELL', 'qty': 10})
        self.signals.append({'ticker': self.active_arb['short'], 'action': 'BUY', 'qty': 10})

        if self.active_arb.get('storage_leased', 0) > 0:
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
        if not prices:
            return 0, 0

        long_price = prices.get(self.active_arb['long'])
        short_price = prices.get(self.active_arb['short'])
        if long_price is None or short_price is None:
            return 0, 0

        current_spread = long_price - short_price
        initial_spread = self.active_arb['long_entry'] - self.active_arb['short_entry']
        spread_change = current_spread - initial_spread

        est_pnl = spread_change * 1000 * 10

        certainty = 0.4  

        return est_pnl, certainty
