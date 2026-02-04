"""
Bybit Whitelist Service
Manages the top 30 cryptocurrencies by market cap for Bybit futures trading
"""
from typing import List, Dict, Optional
from datetime import datetime
from utils.timezone import now_kst

from utils.logger import setup_logger

logger = setup_logger(__name__)

# Fixed list: Top 30 coins by market cap (2024)
# These are the most liquid for futures trading

# Fixed list: Top 50 coins by market cap (2024.12)
# Used as a candidate pool for dynamic volume-based selection
TOP_50_MARKET_CAP_SYMBOLS = [
    # Top 10
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", 
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "TRXUSDT", "SHIBUSDT",
    # 11-20
    "LINKUSDT", "DOTUSDT", "BCHUSDT", "UNIUSDT", "LTCUSDT",
    "MATICUSDT", "NEARUSDT", "APTUSDT", "ICPUSDT", "ETCUSDT",
    # 21-30
    "SUIUSDT", "FILUSDT", "ARBUSDT", "ATOMUSDT", "OPUSDT",
    "RNDRUSDT", "RENDERUSDT", "PEPEUSDT", "STXUSDT", "IMXUSDT",
    # 31-40
    "TAOUSDT", "INJUSDT", "WIFUSDT", "TIAUSDT", "GRTUSDT",
    "FTMUSDT", "BONKUSDT", "FLOKIUSDT", "WLDUSDT", "SEIUSDT",
    # 41-50
    "ONDOUSDT", "RUNEUSDT", "GALAUSDT", "LDOUSDT", "HBARUSDT",
    "MNTUSDT", "FETUSDT", "QNTUSDT", "AAVEUSDT", "ALGOUSDT"
]


