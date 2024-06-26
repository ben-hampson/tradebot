import logging
import os

import easyib  # type: ignore

from src.exchange import Exchange, Position
from typing import Dict

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

log = logging.getLogger(__name__)


class IBExchange(Exchange):
    """Interactive Brokers exchange."""

    def __init__(self):
        """Initialise."""

        IBEAM_HOST = os.getenv("IBEAM_HOST", "https://ibeam:5000")
        self.ib = easyib.REST(url=IBEAM_HOST, ssl=False)

    @property
    def all_positions(self) -> Dict[str, Position]:
        """Get all Positions."""
        client_positions = self.ib.get_portfolio()
        all_positions = dict()

        # for pos in client_positions:
        #     position = Position(
        #       symbol = pos, side=Side.BUY, size=""
        #     )

        return all_positions

    def get_position(self, symbol: str) -> float:
        """Get the current position for a specific instrument.

        Return the amount of the token. e.g. 0.01 ETH.
        Positive means the position is long. Negative means it's short.
        """
        all_positions = self.all_positions

        try:
            # TODO: Check this is accurate when having a stock in the portfolio
            return float(all_positions[symbol].size)
        except KeyError:
            return 0.0

    @property
    def total_equity(self) -> float:
        """Get the total equity on the account."""
        return float(self.ib.get_netvalue())

    def get_current_price(self, symbol: str):
        """Get the price of one unit of this instrument on the exchange.

        The price is the close of the latest minute bar.
        """
        bars = self.ib.get_bars(symbol, period="1d", bar="1min")
        return float(bars["data"][0]["c"])

    def get_symbol(self, base_currency: str, quote_currency: str) -> str:
        """Get symbol used by the Interactive Brokers Web API."""
        # TODO: Check
        return base_currency

    def market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
    ):
        """Creates a market order on the exchange."""
        list_of_orders = [
            {
                "conid": self.ib.get_conid(symbol),
                "orderType": "MKT",
                "side": side,
                "quantity": quantity,
                "tif": "GTC",
            }
        ]

        order = self.ib.submit_orders(list_of_orders)
        return order


if __name__ == "__main__":
    exchange = IBExchange()
    pos = exchange.all_positions
    quote = exchange.get_current_price("NVDA")
