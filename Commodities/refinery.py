# refinery.py

class RefineryModel:
    def __init__(self, session, lease_manager):
        self.session = session
        self.lease_manager = lease_manager
        self.signals = []
        self.lease_id = None
        self.refining = False
        self.refining_start_tick = None
        self.refining_abs_start_tick = None
        self.refinery_leased = False

    def update(self, tick, period):
        self.tick = tick
        self.period = period
        self.abs_tick = (period - 1) * 600 + tick

        self.ensure_refinery_leased()

        if self.refining:
            if self.abs_tick >= self.refining_abs_start_tick + 45:
                self.complete_refining_batch()
        else:
            self.start_refining_batch()

    def ensure_refinery_leased(self):
        if not self.refinery_leased:
            self.session.lease('CL-REFINERY')
            leases = self.session.session.get('http://localhost:9999/v1/leases', params={'ticker': 'CL-REFINERY'}).json()
            for x in leases:
                if x['ticker'] == 'CL-REFINERY':
                    self.lease_id = x['id']
                    self.refinery_leased = True
                    break

    def start_refining_batch(self):
        positions = self.session.session.get('http://localhost:9999/v1/securities').json()
        cl_position = next((sec['position'] for sec in positions if sec['ticker'] == 'CL'), 0)

        if cl_position < 30:
            self.lease_manager.request_storage('CL-STORAGE', 3)
            self.session.place_order('CL', 'BUY', 30)
            return

        self.session.session.post(f'http://localhost:9999/v1/leases/{self.lease_id}', params={'from1': 'CL', 'quantity1': 30})
        hedge_ticker = 'CL-2F'
        self.session.place_order(hedge_ticker, 'SELL', 30)

        self.refining = True
        self.refining_start_tick = self.tick
        self.refining_abs_start_tick = self.abs_tick

    def complete_refining_batch(self):
        hedge_ticker = 'CL-1F' if self.refining_abs_start_tick < 600 else 'CL-2F'

        self.signals.append({'ticker': hedge_ticker, 'action': 'BUY', 'qty': 30, 'note': 'Unwind refinery hedge'})
        self.signals.append({'ticker': 'HO', 'action': 'SELL', 'qty': 10, 'note': 'Sell HO after refining'})
        self.signals.append({'ticker': 'RB', 'action': 'SELL', 'qty': 20, 'note': 'Sell RB after refining'})

        self.refining = False

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)

    def expected_profit(self):
        prices = self.session.get_prices()
        if not prices:
            return 0, 0

        cl = prices.get('CL')
        ho = prices.get('HO')
        rb = prices.get('RB')
        if not cl or not ho or not rb:
            return 0, 0

        refinery_cost = 300000
        crude_cost = 30 * cl * 1000
        product_revenue = (10 * ho * 42000) + (20 * rb * 42000)
        storage_cost = 3 * 500

        expected_profit = product_revenue - crude_cost - refinery_cost - storage_cost
        certainty = 0.7
        return expected_profit, certainty
