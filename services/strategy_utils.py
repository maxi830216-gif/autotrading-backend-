"""
Strategy Common Utilities (Jan 2026 Redesign)

공통 가드 함수 및 유틸리티
- 포지션 방향 불변조건 검증
- risk 계산 (side별 방향식)
- SL/TP 검증
- 전략별 R:R 가드
"""

from typing import Tuple, Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)

# ★ 전략별 최소 R:R 설정
# - 1:2 목표 전략: ATR 버퍼 적용 후 실제 R:R 기준
# - 모든 전략은 최소 1:1 이상
MIN_RR_BY_STRATEGY = {
    # LONG 전략
    '샛별형': 1.2,           # morning_star (1:2 목표, 버퍼 후 ~1.3)
    '상승 다이버전스': 1.0,   # divergence (패턴+버퍼로 1.0~1.5)
    '하모닉 패턴': 1.2,       # harmonic (1:2 목표, 버퍼 후 ~1.3)
    '다람쥐꼬리': 1.0,        # squirrel (패턴 기반)
    '윗꼬리양봉': 1.0,        # inverted_hammer (1:1 목표)
    '리딩 다이아고날': 0.8,   # leading_diagonal (웨지 기반, 가변)
    
    # SHORT 전략
    '석별형': 1.2,           # evening_star (1:2 목표, 버퍼 후 ~1.3)
    '유성형': 1.0,           # shooting_star (1:1 목표)
    '하락 다이버전스': 0.8,   # bearish_divergence (Fib 기반, 가변)
    '하락장악형': 0.8,        # bearish_engulfing (Fib 기반, 가변)
    '리딩다이아 하단이탈': 0.5,  # leading_diagonal_breakdown (가변, 넓은 범위)
}

# 기본 최소 R:R (설정되지 않은 전략용)
DEFAULT_MIN_RR = 1.0


# ===== R:R 보장 헬퍼 함수 =====

def ensure_min_rr_long(
    entry_price: float,
    stop_loss: float,
    pattern_tp: float,
    min_rr: float = 1.5
) -> float:
    """
    LONG 포지션: TP가 최소 R:R을 충족하도록 조정
    
    패턴 기반 TP와 R:R 기반 최소 TP 중 더 좋은 값(더 높은 값) 선택
    
    Args:
        entry_price: 진입가
        stop_loss: 손절가 (ATR 버퍼 적용 후)
        pattern_tp: 패턴 기반 익절가 (피보나치 등)
        min_rr: 최소 R:R 비율 (기본 1.5)
    
    Returns:
        조정된 TP (pattern_tp와 min_rr_tp 중 더 높은 값)
    """
    sl_distance = entry_price - stop_loss
    if sl_distance <= 0:
        return pattern_tp  # 유효하지 않은 경우 원래 값 반환
    
    min_rr_tp = entry_price + (sl_distance * min_rr)
    return max(pattern_tp, min_rr_tp)


def ensure_min_rr_short(
    entry_price: float,
    stop_loss: float,
    pattern_tp: float,
    min_rr: float = 1.5
) -> float:
    """
    SHORT 포지션: TP가 최소 R:R을 충족하도록 조정
    
    패턴 기반 TP와 R:R 기반 최소 TP 중 더 좋은 값(더 낮은 값) 선택
    
    Args:
        entry_price: 진입가
        stop_loss: 손절가 (ATR 버퍼 적용 후)
        pattern_tp: 패턴 기반 익절가 (피보나치 등)
        min_rr: 최소 R:R 비율 (기본 1.5)
    
    Returns:
        조정된 TP (pattern_tp와 min_rr_tp 중 더 낮은 값)
    """
    sl_distance = stop_loss - entry_price
    if sl_distance <= 0:
        return pattern_tp  # 유효하지 않은 경우 원래 값 반환
    
    min_rr_tp = entry_price - (sl_distance * min_rr)
    return min(pattern_tp, min_rr_tp)


def validate_long_position(
    entry_price: float,
    stop_loss: float,
    take_profit: float
) -> Tuple[bool, str]:
    """
    LONG 포지션 SL/TP 불변조건 검증
    
    조건: SL < entry < TP
    
    Returns:
        (is_valid, error_message)
    """
    if stop_loss >= entry_price:
        return False, f"LONG SL >= entry ({stop_loss:.2f} >= {entry_price:.2f})"
    
    if take_profit <= entry_price:
        return False, f"LONG TP <= entry ({take_profit:.2f} <= {entry_price:.2f})"
    
    return True, ""


def validate_short_position(
    entry_price: float,
    stop_loss: float,
    take_profit: float
) -> Tuple[bool, str]:
    """
    SHORT 포지션 SL/TP 불변조건 검증
    
    조건: TP < entry < SL
    
    Returns:
        (is_valid, error_message)
    """
    if stop_loss <= entry_price:
        return False, f"SHORT SL <= entry ({stop_loss:.2f} <= {entry_price:.2f})"
    
    if take_profit >= entry_price:
        return False, f"SHORT TP >= entry ({take_profit:.2f} >= {entry_price:.2f})"
    
    return True, ""


