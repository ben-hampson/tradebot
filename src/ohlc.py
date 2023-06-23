"""Logic for getting OHLC and adding it to the database."""

import datetime as dt
import logging
import os
from abc import ABC
from typing import Optional

import easyib
import numpy as np
import pandas as pd
import requests
from sqlmodel import Session

from src.db_utils import engine, get_instrument, get_latest_record
from src.models import OHLC
from src.time_checker import time_check

log = logging.getLogger(__name__)


class OHLCUpdater(ABC):
    """Abstract base class to define OHLC Updaters."""

    def __init__(self, symbol: str) -> None:
        """Initialiser.

        Args:
        symbol: Instrument symbol e.g. BTCUSD.
        end_date: The latest date to get data for (inclusive?). Defaults to None.
            If None, will get data up to the latest possible date.
        start_date: The earliest date to get data for (inclusive?). Defaults to None.
            If None, will get data from the earliest possible date.
        """
        pass

    def update_ohlc_data(self) -> None:
        """Main runner. Bring OHLC data in OHLC table up to date."""

    def get_ohlc_data(self, start_date: dt.datetime, end_date: dt.datetime) -> None:
        """Get OHLC data for an Instrument between two dates.

        Inclusive of the start date and end date.

        end_date: The latest date to get data for (inclusive?). Defaults to None.
            If None, will get data up to the latest possible date.
        start_date: The earliest date to get data for (inclusive?). Defaults to None.
            If None, will get data from the earliest possible date."""
        pass

    def insert_ohlc_data(self) -> None:
        """Insert OHLC data into 'ohlc' table."""
        pass


class CryptoCompareOHLC(OHLCUpdater):
    """Class to get OHLC data from CryptoCompare and add it to the database."""

    def __init__(
        self,
        symbol: str,
        end_date: Optional[dt.date] = None,
        start_date: Optional[dt.date] = None,
    ):
        """Initialiser.

        Args:
        symbol: Instrument symbol e.g. BTCUSD.
        end_date: The latest date to get data for (inclusive?). Defaults to None.
            If None, will get data up to the latest possible date.
        start_date: The earliest date to get data for (inclusive?). Defaults to None.
            If None, will get data from the earliest possible date.
        """
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.instrument = get_instrument(symbol)

    def update_ohlc_data(self):
        """Main runner. Bring OHLC data in OHLC table up to date."""
        latest_ohlc = get_latest_record(self.symbol, OHLC)

        if not latest_ohlc:
            self.get_ohlc_data(dt.date.today(), None)
            self.insert_ohlc_data()
        elif latest_ohlc.date.date() == dt.date.today() - dt.timedelta(1):
            # Get data for 1 day (today)
            self.get_ohlc_data(dt.date.today(), latest_ohlc.date.date())
            self.insert_ohlc_data()
        elif latest_ohlc.date.date() != dt.date.today():
            # Get date for >1 day
            self.get_ohlc_data(
                dt.date.today(), latest_ohlc.date.date() + dt.timedelta(1)
            )
            self.insert_ohlc_data()
        else:
            log.info(f"{self.symbol} data is already up to date. No records added.")

    def get_ohlc_data(self, end_date: dt.date, start_date: Optional[dt.date]):
        """Get OHLC data for an Instrument between two dates.

        Inclusive of the start date and end date."""
        if not self.instrument.vehicle == "crypto":
            return None

        # Set API limit
        if start_date:
            date_diff = end_date - start_date
            limit = date_diff.days
            log.info(f"# of days to get close data for: {limit}")

        # Request data from CryptoCompare API
        # If no start, get data from the beginning
        if start_date is None:
            log.info("Getting all available historic data.")

            # The start date should be in the Instrument table
            first_ts = requests.get(
                "https://min-api.cryptocompare.com/data/blockchain/list",
                {"api_key": os.getenv("CC_API_KEY")},
            ).json()["Data"]["BTC"]["data_available_from"]

            first_date = dt.datetime.fromtimestamp(first_ts).date()

            date_diff = end_date - first_date
            limit = date_diff.days
            log.info(f"# of days to get close data for: {limit}")

        if limit > 2000:
            # CryptoCompare has a limit of 2000 data points per request
            all_data = []
            to_date = end_date

            while limit > 2000:
                data = self.request_cryptocompare(2000, to_date)
                all_data = data + all_data
                limit -= 2000
                to_date = data[0][0].date() - dt.timedelta(1)

            if limit > 0:
                data = self.request_cryptocompare(limit, to_date)
                all_data = data + all_data
        else:
            all_data = self.request_cryptocompare(limit, end_date)

        if not start_date:
            # Get rid of first data points with OHLC of 0,0,0,0
            # because it breaks ti.volatility()
            for i, ohlc in enumerate(all_data):
                if sum([ohlc[1], ohlc[2], ohlc[3], ohlc[4]]) != 0:
                    break

            all_data = all_data[i:]
        else:
            # CryptoCompare gives 1 too many days at the start. Trim it.
            all_data = all_data[1:]

        self.data = all_data

    def request_cryptocompare(self, limit: int, to_date: dt.date) -> list:
        """Get all OHLC data for {limit} number of days up to, but not including, the end date."""
        data = requests.get(
            "https://min-api.cryptocompare.com/data/v2/histoday",
            {
                "fsym": self.instrument.base_currency,
                "tsym": self.instrument.quote_currency,
                "limit": str(limit),
                "toTs": str(
                    int(dt.datetime.timestamp(dt.datetime.combine(to_date, dt.time(6))))
                    # The API gives the values at 00:00 GMT on that day.
                    # Using 0600 instead of 0000 avoids getting the wrong day due to BST.
                ),
                "api_key": os.getenv("CC_API_KEY"),
            },
        ).json()

        date_array_rev = []
        open_array_rev = []
        high_array_rev = []
        low_array_rev = []
        close_array_rev = []

        # The API returns one more than you asked for, so ignore the first
        for bar in reversed(data["Data"]["Data"]):
            date = dt.datetime.fromtimestamp(bar["time"])
            open = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            log.info(f"{date} - {close}")

            date_array_rev.append(date)  # Returns: First = latest, last = oldest.
            open_array_rev.append(open)
            high_array_rev.append(high)
            low_array_rev.append(low)
            close_array_rev.append(close)

        # Reverse arrays so that first = oldest, last = latest
        date_array = np.flip(np.array(date_array_rev))
        open_array = np.flip(np.array(open_array_rev))
        high_array = np.flip(np.array(high_array_rev))
        low_array = np.flip(np.array(low_array_rev))
        close_array = np.flip(np.array(close_array_rev))

        return list(zip(date_array, open_array, high_array, low_array, close_array))

    def insert_ohlc_data(self):
        """Insert OHLC data into 'ohlc' table."""
        log.info(f"{self.symbol} OHLC data: adding to database.")

        records = []

        for i in self.data:
            record = OHLC(
                symbol_date=f"{self.symbol} {i[0].date()}",
                symbol=self.symbol,
                date=i[0],
                open=i[1],
                high=i[2],
                low=i[3],
                close=i[4],
            )
            records.append(record)

        with Session(engine) as session:
            session.add_all(records)
            session.commit()

        log.info(f"{self.symbol} OHLC data: added to database.")


