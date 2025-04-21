class StorageModel:
    def __init__(self, session):
        self.session = session
        self.signals = []
        self.active_arb = None

    def update(self, tick, period):
        self.tick = tick
        self.period = period
        if self.active_arb:
            self.check_exit()
        else:
            self.check_entry()

    def check_entry(self):
        prices = self.session.get_prices()
        cl = prices.get('CL')
        cl1f = prices.get('CL-1F')
        cl2f = prices.get('CL-2F')

        if cl is None or cl1f is None or cl2f is None:
            return

        future = 'CL-1F' if self.period == 1 else 'CL-2F'
        end_tick = 600 if self.period == 1 else 1200
        future_price = prices.get(future)

        ticks_per_day = 30
        days = (end_tick - self.tick) // ticks_per_day
        storage_cost = days * 0.05
        spread = future_price - cl

        if spread > storage_cost + 0.10:
            self.signals.append({'ticker': 'CL', 'action': 'BUY', 'qty': 10, 'note': f'{future} rich vs CL'})
            self.signals.append({'ticker': future, 'action': 'SELL', 'qty': 10, 'note': 'Hedge'})
            self.active_arb = {'long': 'CL', 'short': future}

        elif spread < -storage_cost - 0.10:
            self.signals.append({'ticker': 'CL', 'action': 'SELL', 'qty': 10, 'note': f'{future} cheap vs CL'})
            self.signals.append({'ticker': future, 'action': 'BUY', 'qty': 10, 'note': 'Hedge'})
            self.active_arb = {'long': future, 'short': 'CL'}

    def check_exit(self):
        prices = self.session.get_prices()
        cl = prices.get('CL')
        cl1f = prices.get('CL-1F')
        cl2f = prices.get('CL-2F')

        future = self.active_arb['short'] if self.active_arb['short'].startswith('CL-') else self.active_arb['long']
        future_price = prices.get(future)

        if cl is None or future_price is None:
            return

        spread = future_price - cl

        if self.active_arb['long'] == 'CL' and spread < 0.05:
            self.signals.append({'ticker': 'CL', 'action': 'SELL', 'qty': 10})
            self.signals.append({'ticker': future, 'action': 'BUY', 'qty': 10})
            self.active_arb = None

        elif self.active_arb['long'].startswith('CL-') and spread > -0.05:
            self.signals.append({'ticker': 'CL', 'action': 'BUY', 'qty': 10})
            self.signals.append({'ticker': future, 'action': 'SELL', 'qty': 10})
            self.active_arb = None

    def best_trade(self):
        if not self.signals:
            return None
        return self.signals.pop(0)