def calculate_risk_long(entry_price: float, stop_loss: float) -> Tuple[float, bool]:
    """
    LONG 포지션 risk 계산
    
    risk = entry - stop_loss (양수여야 함)
    
    Returns:
        (risk, is_valid)
    """
    risk = entry_price - stop_loss
    return risk, risk > 0


def calculate_risk_short(entry_price: float, stop_loss: float) -> Tuple[float, bool]:
    """
    SHORT 포지션 risk 계산
    
    risk = stop_loss - entry (양수여야 함)
    
    Returns:
        (risk, is_valid)
    """
    risk = stop_loss - entry_price
    return risk, risk > 0


def validate_and_calculate_long(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    strategy_name: str = ""
) -> Tuple[bool, float, str]:
    """
    LONG 포지션 전체 검증 + risk 계산
    
    Returns:
        (is_valid, risk, error_message)
    """
    # 불변조건 검증
    is_valid, error_msg = validate_long_position(entry_price, stop_loss, take_profit)
    if not is_valid:
        logger.warning(f"[{strategy_name}] 불변조건 위반: {error_msg}")
        return False, 0.0, error_msg
    
    # risk 계산
    risk, risk_valid = calculate_risk_long(entry_price, stop_loss)
    if not risk_valid:
        error_msg = f"LONG risk <= 0 ({risk:.2f})"
        logger.warning(f"[{strategy_name}] {error_msg}")
        return False, 0.0, error_msg
    
    # ★ 전략별 R:R 가드
    min_rr = MIN_RR_BY_STRATEGY.get(strategy_name, DEFAULT_MIN_RR)
    reward = take_profit - entry_price
    if reward < risk * min_rr:
        actual_rr = reward / risk if risk > 0 else 0
        error_msg = f"R:R 불량 (1:{actual_rr:.2f} < 1:{min_rr})"
        logger.warning(f"[{strategy_name}] {error_msg}")
        return False, 0.0, error_msg
    
    return True, risk, ""


def validate_and_calculate_short(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    strategy_name: str = ""
) -> Tuple[bool, float, str]:
    """
    SHORT 포지션 전체 검증 + risk 계산
    
    Returns:
        (is_valid, risk, error_message)
    """
    # 불변조건 검증
    is_valid, error_msg = validate_short_position(entry_price, stop_loss, take_profit)
    if not is_valid:
        logger.warning(f"[{strategy_name}] 불변조건 위반: {error_msg}")
        return False, 0.0, error_msg
    
    # risk 계산
    risk, risk_valid = calculate_risk_short(entry_price, stop_loss)
    if not risk_valid:
        error_msg = f"SHORT risk <= 0 ({risk:.2f})"
        logger.warning(f"[{strategy_name}] {error_msg}")
        return False, 0.0, error_msg
    
    # ★ 전략별 R:R 가드
    min_rr = MIN_RR_BY_STRATEGY.get(strategy_name, DEFAULT_MIN_RR)
    reward = entry_price - take_profit  # SHORT: 익절은 진입가보다 낮음
    if reward < risk * min_rr:
        actual_rr = reward / risk if risk > 0 else 0
        error_msg = f"R:R 불량 (1:{actual_rr:.2f} < 1:{min_rr})"
        logger.warning(f"[{strategy_name}] {error_msg}")
        return False, 0.0, error_msg
    
    return True, risk, ""


# ===== check_exit 표준 함수 =====

def check_exit_long(
    position: Dict,
    current_price: float
) -> Tuple[bool, str, str]:
    """
    LONG 포지션 청산 조건 확인 (표준 시그니처)
    
    Args:
        position: {stop_loss, take_profit, entry_price, ...}
        current_price: 현재가
        
    Returns:
        (should_exit, reason, exit_type)
        exit_type: 'stop_loss', 'take_profit', 'none'
    """
    stop_loss = position.get('stop_loss', 0)
    take_profit = position.get('take_profit', float('inf'))
    entry_price = position.get('entry_price', current_price)
    
    profit_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
    
    if current_price <= stop_loss:
        return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", "stop_loss"
    
    if current_price >= take_profit:
        return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", "take_profit"
    
    return False, "", "none"


def check_exit_short(
    position: Dict,
    current_price: float
) -> Tuple[bool, str, str]:
    """
    SHORT 포지션 청산 조건 확인 (표준 시그니처)
    
    Args:
        position: {stop_loss, take_profit, entry_price, ...}
        current_price: 현재가
        
    Returns:
        (should_exit, reason, exit_type)
        exit_type: 'stop_loss', 'take_profit', 'none'
    """
    stop_loss = position.get('stop_loss', float('inf'))
    take_profit = position.get('take_profit', 0)
    entry_price = position.get('entry_price', current_price)
    
    profit_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
    
    if current_price >= stop_loss:
        return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", "stop_loss"
    
    if current_price <= take_profit:
        return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", "take_profit"
    
    return False, "", "none"
