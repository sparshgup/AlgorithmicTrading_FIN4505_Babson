# main.py

from master import MasterController

API_KEY = 'QDSFW62B'

sleep_time = 0.1

def main():
    controller = MasterController(API_KEY, sleep_time)
    controller.run()

if __name__ == "__main__":
    main()
