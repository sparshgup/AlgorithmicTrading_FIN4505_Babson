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

TICKERS = ["CNR", "RY", "AC"]

# General
starttime           = 0
endtime             = 300
BASE_SPEEDBUMP      = 0.2
orderslimit         = 8
ordersize           = 4000
rebalancesize       = 500
rebalance_limit     = 4000
POSITION_LIMIT      = 25000

# Short-term trend detection
WINDOW_SIZE          = 10
TREND_UP_THRESHOLD   = 0.03
TREND_DOWN_THRESHOLD = -0.03

# Adaptive improvement
IMPROVE_AMOUNT       = 0.01
MIN_SPREAD_REQUIRED  = 0.03

# NBBO-based dynamic speedbump
NBBO_WINDOW      = 5       
HIGH_NBBO_CHANGE = 0.03    
LOW_NBBO_CHANGE  = 0.01    

# Rolling mid-price windows for slope detection, keyed by ticker
MID_PRICE_WINDOW = {ticker: [] for ticker in TICKERS}

# Rolling NBBO moves for dynamic speedbump, keyed by ticker
NBBO_MOVES       = {ticker: [] for ticker in TICKERS}

# Track previous best bid/ask for each ticker
prev_best_bid    = {ticker: None for ticker in TICKERS}
prev_best_ask    = {ticker: None for ticker in TICKERS}

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
    """
    Returns (best_bid, best_ask) for a given ticker.
    """
    payload = {'ticker': ticker}
    resp = session.get('http://localhost:9999/v1/securities/book', params=payload)
    if not resp.ok:
        raise ApiException(f"Error getting book for {ticker}")
    book = resp.json()
    best_bid = book['bids'][0]['price']
    best_ask = book['asks'][0]['price']
    return best_bid, best_ask

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

def flatten_excess_position(session, ticker, position):
    """
    If position > POSITION_LIMIT, or position < -POSITION_LIMIT,
    flatten the excess with a MARKET order.
    """
    if position > POSITION_LIMIT:
        excess = position - POSITION_LIMIT
        session.post('http://localhost:9999/v1/orders',
                     params={'ticker': ticker,
                             'type': 'MARKET',
                             'quantity': excess,
                             'action': 'SELL'})
    elif position < -POSITION_LIMIT:
        excess = abs(position) - POSITION_LIMIT
        session.post('http://localhost:9999/v1/orders',
                     params={'ticker': ticker,
                             'type': 'MARKET',
                             'quantity': excess,
                             'action': 'BUY'})

###############################################################################
# Main Algorithm
###############################################################################

def main():
    with requests.Session() as s:
        s.headers.update(API_KEY)

        tick = get_tick(s)

        # Run until endtime
        while tick < endtime and not shutdown:
            # We'll keep a global dynamic speedbump (just pick the max 
            # or average across all tickers) or just pick the min for demonstration.
            # Example: We'll use the maximum for a "worst-case" speedbump 
            # (meaning if any ticker is moving a lot, we slow down).
            all_speedbumps = []

            for ticker in TICKERS:
                best_bid, best_ask = ticker_bid_ask(s, ticker)
                cur_position = get_position(s, ticker)

                # 1) NBBO-based dynamic speedbump for this ticker
                # track how much the NBBO changed from last time
                if prev_best_bid[ticker] is not None and prev_best_ask[ticker] is not None:
                    bid_diff = abs(best_bid - prev_best_bid[ticker])
                    ask_diff = abs(best_ask - prev_best_ask[ticker])
                    nbbo_move = bid_diff + ask_diff
                    NBBO_MOVES[ticker].append(nbbo_move)
                    if len(NBBO_MOVES[ticker]) > NBBO_WINDOW:
                        NBBO_MOVES[ticker].pop(0)
                
                prev_best_bid[ticker] = best_bid
                prev_best_ask[ticker] = best_ask

                if NBBO_MOVES[ticker]:
                    avg_nbbo_move = sum(NBBO_MOVES[ticker]) / len(NBBO_MOVES[ticker])
                else:
                    avg_nbbo_move = 0.0

                if avg_nbbo_move > HIGH_NBBO_CHANGE:
                    ticker_speedbump = BASE_SPEEDBUMP * 2.0
                elif avg_nbbo_move < LOW_NBBO_CHANGE:
                    ticker_speedbump = BASE_SPEEDBUMP * 0.5
                else:
                    ticker_speedbump = BASE_SPEEDBUMP

                all_speedbumps.append(ticker_speedbump)

                # 2) Short-term slope detection 
                mid_price = (best_bid + best_ask) / 2.0
                MID_PRICE_WINDOW[ticker].append(mid_price)
                if len(MID_PRICE_WINDOW[ticker]) > WINDOW_SIZE:
                    MID_PRICE_WINDOW[ticker].pop(0)

                slope = 0.0
                if len(MID_PRICE_WINDOW[ticker]) == WINDOW_SIZE:
                    oldest_mid = MID_PRICE_WINDOW[ticker][0]
                    newest_mid = MID_PRICE_WINDOW[ticker][-1]
                    slope = (newest_mid - oldest_mid) / WINDOW_SIZE

                price_adjustment = 0.0
                if slope > TREND_UP_THRESHOLD:
                    price_adjustment = +0.01
                elif slope < TREND_DOWN_THRESHOLD:
                    price_adjustment = -0.01

                # 3) Rebalance logic
                buy_quantity  = ordersize
                sell_quantity = ordersize
                if cur_position > rebalance_limit:
                    buy_quantity = rebalancesize
                elif cur_position < -rebalance_limit:
                    sell_quantity = rebalancesize

                # 4) Compute final buy/sell limit prices
                spread = best_ask - best_bid
                if spread >= MIN_SPREAD_REQUIRED:
                    buy_price  = best_bid + IMPROVE_AMOUNT + price_adjustment
                    sell_price = best_ask - IMPROVE_AMOUNT + price_adjustment
                else:
                    buy_price  = best_bid + price_adjustment
                    sell_price = best_ask + price_adjustment

                # 5) Place single buy + sell limit if not crossing
                if buy_price < sell_price:
                    s.post('http://localhost:9999/v1/orders',
                           params={'ticker': ticker,
                                   'type': 'LIMIT',
                                   'quantity': buy_quantity,
                                   'action': 'BUY',
                                   'price': buy_price})

                    s.post('http://localhost:9999/v1/orders',
                           params={'ticker': ticker,
                                   'type': 'LIMIT',
                                   'quantity': sell_quantity,
                                   'action': 'SELL',
                                   'price': sell_price})

                # 6) Flatten if we exceed the 25,000 limit
                flatten_excess_position(s, ticker, cur_position)

                # 7) Cleanup if too many open orders
                open_orders = get_orders(s, 'OPEN')
                ticker_orders = [o for o in open_orders if o['ticker'] == ticker]
                while len(ticker_orders) > orderslimit:
                    orderid = ticker_orders[-1]['order_id']
                    s.delete(f'http://localhost:9999/v1/orders/{orderid}')
                    sleep(ticker_speedbump)
                    open_orders = get_orders(s, 'OPEN')
                    ticker_orders = [o for o in open_orders if o['ticker'] == ticker]

            # pick the largest or average speedbump across all tickers 
            # (in this example, we pick the max to be conservative)
            final_speedbump = max(all_speedbumps) if all_speedbumps else BASE_SPEEDBUMP
            sleep(final_speedbump)

            # update tick for next loop
            tick = get_tick(s)

if __name__ == '__main__':
    shutdown = False
    signal.signal(signal.SIGINT, signal_handler)
    main()
