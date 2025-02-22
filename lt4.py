import requests
import signal
import time

########################################################
####################### API ############################
########################################################
class ApiException(Exception):
    pass

API_KEY = {'X-API-Key': 'QDSFW62B'}
shutdown = False

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


def evaluate_tender(session, tender, max_attempts=6):
    """Continuously evaluates a tender until it's accepted or declined."""
    ticker = tender['ticker']
    tender_price = tender['price']
    action = tender['action']
    
    attempts = 0
    threshold = 0.15

    while attempts < max_attempts:
        time.sleep(4)  # delay between evaluation

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

    time.sleep(2)
    place_aggressive_limit_orders(session, ticker, get_inventory(session, ticker))
##################################################################################


################################################################################################
########################################## ORDERS ##############################################
################################################################################################
def submit_limit_order(session, ticker, quantity, price, action):
    """Submit a limit order"""
    order = {
        "ticker": ticker,
        "type": "LIMIT",
        "quantity": quantity,
        "action": action,
        "price": price,
    }
    resp = session.post('http://localhost:9999/v1/orders', params=order)
    if not resp.ok:
        raise ApiException(f"Failed to place LIMIT order for {ticker} at {price}")
    print(f"Placed {action} LIMIT order: {quantity} @ {price} on {ticker}")

def submit_market_order(session, ticker, quantity, action):
    """Submit a market order"""
    while quantity > 0:
        order_size = min(10000, quantity)
        order = {
            "ticker": ticker,
            "type": "MARKET",
            "quantity": order_size,
            "action": action,
            "price": 0,
        }
        resp = session.post('http://localhost:9999/v1/orders', params=order)
        if not resp.ok:
            raise ApiException(f"Failed to place MARKET order for {ticker}")
        print(f"Placed {action} MARKET order: {order_size} on {ticker}")
        quantity -= order_size

def place_aggressive_limit_orders(session, ticker, inventory):
    """Places limit orders to match each individual order from the opposite side of the order book."""
    if inventory == 0:
        return 

    # Get market data
    book_main = session.get(f'http://localhost:9999/v1/securities/book', params={'ticker': ticker.replace("_A", "_M")}).json()
    book_alt = session.get(f'http://localhost:9999/v1/securities/book', params={'ticker': ticker.replace("_M", "_A")}).json()

    remaining_quantity = abs(inventory)
    alt_orders_placed = 0

    if inventory < 0:  # Buying
        ask_orders_main = book_main['asks'] if book_main['asks'] else []
        ask_orders_alt = book_alt['asks'] if book_alt['asks'] else []

        for ask in ask_orders_main + ask_orders_alt:
            if remaining_quantity <= 0:
                break 

            # match each order individually
            price = ask['price'] - 0.005
            order_size = min(remaining_quantity, ask['quantity'])

            # Choose market dynamically
            destination = "M" if ask in ask_orders_main else "A"
            if destination == "A" and alt_orders_placed >= 10:
                continue  # Skip A if already 10 orders placed

            ticker = f"{ticker[0:4]}_{destination}"
            submit_limit_order(session, ticker, order_size, price, "BUY")
            
            remaining_quantity -= order_size
            if destination == "A":
                alt_orders_placed += 1  # Track alternate exchange orders

            # delay for orders
            time.sleep(0.15)

    else:  # Selling
        bid_orders_main = book_main['bids'] if book_main['bids'] else []
        bid_orders_alt = book_alt['bids'] if book_alt['bids'] else []

        for bid in bid_orders_main + bid_orders_alt:
            if remaining_quantity <= 0:
                break  

            price = bid['price'] + 0.005
            order_size = min(remaining_quantity, bid['quantity']) 

            # Choose market dynamically
            destination = "M" if bid in bid_orders_main else "A"
            if destination == "A" and alt_orders_placed >= 10:
                continue # Skip A if already 10 orders placed

            ticker = f"{ticker[0:4]}_{destination}"
            submit_limit_order(session, ticker, order_size, price, "SELL")
            
            remaining_quantity -= order_size
            if destination == "A":
                alt_orders_placed += 1  # Track alternate exchange orders

            # delay for orders
            time.sleep(0.15)
##################################################################################

##################################################################################
################################## CLOSE POSITIONS ###############################
##################################################################################
def close_positions(session):
    """Final liquidation of any remaining inventory before trading ends."""
    securities = ["CRZY_M", "CRZY_A", "TAME_M", "TAME_A"]
    for ticker in securities:
        inventory = get_inventory(session, ticker)
        if inventory != 0:
            print("Closing all positions before trading ends.")

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
##################################################################################


##################################################################################
################################### MAIN LOOP ####################################
##################################################################################
def main():
    with requests.Session() as session:
        session.headers.update(API_KEY)

        while not shutdown:
            tick = get_tick(session)

            # Last ticks to close positions
            if tick > 297:
                close_positions(session)
                break

            # Evaluate and accept/reject tenders
            for tender in get_tenders(session):
                evaluate_tender(session, tender)

            time.sleep(1)  # Avoid excessive API calls

if __name__ == "__main__":
    main()
##################################################################################
