# master.py

from fundamental import FundamentalModel
from storage import StorageModel
from transport import TransportModel
from refinery import RefineryModel
from helpers import RITSession
import hedge_manager
import lease_manager
import event_scheduler
import time

CRUDE_TICKERS = ['CL', 'CL-AK', 'CL-NYC', 'CL-1F', 'CL-2F']
PRODUCT_TICKERS = ['HO', 'RB']

GROSS_LIMIT = 500
NET_LIMIT = 100

class MasterController:
    def __init__(self, api_key, sleep_time):
        self.session = RITSession(api_key)
        self.market_state = {
            'pipeline_costs': {
                'AK-CS-PIPE': 40000,
                'CS-NYC-PIPE': 20000
            }
        }
        self.lease_manager = lease_manager.LeaseManager(self.session)
        self.hedge_manager = hedge_manager.HedgeManager(self.session)

        self.fundamental_model = FundamentalModel(self.session, self.market_state, self.lease_manager)

        self.models = [
            #self.fundamental_model,
            StorageModel(self.session, self.lease_manager),
            #TransportModel(self.session, self.market_state, self.lease_manager, self.fundamental_model.get_cl_prediction),
            #RefineryModel(self.session, self.lease_manager, self.hedge_manager, self.fundamental_model.get_cl_forecast)
        ]

        self.hedge_manager = hedge_manager.HedgeManager(self.session)
        self.event_scheduler = event_scheduler.EventScheduler()
        self.sleep_time = sleep_time

    def run(self):
        while True:
            tick = self.session.get_tick()
            period = self.session.get_period()
            prices = self.session.get_prices()

            self.event_scheduler.update(tick, period)

            for model in self.models:
                model.update(tick, period)

            trade_candidates = []
            for model in self.models:
                trade = model.best_trade()
                if trade:
                    est_profit, certainty = model.expected_profit()
                    score = est_profit * certainty
                    trade_candidates.append((score, trade))

            trade_candidates.sort(reverse=True, key=lambda x: x[0])

            for score, trade in trade_candidates:
                if self.session.within_limits(
                    trade['ticker'], trade['action'], trade['qty'],
                    CRUDE_TICKERS, PRODUCT_TICKERS, GROSS_LIMIT, NET_LIMIT):
                    print(f"[p{period}][tick {tick}] Executing: {trade}")
                    self.session.place_order(trade['ticker'], trade['action'], trade['qty'])

            self.hedge_manager.manage(tick, period, prices)
            self.lease_manager.optimize(tick, prices)

            time.sleep(self.sleep_time)
