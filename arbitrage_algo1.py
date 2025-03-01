########################################################
# Case Assignment - ALGO1: Sparsh Gupta
########################################################
# Description:
#
# - The script checks for an arbitrage opportunity 
#   between CRZY_M and CRZY_A by comparing their 
#   best bids and asks.
#
# - If a cross (bid > ask) is detected, it uses threaded 
#   market orders to buy on the cheaper market and sell 
#   on the more expensive one, staying net zero.
#
# - A predictive flow factor lowers the threshold 
#   if one side’s order size jumps quickly.
#
# - It automatically stops after tick 299 and 
#   closes positions.
########################################################

import requests
import signal
import time
import statistics
import threading
from collections import deque

########################################################
# API
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
# Helper functions
########################################################
def get_tick(session):
    resp = session.get('http://localhost:9999/v1/case')
    if not resp.ok:
        raise ApiException("Failed to fetch current tick")
    return resp.json()['tick']

def get_order_book(session, ticker):
    resp = session.get(f'http://localhost:9999/v1/securities/book', 
                       params={'ticker': ticker})
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
    single-threaded version for smaller trades or contrarian logic.
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

def submit_market_orders_pair(session, ticker_buy, ticker_sell, 
                              total_qty_buy, total_qty_sell):
    """
    Places orders concurrently, chunking each side in increments of up to 10,000.
    This ensures both orders execute at the same time in each chunk.
    """
    buy_left = total_qty_buy
    sell_left = total_qty_sell

    while buy_left > 0 or sell_left > 0:
        chunk_buy = min(buy_left, 10000)
        chunk_sell = min(sell_left, 10000)

        threads = []
        if chunk_buy > 0:
            t_buy = threading.Thread(
                target=_submit_single_market_chunk,
                args=(session, ticker_buy, "BUY", chunk_buy)
            )
            threads.append(t_buy)
        if chunk_sell > 0:
            t_sell = threading.Thread(
                target=_submit_single_market_chunk,
                args=(session, ticker_sell, "SELL", chunk_sell)
            )
            threads.append(t_sell)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        buy_left -= chunk_buy
        sell_left -= chunk_sell

def _submit_single_market_chunk(session, ticker, action, quantity):
    """
    Helper function to place a single chunk market order
    """
    order = {
        "ticker": ticker,
        "type": "MARKET",
        "quantity": quantity,
        "action": action,
        "price": 0,
    }
    resp = session.post('http://localhost:9999/v1/orders', params=order)
    if not resp.ok:
        raise ApiException(f"Failed to submit {action} MARKET order on {ticker}")
    print(f"[Threaded] Placed {action} MARKET order: {quantity} on {ticker}")

def close_positions(session):
    """Final liquidation of any remaining inventory before trading ends."""
    securities = ["CRZY_M", "CRZY_A"]
    print("Closing all positions before trading ends.")
    for ticker in securities:
        inventory = get_position(session, ticker)
        if inventory != 0:
            action = "SELL" if inventory > 0 else "BUY"
            best_bid_m, best_ask_m, best_bid_a, best_ask_a, _, _, _, _ = get_order_book(session, ticker)

            # Select the best market to place market orders
            if action == "SELL":
                destination = "M" if best_bid_m > best_bid_a else "A"
                ticker = f"{ticker[0:4]}_{destination}"
            else:  # action == "BUY"
                destination = "M" if best_ask_m > best_ask_a else "A"
                ticker = f"{ticker[0:4]}_{destination}"

            submit_market_order(session, ticker, abs(inventory), action)

########################################################
# Dynamic threshold
########################################################
def compute_dynamic_threshold(rolling_data, ticker_main, ticker_alt,
                              base_cross=0.01, tick=0):
    """
    Adjust cross_threshold based on:
      - short-term volatility (std dev of recent bids/asks)
      - time of day (tick-based), making thresholds higher or lower
    """
    def rolling_std(values):
        if len(values) < 2:
            return 0.0
        return statistics.pstdev(values)

    # time_factor goes from 0 (start) to 1 (end)
    time_factor = min(tick / 300.0, 1.0)

    main_asks = rolling_data[ticker_main]['asks']
    main_bids = rolling_data[ticker_main]['bids']
    alt_asks  = rolling_data[ticker_alt]['asks']
    alt_bids  = rolling_data[ticker_alt]['bids']

    vol_main_ask = rolling_std(main_asks)
    vol_main_bid = rolling_std(main_bids)
    vol_alt_ask  = rolling_std(alt_asks)
    vol_alt_bid  = rolling_std(alt_bids)

    avg_volatility = (vol_main_ask + vol_main_bid + vol_alt_ask + vol_alt_bid) / 4.0

    # cross_threshold: base + factor from volatility/time
    cross_threshold = base_cross + (0.2 * avg_volatility) + (0.1 * time_factor)

    return cross_threshold

