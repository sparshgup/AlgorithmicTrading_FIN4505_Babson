import requests
import signal
import time
from collections import deque

########################################################
####################### API ############################
########################################################
API_KEY = {'X-API-Key': 'QDSFW62B'}
shutdown = False

class ApiException(Exception):
    pass

def signal_handler(signum, frame):
    global shutdown
    shutdown = True
    print("Shutting down...")

signal.signal(signal.SIGINT, signal_handler)
########################################################

def get_tick(session):
    resp = session.get('http://localhost:9999/v1/case')
    if not resp.ok:
        raise ApiException("Failed to fetch current tick")
    return resp.json()['tick']

def get_order_book(session, ticker):
    resp = session.get(f'http://localhost:9999/v1/securities/book', params={'ticker': ticker})
    if not resp.ok:
        raise ApiException(f"Error fetching book for {ticker}")
    return resp.json()

def get_position(session, ticker):
    resp = session.get('http://localhost:9999/v1/securities')
    if not resp.ok:
        raise ApiException("Error fetching positions")
    for security in resp.json():
        if security['ticker'] == ticker:
            return security['position']
    return 0

def submit_market_order(session, ticker, action, quantity):
    """
    Submits a MARKET order, chunked if necessary
    so that no single order exceeds 10,000 shares.
    """
    shares_left = quantity
    while shares_left > 0:
        trade_size = min(shares_left, 10000)
        order = {
            "ticker": ticker,
            "type": "MARKET",
            "quantity": trade_size,
            "action": action,
            "price": 0, 
        }
        resp = session.post('http://localhost:9999/v1/orders', params=order)
        if not resp.ok:
            raise ApiException(f"Failed to submit {action} MARKET order on {ticker}")
        print(f"Placed {action} MARKET order: {trade_size} shares of {ticker}")
        shares_left -= trade_size

def close_positions(session):
    """
    Convert all open positions to zero using 
    MARKET orders in chunks of <= 10,000 shares.
    """
    for ticker in ['CRZY_M', 'CRZY_A']:
        pos = get_position(session, ticker)
        if pos > 0:
            print(f"Flattening {pos} shares of {ticker} (SELL).")
            submit_market_order(session, ticker, 'SELL', pos)
        elif pos < 0:
            pos_abs = abs(pos)
            print(f"Flattening {pos_abs} shares of {ticker} (BUY).")
            submit_market_order(session, ticker, 'BUY', pos_abs)

