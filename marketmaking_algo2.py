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
orderslimit     = 8
ordersize       = 4000
rebalancesize   = 500
rebalance_limit = 4000 

# Short-term trend detection
WINDOW_SIZE = 10
TREND_UP_THRESHOLD   = 0.03
TREND_DOWN_THRESHOLD = -0.03

# Adaptive improvement
IMPROVE_AMOUNT     = 0.01
MIN_SPREAD_REQUIRED = 0.03

# NBBO-based dynamic speedbump
NBBO_WINDOW      = 5       
HIGH_NBBO_CHANGE = 0.03    
LOW_NBBO_CHANGE  = 0.01    

###############################################################################


###############################################################################
# Fixed variables
###############################################################################
starttime = 0
endtime   = 300
POSITION_LIMIT = 25000
BASE_SPEEDBUMP = 0.2
MID_PRICE_WINDOW = []
NBBO_MOVES = []
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
# Algorithmic warfare prevention
###############################################################################

SPOOF_SIZE_THRESHOLD   = 20000
SPOOF_DISAPPEAR_TICKS  = 2  
last_top_bid_qty       = None
last_top_ask_qty       = None
spoof_events           = [] 
spoof_suspect_count    = 0
CHANNEL_STUFF_THRESHOLD = 300

def detect_spoofing(book, tick):
    """
    If best bid or ask jumps by > SPOOF_SIZE_THRESHOLD from last iteration,
    record an event. If that size disappears within SPOOF_DISAPPEAR_TICKS, 
    increment suspect count.
    """
    global last_top_bid_qty, last_top_ask_qty, spoof_suspect_count

    top_bid_qty = book['bids'][0]['quantity']
    top_ask_qty = book['asks'][0]['quantity']

    # Check for big jump
    if last_top_bid_qty is not None and (top_bid_qty - last_top_bid_qty > SPOOF_SIZE_THRESHOLD):
        spoof_events.append({'tick': tick, 'side': 'BID', 'qty': top_bid_qty})
    if last_top_ask_qty is not None and (top_ask_qty - last_top_ask_qty > SPOOF_SIZE_THRESHOLD):
        spoof_events.append({'tick': tick, 'side': 'ASK', 'qty': top_ask_qty})

    # Check if these events vanished
    remove_list = []
    for evt in spoof_events:
        if tick - evt['tick'] >= SPOOF_DISAPPEAR_TICKS:
            # see if it vanished
            if evt['side'] == 'BID':
                if top_bid_qty < evt['qty'] / 2:
                    spoof_suspect_count += 1
            else:
                if top_ask_qty < evt['qty'] / 2:
                    spoof_suspect_count += 1
            remove_list.append(evt)

    for evt in remove_list:
        spoof_events.remove(evt)

    last_top_bid_qty = top_bid_qty
    last_top_ask_qty = top_ask_qty

def detect_channel_stuffing(total_open):
    """
    If total open orders exceed threshold, suspect channel stuffing.
    """
    return (total_open > CHANNEL_STUFF_THRESHOLD)
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

            # 1) Get NBBO + Book (and detect naive spoofing)
            best_bid, best_ask, book = ticker_bid_ask(s, ticker_sym)
            detect_spoofing(book, tick)

            # 2) NBBO dynamic speedbump
            if prev_best_bid is not None and prev_best_ask is not None:
                bid_diff = abs(best_bid - prev_best_bid)
                ask_diff = abs(best_ask - prev_best_ask)
                nbbo_move = bid_diff + ask_diff
                NBBO_MOVES.append(nbbo_move)
                if len(NBBO_MOVES) > NBBO_WINDOW:
                    NBBO_MOVES.pop(0)

            prev_best_bid = best_bid
            prev_best_ask = best_ask

            if NBBO_MOVES:
                avg_nbbo_move = sum(NBBO_MOVES) / len(NBBO_MOVES)
            else:
                avg_nbbo_move = 0.0

            if avg_nbbo_move > HIGH_NBBO_CHANGE:
                dynamic_speedbump = BASE_SPEEDBUMP * 2.0
            elif avg_nbbo_move < LOW_NBBO_CHANGE:
                dynamic_speedbump = BASE_SPEEDBUMP * 0.5
            else:
                dynamic_speedbump = BASE_SPEEDBUMP

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
                price_adjustment = +0.01
            elif slope < TREND_DOWN_THRESHOLD:
                price_adjustment = -0.01

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

            # if we've had multiple spoof suspects, widen quotes
            global spoof_suspect_count
            if spoof_suspect_count > 3:
                buy_price  -= 0.02
                sell_price += 0.02
                spoof_suspect_count = 0  # reset

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

            # 7) Channel stuffing detection
            open_orders = get_orders(s, 'OPEN')
            total_open_orders = len(open_orders)
            if total_open_orders > CHANNEL_STUFF_THRESHOLD:
                # if suspected, slow down further
                dynamic_speedbump *= 1.5

            # 8) Cleanup if orders exceed 'orderslimit'
            while len(open_orders) > orderslimit:
                orderid = open_orders[-1]['order_id']
                s.delete(f'http://localhost:9999/v1/orders/{orderid}')
                sleep(dynamic_speedbump)
                open_orders = get_orders(s, 'OPEN')

            # 9) Sleep dynamic speedbump
            sleep(dynamic_speedbump)

###############################################################################

if __name__ == '__main__':
    shutdown = False
    signal.signal(signal.SIGINT, signal_handler)
    main()
