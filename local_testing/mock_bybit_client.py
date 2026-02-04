"""
Mock Bybit Client
Bybit USDT Perpetual API와 동일한 인터페이스를 제공하는 가상 클라이언트
Long/Short 양방향 거래 지원
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd
import numpy as np

from local_testing.config import (
    DEFAULT_BYBIT_BALANCE,
    BYBIT_FEE_RATE,
    MIN_CANDLE_HISTORY
)


class MockBybitClient:
    """
    Bybit USDT Perpetual Mock 클라이언트
    
    Long/Short 양방향 선물 거래를 시뮬레이션합니다.
    BybitClient와 동일한 메서드 시그니처를 제공합니다.
    """
    
    _instance = None
    
    def __init__(self, scenario_data: dict = None):
        """
        Args:
            scenario_data: 테스트 시나리오 데이터
                - candles: {symbol: {interval: [캔들 리스트]}}
                - initial_balance: 초기 USDT 잔고
        """
        self.scenario = scenario_data or {}
        self.tick = 0
        self.wallet_balance = self.scenario.get('initial_balance', DEFAULT_BYBIT_BALANCE)
        self.positions = {}  # {symbol: {'side': 'Buy/Sell', 'qty': x, 'entry_price': y, 'leverage': z}}
        self.leverage = 5
        self.trade_history = []
        
        self._candles = self.scenario.get('candles', {})
        
        # API 인증 상태 (Mock은 항상 인증됨)
        self._api_key = None
        self._api_secret = None
    
    @classmethod
    def get_instance(cls):
        """싱글톤 인스턴스 반환"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def set_credentials(self, api_key: str, api_secret: str, testnet: bool = False):
        """API 인증 설정 (Mock에서는 저장만 함)"""
        self._api_key = api_key
        self._api_secret = api_secret
    
    def set_scenario(self, scenario_data: dict):
        """시나리오 데이터 설정"""
        self.scenario = scenario_data
        self.tick = MIN_CANDLE_HISTORY
        self.wallet_balance = scenario_data.get('initial_balance', DEFAULT_BYBIT_BALANCE)
        self.positions = {}
        self.trade_history = []
        self._candles = scenario_data.get('candles', {})
    
    def advance_tick(self) -> bool:
        """다음 캔들로 시간 진행"""
        symbol = self._get_first_symbol()
        if symbol:
            max_tick = len(self._candles.get(symbol, {}).get('D', [])) - 1
            if self.tick < max_tick:
                self.tick += 1
                return True
        return False
    
    def has_more_candles(self) -> bool:
        """남은 캔들이 있는지 확인"""
        symbol = self._get_first_symbol()
        if symbol:
            max_tick = len(self._candles.get(symbol, {}).get('D', [])) - 1
            return self.tick < max_tick
        return False
    
    def _get_first_symbol(self) -> Optional[str]:
        """첫 번째 심볼 반환"""
        if self._candles:
            return list(self._candles.keys())[0]
        return None
    
    def get_current_tick(self) -> int:
        """현재 틱 반환"""
        return self.tick
    
    # ==========================================
    # BybitClient 호환 메서드들
    # ==========================================
    
    @staticmethod
    def get_ohlcv(symbol: str, interval: str = "D", limit: int = 100, max_retries: int = 3) -> pd.DataFrame:
        """OHLCV 캔들 데이터 반환 (정적 - 빈 DataFrame)"""
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume', 'timestamp'])
    
    def get_ohlcv_instance(self, symbol: str, interval: str = "D", limit: int = 100) -> pd.DataFrame:
        """OHLCV 캔들 데이터 반환 (인스턴스)"""
        symbol_candles = self._candles.get(symbol, {}).get(interval, [])
        
        if not symbol_candles:
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume', 'timestamp'])
        
        # 현재 tick까지만
        available_candles = symbol_candles[:self.tick + 1]
        candles_to_return = available_candles[-limit:]
        
        df = pd.DataFrame(candles_to_return)
        
        # 컬럼명 정규화
        column_map = {
            'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume',
            'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'
        }
        df = df.rename(columns=column_map)
        
        return df
    
    @staticmethod
    def get_current_price(symbols: List[str], max_retries: int = 3) -> Dict[str, float]:
        """현재 가격 반환 (정적)"""
        return {}
    
    def get_current_price_instance(self, symbols: List[str]) -> Dict[str, float]:
        """현재 가격 반환 (인스턴스)"""
        result = {}
        for symbol in symbols:
            candles = self._candles.get(symbol, {}).get('D', [])
            if candles and self.tick < len(candles):
                result[symbol] = candles[self.tick].get('c') or candles[self.tick].get('close', 0)
        return result
    
    def get_wallet_balance(self) -> Dict[str, float]:
        """USDT 지갑 잔고"""
        # 미실현 손익 계산
        unrealized_pnl = self._calculate_unrealized_pnl()
        
        return {
            'available': self.wallet_balance,
            'total': self.wallet_balance + unrealized_pnl,
            'unrealized_pnl': unrealized_pnl
        }
    
    def _calculate_unrealized_pnl(self) -> float:
        """미실현 손익 계산"""
        total_pnl = 0
        for symbol, pos in self.positions.items():
            prices = self.get_current_price_instance([symbol])
            if symbol in prices:
                current = prices[symbol]
                entry = pos['entry_price']
                qty = pos['qty']
                
                if pos['side'] == 'Buy':  # Long
                    pnl = (current - entry) * qty
                else:  # Short
                    pnl = (entry - current) * qty
                
                total_pnl += pnl * pos.get('leverage', self.leverage)
        
        return total_pnl
    
    def get_positions(self, symbol: str = None) -> List[Dict]:
        """오픈 포지션 조회"""
        positions = []
        
        for sym, pos in self.positions.items():
            if symbol is None or sym == symbol:
                prices = self.get_current_price_instance([sym])
                current_price = prices.get(sym, pos['entry_price'])
                
                # PnL 계산
                if pos['side'] == 'Buy':
                    unrealized_pnl = (current_price - pos['entry_price']) * pos['qty'] * pos.get('leverage', self.leverage)
                else:
                    unrealized_pnl = (pos['entry_price'] - current_price) * pos['qty'] * pos.get('leverage', self.leverage)
                
                positions.append({
                    'symbol': sym,
                    'side': pos['side'],
                    'size': str(pos['qty']),
                    'avgPrice': str(pos['entry_price']),
                    'leverage': str(pos.get('leverage', self.leverage)),
                    'unrealisedPnl': str(unrealized_pnl),
                    'stopLoss': str(pos.get('stop_loss', '')),
                    'takeProfit': str(pos.get('take_profit', ''))
                })
        
        return positions
    
    def set_leverage(self, symbol: str, leverage: int = 5) -> bool:
        """레버리지 설정"""
        self.leverage = leverage
        return True
    
    def place_order(
        self,
        symbol: str,
        side: str,  # "Buy" (Long) or "Sell" (Short)
        qty: float,
        order_type: str = "Market",
        leverage: int = 5,
        reduce_only: bool = False
    ) -> Dict:
        """
        선물 주문 (Long/Short)
        
        Args:
            symbol: 거래 심볼 (예: 'BTCUSDT')
            side: 'Buy' (롱 진입/숏 청산) or 'Sell' (숏 진입/롱 청산)
            qty: 수량 (base currency)
            leverage: 레버리지
            reduce_only: 청산 전용 여부
        """
        prices = self.get_current_price_instance([symbol])
        if symbol not in prices:
            return {'success': False, 'error': 'Price not available'}
        
        current_price = prices[symbol]
        
        # reduce_only: 기존 포지션 청산
        if reduce_only:
            if symbol not in self.positions:
                return {'success': False, 'error': 'No position to close'}
            
            return self._close_position_internal(symbol, qty, current_price, "reduce_only")
        
        # 신규 포지션 진입
        # 필요 마진 계산
        required_margin = (qty * current_price) / leverage
        fee = qty * current_price * BYBIT_FEE_RATE
        
        if required_margin + fee > self.wallet_balance:
            return {'success': False, 'error': 'Insufficient margin'}
        
        # 마진 차감
        self.wallet_balance -= (required_margin + fee)
        
        # 포지션 생성
        self.positions[symbol] = {
            'side': side,
            'qty': qty,
            'entry_price': current_price,
            'leverage': leverage,
            'margin': required_margin,
            'stop_loss': None,
            'take_profit': None
        }
        
        # 거래 기록
        direction = 'long_open' if side == 'Buy' else 'short_open'
        trade = {
            'side': direction,
            'symbol': symbol,
            'price': current_price,
            'qty': qty,
            'leverage': leverage,
            'margin': required_margin,
            'fee': fee,
            'timestamp': datetime.now(),
            'tick': self.tick
        }
        self.trade_history.append(trade)
        
        return {
            'success': True,
            'orderId': f"mock-{direction}-{len(self.trade_history)}",
            'symbol': symbol,
            'side': side,
            'orderType': order_type,
            'qty': qty,
            'price': current_price
        }
    
    def close_position(self, symbol: str, qty: float = None) -> Dict:
        """포지션 청산"""
        if symbol not in self.positions:
            return {'success': False, 'error': 'No position'}
        
        prices = self.get_current_price_instance([symbol])
        if symbol not in prices:
            return {'success': False, 'error': 'Price not available'}
        
        current_price = prices[symbol]
        close_qty = qty or self.positions[symbol]['qty']
        
        return self._close_position_internal(symbol, close_qty, current_price, "manual")
    
    def close_at_price(self, symbol: str, price: float, reason: str = "") -> Dict:
        """지정 가격에 청산 (SL/TP 시뮬레이션용)"""
        if symbol not in self.positions:
            return {'success': False, 'error': 'No position'}
        
        return self._close_position_internal(
            symbol, 
            self.positions[symbol]['qty'], 
            price, 
            reason
        )
    
    def _close_position_internal(self, symbol: str, qty: float, price: float, reason: str) -> Dict:
        """내부 청산 로직"""
        pos = self.positions[symbol]
        
        # PnL 계산
        if pos['side'] == 'Buy':  # Long
            pnl = (price - pos['entry_price']) * qty * pos['leverage']
            direction = 'long_close'
        else:  # Short
            pnl = (pos['entry_price'] - price) * qty * pos['leverage']
            direction = 'short_close'
        
        pnl_percent = ((price - pos['entry_price']) / pos['entry_price']) * 100
        if pos['side'] == 'Sell':  # Short은 반대
            pnl_percent = -pnl_percent
        
        # 수수료
        fee = qty * price * BYBIT_FEE_RATE
        
        # 마진 반환 + PnL
        margin_return = pos['margin'] + pnl - fee
        self.wallet_balance += margin_return
        
        # 포지션 제거
        del self.positions[symbol]
        
        # 거래 기록
        trade = {
            'side': direction,
            'symbol': symbol,
            'price': price,
            'qty': qty,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
            'fee': fee,
            'reason': reason,
            'timestamp': datetime.now(),
            'tick': self.tick
        }
        self.trade_history.append(trade)
        
        return {
            'success': True,
            'orderId': f"mock-{direction}-{len(self.trade_history)}",
            'symbol': symbol,
            'closedQty': qty,
            'closedPrice': price,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
            'reason': reason
        }
    
    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: float = None,
        take_profit: float = None,
        position_idx: int = 0
    ) -> Dict:
        """SL/TP 설정"""
        if symbol not in self.positions:
            return {'success': False, 'error': 'No position'}
        
        if stop_loss:
            self.positions[symbol]['stop_loss'] = stop_loss
        if take_profit:
            self.positions[symbol]['take_profit'] = take_profit
        
        return {'success': True}
    
    @staticmethod
    def get_funding_rate(symbol: str) -> Dict:
        """펀딩비 조회 (Mock: 0% 반환)"""
        return {
            'fundingRate': '0.0001',
            'fundingRateTimestamp': str(datetime.now().timestamp()),
            'nextFundingTime': str((datetime.now().timestamp() + 28800) * 1000)
        }
    
    def get_closed_pnl(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        """청산 기록 조회"""
        closed = [t for t in self.trade_history if 'pnl' in t]
        if symbol:
            closed = [t for t in closed if t['symbol'] == symbol]
        return closed[-limit:]
    
    # ==========================================
    # 테스트 유틸리티 메서드
    # ==========================================
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """포지션 조회"""
        return self.positions.get(symbol)
    
    def get_all_positions(self) -> Dict:
        """전체 포지션 조회"""
        return self.positions.copy()
    
    def get_trade_history(self) -> List[Dict]:
        """거래 기록 조회"""
        return self.trade_history.copy()
    
    def get_pnl_summary(self) -> Dict:
        """손익 요약"""
        trades_with_pnl = [t for t in self.trade_history if 'pnl' in t]
        
        total_pnl = sum(t['pnl'] for t in trades_with_pnl) if trades_with_pnl else 0
        total_pnl_percent = sum(t['pnl_percent'] for t in trades_with_pnl) if trades_with_pnl else 0
        
        return {
            'total_trades': len(self.trade_history),
            'entries': len([t for t in self.trade_history if 'open' in t.get('side', '')]),
            'exits': len([t for t in self.trade_history if 'close' in t.get('side', '')]),
            'total_pnl': total_pnl,
            'total_pnl_percent': total_pnl_percent,
            'final_balance': self.wallet_balance
        }
    
    def reset(self):
        """상태 초기화"""
        self.tick = MIN_CANDLE_HISTORY
        self.wallet_balance = self.scenario.get('initial_balance', DEFAULT_BYBIT_BALANCE)
        self.positions = {}
        self.trade_history = []