#######################################
#       Arbitrage + Contrarian        #
#######################################
def market_arbitrage_and_contrarian(session, 
                                    rolling_data, 
                                    contrarian_threshold=0.5, 
                                    cross_threshold=0.02, 
                                    max_position=25000):
    """
    1) Detect crossed markets (using only MARKET orders).
    2) Add a basic contrarian signal:
       - If current best_ask is well above recent average, short.
       - If current best_bid is well below recent average, buy.
    rolling_data: Dict holding short sliding windows (deque)
    contrarian_threshold: Price difference from average that triggers contrarian trade
    cross_threshold: Minimum difference for cross-based arbitrage
    """

    ticker_main = 'CRZY_M'
    ticker_alt = 'CRZY_A'

    pos_main = get_position(session, ticker_main)
    pos_alt = get_position(session, ticker_alt)
    current_position = pos_main + pos_alt

    book_main = get_order_book(session, ticker_main)
    book_alt = get_order_book(session, ticker_alt)

    if (not book_main['bids'] or not book_main['asks'] or
        not book_alt['bids'] or not book_alt['asks']):
        return

    best_bid_main = book_main['bids'][0]['price']
    best_ask_main = book_main['asks'][0]['price']
    best_bid_alt = book_alt['bids'][0]['price']
    best_ask_alt = book_alt['asks'][0]['price']

    #==========================
    # 1) Crossed Market Check
    #==========================
    # If main bid is higher than alt ask by cross_threshold -> buy alt, sell main
    if (best_bid_main - best_ask_alt) > cross_threshold:
        trade_qty = min(book_main['bids'][0]['quantity'], 
                        book_alt['asks'][0]['quantity'],
                        max_position - abs(current_position)) 
        if trade_qty > 0:
            # Market Buy on Alt
            submit_market_order(session, ticker_alt, 'BUY', trade_qty)
            # Market Sell on Main
            submit_market_order(session, ticker_main, 'SELL', trade_qty)

    # If alt bid is higher than main ask by cross_threshold -> buy main, sell alt
    elif (best_bid_alt - best_ask_main) > cross_threshold:
        trade_qty = min(book_alt['bids'][0]['quantity'], 
                        book_main['asks'][0]['quantity'],
                        max_position - abs(current_position)) 
        if trade_qty > 0:
            # Market Buy on Main
            submit_market_order(session, ticker_main, 'BUY', trade_qty)
            # Market Sell on Alt
            submit_market_order(session, ticker_alt, 'SELL', trade_qty)

    #==========================
    # 2) Contrarian Signals
    #==========================
    # Track rolling average of best_ask and best_bid for a small lookback
    if ticker_main not in rolling_data:
        rolling_data[ticker_main] = {
            'asks': deque(maxlen=5),
            'bids': deque(maxlen=5)
        }
    if ticker_alt not in rolling_data:
        rolling_data[ticker_alt] = {
            'asks': deque(maxlen=5),
            'bids': deque(maxlen=5)
        }

    # Update rolling data
    rolling_data[ticker_main]['asks'].append(best_ask_main)
    rolling_data[ticker_main]['bids'].append(best_bid_main)
    rolling_data[ticker_alt]['asks'].append(best_ask_alt)
    rolling_data[ticker_alt]['bids'].append(best_bid_alt)

    # Compute averages
    avg_ask_main = sum(rolling_data[ticker_main]['asks']) / len(rolling_data[ticker_main]['asks'])
    avg_bid_main = sum(rolling_data[ticker_main]['bids']) / len(rolling_data[ticker_main]['bids'])
    avg_ask_alt  = sum(rolling_data[ticker_alt]['asks'])  / len(rolling_data[ticker_alt]['asks'])
    avg_bid_alt  = sum(rolling_data[ticker_alt]['bids'])  / len(rolling_data[ticker_alt]['bids'])

    # Basic contrarian example:
    # If best_ask_main is contrarian_threshold above its average, short it.
    # If best_bid_main is contrarian_threshold below its average, buy it.
    # (Similar logic for the alternate exchange.)
    # We keep the total trade size small to avoid big risk.

    contrarian_trade_size = 2000  # small chunk

    # MAIN: ask well above average -> short
    if (best_ask_main - avg_ask_main) > contrarian_threshold and abs(current_position) < max_position:
        trade_qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_main, 'SELL', trade_qty)

    # MAIN: bid well below average -> buy
    if (avg_bid_main - best_bid_main) > contrarian_threshold and abs(current_position) < max_position:
        trade_qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_main, 'BUY', trade_qty)

    # ALT: ask well above average -> short
    if (best_ask_alt - avg_ask_alt) > contrarian_threshold and abs(current_position) < max_position:
        trade_qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_alt, 'SELL', trade_qty)

    # ALT: bid well below average -> buy
    if (avg_bid_alt - best_bid_alt) > contrarian_threshold and abs(current_position) < max_position:
        trade_qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_alt, 'BUY', trade_qty)


def main():
    with requests.Session() as session:
        session.headers.update(API_KEY)
        rolling_data = {}

        while not shutdown:

            # Close positions
            tick = get_tick(session)
            if tick > 297:
                close_positions(session)
                break

            # Perform an arbitrage check + contrarian
            market_arbitrage_and_contrarian(session, rolling_data)

            # Delay between arbitrage
            # (if less than 0.4, then API times out submitting market orders)
            time.sleep(0.4)

if __name__ == '__main__':
    main()
