# transport.py

import math

class TransportModel:
    def __init__(self, session, market_state):
        self.session = session
        self.market_state = market_state
        self.signals = []
        self.pending_transports = []
        self.active_storage_leases = set()
        self.active_routes = set()

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

        cost_ak_cs = self.market_state['pipeline_costs']['AK-CS-PIPE']
        cost_cs_nyc = self.market_state['pipeline_costs']['CS-NYC-PIPE']

        # AK to CL batching
        if cl_ak and cl:
            pipeline_cost = cost_ak_cs / 10000
            storage_cost = 0.10
            expected_profit = cl - (cl_ak + pipeline_cost + storage_cost)
            max_batches = 10
            for _ in range(max_batches):
                if expected_profit <= 0.4:
                    break
                if not self.check_storage_capacity('AK-STORAGE') or not self.check_position_limits('CL-AK', 10):
                    break
                route_id = f'AK->CL:{tick}'
                self.active_routes.add(route_id)
                self.session.lease('AK-STORAGE')
                self.session.place_order('CL-AK', 'BUY', 10)
                self.session.lease('AK-CS-PIPE', from1='CL-AK', quantity1=10)
                self.release_storage('AK-STORAGE')
                self.pending_transports.append({
                    'from': 'CL-AK', 'to': 'CL', 'qty': 10, 'entry_tick': tick, 'ticker': 'CL',
                    'pipeline': 'AK-CS-PIPE', 'release_to': 'CL-STORAGE', 'route_id': route_id,
                    'arrival_tick': tick + 30
                })

        # CL to NYC batching
        if cl and cl_nyc:
            pipeline_cost = cost_cs_nyc / 10000
            storage_cost = 0.10
            expected_profit = cl_nyc - (cl + pipeline_cost + storage_cost)
            max_batches = 10
            for _ in range(max_batches):
                if expected_profit <= 0.6:
                    break
                if not self.check_storage_capacity('CL-STORAGE') or not self.check_position_limits('CL', 10):
                    break
                route_id = f'CL->NYC:{tick}'
                self.active_routes.add(route_id)
                self.session.lease('CL-STORAGE')
                self.session.place_order('CL', 'BUY', 10)
                self.session.lease('CS-NYC-PIPE', from1='CL', quantity1=10)
                self.release_storage('CL-STORAGE')
                self.pending_transports.append({
                    'from': 'CL', 'to': 'CL-NYC', 'qty': 10, 'entry_tick': tick, 'ticker': 'CL-NYC',
                    'pipeline': 'CS-NYC-PIPE', 'release_to': 'NYC-STORAGE', 'route_id': route_id,
                    'arrival_tick': tick + 30
                })

    def check_storage_capacity(self, ticker):
        leases = self.session.session.get('http://localhost:9999/v1/leases').json()
        count = sum(1 for lease in leases if lease['ticker'] == ticker)
        return count < 10

    def check_position_limits(self, ticker, qty):
        crude = ['CL', 'CL-AK', 'CL-NYC', 'CL-1F', 'CL-2F']
        prod = ['HO', 'RB']
        gross, net_crude, net_prod = self.session.get_limits(crude, prod)
        if ticker in crude:
            net_crude += qty
        elif ticker in prod:
            net_prod += qty
        gross += qty
        return gross <= 500 and abs(net_crude) <= 100 and abs(net_prod) <= 100

    def release_storage(self, ticker):
        leases = self.session.session.get('http://localhost:9999/v1/leases').json()
        for lease in leases:
            if lease['ticker'] == ticker and lease['containment_usage'] == 0:
                self.session.release_lease(lease['id'])
                break

    def check_exit(self, tick):
        prices = self.session.get_prices()
        for t in self.pending_transports[:]:
            if tick < t.get('arrival_tick', 0):
                continue

            if tick - t['entry_tick'] >= 30:
                current_price = prices.get(t['ticker'])
                if current_price is None:
                    continue
                self.signals.append({
                    'ticker': t['ticker'], 'action': 'SELL', 'qty': t['qty'],
                    'note': f"Exit transport {t['from']}→{t['to']}"
                })
                if t.get('release_to'):
                    self.release_storage(t['release_to'])
                if t.get('route_id'):
                    self.active_routes.discard(t['route_id'])
                self.pending_transports.remove(t)

    def cleanup_expired_leases(self, tick):
        leases = self.session.session.get('http://localhost:9999/v1/leases').json()
        for lease in leases:
            if lease['containment_usage'] == 0:
                self.session.release_lease(lease['id'])

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)
