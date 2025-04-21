import requests
import time

class RITSession:
    def __init__(self, api_key):
        self.session = requests.Session()
        self.session.headers.update({'X-API-Key': api_key})

    def get_tick(self):
        return self.session.get('http://localhost:9999/v1/case').json()['tick']

    def get_period(self):
        return self.session.get('http://localhost:9999/v1/case').json()['period']

    def get_prices(self):
        resp = self.session.get('http://localhost:9999/v1/securities')
        return {x['ticker']: x['last'] for x in resp.json()}

    def get_position(self, ticker):
        resp = self.session.get('http://localhost:9999/v1/securities')
        for sec in resp.json():
            if sec['ticker'] == ticker:
                return sec['position']
        return 0

    def place_order(self, ticker, side, qty, order_type='MARKET', price=0):
        return self.session.post('http://localhost:9999/v1/orders', params={
            'ticker': ticker,
            'type': order_type,
            'quantity': qty,
            'action': side,
            'price': price
        })

    def lease(self, ticker, **kwargs):
        return self.session.post('http://localhost:9999/v1/leases', params={'ticker': ticker, **kwargs})

    def release_lease(self, lease_id):
        return self.session.delete('http://localhost:9999/v1/leases/{}'.format(lease_id))

    def get_limits(self, CRUDE_TICKERS, PRODUCT_TICKERS):
        resp = self.session.get('http://localhost:9999/v1/securities')
        positions = {x['ticker']: x['position'] for x in resp.json()}

        gross = sum(abs(positions.get(tkr, 0)) for tkr in CRUDE_TICKERS + PRODUCT_TICKERS)
        net_crude = sum(positions.get(tkr, 0) for tkr in CRUDE_TICKERS)
        net_product = sum(positions.get(tkr, 0) for tkr in PRODUCT_TICKERS)

        return gross, net_crude, net_product

    def within_limits(self, ticker, action, qty, CRUDE_TICKERS, PRODUCT_TICKERS, GROSS_LIMIT, NET_LIMIT):
        gross, net_crude, net_product = self.get_limits(CRUDE_TICKERS, PRODUCT_TICKERS)

        sign = 1 if action == 'BUY' else -1

        if ticker in CRUDE_TICKERS:
            net_crude += sign * qty
        elif ticker in PRODUCT_TICKERS:
            net_product += sign * qty

        gross += qty

        if gross > GROSS_LIMIT:
            return False
        if abs(net_crude) > NET_LIMIT or abs(net_product) > NET_LIMIT:
            return False

        return True
