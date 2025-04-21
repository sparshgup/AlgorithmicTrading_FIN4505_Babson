# fundamental.py

import re
import math

class FundamentalModel:
    def __init__(self, session):
        self.session = session
        self.last_tick = -1
        self.signals = []
        self.active_position = None

    def update(self, tick, period):
        if tick == self.last_tick:
            return
        self.last_tick = tick

        if self.active_position:
            self.check_exit(tick)
        elif self.active_position is None and hasattr(self, 'release_queue') and self.release_queue:
            for lease_id in self.release_queue:
                self.session.release_lease(lease_id)
                print(f"[p{period}][tick {tick}] Releasing Lease: {lease_id}")
            self.release_queue.clear()
        else:
            self.check_for_news()
            self.check_for_eia()

    def check_for_eia(self):
        resp = self.session.session.get('http://localhost:9999/v1/news')
        for item in resp.json():
            if item['tick'] + 2 < self.session.get_tick() or item['period'] != self.session.get_period():
                continue
            headline = item['headline']
            if 'WEEK' in headline and 'ACTUAL' in headline and 'FORECAST' in headline:
                expected, actual = self._parse_eia_report(headline)
                surprise = actual - expected
                direction = "BUY" if surprise < 0 else "SELL"
                confidence = abs(surprise * 0.10)
                qty = min(30, int(confidence * 100))
                prices = self.session.get_prices()
                price = prices.get('CL')

                if direction == "BUY":
                    tanks_needed = math.ceil(qty / 10)
                    for _ in range(tanks_needed):
                        self.session.lease('CL-STORAGE')
                    if price:
                        self.signals.append({
                            'ticker': 'CL',
                            'action': 'BUY',
                            'qty': qty,
                            'note': f"EIA surprise: {surprise}M bbl"
                        })
                        self.active_position = {
                            'ticker': 'CL',
                            'side': direction,
                            'qty': qty,
                            'entry_price': price,
                            'confidence': confidence,
                            'tick_entered': self.last_tick,
                            'storage_leased': tanks_needed
                        }
                else:
                    price = prices.get('CL-2F')
                    if price:
                        self.signals.append({
                            'ticker': 'CL-2F',
                            'action': 'SELL',
                            'qty': qty,
                            'note': f"EIA surprise: {surprise}M bbl"
                        })
                        self.active_position = {
                            'ticker': 'CL-2F',
                            'side': 'SELL',
                            'qty': qty,
                            'entry_price': price,
                            'confidence': confidence,
                            'tick_entered': self.last_tick,
                            'storage_leased': 0
                        }

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
        pos = self.active_position
        price = prices.get(pos['ticker'])
        if not price:
            return

        pnl = (price - pos['entry_price']) if pos['side'] == 'BUY' else (pos['entry_price'] - price)

        if pnl >= pos['confidence'] or tick - pos['tick_entered'] > 28:
            exit_action = 'SELL' if pos['side'] == 'BUY' else 'BUY'
            self.signals.append({
                'ticker': pos['ticker'],
                'action': exit_action,
                'qty': pos['qty'],
                'note': f"Exit fundamental trade at PnL ${pnl:.2f}"
            })

            if pos['ticker'] == 'CL' and pos['storage_leased'] > 0:
                self.release_queue = []
                leases = self.session.session.get('http://localhost:9999/v1/leases').json()
                count = 0
                for lease in leases:
                    if lease['ticker'] == 'CL-STORAGE' and count < pos['storage_leased']:
                        self.release_queue.append(lease['id'])
                        count += 1

            self.active_position = None

    def check_for_news(self):
        resp = self.session.session.get('http://localhost:9999/v1/news')
        for item in resp.json():
            if item['tick'] + 2 < self.session.get_tick() or item['period'] != self.session.get_period():
                continue
            headline = item['headline']
            impact = self._estimate_news_impact(headline)
            if impact:
                direction = 'BUY' if impact > 0 else 'SELL'
                confidence = abs(impact)
                qty = min(30, int(confidence * 100))
                prices = self.session.get_prices()
                price = prices.get('CL') if direction == 'BUY' else prices.get('CL-2F')
                if not price:
                    return
                ticker = 'CL' if direction == 'BUY' else 'CL-2F'
                self.signals.append({
                    'ticker': ticker,
                    'action': direction,
                    'qty': qty,
                    'note': f"News: {headline}"
                })
                storage_leased = 0
                if ticker == 'CL':
                    tanks = math.ceil(qty / 10)
                    for _ in range(tanks):
                        self.session.lease('CL-STORAGE')
                    storage_leased = tanks
                self.active_position = {
                    'ticker': ticker,
                    'side': direction,
                    'qty': qty,
                    'entry_price': price,
                    'confidence': confidence,
                    'tick_entered': self.last_tick,
                    'storage_leased': storage_leased
                }

    def _estimate_news_impact(self, headline):
        return 0

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)