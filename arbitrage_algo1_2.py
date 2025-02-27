import requests
import signal
import time
import statistics
from collections import deque

API_KEY = {'X-API-Key': 'QDSFW62B'}
shutdown = False

class ApiException(Exception):
    pass

def signal_handler(signum, frame):
    global shutdown
    shutdown = True
    print("Shutting down...")

signal.signal(signal.SIGINT, signal_handler)

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

#############################
#     Dynamic Thresholds    #
#############################
def compute_dynamic_thresholds(rolling_data, ticker_main, ticker_alt, base_contrarian=0.3, base_cross=0.01, tick=0):
    """
    Computes dynamic contrarian_threshold & cross_threshold
    based on:
      - The short-term volatility of bids/asks (std dev).
      - Time-of-day (tick-based).
    """
    # We'll retrieve the last few best_ask and best_bid values
    # to measure volatility. If there's not enough data, just
    # return the base values.
    def rolling_std(values):
        if len(values) < 2:  # Need at least 2 for stdev
            return 0.0
        return statistics.pstdev(values)  # population stdev

    # Time-of-day factor: As the day gets later (e.g., tick near 300),
    # we might want to reduce thresholds to be more aggressive (or the opposite).
    # We'll do a simple linear factor from 0 to 1 across ticks 0 -> 300.
    time_factor = min(tick / 300.0, 1.0)

    # Retrieve rolling data
    main_asks = rolling_data[ticker_main]['asks']
    main_bids = rolling_data[ticker_main]['bids']
    alt_asks  = rolling_data[ticker_alt]['asks']
    alt_bids  = rolling_data[ticker_alt]['bids']

    # Compute stdev across these
    vol_main_ask = rolling_std(main_asks)
    vol_main_bid = rolling_std(main_bids)
    vol_alt_ask  = rolling_std(alt_asks)
    vol_alt_bid  = rolling_std(alt_bids)

    avg_volatility = (vol_main_ask + vol_main_bid + vol_alt_ask + vol_alt_bid) / 4.0

    # Contrarian threshold: base + (some fraction of volatility) ± time factor
    contrarian_threshold = base_contrarian + (0.5 * avg_volatility) + (0.2 * time_factor)

    # Cross threshold: base + some fraction of volatility ± time factor
    cross_threshold = base_cross + (0.2 * avg_volatility) + (0.1 * time_factor)

    return contrarian_threshold, cross_threshold

#############################
#   Predictive Arbitrage    #
#############################
def detect_large_order_flow(rolling_data, ticker, current_ask_qty, current_bid_qty):
    """
    Example logic: If the ask or bid quantity has changed significantly
    from the previous iteration, we say there's large order flow
    on that side. We'll return a 'flow_factor' to indicate how
    aggressively we should reduce the cross_threshold for that ticker.

    The bigger the delta, the bigger the flow_factor (0 to 1).
    """
    # We'll store the last known best_ask_qty and best_bid_qty in rolling_data for each ticker.
    # If not present, initialize them.
    if 'last_ask_qty' not in rolling_data[ticker]:
        rolling_data[ticker]['last_ask_qty'] = current_ask_qty
    if 'last_bid_qty' not in rolling_data[ticker]:
        rolling_data[ticker]['last_bid_qty'] = current_bid_qty

    # Compute deltas
    delta_ask = abs(current_ask_qty - rolling_data[ticker]['last_ask_qty'])
    delta_bid = abs(current_bid_qty - rolling_data[ticker]['last_bid_qty'])

    # We'll define an arbitrary scale. If delta_ask or delta_bid > 5000 => flow_factor near 1
    # This is purely an example; tune to your environment.
    scale = 5000.0
    flow_factor = min(max(delta_ask, delta_bid) / scale, 1.0)

    # Update rolling_data for next iteration
    rolling_data[ticker]['last_ask_qty'] = current_ask_qty
    rolling_data[ticker]['last_bid_qty'] = current_bid_qty

    return flow_factor