class IBOHLC(OHLCUpdater):
    """Get OHLC data from Interactive Brokers."""

    def __init__(self, symbol: str):
        """Initialiser.

        Args:
        symbol: Instrument symbol e.g. NVDA.
        """
        IBEAM_HOST = os.getenv("IBEAM_HOST", "https://ibeam:5000")
        self.ib = easyib.REST(url=IBEAM_HOST, ssl=False)
        self.symbol = symbol

    def update_ohlc_data(self) -> None:
        """Main runner. Download OHLC data and insert into table."""
        latest_ohlc = get_latest_record(self.symbol, OHLC)

        if not latest_ohlc:
            start = dt.datetime(2000, 1, 1)
            end = dt.datetime.now() - dt.timedelta(minutes=20)
        elif latest_ohlc.date.date() == dt.date.today():
            log.info(f"{self.symbol} data is already up to date. No records added.")
            return None
        elif latest_ohlc.date.date() == dt.date.today() - dt.timedelta(1):
            # Get data for 1 day (today)
            start = latest_ohlc.date + dt.timedelta(1)
            end = dt.datetime.now() - dt.timedelta(minutes=20)
        elif latest_ohlc.date.date() != dt.date.today():
            # Get date for >1 day
            start = latest_ohlc.date + dt.timedelta(1)
            end = dt.datetime.now() - dt.timedelta(minutes=20)

        self.get_ohlc_data(start, end)

        if hasattr(self, "df"):
            self.insert_ohlc_data()
            log.info("%s: Data inserted.", self.symbol)
        else:
            log.info("%s data is already up to date. No records added.", self.symbol)

    def get_ohlc_data(self, start_date: dt.datetime, end_date: dt.datetime) -> None:
        """Get OHLC data for an Instrument between two dates.

        IB API returns a max 1000 data points. There's a limit of 5 concurrent requests.

        Inclusive of the start date and end date.

        end_date: The latest date to get data for (inclusive?). Defaults to None.
            If None, will get data up to the latest possible date.
        start_date: The earliest date to get data for (inclusive?). Defaults to None.
            If None, will get data from the earliest possible date."""
        if os.getenv("TIME_CHECKER") == "1":
            if not time_check(self.symbol, "forecast"):
                return None

        # TODO: Fix period
        # period: {1-30}min, {1-8}h, {1-1000}d, {1-792}w, {1-182}m, {1-15}y
        bars = self.ib.get_bars(self.symbol, period="5y", bar="1d")

        df = pd.DataFrame(bars["data"])
        df.t = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(
            columns={
                "o": "open",
                "c": "close",
                "h": "high",
                "l": "low",
                "v": "volume",
                "t": "date",
            }
        )
        df["symbol"] = self.symbol
        df["symbol_date"] = df["symbol"] + " " + df.date.dt.strftime("%Y-%m-%d")

        if start_date:
            df = df[(df["date"] > start_date)]
        if end_date:
            df = df[(df["date"] < end_date)]

        if not df.empty:
            self.df = df

    def insert_ohlc_data(self) -> None:
        """Insert OHLC data into 'ohlc' table."""
        self.df.to_sql("ohlc", engine, if_exists="append", index=False)


class OHLCUpdaterFactory:
    """Factory to match symbol with the right OHLC data source."""

    @staticmethod
    def create_updater(ohlc_data_source: str, symbol: str) -> OHLCUpdater:
        """Return the correct OHLCUpdate for the symbol.

        Args:
            ohlc_data_source: OHLC data source name.
            symbol: The ticket symbol of the instrument.

        Returns:
            OHLCUpdater
        """
        if ohlc_data_source.lower() == "crypto-compare":
            return CryptoCompareOHLC(symbol)
        elif ohlc_data_source.lower() == "interactive-brokers":
            return IBOHLC(symbol)
