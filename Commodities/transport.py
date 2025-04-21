# transport.py

import math

class TransportModel:
    def __init__(self, session):
        self.session = session
        self.signals = []
        self.pending_transports = []
        self.pipeline_costs = {'AK-CS-PIPE': 40000, 'CS-NYC-PIPE': 20000}
        self.lease_ages = []

    def update(self, tick, period):
        self.tick = tick
        self.period = period
        self.check_arbitrage(tick)
        self.check_exit(tick)
        self.cleanup_expired_leases(tick)

    def check_arbitrage(self, tick):
        prices = self.session.get_prices()
        cl_ak = prices.get('CL-AK')
        cl = prices.get('CL')
        cl_nyc = prices.get('CL-NYC')

        storage_cost = 0.10  # $0.10 per day (30 ticks)

        if cl_ak and cl:
            total_cost = (self.pipeline_costs['AK-CS-PIPE'] / 10000) + 2 * storage_cost
            profit_ak_to_cl = cl - cl_ak - total_cost
            if profit_ak_to_cl > 0.25:
                self.execute_transport('CL-AK', 'CL', 10, 'AK-STORAGE', 'CL-STORAGE', 'AK-CS-PIPE', tick)

        if cl and cl_nyc:
            total_cost = (self.pipeline_costs['CS-NYC-PIPE'] / 10000) + 2 * storage_cost
            profit_cl_to_nyc = cl_nyc - cl - total_cost
            if profit_cl_to_nyc > 0.25:
                self.execute_transport('CL', 'CL-NYC', 10, 'CL-STORAGE', 'NYC-STORAGE', 'CS-NYC-PIPE', tick)

        if cl_ak and cl_nyc:
            total_pipeline = (self.pipeline_costs['AK-CS-PIPE'] + self.pipeline_costs['CS-NYC-PIPE']) / 10000
            total_cost = total_pipeline + 3 * storage_cost
            profit_ak_to_nyc = cl_nyc - cl_ak - total_cost
            if profit_ak_to_nyc > 0.30:
                self.execute_transport('CL-AK', 'CL', 10, 'AK-STORAGE', 'CL-STORAGE', 'AK-CS-PIPE', tick, chain_next=True)
                self.pending_transports.append({
                    'chain_next': True,
                    'from': 'CL',
                    'to': 'CL-NYC',
                    'qty': 10,
                    'tick_ready': tick + 30,
                    'pipeline': 'CS-NYC-PIPE',
                    'to_storage': 'NYC-STORAGE',
                    'final_dest': 'CL-NYC'
                })

    def execute_transport(self, from_ticker, to_ticker, qty, from_storage, to_storage, pipeline, tick, chain_next=False):
        if from_storage:
            self.session.lease(from_storage)
            self.lease_ages.append({'id': None, 'tick_acquired': tick, 'storage': from_storage})

        self.session.place_order(from_ticker, 'BUY', qty)
        self.session.lease(pipeline, from1=from_ticker, quantity1=qty)

        if to_storage:
            self.lease_ages.append({'id': None, 'tick_acquired': tick, 'storage': to_storage})

        self.pending_transports.append({
            'from': from_ticker,
            'to': to_ticker,
            'qty': qty,
            'entry_tick': tick,
            'ticker': to_ticker,
            'pipeline': pipeline,
            'auto_storage': to_storage,
            'chain_next': chain_next
        })

    def check_exit(self, tick):
        prices = self.session.get_prices()
        for t in self.pending_transports[:]:
            if t.get('chain_next') and tick >= t['tick_ready']:
                self.session.lease(t['pipeline'], from1=t['from'], quantity1=t['qty'])
                if t.get('to_storage'):
                    self.lease_ages.append({'id': None, 'tick_acquired': tick, 'storage': t['to_storage']})
                self.pending_transports.remove(t)
                continue

            if tick - t['entry_tick'] >= 30:
                current_price = prices.get(t['ticker'])
                if current_price is None:
                    continue
                self.signals.append({
                    'ticker': t['ticker'],
                    'action': 'SELL',
                    'qty': t['qty'],
                    'note': f"Exit transport {t['from']}→{t['to']}"
                })
                self.pending_transports.remove(t)

    def cleanup_expired_leases(self, tick):
        leases = self.session.session.get('http://localhost:9999/v1/leases').json()
        for lease in leases:
            for tracked in self.lease_ages[:]:
                if lease['ticker'] == tracked['storage'] and tick - tracked['tick_acquired'] >= 29:
                    if lease['containment_usage'] == 0:
                        self.session.release_lease(lease['id'])
                        self.lease_ages.remove(tracked)

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)