#######################################
#       Arbitrage + Contrarian        #
#######################################
def market_arbitrage_and_contrarian(session, rolling_data, max_position=25000):
    """
    1) Detect crossed markets with dynamic cross_threshold.
    2) Add basic contrarian trades with dynamic contrarian_threshold.
    3) Add a predictive twist: if we detect large order flow
       on one exchange, lower the cross_threshold for that exchange
       to jump in earlier.
    """

    ticker_main = 'CRZY_M'
    ticker_alt = 'CRZY_A'

    pos_main = get_position(session, ticker_main)
    pos_alt  = get_position(session, ticker_alt)
    current_position = pos_main + pos_alt

    if abs(current_position) >= max_position:
        print("Max position reached; skipping new trades.")
        return

    book_main = get_order_book(session, ticker_main)
    book_alt  = get_order_book(session, ticker_alt)

    if (not book_main['bids'] or not book_main['asks'] or
        not book_alt['bids'] or not book_alt['asks']):
        return

    best_bid_main = book_main['bids'][0]['price']
    best_ask_main = book_main['asks'][0]['price']
    best_bid_alt  = book_alt['bids'][0]['price']
    best_ask_alt  = book_alt['asks'][0]['price']

    main_bid_qty = book_main['bids'][0]['quantity']
    main_ask_qty = book_main['asks'][0]['quantity']
    alt_bid_qty  = book_alt['bids'][0]['quantity']
    alt_ask_qty  = book_alt['asks'][0]['quantity']

    # Update rolling data with new prices
    if ticker_main not in rolling_data:
        rolling_data[ticker_main] = {'asks': deque(maxlen=5), 'bids': deque(maxlen=5)}
    if ticker_alt not in rolling_data:
        rolling_data[ticker_alt] = {'asks': deque(maxlen=5), 'bids': deque(maxlen=5)}

    rolling_data[ticker_main]['asks'].append(best_ask_main)
    rolling_data[ticker_main]['bids'].append(best_bid_main)
    rolling_data[ticker_alt]['asks'].append(best_ask_alt)
    rolling_data[ticker_alt]['bids'].append(best_bid_alt)

    #=================================================
    # Compute dynamic thresholds
    #=================================================
    current_tick = get_tick(session)
    contrarian_threshold, cross_threshold = compute_dynamic_thresholds(
        rolling_data, ticker_main, ticker_alt,
        base_contrarian=0.3,  # baseline contrarian
        base_cross=0.01,      # baseline cross threshold
        tick=current_tick
    )

    #====================================================
    # Predictive Arbitrage
    #====================================================
    flow_factor_main = detect_large_order_flow(rolling_data, ticker_main, main_ask_qty, main_bid_qty)
    flow_factor_alt  = detect_large_order_flow(rolling_data, ticker_alt, alt_ask_qty, alt_bid_qty)

    # Example usage: if flow_factor_main is 0.8, 
    # we reduce cross_threshold by e.g. 80% of some portion:
    # We'll reduce the cross_threshold by 50% * flow_factor.
    # So if cross_threshold was 0.03 and flow_factor_main=0.8, 
    # we reduce by 0.03 * 0.5 * 0.8 = 0.012 => cross_threshold ~ 0.018
    # We'll choose whichever is larger (main vs alt).
    biggest_flow_factor = max(flow_factor_main, flow_factor_alt)
    cross_threshold_reduction = cross_threshold * 0.5 * biggest_flow_factor
    cross_threshold -= cross_threshold_reduction
    cross_threshold = max(cross_threshold, 0.0001)

    #=========================================
    # 1) Crossed Market Check (market orders)
    #=========================================
    if (best_bid_main - best_ask_alt) > cross_threshold:
        trade_qty = min(main_bid_qty, alt_ask_qty, max_position - abs(current_position))
        if trade_qty > 0:
            submit_market_order(session, ticker_alt, 'BUY', trade_qty)
            submit_market_order(session, ticker_main, 'SELL', trade_qty)

    elif (best_bid_alt - best_ask_main) > cross_threshold:
        trade_qty = min(alt_bid_qty, main_ask_qty, max_position - abs(current_position))
        if trade_qty > 0:
            submit_market_order(session, ticker_main, 'BUY', trade_qty)
            submit_market_order(session, ticker_alt, 'SELL', trade_qty)

    #========================================
    # 2) Contrarian Signals (market orders)
    #========================================
    # Compute short rolling averages for each
    avg_ask_main = sum(rolling_data[ticker_main]['asks']) / len(rolling_data[ticker_main]['asks'])
    avg_bid_main = sum(rolling_data[ticker_main]['bids']) / len(rolling_data[ticker_main]['bids'])
    avg_ask_alt  = sum(rolling_data[ticker_alt]['asks'])  / len(rolling_data[ticker_alt]['asks'])
    avg_bid_alt  = sum(rolling_data[ticker_alt]['bids'])  / len(rolling_data[ticker_alt]['bids'])

    contrarian_trade_size = 2000  # small chunk

    # MAIN: ask well above average -> short
    if (best_ask_main - avg_ask_main) > contrarian_threshold and abs(current_position) < max_position:
        qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_main, 'SELL', qty)

    # MAIN: bid well below average -> buy
    if (avg_bid_main - best_bid_main) > contrarian_threshold and abs(current_position) < max_position:
        qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_main, 'BUY', qty)

    # ALT: ask well above average -> short
    if (best_ask_alt - avg_ask_alt) > contrarian_threshold and abs(current_position) < max_position:
        qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_alt, 'SELL', qty)

    # ALT: bid well below average -> buy
    if (avg_bid_alt - best_bid_alt) > contrarian_threshold and abs(current_position) < max_position:
        qty = min(contrarian_trade_size, max_position - abs(current_position))
        submit_market_order(session, ticker_alt, 'BUY', qty)


####################
# Main Loop
####################
def main():
    with requests.Session() as session:
        session.headers.update(API_KEY)

        # Rolling data for contrarian signals and predictive flows
        rolling_data = {}

        while not shutdown:

            # Close positions
            tick = get_tick(session)
            if tick > 297:
                close_positions(session)
                break

            # Perform an arbitrage check + contrarian + predictive
            market_arbitrage_and_contrarian(session, rolling_data)

            # Delay between arbitrage 
            # (if less than 0.4, then API times out submitting market orders)
            time.sleep(0.4) 

if __name__ == '__main__':
    main()
