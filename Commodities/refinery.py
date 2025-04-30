# refinery.py

import time
from price_predictor import PricePredictor

class RefineryModel:
    def __init__(self, session, lease_manager, hedge_manager, get_cl_forecast):
        self.session = session
        self.lease_manager = lease_manager
        self.hedge_manager = hedge_manager
        self.predictor = PricePredictor(get_cl_forecast)
        self.signals = []
        self.lease_id = None
        self.refining = False
        self.refining_start_tick = None
        self.refining_abs_start_tick = None
        self.refinery_leased = False
        self.lease_tick_log = []
        self.holding_ho = False
        self.holding_rb = False
        self.hold_start_tick = None
        self.max_hold_ticks = 50
        self.last_hedge_qty = 0

    def update(self, tick, period):
        self.tick = tick
        self.period = period
        self.abs_tick = (period - 1) * 600 + tick

        prices = self.session.get_prices()
        self.predictor.update_last_prices(prices.get('CL'), prices.get('HO'), prices.get('RB'), self.tick)

        if self.abs_tick < 1170:
            self.ensure_refinery_leased()
            if not self.refining:
                self.start_refining_batch()
        elif self.abs_tick >= 1170 and self.refining is not True:
            if self.refinery_leased and self.lease_id:
                print(f"[tick {self.abs_tick}] Ending refinery lease {self.lease_id}")
                self.session.release_lease(self.lease_id)
                self.refinery_leased = False
                self.lease_id = None

        if self.refining:
            if self.abs_tick >= self.refining_abs_start_tick + 45:
                self.complete_refining_batch()

        self.clear_held_products_if_needed()

    def ensure_refinery_leased(self):
        if self.refinery_leased:
            return

        expected_pnl, _ = self.expected_profit()
        if expected_pnl < -75000:
            print(f"[tick {self.abs_tick}] Skipping refinery lease due to low expected PnL: {expected_pnl:.2f}")
            return

        print(f"[tick {self.abs_tick}] Leasing CL-REFINERY")

        for attempt in range(5):
            self.session.lease('CL-REFINERY')
            leases = self.session.session.get(
                'http://localhost:9999/v1/leases',
                params={'ticker': 'CL-REFINERY'}
            ).json()

            for x in leases:
                if x['ticker'] == 'CL-REFINERY':
                    self.lease_id = x['id']
                    self.refinery_leased = True
                    self.lease_tick_log.append((self.abs_tick, self.abs_tick + 45))
                    print(f"[tick {self.abs_tick}] Successfully obtained CL-REFINERY lease on attempt {attempt+1}")
                    return
                
            print(f"[tick {self.abs_tick}] Failed to obtain refinery lease (attempt {attempt+1}), retrying...")

            time.sleep(0.2)

    def start_refining_batch(self):
        positions = self.session.session.get('http://localhost:9999/v1/securities').json()
        cl_position = next((sec['position'] for sec in positions if sec['ticker'] == 'CL'), 0)

        if cl_position < 30:
            self.lease_manager.request_storage('CL-STORAGE', 3)
            self.session.place_order('CL', 'BUY', 30)
            return
        
        print(f"[tick {self.abs_tick}] Starting new refining batch")
        time.sleep(0.2)
        self.session.session.post(f'http://localhost:9999/v1/leases/{self.lease_id}', params={'from1': 'CL', 'quantity1': 30})

        # Hedging
        _, certainty = self.expected_profit()
        hedge_qty = self.hedge_manager.hedge_position(30, certainty)
        self.last_hedge_qty = hedge_qty

        self.refining = True
        self.refining_start_tick = self.tick
        self.refining_abs_start_tick = self.abs_tick

    def complete_refining_batch(self):
        hedge_ticker = 'CL-2F'

        if self.last_hedge_qty > 0:
            self.signals.append({
                'ticker': hedge_ticker,
                'action': 'BUY',
                'qty': self.last_hedge_qty,
                'note': 'Unwind refinery hedge'
            })
            self.last_hedge_qty = 0

        prediction = self.predictor.predict()
        pos = self.session.session.get('http://localhost:9999/v1/securities').json()
        ho_pos = next((x['position'] for x in pos if x['ticker'] == 'HO'), 0)
        rb_pos = next((x['position'] for x in pos if x['ticker'] == 'RB'), 0)

        total_product = ho_pos + 10 + rb_pos + 20

        self.hold_start_tick = self.abs_tick

        if total_product <= 100:
            if prediction['HO'] != 'up':
                self.signals.append({'ticker': 'HO', 'action': 'SELL', 'qty': 10, 'note': 'Sell HO after refining'})
                self.holding_ho = False
            else:
                self.holding_ho = True

            if prediction['RB'] != 'up':
                self.signals.append({'ticker': 'RB', 'action': 'SELL', 'qty': 20, 'note': 'Sell RB after refining'})
                self.holding_rb = False
            else:
                self.holding_rb = True
        else:
            self.signals.append({'ticker': 'HO', 'action': 'SELL', 'qty': 10, 'note': 'Sell HO (forced by product limit)'})
            self.signals.append({'ticker': 'RB', 'action': 'SELL', 'qty': 20, 'note': 'Sell RB (forced by product limit)'})
            self.holding_ho = False
            self.holding_rb = False

        self.refining = False

    def clear_held_products_if_needed(self):
        if not (self.holding_ho or self.holding_rb):
            return

        prediction = self.predictor.predict()
        should_force_expire = self.hold_start_tick is not None and self.abs_tick - self.hold_start_tick > self.max_hold_ticks

        if self.holding_ho and (prediction['HO'] != 'up' or should_force_expire):
            self.signals.append({'ticker': 'HO', 'action': 'SELL', 'qty': 10, 'note': 'Delayed sell of held HO'})
            self.holding_ho = False

        if self.holding_rb and (prediction['RB'] != 'up' or should_force_expire):
            self.signals.append({'ticker': 'RB', 'action': 'SELL', 'qty': 20, 'note': 'Delayed sell of held RB'})
            self.holding_rb = False

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

        crude_cost = 30 * cl * 1000
        product_revenue = (10 * ho * 42000) + (20 * rb * 42000)
        storage_cost = 3 * 500

        # Lease cost calculation
        total_ticks = sum(end - start for start, end in self.lease_tick_log)
        estimated_batches = max(1, total_ticks // 45)
        effective_lease_cost = (len(self.lease_tick_log) * 300000) / estimated_batches

        expected_profit = product_revenue - crude_cost - effective_lease_cost - storage_cost

        # Base dynamic certainty from margin
        margin = expected_profit / abs(crude_cost) if crude_cost else 0
        certainty = 0.5 + min(max(margin, -0.2), 0.2)

        # Confidence boost from price predictor
        price_trends = self.predictor.predict()
        if price_trends['HO'] == 'up' and price_trends['RB'] == 'up':
            certainty += 0.05
        elif price_trends['HO'] == 'down' and price_trends['RB'] == 'down':
            certainty -= 0.05

        # Additional boost if EIA report (or similar news) predicts strong CL uptrend
        if hasattr(self, 'fundamental_model'):
            cl_deltas = [x['delta'] for x in self.fundamental_model.delta_projections if x['ticker'] == 'CL']
            eia_strength = sum(d for d in cl_deltas if d > 0.1)  # EIA impact likely > 0.1
            if eia_strength > 0.2:
                certainty += 0.1

        # Final bounding
        certainty = max(0.3, min(certainty, 0.95))

        return expected_profit, certainty
        