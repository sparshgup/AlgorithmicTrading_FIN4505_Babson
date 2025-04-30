# price_predictor.py

class PricePredictor:
    def __init__(self, get_cl_forecast = None):
        self.history = {'CL': [], 'HO': [], 'RB': []}
        self.max_len = 30
        self.short_len = 5
        self.last_prediction = {'HO': None, 'RB': None}
        self.up_counter = {'HO': 0, 'RB': 0}
        self.get_cl_forecast = get_cl_forecast
        self.current_tick = 0

    def update_last_prices(self, cl_price, ho_price, rb_price, tick: int):
        self.current_tick = tick
        if cl_price is not None:
            self.history['CL'].append(cl_price)
            if len(self.history['CL']) > self.max_len:
                self.history['CL'].pop(0)
        if ho_price is not None:
            self.history['HO'].append(ho_price)
            if len(self.history['HO']) > self.max_len:
                self.history['HO'].pop(0)
        if rb_price is not None:
            self.history['RB'].append(rb_price)
            if len(self.history['RB']) > self.max_len:
                self.history['RB'].pop(0)

    def trend(self, prices, window):
        if len(prices) < window:
            return 0.0
        return (prices[-1] - prices[-window]) / window

    def predict(self):
        pred = {}

        # Integrate fundamental forecast if available
        cl_forecast = self.get_cl_forecast(self.current_tick) if self.get_cl_forecast else None

        if cl_forecast is not None:
            if cl_forecast > 0.001:
                cl_direction = 'up'
            elif cl_forecast < -0.001:
                cl_direction = 'down'
            else:
                cl_direction = 'hold'
        else:
            long_trend_cl = self.trend(self.history['CL'], self.max_len)
            cl_direction = 'up' if long_trend_cl > 0.0004 else 'down' if long_trend_cl < -0.0004 else 'hold'

        pred['CL'] = cl_direction

        for prod in ['HO', 'RB']:
            long_trend = self.trend(self.history[prod], self.max_len)
            short_trend = self.trend(self.history[prod], self.short_len)

            if long_trend > 0.00025 and short_trend > 0.00025:
                self.up_counter[prod] += 1
                pred[prod] = 'up'
            elif long_trend < -0.00025 and short_trend < -0.00025:
                self.up_counter[prod] = 0
                pred[prod] = 'down'
            else:
                if self.up_counter[prod] >= 3:
                    pred[prod] = 'up'
                else:
                    pred[prod] = 'hold'

            self.last_prediction[prod] = pred[prod]

        return pred
