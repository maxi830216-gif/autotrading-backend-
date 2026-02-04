"""
Bybit USDT Perpetual Futures Client
Handles all Bybit API interactions for futures trading
"""
from typing import Optional, Dict, List, Any
import pandas as pd
from datetime import datetime
import time

from utils.logger import setup_logger

logger = setup_logger(__name__)


class BybitClient:
    """
    Bybit USDT Perpetual Futures Client
    
    Features:
    - OHLCV data fetching (public API)
    - Wallet balance checking
    - Position management
    - Order placement (Long only)
    - Funding rate queries
    """
    
    _instance = None
    
    def __init__(self):
        self.client = None
        self.testnet = False
        self._initialized = False
    
    @classmethod
    def get_instance(cls) -> 'BybitClient':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def set_credentials(self, api_key: str, api_secret: str, testnet: bool = False):
        """Initialize with API credentials"""
        try:
            from pybit.unified_trading import HTTP
            self.testnet = testnet
            self.client = HTTP(
                api_key=api_key,
                api_secret=api_secret,
                testnet=testnet
            )
            self._initialized = True
            logger.info(f"BybitClient initialized (testnet={testnet})")
        except Exception as e:
            logger.error(f"Failed to initialize BybitClient: {e}")
            self._initialized = False
    
    @staticmethod
    def get_ohlcv(symbol: str, interval: str = "D", limit: int = 100, max_retries: int = 3) -> Optional[pd.DataFrame]:
        """
        Get OHLCV data (public API - no auth required) with retry logic
        
        Args:
            symbol: Trading pair e.g., 'BTCUSDT'
            interval: '1' (1min), '5', '15', '60', '240' (4H), 'D' (daily)
            limit: Number of candles (max 200)
            max_retries: Maximum retry attempts
        
        Returns:
            DataFrame with columns: open, high, low, close, volume, timestamp
        """
        from pybit.unified_trading import HTTP
        
        for attempt in range(max_retries):
            try:
                client = HTTP()
                
                response = client.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval=interval,
                    limit=limit
                )
                
                if response['retCode'] != 0:
                    if attempt < max_retries - 1:
                        logger.debug(f"Bybit OHLCV attempt {attempt + 1} failed for {symbol}: {response['retMsg']}")
                        time.sleep(0.5)
                        continue
                    logger.error(f"Bybit API error for {symbol}: {response['retMsg']}")
                    return None
                
                data = response['result']['list']
                if not data:
                    return None
                
                # Bybit returns newest first, so reverse
                data = list(reversed(data))
                
                df = pd.DataFrame(data, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
                ])
                
                # Convert types
                df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
                df['open'] = df['open'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                df['close'] = df['close'].astype(float)
                df['volume'] = df['volume'].astype(float)
                
                return df
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"Bybit OHLCV attempt {attempt + 1} failed for {symbol}: {e}")
                    time.sleep(0.5)
                else:
                    logger.error(f"Error fetching OHLCV for {symbol} after {max_retries} attempts: {e}")
        
        return None
    
    @staticmethod
    def get_current_price(symbols: List[str], max_retries: int = 3) -> Dict[str, float]:
        """
        Get current prices for multiple symbols (public API) with retry logic
        
        Args:
            symbols: List of symbols e.g., ['BTCUSDT', 'ETHUSDT']
            max_retries: Maximum retry attempts per symbol
        
        Returns:
            Dict mapping symbol to current price
        """
        from pybit.unified_trading import HTTP
        
        prices = {}
        failed_symbols = list(symbols)
        
        for symbol in symbols:
            for attempt in range(max_retries):
                try:
                    client = HTTP()
                    response = client.get_tickers(
                        category="linear",
                        symbol=symbol
                    )
                    if response['retCode'] == 0 and response['result']['list']:
                        prices[symbol] = float(response['result']['list'][0]['lastPrice'])
                        break
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.debug(f"Bybit price lookup attempt {attempt + 1} failed for {symbol}: {e}")
                        time.sleep(0.5)
                    else:
                        logger.warning(f"Bybit price lookup failed for {symbol} after {max_retries} attempts: {e}")
        
        return prices
    
    @staticmethod
    def get_funding_rate(symbol: str) -> Optional[Dict]:
        """
        Get current funding rate for a symbol (public API)
        
        Returns:
            Dict with fundingRate, fundingRateTimestamp, nextFundingTime
        """
        try:
            from pybit.unified_trading import HTTP
            client = HTTP()
            
            response = client.get_tickers(
                category="linear",
                symbol=symbol
            )
            
            if response['retCode'] == 0 and response['result']['list']:
                ticker = response['result']['list'][0]
                return {
                    'symbol': symbol,
                    'fundingRate': float(ticker.get('fundingRate', 0)),
                    'nextFundingTime': int(ticker.get('nextFundingTime', 0))
                }
            return None
            
        except Exception as e:
            logger.error(f"Error fetching funding rate for {symbol}: {e}")
            return None
    
    def get_wallet_balance(self) -> Dict[str, float]:
        """
        Get USDT wallet balance (requires auth)
        
        Returns:
            Dict with 'available' and 'total' keys
        """
        if not self._initialized or not self.client:
            return {"available": 0, "total": 0}
        
        try:
            response = self.client.get_wallet_balance(
                accountType="UNIFIED",
                coin="USDT"
            )
            
            if response['retCode'] == 0:
                coins = response['result']['list'][0]['coin']
                for coin in coins:
                    if coin['coin'] == 'USDT':
                        # Parse values (handle empty strings)
                        wallet_balance = float(coin.get('walletBalance', '0') or '0')
                        unrealized_pnl = float(coin.get('unrealisedPnl', '0') or '0')
                        equity = float(coin.get('equity', '0') or '0')
                        available_to_withdraw = coin.get('availableToWithdraw', '')
                        
                        # Calculate position value (invested capital + unrealized PnL)
                        position_value = wallet_balance + unrealized_pnl
                        
                        # Available balance (free cash not in positions)
                        # If availableToWithdraw is empty, calculate it as: equity - total margin used
                        total_position_im = float(coin.get('totalPositionIM', '0') or '0')
                        if available_to_withdraw:
                            available_balance = float(available_to_withdraw)
                        else:
                            # If no positions, available = equity. If positions, available = equity - position margin
                            available_balance = max(0, equity - total_position_im - unrealized_pnl) if total_position_im > 0 else equity
                        
                        return {
                            "available": available_balance,  # Free cash (not in positions)
                            "total": wallet_balance,         # Invested capital in positions
                            "equity": equity,                # Total equity
                            "unrealized_pnl": unrealized_pnl,
                            "position_value": position_value  # Position value = wallet + unrealized PnL
                        }
            return {"available": 0, "total": 0, "equity": 0, "unrealized_pnl": 0, "position_value": 0}
            
        except Exception as e:
            logger.error(f"Error fetching wallet balance: {e}")
            return {"available": 0, "total": 0}
    
    def get_positions(self, symbol: str = None) -> List[Dict]:
        """
        Get current open positions (requires auth)
        
        Args:
            symbol: Optional specific symbol
        
        Returns:
            List of position dictionaries
        """
        if not self._initialized or not self.client:
            return []
        
        try:
            params = {"category": "linear", "settleCoin": "USDT"}
            if symbol:
                params["symbol"] = symbol
            
            response = self.client.get_positions(**params)
            
            if response['retCode'] == 0:
                positions = []
                for pos in response['result']['list']:
                    if float(pos.get('size', 0)) > 0:
                        positions.append({
                            'symbol': pos['symbol'],
                            'side': pos['side'],  # 'Buy' = Long, 'Sell' = Short
                            'size': float(pos['size']),
                            'entryPrice': float(pos.get('avgPrice', 0)),
                            'leverage': pos.get('leverage', '1'),
                            'unrealisedPnl': float(pos.get('unrealisedPnl', 0)),
                            'liqPrice': float(pos.get('liqPrice', 0)) if pos.get('liqPrice') else None
                        })
                return positions
            return []
            
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []
    
    def set_leverage(self, symbol: str, leverage: int = 5) -> bool:
        """
        Set leverage for a symbol (requires auth)
        
        Args:
            symbol: Trading pair
            leverage: Leverage value (1-100)
        
        Returns:
            True if successful
        """
        if not self._initialized or not self.client:
            return False
        
        try:
            response = self.client.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage)
            )
            
            if response['retCode'] == 0 or 'leverage not modified' in str(response.get('retMsg', '')).lower():
                return True
            
            logger.warning(f"Set leverage response: {response}")
            return False
            
        except Exception as e:
            logger.error(f"Error setting leverage: {e}")
            return False
    
    def place_order(
        self,
        symbol: str,
        side: str,  # "Buy" (open long) or "Sell" (close long)
        qty: float,
        order_type: str = "Market",
        leverage: int = 5,
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """
        Place a futures order (Long only)
        
        Args:
            symbol: Trading pair e.g., 'BTCUSDT'
            side: 'Buy' for opening long, 'Sell' for closing long
            qty: Quantity in base currency (e.g., 0.001 BTC)
            order_type: 'Market' or 'Limit'
            leverage: Leverage to use (default 5x)
            reduce_only: True if closing position only
        
        Returns:
            Dict with success status and order details
        """
        if not self._initialized or not self.client:
            return {"success": False, "error": "Client not initialized"}
        
        try:
            # Set leverage first (only for new positions)
            if not reduce_only:
                self.set_leverage(symbol, leverage)
            
            # ★ Qty precision rounding based on symbol
            # Bybit requires different decimal precision per symbol
            # Most altcoins like ADAUSDT require integer qty
            qty_precision_map = {
                'BTCUSDT': 3,   # 0.001 BTC
                'ETHUSDT': 2,   # 0.01 ETH
                'BNBUSDT': 2,   # 0.01 BNB
                'SOLUSDT': 1,   # 0.1 SOL
                'XRPUSDT': 0,   # 1 XRP (integer)
                'ADAUSDT': 0,   # 1 ADA (integer)
                'DOGEUSDT': 0,  # 1 DOGE (integer)
                'AVAXUSDT': 1,  # 0.1 AVAX
                'LINKUSDT': 1,  # 0.1 LINK
                'DOTUSDT': 1,   # 0.1 DOT
            }
            precision = qty_precision_map.get(symbol, 0)  # Default to integer
            
            if precision == 0:
                rounded_qty = int(qty)
            else:
                rounded_qty = round(qty, precision)
            
            # Ensure minimum qty
            if rounded_qty <= 0:
                return {"success": False, "error": f"Qty too small after rounding: {qty} -> {rounded_qty}"}
            
            logger.debug(f"[Bybit] {symbol} qty: {qty} -> {rounded_qty} (precision={precision})")
            
            # Place order
            order_params = {
                "category": "linear",
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "qty": str(rounded_qty),
                "positionIdx": 0  # One-way mode
            }
            
            if reduce_only:
                order_params["reduceOnly"] = True
            
            response = self.client.place_order(**order_params)
            
            if response['retCode'] == 0:
                return {
                    "success": True,
                    "order_id": response['result']['orderId'],
                    "data": response['result']
                }
            else:
                return {
                    "success": False,
                    "error": response['retMsg'],
                    "message": response['retMsg']
                }
                
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {"success": False, "error": str(e), "message": str(e)}
    
    def close_position(self, symbol: str, qty: float = None) -> Dict[str, Any]:
        """
        Close a long position (full or partial)
        
        Args:
            symbol: Trading pair
            qty: Quantity to close (None = close all)
        
        Returns:
            Dict with success status
        """
        if qty is None:
            # Get current position size
            positions = self.get_positions(symbol)
            if positions:
                qty = positions[0]['size']
            else:
                return {"success": False, "error": "No position to close"}
        
        return self.place_order(symbol, "Sell", qty)
    
    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: float = None,
        take_profit: float = None,
        position_idx: int = 0  # 0 = one-way mode
    ) -> Dict[str, Any]:
        """
        Set stop-loss and take-profit for an existing position
        
        Args:
            symbol: Trading pair e.g., 'BTCUSDT'
            stop_loss: Stop-loss price (None = don't set)
            take_profit: Take-profit price (None = don't set)
            position_idx: Position index (0 = one-way mode)
        
        Returns:
            Dict with success status
        """
        if not self._initialized or not self.client:
            return {"success": False, "error": "Client not initialized"}
        
        if not stop_loss and not take_profit:
            return {"success": False, "error": "At least one of stop_loss or take_profit required"}
        
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
                "positionIdx": position_idx
            }
            
            if stop_loss:
                params["stopLoss"] = str(stop_loss)
            if take_profit:
                params["takeProfit"] = str(take_profit)
            
            response = self.client.set_trading_stop(**params)
            
            if response['retCode'] == 0:
                logger.info(f"[Bybit] Set trading stop for {symbol}: SL={stop_loss}, TP={take_profit}")
                return {
                    "success": True,
                    "data": response['result']
                }
            else:
                return {
                    "success": False,
                    "error": response['retMsg'],
                    "message": response['retMsg']
                }
                
        except Exception as e:
            logger.error(f"Error setting trading stop for {symbol}: {e}")
            return {"success": False, "error": str(e)}
    
    def get_closed_pnl(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        """
        Get closed position PnL records (requires auth)
        Used to detect positions closed by SL/TP orders
        
        Args:
            symbol: Optional specific symbol
            limit: Number of records (max 100)
        
        Returns:
            List of closed PnL records
        """
        if not self._initialized or not self.client:
            return []
        
        try:
            params = {
                "category": "linear",
                "limit": limit
            }
            if symbol:
                params["symbol"] = symbol
            
            response = self.client.get_closed_pnl(**params)
            
            if response['retCode'] == 0:
                records = []
                for record in response['result']['list']:
                    records.append({
                        'symbol': record['symbol'],
                        'side': record['side'],  # 'Buy' or 'Sell'
                        'qty': float(record['qty']),
                        'entryPrice': float(record['avgEntryPrice']),
                        'exitPrice': float(record['avgExitPrice']),
                        'closedPnl': float(record['closedPnl']),
                        'orderType': record.get('orderType', 'Market'),
                        'createdTime': int(record['createdTime']),
                        'updatedTime': int(record['updatedTime'])
                    })
                return records
            return []
            
        except Exception as e:
            logger.error(f"Error fetching closed PnL: {e}")
            return []


# Global instance
bybit_client = BybitClient.get_instance()
