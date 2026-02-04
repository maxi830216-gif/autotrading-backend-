"""
Test Scheduler
로컬 테스트용 스케줄러 - 전략 분석 및 매수/매도 시뮬레이션
"""
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import pandas as pd

# 프로젝트 루트 경로 추가
backend_path = Path(__file__).parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from local_testing.mock_upbit_client import MockUpbitClient
from local_testing.mock_bybit_client import MockBybitClient
from local_testing.config import MIN_CANDLE_HISTORY, UPBIT_FEE_RATE, BYBIT_FEE_RATE


@dataclass
class Position:
    """포지션 정보"""
    market: str
    direction: str  # 'long' or 'short'
    entry_price: float
    volume: float
    stop_loss: float
    take_profit: float
    entry_tick: int
    strategy: str


@dataclass
class TradeResult:
    """거래 결과"""
    market: str
    direction: str
    entry_price: float
    exit_price: float
    entry_tick: int
    exit_tick: int
    pnl_percent: float
    exit_reason: str  # 'take_profit', 'stop_loss', 'end_of_data'
    strategy: str


@dataclass
class TestResult:
    """테스트 결과"""
    scenario_name: str
    strategy: str
    exchange: str
    expected_exit: Optional[str]
    actual_entry: bool
    actual_exit_reason: Optional[str]
    passed: bool
    trades: List[TradeResult] = field(default_factory=list)
    error: Optional[str] = None
    final_balance: float = 0.0
    pnl_percent: float = 0.0


