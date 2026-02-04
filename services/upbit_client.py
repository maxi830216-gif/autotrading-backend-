"""
Upbit API Client Service
Wrapper around pyupbit library with additional functionality
"""
import pyupbit
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd

from utils.logger import setup_logger
from utils.encryption import encryptor

logger = setup_logger(__name__)


class UpbitClient:
    """Upbit API client for trading operations"""
    
    def __init__(self, access_key: str = None, secret_key: str = None):
        self._access_key = access_key
        self._secret_key = secret_key
        self._upbit: Optional[pyupbit.Upbit] = None
        
        if access_key and secret_key:
            self._initialize_client()
    
    def _initialize_client(self):
        """Initialize pyupbit client with credentials"""
        try:
            self._upbit = pyupbit.Upbit(self._access_key, self._secret_key)
            logger.info("Upbit client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Upbit client: {e}")
            self._upbit = None
    
    def set_credentials(self, access_key: str, secret_key: str):
        """Update API credentials"""
        self._access_key = access_key
        self._secret_key = secret_key
        self._initialize_client()
    
    @property
    def is_authenticated(self) -> bool:
        """Check if client is properly authenticated"""
        return self._upbit is not None
    
    # ===================
    # Account Methods
    # ===================
    
    def get_balances(self) -> List[Dict[str, Any]]:
        """Get all account balances"""
        if not self.is_authenticated:
            return []
        try:
            balances = self._upbit.get_balances()
            return balances if balances else []
        except Exception as e:
            logger.error(f"Failed to get balances: {e}")
            return []
    
    def get_balance(self, ticker: str = "KRW") -> float:
        """Get balance for specific currency"""
        if not self.is_authenticated:
            return 0.0
        try:
            balance = self._upbit.get_balance(ticker)
            return float(balance) if balance else 0.0
        except Exception as e:
            logger.error(f"Failed to get balance for {ticker}: {e}")
            return 0.0
    
    def get_avg_buy_price(self, ticker: str) -> float:
        """Get average buy price for a coin"""
        if not self.is_authenticated:
            return 0.0
        try:
            return float(self._upbit.get_avg_buy_price(ticker) or 0.0)
        except Exception as e:
            logger.error(f"Failed to get avg buy price for {ticker}: {e}")
            return 0.0
    
    # ===================
    # Market Data Methods
    # ===================
    
    @staticmethod
    def get_tickers(fiat: str = "KRW") -> List[str]:
        """Get all tickers for given fiat currency"""
        try:
            tickers = pyupbit.get_tickers(fiat=fiat)
            return tickers if tickers else []
        except Exception as e:
            logger.error(f"Failed to get tickers: {e}")
            return []
    
    @staticmethod
    def get_ticker(market: str) -> Optional[float]:
        """Get current price for a single market"""
        try:
            price = pyupbit.get_current_price(market)
            return float(price) if price else None
        except Exception as e:
            logger.error(f"Failed to get ticker for {market}: {e}")
            return None
    
    @staticmethod
    def get_current_price(markets: List[str], max_retries: int = 3) -> Dict[str, float]:
        """Get current prices for multiple markets with retry logic"""
        import time
        
        if not markets:
            return {}
        
        # Ensure we have a list, not a string
        if isinstance(markets, str):
            markets = [markets]
        
        result = {}
        failed_markets = list(markets)
        
        for attempt in range(max_retries):
            if not failed_markets:
                break
                
            try:
                # Try batch lookup for remaining failed markets
                prices = pyupbit.get_current_price(failed_markets)
                
                if prices is None:
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue
                    logger.warning(f"get_current_price returned None for {failed_markets}")
                    break
                
                if isinstance(prices, (int, float)):
                    # Single market case
                    result[failed_markets[0]] = float(prices)
                    failed_markets = []
                elif isinstance(prices, dict):
                    for k, v in prices.items():
                        if v is not None:
                            result[k] = float(v)
                    # Update failed_markets to only those still missing
                    failed_markets = [m for m in failed_markets if m not in result]
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"Price lookup attempt {attempt + 1} failed, retrying: {e}")
                    time.sleep(0.5)
                else:
                    logger.warning(f"Batch price lookup failed after {max_retries} attempts: {e}")
        
        # Final fallback: individual lookups for any remaining failed markets
        if failed_markets:
            logger.info(f"Trying individual lookups for {len(failed_markets)} failed markets")
            for market in failed_markets:
                for retry in range(2):  # 2 retries per individual market
                    try:
                        price = pyupbit.get_current_price(market)
                        if price is not None:
                            result[market] = float(price)
                            break
                    except Exception:
                        if retry == 0:
                            time.sleep(0.3)
        
        return result
    
    @staticmethod
    def get_ohlcv(ticker: str, interval: str = "day", count: int = 200) -> Optional[pd.DataFrame]:
        """
        Get OHLCV candle data
        
        Args:
            ticker: Market ticker (e.g., 'KRW-BTC')
            interval: 'day', 'minute60', 'minute240', etc.
            count: Number of candles to fetch
        
        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        try:
            df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
            return df
        except Exception as e:
            logger.error(f"Failed to get OHLCV for {ticker}: {e}")
            return None
    
    @staticmethod
    def get_orderbook(markets: List[str]) -> List[Dict]:
        """Get orderbook data"""
        try:
            return pyupbit.get_orderbook(markets)
        except Exception as e:
            logger.error(f"Failed to get orderbook: {e}")
            return []
    
    @staticmethod
    def get_market_info() -> List[Dict]:
        """Get all market information including korean/english names"""
        try:
            import requests
            url = "https://api.upbit.com/v1/market/all"
            response = requests.get(url, params={"isDetails": "true"})
            return response.json() if response.status_code == 200 else []
        except Exception as e:
            logger.error(f"Failed to get market info: {e}")
            return []
    
    # ===================
    # Order Methods
    # ===================
    
    def buy_limit_order(self, ticker: str, price: float, volume: float) -> Optional[Dict]:
        """Place limit buy order"""
        if not self.is_authenticated:
            logger.error("Cannot place order: not authenticated")
            return None
        try:
            result = self._upbit.buy_limit_order(ticker, price, volume)
            logger.info(f"Buy limit order placed: {ticker} @ {price} x {volume}")
            return result
        except Exception as e:
            logger.error(f"Failed to place buy limit order: {e}")
            return None
    
    def buy_market_order(self, ticker: str, price: float) -> Optional[Dict]:
        """Place market buy order (price is the amount in KRW)"""
        if not self.is_authenticated:
            logger.error("Cannot place order: not authenticated")
            return None
        try:
            result = self._upbit.buy_market_order(ticker, price)
            logger.info(f"Buy market order placed: {ticker} with {price} KRW")
            return result
        except Exception as e:
            logger.error(f"Failed to place buy market order: {e}")
            return None
    
    def sell_limit_order(self, ticker: str, price: float, volume: float) -> Optional[Dict]:
        """Place limit sell order"""
        if not self.is_authenticated:
            logger.error("Cannot place order: not authenticated")
            return None
        try:
            result = self._upbit.sell_limit_order(ticker, price, volume)
            logger.info(f"Sell limit order placed: {ticker} @ {price} x {volume}")
            return result
        except Exception as e:
            logger.error(f"Failed to place sell limit order: {e}")
            return None
    
    def sell_market_order(self, ticker: str, volume: float) -> Optional[Dict]:
        """Place market sell order"""
        if not self.is_authenticated:
            logger.error("Cannot place order: not authenticated")
            return None
        try:
            result = self._upbit.sell_market_order(ticker, volume)
            logger.info(f"Sell market order placed: {ticker} x {volume}")
            return result
        except Exception as e:
            logger.error(f"Failed to place sell market order: {e}")
            return None
    
    def cancel_order(self, uuid: str) -> Optional[Dict]:
        """Cancel an order by UUID"""
        if not self.is_authenticated:
            return None
        try:
            result = self._upbit.cancel_order(uuid)
            logger.info(f"Order cancelled: {uuid}")
            return result
        except Exception as e:
            logger.error(f"Failed to cancel order {uuid}: {e}")
            return None
    
    def get_order(self, uuid: str) -> Optional[Dict]:
        """Get order details by UUID"""
        if not self.is_authenticated:
            return None
        try:
            return self._upbit.get_order(uuid)
        except Exception as e:
            logger.error(f"Failed to get order {uuid}: {e}")
            return None
    
    def get_open_orders(self, ticker: str = None) -> List[Dict]:
        """Get all open (unfilled) orders"""
        if not self.is_authenticated:
            return []
        try:
            # pyupbit의 wait 타입 주문 조회
            orders = self._upbit.get_order(ticker, state='wait') if ticker else []
            return orders if orders else []
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []


# Global client instance (will be initialized with credentials from DB)
upbit_client = UpbitClient()
