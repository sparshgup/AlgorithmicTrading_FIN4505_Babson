import signal
import requests
from time import sleep

class ApiException(Exception):
    pass

def signal_handler(signum, frame):
    global shutdown
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    shutdown = True

###############################################################################
# CONFIGURATION
###############################################################################

API_KEY = {'X-API-Key': 'QDSFW62B'}

# Overall time constraint
ENDTIME = 300 
BASE_SPEEDBUMP = 0.1

# Global position limit: 25k
GLOBAL_POSITION_LIMIT = 25000

# Keep track of rolling mid-prices per ticker
MID_PRICE_WINDOWS = {
    'CNR': [],
    'RY': [],
    'AC': []
}

TICKER_CONFIG = {
    'CNR': {
        'WINDOW_SIZE': 10,
        'TREND_UP_THRESHOLD':  0.20,
        'TREND_DOWN_THRESHOLD': -0.20,
        'TREND_VALUE': 0.05,        
        'ORDER_SIZE':  3000, 
        'REB_SIZE':    500,       
        'REB_LIMIT':   3000,     
        'MIN_SPREAD':  0.20,
        'IMPROVE':     0.02
    },
    'RY': {
        'WINDOW_SIZE': 10,
        'TREND_UP_THRESHOLD':  0.10,
        'TREND_DOWN_THRESHOLD': -0.10,
        'TREND_VALUE': 0.03,
        'ORDER_SIZE':  800, 
        'REB_SIZE':    400,
        'REB_LIMIT':   2500,
        'MIN_SPREAD':  0.10,
        'IMPROVE':     0.01
    },
    'AC': {
        'WINDOW_SIZE': 10,
        'TREND_UP_THRESHOLD':  0.15,
        'TREND_DOWN_THRESHOLD': -0.15,
        'TREND_VALUE': 0.04,
        'ORDER_SIZE':  1200, 
        'REB_SIZE':    600,
        'REB_LIMIT':   4000,
        'MIN_SPREAD':  0.10,
        'IMPROVE':     0.02
    }
}

###############################################################################
# Helper Functions
###############################################################################

def get_tick(session):
    """Fetch the current time/tick in the simulation."""
    resp = session.get('http://localhost:9999/v1/case')
    if not resp.ok:
        raise ApiException("Error in get_tick()")
    return resp.json()['tick']

def get_positions(session):
    """
    Returns a dict of { 'CNR': int_position, 'RY': int_position, 'AC': int_position } 
    by querying the /securities endpoint. 
    """
    resp = session.get('http://localhost:9999/v1/securities')
    if not resp.ok:
        raise ApiException("Could not get securities info")
    data = resp.json()

    pos_dict = {}
    for sec in data:
        tkr = sec['ticker']
        if tkr in ['CNR', 'RY', 'AC']:
            pos_dict[tkr] = sec['position']
    return pos_dict

def total_gross_position(positions):
    """
    Sum of absolute values for each ticker's position. 
    E.g. if we want 'gross' across all 3. 
    If you're using net, you might do abs(sum(positions.values())) instead.
    """
    return abs(positions['CNR']) + abs(positions['RY']) + abs(positions['AC'])

def ticker_bid_ask(session, ticker):
    """
    Returns best_bid, best_ask, entire_book for the given ticker.
    """
    resp = session.get('http://localhost:9999/v1/securities/book', params={'ticker': ticker})
    if not resp.ok:
        raise ApiException(f"Error getting book for {ticker}")
    book = resp.json()
    best_bid = book['bids'][0]['price'] if book['bids'] else None
    best_ask = book['asks'][0]['price'] if book['asks'] else None
    return best_bid, best_ask, book

def get_orders(session, status='OPEN'):
    """Returns all orders with a given status."""
    resp = session.get('http://localhost:9999/v1/orders', params={'status': status})
    if not resp.ok:
        raise ApiException("Error getting orders list")
    return resp.json()

def flatten_if_exceeded(session, positions):
    """
    If our total position is beyond the global limit, we flatten 
    or partially flatten across tickers as needed. 
    Since ALGO2e typically rejects trades that would break the limit, 
    this might be mostly defensive. 
    """
    gross = total_gross_position(positions)
    if gross <= GLOBAL_POSITION_LIMIT:
        return  # No action needed

    # If we do exceed, we can systematically flatten from the largest absolute position down.
    # Sort tickers by who is biggest in absolute position and flatten with a MARKET order.
    # This code snippet *immediately* tries to reduce the largest position first.
    sorted_by_abs = sorted(positions.items(), key=lambda x: abs(x[1]), reverse=True)
    for ticker, pos in sorted_by_abs:
        if abs(pos) == 0:
            continue
        # Example: If pos > 0, sell 'pos' shares; if pos < 0, buy abs(pos) shares
        action = 'SELL' if pos > 0 else 'BUY'
        qty = abs(pos)

        # Only flatten enough to get below the limit
        # e.g. if gross=26000, we only need to flatten 1000 shares in total. 
        # We'll do it from the largest position first. 
        amount_over = gross - GLOBAL_POSITION_LIMIT
        to_flatten = min(qty, amount_over)
        if to_flatten <= 0:
            continue

        session.post('http://localhost:9999/v1/orders',
                     params={
                         'ticker': ticker,
                         'type': 'MARKET',
                         'quantity': to_flatten,
                         'action': action
                     })
        sleep(BASE_SPEEDBUMP)  # small pause
        # Re-check positions
        new_positions = get_positions(session)
        gross = total_gross_position(new_positions)
        if gross <= GLOBAL_POSITION_LIMIT:
            return