class LocalTestScheduler:
    """
    로컬 테스트용 스케줄러
    
    프로덕션 전략 클래스를 임포트하여 테스트합니다.
    """
    
    def __init__(self, exchange: str, scenario: Dict):
        """
        Args:
            exchange: 'upbit' or 'bybit'
            scenario: 테스트 시나리오 데이터
        """
        self.exchange = exchange
        self.scenario = scenario
        self.positions: List[Position] = []
        self.trades: List[TradeResult] = []
        
        # Mock 클라이언트 설정
        if exchange == 'upbit':
            self.mock_client = MockUpbitClient(scenario)
            self.market = scenario.get('market', 'KRW-BTC')
            self.fee_rate = UPBIT_FEE_RATE
        else:
            self.mock_client = MockBybitClient(scenario)
            self.market = scenario.get('symbol', 'BTCUSDT')
            self.fee_rate = BYBIT_FEE_RATE
        
        # 시작 틱 설정
        self.mock_client.tick = MIN_CANDLE_HISTORY
        
        # 전략 로드
        self.strategy_name = scenario.get('strategy', 'morning_star')
        self.strategy = self._load_strategy()
    
    def _load_strategy(self):
        """전략 클래스 로드"""
        strategy_map = {
            # Long 전략
            'morning_star': ('services.strategy_morning', 'MorningStarStrategy'),
            'divergence': ('services.strategy_divergence', 'BullishDivergenceStrategy'),
            'harmonic': ('services.strategy_harmonic', 'HarmonicPatternStrategy'),
            'squirrel': ('services.strategy_squirrel', 'SquirrelStrategy'),
            'inverted_hammer': ('services.strategy_inverted_hammer', 'InvertedHammerStrategy'),
            'leading_diagonal': ('services.strategy_leading_diagonal', 'LeadingDiagonalStrategy'),
            # Short 전략
            'shooting_star': ('services.strategy_shooting_star', 'ShootingStarStrategy'),
            'bearish_divergence': ('services.strategy_bearish_divergence', 'BearishDivergenceStrategy'),
            'evening_star': ('services.strategy_evening_star', 'EveningStarStrategy'),
            'bearish_engulfing': ('services.strategy_bearish_engulfing', 'BearishEngulfingStrategy'),
            'leading_diagonal_breakdown': ('services.strategy_leading_diagonal_breakdown', 'LeadingDiagonalBreakdownStrategy'),
        }
        
        if self.strategy_name not in strategy_map:
            return None
        
        module_name, class_name = strategy_map[self.strategy_name]
        
        try:
            import importlib
            module = importlib.import_module(module_name)
            strategy_class = getattr(module, class_name)
            return strategy_class()
        except Exception as e:
            print(f"⚠️ 전략 로드 실패: {self.strategy_name} - {e}")
            return None
    
    def _get_candles_df(self) -> pd.DataFrame:
        """현재 틱까지의 캔들 DataFrame 반환"""
        if self.exchange == 'upbit':
            return self.mock_client.get_ohlcv_instance(self.market, 'day', 100)
        else:
            return self.mock_client.get_ohlcv_instance(self.market, 'D', 100)
    
    def _analyze_strategy(self) -> Tuple[bool, Optional[Dict]]:
        """현재 캔들에서 전략 분석"""
        if not self.strategy:
            return False, None
        
        df = self._get_candles_df()
        if df.empty or len(df) < MIN_CANDLE_HISTORY:
            return False, None
        
        try:
            # 방법 1: analyze_df(df, symbol) - dict 반환 (MorningStar, InvertedHammer, Squirrel)
            if hasattr(self.strategy, 'analyze_df'):
                result = self.strategy.analyze_df(df, self.market)
                if result:
                    action = result.get('action', '').upper()
                    if action in ['BUY', 'SELL']:
                        ref_data = result.get('reference_data', {})
                        return True, {
                            'confidence': result.get('confidence', 1.0),
                            'stop_loss': ref_data.get('stop_loss'),
                            'take_profit': ref_data.get('take_profit'),
                            'direction': 'long' if action == 'BUY' else 'short',
                            'reason': result.get('reason', '')
                        }
            
            # 방법 2: analyze(df, market) - tuple (bool, float, dict) 반환 (Divergence, Harmonic, LeadingDiagonal)
            if hasattr(self.strategy, 'analyze'):
                result = self.strategy.analyze(df, self.market)
                # tuple 형태: (is_signal, confidence, info_dict)
                if isinstance(result, tuple) and len(result) >= 3:
                    is_signal, confidence, info = result
                    if is_signal and info:
                        # Short 전략 판별 (전략 이름 기반)
                        short_strategies = ['shooting_star', 'bearish_divergence', 'evening_star', 
                                           'bearish_engulfing', 'leading_diagonal_breakdown']
                        direction = 'short' if self.strategy_name in short_strategies else 'long'
                        
                        return True, {
                            'confidence': confidence,
                            'stop_loss': info.get('stop_loss'),
                            'take_profit': info.get('take_profit'),
                            'direction': direction,
                            'reason': info.get('reason', '')
                        }
                        
        except Exception as e:
            # 디버그용 - 필요시 주석 해제
            # print(f"⚠️ 전략 분석 오류: {e}")
            pass
        
        return False, None
    
    def _execute_entry(self, signal: Dict):
        """진입 실행"""
        current_price = self._get_current_price()
        if not current_price:
            return
        
        direction = signal.get('direction', 'long')
        stop_loss = signal.get('stop_loss') or self._calculate_default_sl(current_price, direction)
        take_profit = signal.get('take_profit') or self._calculate_default_tp(current_price, direction)
        
        # 주문 수량 계산
        if self.exchange == 'upbit':
            order_amount = self.mock_client.balance * 0.3  # 30%
            volume = order_amount / current_price
        else:
            order_amount = self.mock_client.wallet_balance * 0.3 * 5  # 30% * 5x leverage
            volume = order_amount / current_price
        
        position = Position(
            market=self.market,
            direction=direction,
            entry_price=current_price,
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_tick=self.mock_client.tick,
            strategy=self.strategy_name
        )
        
        self.positions.append(position)
        
        # 잔고 차감
        if self.exchange == 'upbit':
            self.mock_client.balance -= order_amount
        else:
            self.mock_client.wallet_balance -= order_amount / 5  # 마진만 차감
    
    def _check_exit(self, position: Position) -> Tuple[bool, str, float]:
        """청산 조건 체크"""
        candles = self._get_candle_at_tick()
        if not candles:
            return False, '', 0
        
        high = candles.get('high') or candles.get('h', 0)
        low = candles.get('low') or candles.get('l', 0)
        
        if position.direction == 'long':
            if low <= position.stop_loss:
                return True, 'stop_loss', position.stop_loss
            if high >= position.take_profit:
                return True, 'take_profit', position.take_profit
        else:  # short
            if high >= position.stop_loss:
                return True, 'stop_loss', position.stop_loss
            if low <= position.take_profit:
                return True, 'take_profit', position.take_profit
        
        return False, '', 0
    
    def _execute_exit(self, position: Position, reason: str, exit_price: float):
        """청산 실행"""
        if position.direction == 'long':
            pnl_percent = ((exit_price - position.entry_price) / position.entry_price) * 100
        else:
            pnl_percent = ((position.entry_price - exit_price) / position.entry_price) * 100
        
        trade = TradeResult(
            market=position.market,
            direction=position.direction,
            entry_price=position.entry_price,
            exit_price=exit_price,
            entry_tick=position.entry_tick,
            exit_tick=self.mock_client.tick,
            pnl_percent=pnl_percent,
            exit_reason=reason,
            strategy=position.strategy
        )
        
        self.trades.append(trade)
        self.positions.remove(position)
        
        # 잔고 복원
        if self.exchange == 'upbit':
            self.mock_client.balance += position.volume * exit_price * (1 - self.fee_rate)
        else:
            pnl = (exit_price - position.entry_price) * position.volume
            if position.direction == 'short':
                pnl = -pnl
            self.mock_client.wallet_balance += (position.volume * position.entry_price / 5) + pnl
    
    def _get_current_price(self) -> Optional[float]:
        """현재 가격 반환"""
        candle = self._get_candle_at_tick()
        if candle:
            return candle.get('close') or candle.get('c', 0)
        return None
    
    def _get_candle_at_tick(self) -> Optional[Dict]:
        """현재 틱의 캔들 반환"""
        if self.exchange == 'upbit':
            candles = self.scenario.get('candles', {}).get(self.market, {}).get('day', [])
        else:
            candles = self.scenario.get('candles', {}).get(self.market, {}).get('D', [])
        
        if candles and self.mock_client.tick < len(candles):
            return candles[self.mock_client.tick]
        return None
    
    def _calculate_default_sl(self, price: float, direction: str) -> float:
        """기본 손절가 계산"""
        if direction == 'long':
            return price * 0.97  # -3%
        return price * 1.03  # +3% for short
    
    def _calculate_default_tp(self, price: float, direction: str) -> float:
        """기본 익절가 계산"""
        if direction == 'long':
            return price * 1.05  # +5%
        return price * 0.95  # -5% for short
    
    def run_single_tick(self) -> Dict:
        """단일 틱 실행"""
        result = {
            'tick': self.mock_client.tick,
            'entry': False,
            'exit': False,
            'exit_reason': None
        }
        
        # 1. 기존 포지션 청산 체크
        for position in self.positions[:]:  # 복사본으로 순회
            should_exit, reason, exit_price = self._check_exit(position)
            if should_exit:
                self._execute_exit(position, reason, exit_price)
                result['exit'] = True
                result['exit_reason'] = reason
        
        # 2. 새로운 진입 신호 체크 (포지션이 없을 때만)
        if not self.positions:
            is_signal, signal_data = self._analyze_strategy()
            if is_signal and signal_data:
                self._execute_entry(signal_data)
                result['entry'] = True
        
        return result
    
    def run_full_scenario(self) -> TestResult:
        """전체 시나리오 실행"""
        expected = self.scenario.get('expected', {})
        expected_exit = expected.get('exit_reason')
        
        try:
            while self.mock_client.has_more_candles():
                self.run_single_tick()
                self.mock_client.advance_tick()
            
            # 마지막 틱도 실행
            self.run_single_tick()
            
            # 남은 포지션 강제 청산
            for position in self.positions[:]:
                current_price = self._get_current_price() or position.entry_price
                self._execute_exit(position, 'end_of_data', current_price)
            
            # 결과 분석
            actual_entry = len(self.trades) > 0
            actual_exit_reason = self.trades[-1].exit_reason if self.trades else None
            
            # 패스 여부 판단
            if expected_exit is None:
                # no_signal 시나리오
                passed = not actual_entry
            else:
                passed = actual_entry and actual_exit_reason == expected_exit
            
            total_pnl = sum(t.pnl_percent for t in self.trades)
            
            final_balance = (
                self.mock_client.balance if self.exchange == 'upbit' 
                else self.mock_client.wallet_balance
            )
            
            return TestResult(
                scenario_name=self.scenario.get('name', 'unknown'),
                strategy=self.strategy_name,
                exchange=self.exchange,
                expected_exit=expected_exit,
                actual_entry=actual_entry,
                actual_exit_reason=actual_exit_reason,
                passed=passed,
                trades=self.trades,
                final_balance=final_balance,
                pnl_percent=total_pnl
            )
            
        except Exception as e:
            return TestResult(
                scenario_name=self.scenario.get('name', 'unknown'),
                strategy=self.strategy_name,
                exchange=self.exchange,
                expected_exit=expected_exit,
                actual_entry=False,
                actual_exit_reason=None,
                passed=False,
                error=str(e)
            )
