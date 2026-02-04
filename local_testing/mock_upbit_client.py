"""
Mock Upbit Client
Upbit API와 동일한 인터페이스를 제공하는 가상 클라이언트
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from local_testing.config import (
    DEFAULT_UPBIT_BALANCE, 
    UPBIT_FEE_RATE,
    MIN_CANDLE_HISTORY
)


class MockUpbitClient:
    """
    Upbit API Mock 클라이언트
    
    실제 API 호출 없이 시나리오 데이터를 기반으로 동작합니다.
    UpbitClient와 동일한 메서드 시그니처를 제공합니다.
    """
    
    def __init__(self, scenario_data: dict = None):
        """
        Args:
            scenario_data: 테스트 시나리오 데이터
                - candles: {market: {interval: [캔들 리스트]}}
                - initial_balance: 초기 잔고
        """
        self.scenario = scenario_data or {}
        self.tick = 0  # 현재 캔들 인덱스
        self.balance = self.scenario.get('initial_balance', DEFAULT_UPBIT_BALANCE)
        self.positions = {}  # {market: {'volume': x, 'avg_price': y}}
        self.trade_history = []  # 거래 기록
        
        # 캔들 데이터 준비
        self._candles = self.scenario.get('candles', {})
        
    def set_scenario(self, scenario_data: dict):
        """시나리오 데이터 설정"""
        self.scenario = scenario_data
        self.tick = MIN_CANDLE_HISTORY  # 히스토리 이후부터 시작
        self.balance = scenario_data.get('initial_balance', DEFAULT_UPBIT_BALANCE)
        self.positions = {}
        self.trade_history = []
        self._candles = scenario_data.get('candles', {})
    
    def advance_tick(self) -> bool:
        """다음 캔들로 시간 진행"""
        market = self._get_first_market()
        if market:
            max_tick = len(self._candles.get(market, {}).get('day', [])) - 1
            if self.tick < max_tick:
                self.tick += 1
                return True
        return False
    
    def has_more_candles(self) -> bool:
        """남은 캔들이 있는지 확인"""
        market = self._get_first_market()
        if market:
            max_tick = len(self._candles.get(market, {}).get('day', [])) - 1
            return self.tick < max_tick
        return False
    
    def _get_first_market(self) -> Optional[str]:
        """첫 번째 마켓 반환"""
        if self._candles:
            return list(self._candles.keys())[0]
        return None
    
    def get_current_tick(self) -> int:
        """현재 틱 반환"""
        return self.tick
    
    # ==========================================
    # UpbitClient 호환 메서드들
    # ==========================================
    
    @staticmethod
    def get_ohlcv(ticker: str, interval: str = "day", count: int = 200) -> pd.DataFrame:
        """
        OHLCV 캔들 데이터 반환 (정적 메서드로 호출 시 빈 DataFrame 반환)
        
        실제 테스트에서는 인스턴스 메서드로 호출해야 합니다.
        """
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    
    def get_ohlcv_instance(self, ticker: str, interval: str = "day", count: int = 200) -> pd.DataFrame:
        """
        OHLCV 캔들 데이터 반환 (인스턴스 메서드)
        
        현재 tick까지의 캔들만 반환합니다.
        """
        market_candles = self._candles.get(ticker, {}).get(interval, [])
        
        if not market_candles:
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        
        # 현재 tick까지만 (마감된 캔들)
        available_candles = market_candles[:self.tick + 1]
        
        # 요청한 count만큼
        candles_to_return = available_candles[-count:]
        
        df = pd.DataFrame(candles_to_return)
        
        # 컬럼명 정규화
        column_map = {
            'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume',
            'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'
        }
        df = df.rename(columns=column_map)
        
        return df
    
    @staticmethod
    def get_current_price(markets: List[str], max_retries: int = 3) -> Dict[str, float]:
        """현재 가격 반환 (정적 - 빈 dict 반환)"""
        return {}
    
    def get_current_price_instance(self, markets: List[str]) -> Dict[str, float]:
        """현재 가격 반환 (인스턴스)"""
        result = {}
        for market in markets:
            candles = self._candles.get(market, {}).get('day', [])
            if candles and self.tick < len(candles):
                result[market] = candles[self.tick].get('c') or candles[self.tick].get('close', 0)
        return result
    
    def get_balance(self, ticker: str = "KRW") -> float:
        """잔고 조회"""
        if ticker == "KRW":
            return self.balance
        
        market = f"KRW-{ticker}"
        if market in self.positions:
            return self.positions[market]['volume']
        return 0.0
    
    def get_balances(self) -> List[Dict]:
        """전체 잔고 조회"""
        balances = [{'currency': 'KRW', 'balance': str(self.balance)}]
        for market, pos in self.positions.items():
            currency = market.replace('KRW-', '')
            balances.append({
                'currency': currency,
                'balance': str(pos['volume']),
                'avg_buy_price': str(pos['avg_price'])
            })
        return balances
    
    def get_avg_buy_price(self, ticker: str) -> float:
        """평균 매수가 조회"""
        market = f"KRW-{ticker}" if not ticker.startswith("KRW-") else ticker
        if market in self.positions:
            return self.positions[market]['avg_price']
        return 0.0
    
    def buy_market_order(self, ticker: str, price: float) -> Dict:
        """
        시장가 매수 (가상)
        
        Args:
            ticker: 마켓 티커 (예: 'KRW-BTC')
            price: 매수 금액 (KRW)
            
        Returns:
            주문 결과
        """
        if price > self.balance:
            return {'error': 'Insufficient balance', 'success': False}
        
        # 현재 가격 가져오기
        current_prices = self.get_current_price_instance([ticker])
        if ticker not in current_prices:
            return {'error': 'Price not available', 'success': False}
        
        current_price = current_prices[ticker]
        
        # 수수료 적용
        fee = price * UPBIT_FEE_RATE
        actual_amount = price - fee
        volume = actual_amount / current_price
        
        # 잔고 차감
        self.balance -= price
        
        # 포지션 추가/업데이트
        if ticker in self.positions:
            # 평균 단가 재계산
            existing_vol = self.positions[ticker]['volume']
            existing_avg = self.positions[ticker]['avg_price']
            total_vol = existing_vol + volume
            new_avg = (existing_vol * existing_avg + volume * current_price) / total_vol
            self.positions[ticker] = {'volume': total_vol, 'avg_price': new_avg}
        else:
            self.positions[ticker] = {'volume': volume, 'avg_price': current_price}
        
        # 거래 기록
        trade = {
            'side': 'buy',
            'market': ticker,
            'price': current_price,
            'volume': volume,
            'total_amount': price,
            'fee': fee,
            'timestamp': datetime.now(),
            'tick': self.tick
        }
        self.trade_history.append(trade)
        
        return {
            'success': True,
            'uuid': f"mock-buy-{len(self.trade_history)}",
            'side': 'bid',
            'ord_type': 'price',
            'price': price,
            'executed_volume': volume,
            'executed_price': current_price
        }
    
    def sell_market_order(self, ticker: str, volume: float) -> Dict:
        """
        시장가 매도 (가상)
        
        Args:
            ticker: 마켓 티커
            volume: 매도 수량
            
        Returns:
            주문 결과
        """
        if ticker not in self.positions:
            return {'error': 'No position', 'success': False}
        
        if volume > self.positions[ticker]['volume']:
            return {'error': 'Insufficient volume', 'success': False}
        
        # 현재 가격
        current_prices = self.get_current_price_instance([ticker])
        if ticker not in current_prices:
            return {'error': 'Price not available', 'success': False}
        
        current_price = current_prices[ticker]
        
        # 매도 금액 계산
        gross_amount = volume * current_price
        fee = gross_amount * UPBIT_FEE_RATE
        net_amount = gross_amount - fee
        
        # 잔고 증가
        self.balance += net_amount
        
        # 포지션 업데이트
        self.positions[ticker]['volume'] -= volume
        if self.positions[ticker]['volume'] <= 0:
            del self.positions[ticker]
        
        # 거래 기록
        trade = {
            'side': 'sell',
            'market': ticker,
            'price': current_price,
            'volume': volume,
            'total_amount': net_amount,
            'fee': fee,
            'timestamp': datetime.now(),
            'tick': self.tick
        }
        self.trade_history.append(trade)
        
        return {
            'success': True,
            'uuid': f"mock-sell-{len(self.trade_history)}",
            'side': 'ask',
            'ord_type': 'market',
            'volume': volume,
            'executed_volume': volume,
            'executed_price': current_price,
            'total_amount': net_amount
        }
    
    def sell_at_price(self, ticker: str, volume: float, price: float, reason: str = "") -> Dict:
        """
        지정 가격에 매도 (SL/TP 시뮬레이션용)
        
        Args:
            ticker: 마켓 티커
            volume: 매도 수량
            price: 체결 가격
            reason: 매도 사유
        """
        if ticker not in self.positions:
            return {'error': 'No position', 'success': False}
        
        # 매도 금액 계산
        gross_amount = volume * price
        fee = gross_amount * UPBIT_FEE_RATE
        net_amount = gross_amount - fee
        
        # PnL 계산
        entry_price = self.positions[ticker]['avg_price']
        pnl_percent = ((price - entry_price) / entry_price) * 100
        
        # 잔고 증가
        self.balance += net_amount
        
        # 포지션 업데이트
        self.positions[ticker]['volume'] -= volume
        if self.positions[ticker]['volume'] <= 0:
            del self.positions[ticker]
        
        # 거래 기록
        trade = {
            'side': 'sell',
            'market': ticker,
            'price': price,
            'volume': volume,
            'total_amount': net_amount,
            'fee': fee,
            'reason': reason,
            'pnl_percent': pnl_percent,
            'timestamp': datetime.now(),
            'tick': self.tick
        }
        self.trade_history.append(trade)
        
        return {
            'success': True,
            'uuid': f"mock-sell-{len(self.trade_history)}",
            'executed_price': price,
            'pnl_percent': pnl_percent,
            'reason': reason
        }
    
    # ==========================================
    # 테스트 유틸리티 메서드
    # ==========================================
    
    def get_position(self, market: str) -> Optional[Dict]:
        """포지션 조회"""
        return self.positions.get(market)
    
    def get_all_positions(self) -> Dict:
        """전체 포지션 조회"""
        return self.positions.copy()
    
    def get_trade_history(self) -> List[Dict]:
        """거래 기록 조회"""
        return self.trade_history.copy()
    
    def get_pnl_summary(self) -> Dict:
        """손익 요약"""
        total_pnl = 0
        trades_with_pnl = [t for t in self.trade_history if 'pnl_percent' in t]
        
        if trades_with_pnl:
            total_pnl = sum(t['pnl_percent'] for t in trades_with_pnl)
        
        return {
            'total_trades': len(self.trade_history),
            'buy_trades': len([t for t in self.trade_history if t['side'] == 'buy']),
            'sell_trades': len([t for t in self.trade_history if t['side'] == 'sell']),
            'total_pnl_percent': total_pnl,
            'final_balance': self.balance
        }
    
    def reset(self):
        """상태 초기화"""
        self.tick = MIN_CANDLE_HISTORY
        self.balance = self.scenario.get('initial_balance', DEFAULT_UPBIT_BALANCE)
        self.positions = {}
        self.trade_history = []
