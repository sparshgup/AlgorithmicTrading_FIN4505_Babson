# transport.py

from collections import defaultdict

class TransportModel:
    def __init__(self, session, market_state, lease_manager, cl_prediction_func):
        self.session = session
        self.market_state = market_state
        self.lease_manager = lease_manager
        self.cl_prediction_func = cl_prediction_func 
        self.signals = []
        self.pending_transports = []
        self.active_storage_leases = set()
        self.in_flight = defaultdict(int)
        self.reserved_lease_ids = {}

    def update(self, tick, period):
        self.tick = tick
        self.period = period
        self.check_arbitrage(tick)
        self.lease_destination_storage(tick)
        self.check_exit(tick)

    def lease_destination_storage(self, tick):
        for t in self.pending_transports:
            if t.get('leased_dest'):
                continue
            if tick >= t['arrival_tick'] - 5:
                response = self.session.lease(t['release_to'])
                if response.ok:
                    lease_info = response.json()
                    t['lease_id'] = lease_info['id']
                    self.lease_manager.mark_reserved(lease_info['id'])
                    self.reserved_lease_ids[lease_info['id']] = tick
                    t['leased_dest'] = True

    def check_arbitrage(self, tick):
        prices = self.session.get_prices()
        cl_ak = prices.get('CL-AK')
        cl = prices.get('CL')
        cl_nyc = prices.get('CL-NYC')

        cost_ak_cs = self.market_state['pipeline_costs']['AK-CS-PIPE']
        cost_cs_nyc = self.market_state['pipeline_costs']['CS-NYC-PIPE']

        cl_pred = self.cl_prediction_func(tick)

        # AK → CL arbitrage
        if cl_ak and cl and cl_pred != 'down':  # don't buy if CL expected to fall
            route_id = 'AK->CL'
            profit = cl - (cl_ak + cost_ak_cs / 10000 + 0.10)
            while profit > 0.4 and self.in_flight[route_id] + 10 <= 100: 
                if not self.check_storage_capacity('AK-STORAGE') or not self.check_position_limits('CL-AK', 10):
                    break
                self.in_flight[route_id] += 10

                self.lease_manager.request_storage('AK-STORAGE', 1)
                self.session.place_order('CL-AK', 'BUY', 10)
                self.session.lease('AK-CS-PIPE', from1='CL-AK', quantity1=10)
                self.release_storage('AK-STORAGE')
                self.pending_transports.append({
                    'from': 'CL-AK', 'to': 'CL', 'qty': 10, 'ticker': 'CL',
                    'pipeline': 'AK-CS-PIPE', 'release_to': 'CL-STORAGE',
                    'entry_tick': tick, 'arrival_tick': tick + 30,
                    'route_id': route_id, 'leased_dest': False
                })

                print(f"[p{self.period}] [tick {self.tick}] Performing normal arbitrage from AK->CL")

        # CL → NYC arbitrage
        if cl and cl_nyc and cl_pred != 'up':  # don't ship if CL expected to rise
            route_id = 'CL->NYC'
            profit = cl_nyc - (cl + cost_cs_nyc / 10000 + 0.10)
            while profit > 0.6 and self.in_flight[route_id] + 10 <= 100: #0.6
                if not self.check_storage_capacity('CL-STORAGE') or not self.check_position_limits('CL', 10):
                    break
                self.in_flight[route_id] += 10

                self.lease_manager.request_storage('CL-STORAGE', 1)
                self.session.place_order('CL', 'BUY', 10)
                self.session.lease('CS-NYC-PIPE', from1='CL', quantity1=10)
                self.release_storage('CL-STORAGE')
                self.pending_transports.append({
                    'from': 'CL', 'to': 'CL-NYC', 'qty': 10, 'ticker': 'CL-NYC',
                    'pipeline': 'CS-NYC-PIPE', 'release_to': 'NYC-STORAGE',
                    'entry_tick': tick, 'arrival_tick': tick + 30,
                    'route_id': route_id, 'leased_dest': False
                })

                print(f"[p{self.period}] [tick {self.tick}] Performing normal arbitrage from AK->CL")

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
            if lease['ticker'] == ticker and lease['containment_usage'] == 0 and lease['id'] not in self.reserved_lease_ids:
                self.lease_manager.unmark_reserved(lease['id'])

    def check_exit(self, tick):
        prices = self.session.get_prices()
        for t in self.pending_transports[:]:
            if tick < t.get('arrival_tick', 0):
                continue

            if tick - t['entry_tick'] >= 31:
                current_price = prices.get(t['ticker'])
                if current_price is None:
                    continue
                self.signals.append({
                    'ticker': t['ticker'],
                    'action': 'SELL',
                    'qty': t['qty'],
                    'note': f"Exit transport {t['from']}→{t['to']}"
                })
                if t.get('lease_id'):
                    self.session.release_lease(t['lease_id'])
                    self.lease_manager.unmark_reserved(t['lease_id'])
                    self.reserved_lease_ids.pop(t['lease_id'], None)
                if t.get('route_id'):
                    self.in_flight[t['route_id']] -= t['qty']
                self.pending_transports.remove(t)

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)

    def expected_profit(self):
        if not self.pending_transports:
            return 0, 0

        prices = self.session.get_prices()
        if not prices:
            return 0, 0

        t = self.pending_transports[0]
        expected_sell_price = prices.get(t['ticker'])
        from_price = prices.get(t['from'])
        if expected_sell_price is None or from_price is None:
            return 0, 0

        pipeline_cost = self.market_state['pipeline_costs'][t['pipeline']] / 10000
        storage_cost = 0.10

        expected_profit = (expected_sell_price - (from_price + pipeline_cost + storage_cost)) * 1000 * t['qty']
        certainty = 0.5

        return expected_profit, certainty
