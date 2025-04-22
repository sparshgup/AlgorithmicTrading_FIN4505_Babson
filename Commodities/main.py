# main.py

from helpers import RITSession
from fundamental import FundamentalModel
from storage import StorageModel
from transport import TransportModel
from refinery import RefineryModel
import time

API_KEY = 'QDSFW62B'

CRUDE_TICKERS = ['CL', 'CL-AK', 'CL-NYC', 'CL-1F', 'CL-2F']
PRODUCT_TICKERS = ['HO', 'RB']

GROSS_LIMIT = 500
NET_LIMIT = 100

def main():
    session = RITSession(API_KEY)
    market_state = {
        'pipeline_costs': {
            'AK-CS-PIPE': 40000,
            'CS-NYC-PIPE': 20000
        }
    }

    models = [
        FundamentalModel(session, market_state),
        #StorageModel(session),
        TransportModel(session, market_state),
        #RefineryModel(session)
    ]

    while True:
        tick = session.get_tick()
        period = session.get_period()
        prices = session.get_prices()

        for model in models:
            model.update(tick, period)

        for model in models:
            trade = model.best_trade()
            if trade and session.within_limits(
                trade['ticker'], trade['action'], trade['qty'], 
                CRUDE_TICKERS, PRODUCT_TICKERS, GROSS_LIMIT, NET_LIMIT):
                print(f"[p{period}][tick {tick}] Executing: {trade}")
                session.place_order(trade['ticker'], trade['action'], trade['qty'])

        time.sleep(0.1)

if __name__ == "__main__":
    main()