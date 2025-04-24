# fundamental.py

import re
import math

PIPELINE_PATTERN = re.compile(
    r'PIPELINE COST FOR (.+?) (GOING UP TO|GOING DOWN TO|BACK TO) \$(\d{1,3}(?:,\d{3})*|\d+) PER LEASE',
    re.IGNORECASE
)

ROUTE_TO_TICKER = {
    'ALASKA TO CUSHING': 'AK-CS-PIPE',
    'CUSHING TO NYC': 'CS-NYC-PIPE'
}

class FundamentalModel:
    def __init__(self, session, market_state):
        self.session = session
        self.market_state = market_state
        self.last_tick = -1
        self.signals = []
        self.positions = []
        self.release_queue = []
        self.delta_projections = []
        self.processed_headlines = set()
        self.pending_release_after_exit = []

    def update(self, tick, period):
        if tick == self.last_tick:
            return
        self.last_tick = tick

        self.check_exit(tick)

        if not self.signals and self.pending_release_after_exit:
            for lease_id in self.pending_release_after_exit:
                self.session.release_lease(lease_id)
                print(f"[p{period}][tick {tick}] Releasing Lease: {lease_id}")
            self.pending_release_after_exit.clear()

        self.cleanup_deltas(tick)
        self.check_for_news()
        self.check_for_eia()
        self.check_for_pipeline_news()

    def check_for_pipeline_news(self):
        resp = self.session.session.get('http://localhost:9999/v1/news')
        for item in resp.json():
            if item['tick'] + 2 < self.session.get_tick() or item['period'] != self.session.get_period():
                continue
            headline = item['headline']
            if headline in self.processed_headlines:
                continue

            match = re.search(PIPELINE_PATTERN, headline)
            if match:
                route_str, action, price_str = match.groups()
                price = int(price_str.replace(',', ''))
                pipeline = ROUTE_TO_TICKER.get(route_str.upper())
                if pipeline:
                    old_cost = self.market_state['pipeline_costs'].get(pipeline, price)
                    self.market_state['pipeline_costs'][pipeline] = price
                    delta_cost = price - old_cost
                    delta_price = delta_cost / 100000  # ~0.01 per $1000 change

                    impacted_ticker = 'CL' if pipeline == 'AK-CS-PIPE' else 'CL-NYC'
                    self.delta_projections.append({
                        'ticker': impacted_ticker,
                        'delta': delta_price,
                        'decay_tick': self.last_tick + 20
                    })
                    print(f"[Pipeline news adjustment] {impacted_ticker} by {delta_price:.2f}")

                self.processed_headlines.add(headline)

    def check_for_eia(self):
        resp = self.session.session.get('http://localhost:9999/v1/news')
        for item in resp.json():
            if item['tick'] + 2 < self.session.get_tick() or item['period'] != self.session.get_period():
                continue
            headline = item['headline']
            if headline in self.processed_headlines:
                continue
            if 'WEEK' in headline and 'ACTUAL' in headline and 'FORECAST' in headline:
                expected, actual = self._parse_eia_report(headline)
                surprise = actual - expected
                direction = "BUY" if surprise < 0 else "SELL"
                confidence = abs(surprise * 0.10)
                qty = min(30, int(confidence * 100))
                projected_delta = -confidence if direction == "SELL" else confidence

                self.delta_projections.append({
                    'ticker': 'CL',
                    'delta': projected_delta,
                    'decay_tick': self.last_tick + 20
                })

                prices = self.session.get_prices()
                price = prices.get('CL') if direction == 'BUY' else prices.get('CL-2F')
                if not price:
                    continue

                if direction == 'BUY':
                    lease_cost_total = math.ceil(qty / 10) * 500
                    expected_gain = confidence * 1000 * qty
                    if expected_gain < lease_cost_total:
                        continue
                    for _ in range(math.ceil(qty / 10)):
                        self.session.lease('CL-STORAGE')
                    storage_leased = math.ceil(qty / 10)
                else:
                    storage_leased = 0

                ticker = 'CL' if direction == 'BUY' else 'CL-2F'
                self.signals.append({
                    'ticker': ticker,
                    'action': direction,
                    'qty': qty,
                    'note': f"EIA surprise: {surprise}M bbl"
                })
                self.positions.append({
                    'ticker': ticker,
                    'side': direction,
                    'qty': qty,
                    'entry_price': price,
                    'confidence': confidence,
                    'tick_entered': self.last_tick,
                    'storage_leased': storage_leased
                })
                self.processed_headlines.add(headline)

    def _parse_eia_report(self, headline):
        actual_sign = -1 if 'ACTUAL DRAW' in headline else 1
        forecast_sign = -1 if 'FORECAST DRAW' in headline else 1

        actual_match = re.search(r'ACTUAL (?:DRAW|BUILD) (\d+)', headline)
        forecast_match = re.search(r'FORECAST (?:DRAW|BUILD) (\d+)', headline)

        if actual_match and forecast_match:
            actual = actual_sign * int(actual_match.group(1))
            expected = forecast_sign * int(forecast_match.group(1))
            return expected, actual

        return 0, 0

    def check_exit(self, tick):
        prices = self.session.get_prices()
        remaining_positions = []

        for pos in self.positions:
            price = prices.get(pos['ticker'])
            if not price:
                remaining_positions.append(pos)
                continue

            pnl = (price - pos['entry_price']) if pos['side'] == 'BUY' else (pos['entry_price'] - price)

            if pnl >= pos['confidence'] or tick - pos['tick_entered'] > 28:
                exit_action = 'SELL' if pos['side'] == 'BUY' else 'BUY'
                self.signals.append({
                    'ticker': pos['ticker'],
                    'action': exit_action,
                    'qty': pos['qty'],
                    'note': f"Exit trade at PnL ${pnl:.2f}"
                })

                # Queue lease release after exit is executed
                if pos['ticker'] == 'CL' and pos['storage_leased'] > 0:
                    leases = self.session.session.get('http://localhost:9999/v1/leases').json()
                    count = 0
                    for lease in leases:
                        if lease['ticker'] == 'CL-STORAGE' and count < pos['storage_leased']:
                            self.pending_release_after_exit.append(lease['id'])
                            count += 1
            else:
                remaining_positions.append(pos)

        self.positions = remaining_positions

    def check_for_news(self):
        resp = self.session.session.get('http://localhost:9999/v1/news')
        for item in resp.json():
            if item['tick'] + 2 < self.session.get_tick() or item['period'] != self.session.get_period():
                continue
            headline = item['headline']
            if headline in self.processed_headlines:
                continue
            impact = self._estimate_news_impact(headline)
            if impact:
                self.delta_projections.append({
                    'ticker': 'CL',
                    'delta': impact,
                    'decay_tick': self.last_tick + 20
                })
                net_impact = sum([x['delta'] for x in self.delta_projections if x['ticker'] == 'CL'])

                direction = 'BUY' if net_impact > 0 else 'SELL'
                confidence = abs(net_impact)
                qty = min(30, int(confidence * 100))

                prices = self.session.get_prices()
                price = prices.get('CL') if direction == 'BUY' else prices.get('CL-2F')
                if not price:
                    continue

                if direction == 'BUY':
                    lease_cost_total = math.ceil(qty / 10) * 500
                    expected_gain = confidence * 1000 * qty
                    if expected_gain < lease_cost_total:
                        continue
                    for _ in range(math.ceil(qty / 10)):
                        self.session.lease('CL-STORAGE')
                    storage_leased = math.ceil(qty / 10)
                else:
                    storage_leased = 0

                ticker = 'CL' if direction == 'BUY' else 'CL-2F'
                self.signals.append({
                    'ticker': ticker,
                    'action': direction,
                    'qty': qty,
                    'note': f"News: {headline}"
                })
                self.positions.append({
                    'ticker': ticker,
                    'side': direction,
                    'qty': qty,
                    'entry_price': price,
                    'confidence': confidence,
                    'tick_entered': self.last_tick,
                    'storage_leased': storage_leased
                })
                self.processed_headlines.add(headline)

    def cleanup_deltas(self, tick):
        self.delta_projections = [d for d in self.delta_projections if d['decay_tick'] > tick]

    def _estimate_news_impact(self, headline):
        headline = headline.upper()
        if 'STRAIT OF HORMUZ' and 'TRAFFIC SLOWS' in headline:
            return 0.2
        if 'STRAIT OF HORMUZ' and 'READY TO DEFEND' in headline:
            return -0.2
        elif 'REPAIRS TO IMPERIAL OIL REFINERY' in headline:
            return 0.2 
        elif 'OFFSHORE DRILLING' in headline and 'HIGHER INSURANCE PREMIUMS' in headline:
            return 0.3
        elif 'REPAIRS SUCCESSFULLY COMPLETED AT IMPERIAL OIL REFINERY' in headline:
            return -0.2
        elif 'NEW OIL PROJECT IN NORTHWEST TERRITORIES' in headline:
            return -0.1
        elif 'INFLATION SLOWS DOWN' in headline:
            return 0.3
        elif 'PUNTLAND STATE OF SOMALIA' in headline:
            return 0.2
        elif 'CHINA BEGINS PRODUCTION ON NEW OIL SANDS' in headline:
            return 0.15
        elif 'NIGERIA TO INVEST' and 'IN NEW REFINERIES' in headline:
            return -0.1
        elif 'OPEC INCREASES OIL DEMAND FORECAST' in headline:
            return 0.1
        elif 'ECONOMISTS CONCERNED BY A RISE IN CONSUMER PRICES' in headline:
            return -0.1
        elif 'OPEC: TALKS OF NEW PRICE BAND' in headline:
            return 0.2
        elif 'METHANE BLOWOUT IN ALBERTA OIL RIG' in headline:
            return -0.2
        elif 'PEMEX INCREASES OUTPUT' in headline:
            return 0.1
        elif 'FIRST TRANSPORT FOR NEW' and 'PIPELINE' in headline:
            return -0.1
        elif 'EUR' and 'USD' and 'DROPS TO' and 'LOW' in headline:
            return -0.4
        elif 'EURO RECOVERS' in headline: 
            return 0.4
        elif 'IMF RAISES' and 'GAINS' in headline:
            return 0.8
        elif 'KELLOGG' and 'NEW BOARD MEMBERS' in headline:
            return -0.3
        elif 'TOYOTA' and 'SOLAR' and 'CARS' in headline:
            return -0.4
        elif 'GLOBAL STOCKS TUMBLE' in headline:
            return -0.4
        elif 'TENSION' and 'SUDAN OIL SHUTDOWN' in headline:
            return -0.4
        elif 'FLASH CRASH' in headline:
            return -0.2
        elif 'EXPERIENCES LARGE SLOW IN REGIONAL TRAVEL' in headline:
            return -0.5
        elif 'MARKETS SLIDE' and 'JOB REPORTS' in headline:
            return 0.1
        elif 'OIL EXTRACTION WORKERS' and 'STRIKE' in headline:
            return 0.1
        elif 'UNUSUAL WEATHER PATTERN FREEZES EUROPE' in headline:
            return 0.2 
        elif 'NIGERIAN GOVERNMENT REVOKES DRILLING RIGHTS' in headline:
            return 0.2
        elif 'PIRATES ATTACK' in headline:
            return 0.15
        elif 'EXTEREME WEATHER CONDITIONS' and 'PIPELINE DAMAGE' in headline:
            return -0.1
        elif 'BOMBING IN SYRIAN CAPITAL' in headline:
            return 0.1
        elif 'RUMORS OF DEPLETING RESOURCES' in headline:
            return -0.15
        elif 'US DOLLAR' and 'STRENGTHEN' in headline:
            return 0.2
        elif 'LARGE OIL WELLS FOUND' in headline:
            return -0.5
        elif 'TENSION AS NIGERIAN ELECTIONS UNDERWAY' in headline:
            return -0.2
        
        return 0

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)
