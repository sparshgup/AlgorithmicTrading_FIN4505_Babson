import requests
import signal
import time

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


################################################################################
#################################### INFO ######################################
################################################################################
def get_tick(session):
    resp = session.get('http://localhost:9999/v1/case')
    if not resp.ok:
        raise ApiException("Failed to fetch current tick")
    return resp.json()['tick']

def get_tenders(session):
    resp = session.get('http://localhost:9999/v1/tenders')
    if not resp.ok:
        raise ApiException("Failed to fetch tenders")
    return resp.json()

def get_order_book(session, ticker):
    """Fetch order book for a ticker from both markets and compute VWAP"""
    book_main = session.get(f'http://localhost:9999/v1/securities/book', params={'ticker': ticker.replace("_A", "_M")}).json()
    book_alt = session.get(f'http://localhost:9999/v1/securities/book', params={'ticker': ticker.replace("_M", "_A")}).json()

    best_bid_m = book_main['bids'][0]['price'] if book_main['bids'] else None
    best_ask_m = book_main['asks'][0]['price'] if book_main['asks'] else None
    best_bid_a = book_alt['bids'][0]['price'] if book_alt['bids'] else None
    best_ask_a = book_alt['asks'][0]['price'] if book_alt['asks'] else None

    bid_volume_m = sum([bid['quantity'] for bid in book_main['bids']]) if book_main['bids'] else 0
    ask_volume_m = sum([ask['quantity'] for ask in book_main['asks']]) if book_main['asks'] else 0
    bid_volume_a = sum([bid['quantity'] for bid in book_alt['bids']]) if book_alt['bids'] else 0
    ask_volume_a = sum([ask['quantity'] for ask in book_alt['asks']]) if book_alt['asks'] else 0

    # Compute VWAP (Volume Weighted Average Price)
    vwap_bid = (sum([bid['price'] * bid['quantity'] for bid in book_main['bids']]) + sum([bid['price'] * bid['quantity'] for bid in book_alt['bids']])) / (bid_volume_m + bid_volume_a + 1e-9)
    vwap_ask = (sum([ask['price'] * ask['quantity'] for ask in book_main['asks']]) + sum([ask['price'] * ask['quantity'] for ask in book_alt['asks']])) / (ask_volume_m + ask_volume_a + 1e-9)

    return best_bid_m, best_ask_m, best_bid_a, best_ask_a, bid_volume_m + bid_volume_a, ask_volume_m + ask_volume_a, vwap_bid, vwap_ask

def get_inventory(session, ticker):
    resp = session.get('http://localhost:9999/v1/securities')
    if not resp.ok:
        raise ApiException(f"Failed to fetch inventory for {ticker}")

    securities = resp.json()
    for security in securities:
        if security['ticker'] == ticker:
            return security['position']
    return 0
################################################################################


##################################################################################
################################# TENDERS ########################################
##################################################################################
def accept_tender(session, tender):
    """Accept a tender offer and log details."""
    tender_id = tender['tender_id']
    ticker = tender['ticker']
    price = tender['price']
    action = tender['action']

    resp = session.post(f'http://localhost:9999/v1/tenders/{tender_id}')
    if not resp.ok:
        raise ApiException(f"Failed to accept tender {tender_id} for {ticker} at {price} ({action})")
    
    print(f"Accepted Tender {tender_id}: {ticker} {action} @ {price}")

def decline_tender(session, tender):
    """Decline a tender offer and log details."""
    tender_id = tender['tender_id']
    ticker = tender['ticker']
    price = tender['price']
    action = tender['action']

    resp = session.delete(f'http://localhost:9999/v1/tenders/{tender_id}')
    if not resp.ok:
        raise ApiException(f"Failed to decline tender {tender_id} for {ticker} at {price} ({action})")
    
    print(f"Declined Tender {tender_id}: {ticker} {action} @ {price}")

def evaluate_tender(session, tender):
    """Continuously evaluates a tender until it's accepted or declined."""
    ticker = tender['ticker']
    tender_price = tender['price']
    action = tender['action']
    
    # Parameters
    attempts = 0
    max_attempts=13
    threshold = 0.15
    evaluation_delay = 2

    while attempts < max_attempts:
        time.sleep(evaluation_delay)

        _, _, _, _, bid_volume, ask_volume, vwap_bid, vwap_ask = get_order_book(session, ticker)

        if action == "BUY" and tender_price < vwap_bid + threshold and bid_volume * 1.2 > ask_volume:
            accept_tender(session, tender)
            break
        elif action == "SELL" and tender_price > vwap_ask - threshold and ask_volume * 1.2 > bid_volume:
            accept_tender(session, tender)
            break
        
        attempts += 1
        print(f"Evaluating tender {tender['tender_id']} ({attempts}/{max_attempts})")

    if attempts == max_attempts:  
        decline_tender(session, tender)
##################################################################################

##################################################################################
################################### MAIN LOOP ####################################
##################################################################################
def main():
    with requests.Session() as session:
        session.headers.update(API_KEY)

        while not shutdown:
            tick = get_tick(session)

            # Evaluate and accept/reject tenders
            for tender in get_tenders(session):
                evaluate_tender(session, tender)

            time.sleep(1)  # Avoid excessive API calls

if __name__ == "__main__":
    main()
##################################################################################
