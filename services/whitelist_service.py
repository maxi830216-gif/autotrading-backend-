"""
Whitelist Service
Manages top 20 coins by trading volume FROM market cap top 50
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import json

from services.upbit_client import UpbitClient
from models.database import get_setting, set_setting, SessionLocal
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Constants
WHITELIST_SIZE = 50
WHITELIST_CACHE_KEY = "whitelist_cache"
WHITELIST_UPDATED_KEY = "whitelist_updated_at"

# Market Cap Top 50 (excluding stablecoins) - Updated 2024.12
# These are the KRW market codes for top 50 coins by market cap
MARKET_CAP_TOP_50 = [
    "KRW-BTC",    # 비트코인
    "KRW-ETH",    # 이더리움
    "KRW-XRP",    # 엑스알피(리플)
    "KRW-SOL",    # 솔라나
    "KRW-TRX",    # 트론
    "KRW-DOGE",   # 도지코인
    "KRW-ADA",    # 에이다
    "KRW-BCH",    # 비트코인캐시
    "KRW-LINK",   # 체인링크
    "KRW-XLM",    # 스텔라루멘
    "KRW-SUI",    # 수이
    "KRW-AVAX",   # 아발란체
    "KRW-HBAR",   # 헤데라
    "KRW-SHIB",   # 시바이누
    "KRW-CRO",    # 크로노스
    "KRW-DOT",    # 폴카닷
    "KRW-UNI",    # 유니스왑
    "KRW-MNT",    # 맨틀
    "KRW-AAVE",   # 에이브
    "KRW-NEAR",   # 니어프로토콜
    "KRW-ENA",    # 에테나
    "KRW-ETC",    # 이더리움클래식
    "KRW-PEPE",   # 페페
    "KRW-ONDO",   # 온도파이낸스
    "KRW-WLD",    # 월드코인
    "KRW-APT",    # 앱토스
    "KRW-POL",    # 폴리곤에코시스템토큰
    "KRW-ALGO",   # 알고랜드
    "KRW-ARB",    # 아비트럼
    "KRW-TRUMP",  # 오피셜트럼프
    "KRW-FIL",    # 파일코인
    "KRW-VET",    # 비체인
    "KRW-ATOM",   # 코스모스
    "KRW-RENDER", # 렌더토큰
    "KRW-SEI",    # 세이
    "KRW-BONK",   # 봉크
    "KRW-JUP",    # 주피터
    "KRW-IP",     # 스토리
    "KRW-PENGU",  # 펏지펭귄
    "KRW-AERO",   # 에어로드롬파이낸스
    "KRW-OP",     # 옵티미즘
    "KRW-IMX",    # 이뮤터블엑스
    "KRW-VIRTUAL",# 버추얼프로토콜
    "KRW-CRV",    # 커브
    "KRW-INJ",    # 인젝티브
]


class WhitelistService:
    """Service for managing whitelist of top trading volume coins from market cap top 50"""
    
    def __init__(self):
        self._cached_whitelist: List[Dict] = []
        self._last_updated: Optional[datetime] = None
    
    def refresh_whitelist(self) -> Tuple[List[Dict], List[str], List[str]]:
        """
        Refresh whitelist with top 20 coins by 24h trading volume
        FROM market cap top 50 list
        Called every minute by scheduler
        
        Returns:
            Tuple of (whitelist, added_markets, removed_markets)
        """
        try:
            # Store previous markets for comparison
            prev_markets = {coin['market'] for coin in self._cached_whitelist}
            
            # Get market info for names
            market_info = UpbitClient.get_market_info()
            market_names = {
                info['market']: {
                    'korean_name': info.get('korean_name', ''),
                    'english_name': info.get('english_name', '')
                }
                for info in market_info if info['market'].startswith('KRW-')
            }
            
            # Filter to only coins available on Upbit from our top 50 list
            available_markets = [m for m in MARKET_CAP_TOP_50 if m in market_names]
            
            if not available_markets:
                logger.error("No available markets found from top 50 list")
                return self._cached_whitelist, [], []
            
            # Get 24h volume data for these markets only
            import requests
            url = "https://api.upbit.com/v1/ticker"
            response = requests.get(url, params={"markets": ",".join(available_markets)})
            
            if response.status_code != 200:
                logger.error(f"Failed to get ticker data: {response.status_code}")
                return self._cached_whitelist, [], []
            
            ticker_data = response.json()
            
            # Sort by acc_trade_price_24h (24h trading volume in KRW)
            sorted_tickers = sorted(
                ticker_data,
                key=lambda x: x.get('acc_trade_price_24h', 0),
                reverse=True
            )
            
            # Take top N by trading volume
            top_coins = sorted_tickers[:WHITELIST_SIZE]
            
            # Build whitelist
            whitelist = []
            for coin in top_coins:
                market = coin['market']
                names = market_names.get(market, {'korean_name': '', 'english_name': ''})
                whitelist.append({
                    'market': market,
                    'korean_name': names['korean_name'],
                    'english_name': names['english_name'],
                    'trade_volume_24h': coin.get('acc_trade_price_24h', 0),
                    'current_price': coin.get('trade_price', 0),
                    'change_rate': coin.get('signed_change_rate', 0) * 100,
                    'status': 'watching'
                })
            
            # Calculate changes
            new_markets = {coin['market'] for coin in whitelist}
            added = list(new_markets - prev_markets)
            removed = list(prev_markets - new_markets)
            
            # Cache in memory with KST time
            from datetime import timezone, timedelta
            kst = timezone(timedelta(hours=9))
            self._cached_whitelist = whitelist
            self._last_updated = datetime.now(kst)
            
            # Persist to database
            self._save_to_db(whitelist)
            
            logger.info(f"Whitelist refreshed: {len(whitelist)} coins (from top 50 market cap)")
            return whitelist, added, removed
            
        except Exception as e:
            logger.error(f"Error refreshing whitelist: {e}")
            return self._cached_whitelist, [], []
    
    def _save_to_db(self, whitelist: List[Dict]):
        """Save whitelist to database"""
        try:
            from datetime import timezone, timedelta
            # KST = UTC+9
            kst = timezone(timedelta(hours=9))
            now_kst = datetime.now(kst)
            
            db = SessionLocal()
            set_setting(db, WHITELIST_CACHE_KEY, json.dumps(whitelist))
            set_setting(db, WHITELIST_UPDATED_KEY, now_kst.isoformat())
            db.close()
        except Exception as e:
            logger.error(f"Failed to save whitelist to DB: {e}")
    
    def _load_from_db(self) -> List[Dict]:
        """Load cached whitelist from database"""
        try:
            db = SessionLocal()
            cached = get_setting(db, WHITELIST_CACHE_KEY)
            updated_at = get_setting(db, WHITELIST_UPDATED_KEY)
            db.close()
            
            if cached:
                self._cached_whitelist = json.loads(cached)
                if updated_at:
                    self._last_updated = datetime.fromisoformat(updated_at)
                return self._cached_whitelist
        except Exception as e:
            logger.error(f"Failed to load whitelist from DB: {e}")
        return []
    
    # Cache TTL in seconds (5 minutes)
    CACHE_TTL_SECONDS = 300
    
    def get_whitelist(self) -> List[Dict]:
        """Get current whitelist (from cache or DB)"""
        from datetime import timezone, timedelta
        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        
        # Check if we have a cache
        if not self._cached_whitelist:
            self._load_from_db()
        
        # If still empty, do initial refresh
        if not self._cached_whitelist:
            whitelist, _, _ = self.refresh_whitelist()
            return whitelist
        
        # ★ Check cache freshness - refresh if older than TTL
        if self._last_updated:
            # Handle timezone-aware comparison
            last_updated = self._last_updated
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=kst)
            
            elapsed = (now - last_updated).total_seconds()
            if elapsed > self.CACHE_TTL_SECONDS:
                logger.info(f"Whitelist cache expired ({elapsed:.0f}s > {self.CACHE_TTL_SECONDS}s), refreshing...")
                whitelist, _, _ = self.refresh_whitelist()
                return whitelist
        else:
            # No last_updated timestamp - refresh to be safe
            whitelist, _, _ = self.refresh_whitelist()
            return whitelist
        
        return self._cached_whitelist
    
    def get_whitelist_markets(self) -> List[str]:
        """Get list of market codes only"""
        whitelist = self.get_whitelist()
        return [coin['market'] for coin in whitelist]
    
    def is_in_whitelist(self, market: str) -> bool:
        """Check if a market is in the whitelist"""
        return market in self.get_whitelist_markets()
    
    def get_last_updated(self) -> Optional[str]:
        """Get last update timestamp"""
        if self._last_updated:
            return self._last_updated.isoformat()
        return None
    
    def update_coin_status(self, market: str, status: str):
        """Update status of a coin in whitelist (watching/pending_buy/holding)"""
        for coin in self._cached_whitelist:
            if coin['market'] == market:
                coin['status'] = status
                break


# Global instance
whitelist_service = WhitelistService()

