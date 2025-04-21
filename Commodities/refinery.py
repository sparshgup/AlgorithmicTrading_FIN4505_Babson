# refinery.py

import math

class RefineryModel:
    def __init__(self, session):
        self.session = session
        self.signals = []
        self.lease_id = None
        self.lease_end_tick = None
        self.refining = False
        self.refining_tick = None
        self.refining_abs_tick = None
        self.refine_count = 0
        self.max_refinements = 26
        self.storage_leased = False
        self.refinery_leased = False

    def update(self, tick, period):
        self.tick = tick
        self.period = period
        abs_tick = (period - 1) * 600 + tick

        if not self.refinery_leased:
            self.session.lease('CL-REFINERY')
            leases = self.session.session.get('http://localhost:9999/v1/leases', params={'ticker': 'CL-REFINERY'}).json()
            for x in leases:
                if x['ticker'] == 'CL-REFINERY':
                    self.lease_id = x['id']
                    self.lease_end_tick = x['next_lease_tick']
                    self.refinery_leased = True
                    break

        if not self.storage_leased:
            for _ in range(3):
                self.session.lease('CL-STORAGE')
            self.storage_leased = True

        if self.refining and abs_tick == self.refining_abs_tick + 45:
            hedge_ticker = 'CL-1F' if self.refining_abs_tick < 600 else 'CL-2F'
            self.signals.append({'ticker': hedge_ticker, 'action': 'BUY', 'qty': 30, 'note': 'Unwind hedge'})
            self.signals.append({'ticker': 'HO', 'action': 'SELL', 'qty': 10, 'note': 'Refined HO'})
            self.signals.append({'ticker': 'RB', 'action': 'SELL', 'qty': 20, 'note': 'Refined RB'})
            self.refining = False

        if not self.refining and self.refine_count < self.max_refinements:
            self.start_refinement(tick, period, abs_tick)

        if self.lease_id and tick == self.lease_end_tick - 1:
            self.session.release_lease(self.lease_id)
            self.lease_id = None
            self.lease_end_tick = None
            self.refinery_leased = False

    def start_refinement(self, tick, period, abs_tick):
        prices = self.session.get_prices()
        cl = prices.get('CL')
        ho = prices.get('HO')
        rb = prices.get('RB')
        if not cl or not ho or not rb:
            return

        self.session.place_order('CL', 'BUY', 30)

        positions = self.session.session.get('http://localhost:9999/v1/securities').json()
        for x in positions:
            if x['ticker'] == 'CL' and x['position'] < 30:
                return

        self.session.session.post(f'http://localhost:9999/v1/leases/{self.lease_id}', params={'from1': 'CL', 'quantity1': 30})

        hedge_ticker = 'CL-1F' if abs_tick < 600 else 'CL-2F'
        self.session.place_order(hedge_ticker, 'SELL', 30)

        self.refining_tick = tick
        self.refining_abs_tick = abs_tick
        self.refining = True
        self.refine_count += 1

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)