###############################################################################
# Main Algorithm
###############################################################################

def main():
    global shutdown
    shutdown = False
    signal.signal(signal.SIGINT, signal_handler)

    with requests.Session() as s:
        s.headers.update(API_KEY)

        tick = get_tick(s)
        while tick < ENDTIME and not shutdown:
            positions = get_positions(s)
            flatten_if_exceeded(s, positions)  # just in case

            # Loop over each ticker we care about
            for ticker, cfg in TICKER_CONFIG.items():
                best_bid, best_ask, _ = ticker_bid_ask(s, ticker)
                if not best_bid or not best_ask:
                    # If book is empty, skip
                    continue

                # 1) Update rolling mid price
                mid_price = (best_bid + best_ask) / 2
                MID_PRICE_WINDOWS[ticker].append(mid_price)
                if len(MID_PRICE_WINDOWS[ticker]) > cfg['WINDOW_SIZE']:
                    MID_PRICE_WINDOWS[ticker].pop(0)

                # 2) Calculate slope if window is full
                slope = 0.0
                window_len = len(MID_PRICE_WINDOWS[ticker])
                if window_len == cfg['WINDOW_SIZE']:
                    oldest = MID_PRICE_WINDOWS[ticker][0]
                    newest = MID_PRICE_WINDOWS[ticker][-1]
                    slope = (newest - oldest) / cfg['WINDOW_SIZE']

                # 3) Trend-based price adjustment
                price_adjust = 0.0
                if slope > cfg['TREND_UP_THRESHOLD']:
                    price_adjust = +cfg['TREND_VALUE']
                elif slope < cfg['TREND_DOWN_THRESHOLD']:
                    price_adjust = -cfg['TREND_VALUE']

                # 4) Rebalance if local position is large (for *this ticker*).
                pos = positions[ticker]
                buy_quantity = cfg['ORDER_SIZE']
                sell_quantity = cfg['ORDER_SIZE']
                if pos > cfg['REB_LIMIT']:
                    buy_quantity = cfg['REB_SIZE']
                elif pos < -cfg['REB_LIMIT']:
                    sell_quantity = cfg['REB_SIZE']

                # 5) Check the spread
                spread = best_ask - best_bid
                if spread >= cfg['MIN_SPREAD']:
                    # We'll "improve" if the spread is big enough
                    buy_price  = best_bid + cfg['IMPROVE'] + price_adjust
                    sell_price = best_ask - cfg['IMPROVE'] + price_adjust
                else:
                    # If not wide, place them close to the inside
                    buy_price  = best_bid + price_adjust
                    sell_price = best_ask + price_adjust

                # 6) Only place symmetrical limit orders if they won't cross
                #    so that we collect passive rebates (rather than paying active fees).
                if buy_price < sell_price:
                    # But first ensure that placing these orders won't exceed global limit 
                    # if they get fully filled.
                    # For example, if pos is +10k and we do a 4k buy, we'd have 14k net in this ticker.
                    # Check other ticker positions to ensure total doesn't exceed 25k if all new orders fill.

                    # Hypothetical new total if both a buy *and* sell fill:
                    #   The net effect for "both filled" is zero net change.  We only risk extra position 
                    #   if one side fills but not the other. 
                    # For safety, check if buy side alone would push us over the limit:
                    hypothetical_buy_fill = pos + buy_quantity
                    # Then see if that combined with other positions would exceed limit
                    new_pos_dict = positions.copy()
                    new_pos_dict[ticker] = hypothetical_buy_fill
                    if total_gross_position(new_pos_dict) <= GLOBAL_POSITION_LIMIT:
                        # Place the buy limit 
                        s.post('http://localhost:9999/v1/orders',
                               params={
                                   'ticker': ticker,
                                   'type': 'LIMIT',
                                   'quantity': buy_quantity,
                                   'action': 'BUY',
                                   'price': buy_price
                               })
                    
                    # Check if the sell side alone would push us over. 
                    hypothetical_sell_fill = pos - sell_quantity
                    new_pos_dict[ticker] = hypothetical_sell_fill
                    if total_gross_position(new_pos_dict) <= GLOBAL_POSITION_LIMIT:
                        # Place the sell limit 
                        s.post('http://localhost:9999/v1/orders',
                               params={
                                   'ticker': ticker,
                                   'type': 'LIMIT',
                                   'quantity': sell_quantity,
                                   'action': 'SELL',
                                   'price': sell_price
                               })

            # 7) Housekeeping: if we have too many open orders, consider culling older ones, etc.
            open_orders = get_orders(s, 'OPEN')
            # Suppose we keep no more than 20 total across all tickers
            while len(open_orders) > 20:
                orderid = open_orders[-1]['order_id']
                s.delete(f'http://localhost:9999/v1/orders/{orderid}')
                sleep(BASE_SPEEDBUMP)
                open_orders = get_orders(s, 'OPEN')

            # 8) Sleep
            sleep(BASE_SPEEDBUMP)
            tick = get_tick(s)

if __name__ == '__main__':
    main()
