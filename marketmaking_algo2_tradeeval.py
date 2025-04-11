import signal
import requests
from time import sleep

class ApiException(Exception):
    pass

def signal_handler(signum, frame):
    global shutdown
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    shutdown = True

API_KEY = {'X-API-Key': 'QDSFW62B'}
shutdown = False

###############################################################################
# PARAMETERS
###############################################################################

# General
orderslimit = 6
ordersize   = 4000
# 0.2 for 100% and 0.1 for 200%
BASE_SPEEDBUMP = 0.1

# Short-term trend detection
WINDOW_SIZE          = 10
TREND_UP_THRESHOLD   = 0.03
TREND_DOWN_THRESHOLD = -0.03
TREND_VALUE = 0.0075

# Adaptive improvement
IMPROVE_AMOUNT      = 0.01
MIN_SPREAD_REQUIRED = 0.03

###############################################################################


###############################################################################
# Fixed variables
###############################################################################
starttime = 0
endtime   = 300
POSITION_LIMIT = 24000
rebalancesize   = 500
rebalance_limit = 4000
MID_PRICE_WINDOW = []
prev_best_bid = None
prev_best_ask = None
###############################################################################


###############################################################################
# Helper functions
###############################################################################

def get_position(session, ticker):
    resp = session.get('http://localhost:9999/v1/securities')
    if not resp.ok:
        raise ApiException("Error getting securities info")
    data = resp.json()
    for sec in data:
        if sec['ticker'] == ticker:
            return sec['position']
    return 0

def ticker_bid_ask(session, ticker):
    payload = {'ticker': ticker}
    resp = session.get('http://localhost:9999/v1/securities/book', params=payload)
    if not resp.ok:
        raise ApiException(f"Error getting book for {ticker}")
    book = resp.json()
    best_bid = book['bids'][0]['price']
    best_ask = book['asks'][0]['price']
    return best_bid, best_ask, book

def get_tick(session):
    resp = session.get('http://localhost:9999/v1/case')
    if resp.status_code == 401:
        raise ApiException("Response error in get_tick")
    return resp.json()['tick']

def get_orders(session, status):
    payload = {'status': status}
    resp = session.get('http://localhost:9999/v1/orders', params=payload)
    if not resp.ok:
        raise ApiException("Error getting orders")
    return resp.json()

def flatten_excess_position(session, ticker_sym, position):
    if position > POSITION_LIMIT:
        excess = position - POSITION_LIMIT
        # Sell the excess
        session.post('http://localhost:9999/v1/orders',
                     params={'ticker': ticker_sym,
                             'type': 'MARKET',
                             'quantity': excess,
                             'action': 'SELL'})
    elif position < -POSITION_LIMIT:
        excess = abs(position) - POSITION_LIMIT
        # Buy the excess
        session.post('http://localhost:9999/v1/orders',
                     params={'ticker': ticker_sym,
                             'type': 'MARKET',
                             'quantity': excess,
                             'action': 'BUY'})
        
###############################################################################

###############################################################################
# Algorithm
###############################################################################

def main():
    global prev_best_bid, prev_best_ask
    with requests.Session() as s:
        s.headers.update(API_KEY)

        ticker_sym = 'ALGO'
        tick = get_tick(s)

        while tick < endtime and not shutdown:

            # 1) Get NBBO + Book
            best_bid, best_ask, book = ticker_bid_ask(s, ticker_sym)

            # 3) Position & short-term trend
            position = get_position(s, ticker_sym)

            mid_price = (best_bid + best_ask) / 2.0
            MID_PRICE_WINDOW.append(mid_price)
            if len(MID_PRICE_WINDOW) > WINDOW_SIZE:
                MID_PRICE_WINDOW.pop(0)

            slope = 0.0
            if len(MID_PRICE_WINDOW) == WINDOW_SIZE:
                oldest_mid = MID_PRICE_WINDOW[0]
                newest_mid = MID_PRICE_WINDOW[-1]
                slope = (newest_mid - oldest_mid) / WINDOW_SIZE

            price_adjustment = 0.0
            if slope > TREND_UP_THRESHOLD:
                price_adjustment = +TREND_VALUE
            elif slope < TREND_DOWN_THRESHOLD:
                price_adjustment = -TREND_VALUE

            # 4) Rebalance logic for normal size
            buy_quantity = ordersize
            sell_quantity = ordersize
            if position > rebalance_limit:
                buy_quantity = rebalancesize
            elif position < -rebalance_limit:
                sell_quantity = rebalancesize

            # 5) Compute final buy/sell price
            spread = best_ask - best_bid
            if spread >= MIN_SPREAD_REQUIRED:
                buy_price  = best_bid + IMPROVE_AMOUNT + price_adjustment
                sell_price = best_ask - IMPROVE_AMOUNT + price_adjustment
            else:
                buy_price  = best_bid + price_adjustment
                sell_price = best_ask + price_adjustment

            # place single buy+sell limit if not crossing
            if buy_price < sell_price:
                s.post('http://localhost:9999/v1/orders',
                       params={'ticker': ticker_sym,
                               'type': 'LIMIT',
                               'quantity': buy_quantity,
                               'action': 'BUY',
                               'price': buy_price})
                s.post('http://localhost:9999/v1/orders',
                       params={'ticker': ticker_sym,
                               'type': 'LIMIT',
                               'quantity': sell_quantity,
                               'action': 'SELL',
                               'price': sell_price})

            # 6) Flatten if we've gone above the POSITION_LIMIT
            flatten_excess_position(s, ticker_sym, position)

            # 8) Cleanup if orders exceed 'orderslimit'
            open_orders = get_orders(s, 'OPEN')
            while len(open_orders) > orderslimit:
                orderid = open_orders[-1]['order_id']
                s.delete(f'http://localhost:9999/v1/orders/{orderid}')
                sleep(BASE_SPEEDBUMP)
                open_orders = get_orders(s, 'OPEN')

            # 9) Sleep dynamic speedbump
            sleep(BASE_SPEEDBUMP)

###############################################################################

if __name__ == '__main__':
    shutdown = False
    signal.signal(signal.SIGINT, signal_handler)
    main()
