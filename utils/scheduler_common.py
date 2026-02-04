"""
Scheduler Common Utilities
Shared functions between Upbit and Bybit schedulers
"""
from datetime import datetime
from typing import Tuple
from utils.timezone import now_kst

# ===================
# Candle Close Settings
# ===================
# 4H candle close hours (KST)
CANDLE_CLOSE_HOURS_4H = [1, 5, 9, 13, 17, 21]
# Window after candle close to allow trades (in minutes)
CANDLE_CLOSE_WINDOW_MINUTES = 30


def is_within_candle_close_window(timeframe: str) -> Tuple[bool, str]:
    """
    Check if current time is within the allowed window after candle close.
    
    Args:
        timeframe: '1D' for daily, '4H' for 4-hour
        
    Returns:
        Tuple of (is_allowed, reason)
    """
    now = now_kst()
    hour = now.hour
    minute = now.minute
    
    if timeframe == "1D":
        # Daily candle closes at 09:00 KST
        # Allow trades from 09:00 to 09:30
        if hour == 9 and minute < CANDLE_CLOSE_WINDOW_MINUTES:
            return True, f"1D 캔들 마감 후 {minute}분"
        else:
            return False, f"1D 매수 허용 시간: 09:00~09:30 (현재: {hour:02d}:{minute:02d})"
    
    elif timeframe == "4H":
        # 4H candle closes at 01:00, 05:00, 09:00, 13:00, 17:00, 21:00 KST
        for close_hour in CANDLE_CLOSE_HOURS_4H:
            if hour == close_hour and minute < CANDLE_CLOSE_WINDOW_MINUTES:
                return True, f"4H 캔들 마감 후 {minute}분 ({close_hour:02d}:00)"
        
        # Not in any window
        next_close = min([h for h in CANDLE_CLOSE_HOURS_4H if h > hour] or [CANDLE_CLOSE_HOURS_4H[0] + 24])
        return False, f"4H 매수 허용 시간: 마감 후 30분 (다음: {next_close % 24:02d}:00)"
    
    else:
        # Unknown timeframe - allow by default
        return True, "알 수 없는 타임프레임"