########################################################
# Predictive trade flow
########################################################
def detect_large_order_flow(rolling_data, ticker, current_ask_qty, current_bid_qty):
    """
    Looks for big changes in ask or bid quantity since last time.
    Returns a flow_factor (0 to 1) that indicates how large the change is.
    """
    if 'last_ask_qty' not in rolling_data[ticker]:
        rolling_data[ticker]['last_ask_qty'] = current_ask_qty
    if 'last_bid_qty' not in rolling_data[ticker]:
        rolling_data[ticker]['last_bid_qty'] = current_bid_qty

    delta_ask = abs(current_ask_qty - rolling_data[ticker]['last_ask_qty'])
    delta_bid = abs(current_bid_qty - rolling_data[ticker]['last_bid_qty'])

    # If delta_ask or delta_bid > 5000 => factor near 1
    scale = 5000
    flow_factor = min(max(delta_ask, delta_bid) / scale, 1.0)

    rolling_data[ticker]['last_ask_qty'] = current_ask_qty
    rolling_data[ticker]['last_bid_qty'] = current_bid_qty

    return flow_factor

########################################################
# Arbitrage
########################################################
def arbitrage(session, rolling_data, max_position=25000):
    """
    - cross threshold to detect arbitrage between CRZY_M and CRZY_A.
    - flow_factor to predict big moves and adapt the cross threshold.
    - for arbitrage crosses, use threaded pairs for near-simultaneous order execution.
    """

    ticker_main = 'CRZY_M'
    ticker_alt  = 'CRZY_A'

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

    # Initialize rolling_data if needed
    if ticker_main not in rolling_data:
        rolling_data[ticker_main] = {'asks': deque(maxlen=5), 'bids': deque(maxlen=5)}
    if ticker_alt not in rolling_data:
        rolling_data[ticker_alt] = {'asks': deque(maxlen=5), 'bids': deque(maxlen=5)}

    # Record recent ask/bid prices
    rolling_data[ticker_main]['asks'].append(best_ask_main)
    rolling_data[ticker_main]['bids'].append(best_bid_main)
    rolling_data[ticker_alt]['asks'].append(best_ask_alt)
    rolling_data[ticker_alt]['bids'].append(best_bid_alt)

    # Dynamic thresholds
    current_tick = get_tick(session)
    cross_threshold = compute_dynamic_threshold(
        rolling_data, 
        ticker_main, 
        ticker_alt,
        base_cross=0.01,
        tick=current_tick
    )

    # Predictive arbitrage
    flow_factor_main = detect_large_order_flow(rolling_data, ticker_main, main_ask_qty, main_bid_qty)
    flow_factor_alt  = detect_large_order_flow(rolling_data, ticker_alt, alt_ask_qty, alt_bid_qty)
    biggest_flow_factor = max(flow_factor_main, flow_factor_alt)
    cross_threshold -= cross_threshold * 0.5 * biggest_flow_factor
    cross_threshold = max(cross_threshold, 0.0001)

    # Check if markets are crossed
    if (best_bid_main - best_ask_alt) > cross_threshold:
        trade_qty = min(main_bid_qty, alt_ask_qty, max_position - abs(current_position))
        if trade_qty > 0:
            # buy on alt, sell on main
            submit_market_orders_pair(session, ticker_buy=ticker_alt, ticker_sell=ticker_main,
                                      total_qty_buy=trade_qty, total_qty_sell=trade_qty)
            
    elif (best_bid_alt - best_ask_main) > cross_threshold:
        trade_qty = min(alt_bid_qty, main_ask_qty, max_position - abs(current_position))
        if trade_qty > 0:
            # buy on main, sell on alt
            submit_market_orders_pair(session, ticker_buy=ticker_main, ticker_sell=ticker_alt,
                                      total_qty_buy=trade_qty, total_qty_sell=trade_qty)

########################################################
# Main
########################################################
def main():
    with requests.Session() as session:
        session.headers.update(API_KEY)
        rolling_data = {}

        while not shutdown:
            # stop arbitrage and close all positions after tick 299
            tick = get_tick(session)
            if tick > 299:
                close_positions(session)
                break

            arbitrage(session, rolling_data)

            # if less than 0.35, the API times out submitting market orders
            time.sleep(0.35)

if __name__ == '__main__':
    main()