class BybitWhitelistService:
    """
    Manages Bybit futures whitelist (top 30 by market cap)
    """
    
    _instance = None
    CACHE_TTL_SECONDS = 30  # 30초 캐시
    
    def __init__(self):
        self.whitelist: List[Dict] = []
        self.last_updated: Optional[datetime] = None
        self._last_api_call: Optional[datetime] = None
        self._init_whitelist()
    
    @classmethod
    def get_instance(cls) -> 'BybitWhitelistService':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def _init_whitelist(self):
        """Initialize whitelist with default coins"""
        self.whitelist = []
        # Fallback init
        rank = 1
        for symbol in TOP_50_MARKET_CAP_SYMBOLS[:30]:
            self.whitelist.append({
                "symbol": symbol,
                "name": symbol.replace("USDT", ""),
                "rank": rank,
                "current_price": 0,
                "volume_24h": 0,
                "change_24h": 0,
                "funding_rate": 0,
                "status": "watching"
            })
            rank += 1
        self.last_updated = datetime.utcnow()
    
    def get_whitelist(self) -> List[Dict]:
        """Get the full whitelist with metadata - auto-refresh if stale"""
        # ★ Check cache freshness - refresh if older than 5 minutes (300s)
        CACHE_TTL_SECONDS = 300
        
        if self.last_updated:
            elapsed = (datetime.utcnow() - self.last_updated).total_seconds()
            if elapsed > CACHE_TTL_SECONDS:
                logger.info(f"[Bybit] Whitelist cache expired ({elapsed:.0f}s > {CACHE_TTL_SECONDS}s), refreshing...")
                self.refresh_prices(force=True)
        else:
            # No last_updated timestamp - refresh to be safe
            logger.info("[Bybit] No whitelist timestamp, refreshing...")
            self.refresh_prices(force=True)
        
        return self.whitelist
    
    def get_whitelist_symbols(self) -> List[str]:
        """Get just the symbol list"""
        return [coin["symbol"] for coin in self.whitelist]
    
    def get_last_updated(self) -> Optional[str]:
        """Get last update time as ISO string"""
        return self.last_updated.isoformat() if self.last_updated else None
    
    def refresh_prices(self, force: bool = False) -> tuple:
        """
        Refresh whitelist by fetching ALL tickers and selecting top 30 by 24h turnover (volume)
        Uses 30-second cache to avoid excessive API calls.
        
        Args:
            force: If True, ignore cache and force refresh
        
        Returns:
            Tuple of (whitelist, added_symbols, removed_symbols)
        """
        # 캐시 체크 - 30초 이내 재요청 시 캐시 반환
        if not force and self._last_api_call and self.whitelist:
            elapsed = (datetime.utcnow() - self._last_api_call).total_seconds()
            if elapsed < self.CACHE_TTL_SECONDS:
                logger.debug(f"[Bybit Whitelist] 캐시 사용 (남은 시간: {self.CACHE_TTL_SECONDS - elapsed:.0f}초)")
                return self.whitelist, [], []
        
        try:
            from pybit.unified_trading import HTTP
            client = HTTP()
            
            # Keep track of previous symbols for change detection
            old_symbols = set(self.get_whitelist_symbols())
            
            # 1. Get all tickers
            response = client.get_tickers(category="linear")
            
            if response['retCode'] != 0 or not response['result']['list']:
                logger.error(f"Failed to fetch Bybit tickers: {response}")
                return self.whitelist, [], []
                
            tickers = response['result']['list']
            
            # 2. Filter for USDT perps and valid data
            valid_tickers = []
            for t in tickers:
                symbol = t['symbol']
                if not symbol.endswith('USDT'):
                    continue
                    
                # Skip stablecoins or special symbols if needed
                if symbol == 'USDCUSDT':
                    continue

                # Filter: Must be in Top 50 Market Cap list
                if symbol not in TOP_50_MARKET_CAP_SYMBOLS:
                    continue
                    
                try:
                    turnover = float(t.get('turnover24h', 0))
                    valid_tickers.append({
                        'symbol': symbol,
                        'ticker': t,
                        'turnover': turnover
                    })
                except:
                    continue
            
            # 3. Sort by turnover (volume) desc
            valid_tickers.sort(key=lambda x: x['turnover'], reverse=True)
            
            # 4. Take top 30
            top_tickers = valid_tickers[:30]
            
            # 5. Build new whitelist
            new_whitelist = []
            rank = 1
            
            for item in top_tickers:
                t = item['ticker']
                symbol = item['symbol']
                
                # Get name from valid list or use symbol
                name = symbol.replace('USDT', '')
                
                new_whitelist.append({
                    "symbol": symbol,
                    "name": name,
                    "rank": rank,
                    "current_price": float(t.get('lastPrice', 0)),
                    "volume_24h": float(t.get('turnover24h', 0)),
                    "change_24h": float(t.get('price24hPcnt', 0)) * 100,
                    "funding_rate": float(t.get('fundingRate', 0)) * 100,
                    "status": 'watching'
                })
                rank += 1
            
            # 6. Detect changes
            new_symbols = set([item['symbol'] for item in new_whitelist])
            added = list(new_symbols - old_symbols)
            removed = list(old_symbols - new_symbols)
            
            # 7. Update state
            self.whitelist = new_whitelist
            self.last_updated = now_kst()
            self._last_api_call = datetime.utcnow()  # 캐시 타임스탬프 업데이트
            
            logger.info(f"Bybit whitelist updated: {len(self.whitelist)} coins (Top 30 by Volume)")
            
            return self.whitelist, added, removed
            
        except Exception as e:
            logger.error(f"Error refreshing Bybit whitelist: {e}")
            return self.whitelist, [], []
    
    def get_coin_info(self, symbol: str) -> Optional[Dict]:
        """Get info for a specific coin"""
        for coin in self.whitelist:
            if coin['symbol'] == symbol:
                return coin
        return None
    
    def is_valid_symbol(self, symbol: str) -> bool:
        """Check if symbol is in whitelist"""
        return symbol in self.get_whitelist_symbols()


# Global instance
bybit_whitelist_service = BybitWhitelistService.get_instance()
