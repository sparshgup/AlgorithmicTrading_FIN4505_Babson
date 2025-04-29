# lease_manager.py

class LeaseManager:
    def __init__(self, session):
        self.session = session
        self.reserved_lease_ids = set()

    def request_storage(self, ticker, tanks_needed):
        leases = self.session.session.get('http://localhost:9999/v1/leases').json()
        active_tanks = sum(1 for lease in leases if lease['ticker'] == ticker)

        while active_tanks < tanks_needed:
            response = self.session.lease(ticker)
            if response.ok:
                lease_info = response.json()
                self.mark_reserved(lease_info['id'])
                active_tanks += 1
                print(f"Leasing {ticker} storage {lease_info['id']}")

    def mark_reserved(self, lease_id):
        self.reserved_lease_ids.add(lease_id)

    def unmark_reserved(self, lease_id):
        self.reserved_lease_ids.discard(lease_id)

    def optimize(self, tick, prices):
        leases = self.session.session.get('http://localhost:9999/v1/leases').json()

        for lease in leases:
            if lease['ticker'].endswith('STORAGE') \
               and lease['containment_usage'] == 0 \
               and lease['id'] not in self.reserved_lease_ids:
                self.session.release_lease(lease['id'])

        for lease in leases:
            if lease['ticker'] == 'CL-REFINERY':
                ticks_remaining = lease['next_lease_tick'] - tick
                if ticks_remaining <= 1:
                    self.session.release_lease(lease['id'])