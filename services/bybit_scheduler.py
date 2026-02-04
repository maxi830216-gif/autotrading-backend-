"""
Bybit Scheduler Service
Background tasks for Bybit futures trading
"""
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from models.database import SessionLocal, UserSettings, Position, SystemLog, TradeLog
from services.bybit_whitelist import bybit_whitelist_service
from services.bybit_order_manager import BybitOrderManager
from services.telegram_service import telegram_service

# Import strategies (Long)
from services.strategy_squirrel import squirrel_strategy
from services.strategy_morning import morning_star_strategy, morning_star_strategy_daily
from services.strategy_inverted_hammer import inverted_hammer_strategy, inverted_hammer_strategy_4h
from services.strategy_divergence import divergence_strategy
from services.strategy_harmonic import harmonic_strategy
from services.strategy_leading_diagonal import leading_diagonal_strategy

# Import strategies (Short)
from services.strategy_bearish_divergence import bearish_divergence_strategy
from services.strategy_evening_star import evening_star_strategy
from services.strategy_shooting_star import shooting_star_strategy
from services.strategy_bearish_engulfing import bearish_engulfing_strategy
from services.strategy_leading_diagonal_breakdown import leading_diagonal_breakdown_strategy

from utils.logger import setup_logger
from utils.timezone import now_kst, KST

logger = setup_logger(__name__)


# ===================
# Bot State for Bybit
# ===================
class BybitBotState:
    """Bot state for Bybit - separate simulation and real modes"""
    
    def __init__(self):
        self.simulation_running = False
        self.simulation_started_at: Optional[datetime] = None
        self.simulation_last_check: Optional[datetime] = None
        
        self.real_running = False
        self.real_started_at: Optional[datetime] = None
        self.real_last_check: Optional[datetime] = None
    
    def restore_from_db(self):
        """Restore bot states from database after server restart"""
        try:
            db = SessionLocal()
            # Get all user settings with running bots (업비트와 동일한 방식)
            user_settings_list = db.query(UserSettings).filter(
                (UserSettings.bybit_bot_simulation_running == True) | 
                (UserSettings.bybit_bot_real_running == True)
            ).all()
            
            for user_settings in user_settings_list:
                if user_settings.bybit_bot_simulation_running:
                    self.simulation_running = True
                    self.simulation_started_at = now_kst()
                    logger.info(f"🔄 [Bybit] User {user_settings.user_id}: 모의투자 봇 상태 복원됨")
                
                if user_settings.bybit_bot_real_running:
                    self.real_running = True
                    self.real_started_at = now_kst()
                    logger.info(f"🔄 [Bybit] User {user_settings.user_id}: 실전투자 봇 상태 복원됨")
            
            db.close()
            
            if self.simulation_running or self.real_running:
                logger.info("✅ [Bybit] 봇 상태가 DB에서 복원되었습니다")
            else:
                logger.info("ℹ️ [Bybit] 복원할 봇 상태가 없습니다")
                
        except Exception as e:
            logger.error(f"[Bybit] Failed to restore bot state from DB: {e}")
    
    def save_to_db(self, mode: str, running: bool, user_id: int = None):
        """Save bot state to database for specific user or all users"""
        try:
            db = SessionLocal()
            if user_id:
                # 특정 사용자 설정 업데이트
                user_settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
            else:
                # user_id가 없으면 첫 번째 사용자 (기존 동작)
                user_settings = db.query(UserSettings).first()
                
            if user_settings:
                if mode == "simulation":
                    user_settings.bybit_bot_simulation_running = running
                else:
                    user_settings.bybit_bot_real_running = running
                db.commit()
            db.close()
        except Exception as e:
            logger.error(f"[Bybit] Error saving bot state: {e}")
    
    def is_running(self, mode: str) -> bool:
        return self.simulation_running if mode == "simulation" else self.real_running
    
    def start(self, mode: str, user_id: int = None):
        if mode == "simulation":
            self.simulation_running = True
            self.simulation_started_at = now_kst()
        else:
            self.real_running = True
            self.real_started_at = now_kst()
        self.save_to_db(mode, True, user_id)
    
    def stop(self, mode: str, user_id: int = None):
        if mode == "simulation":
            self.simulation_running = False
            self.simulation_started_at = None
        else:
            self.real_running = False
            self.real_started_at = None
        self.save_to_db(mode, False, user_id)
    
    def set_last_check(self, mode: str):
        if mode == "simulation":
            self.simulation_last_check = now_kst()
        else:
            self.real_last_check = now_kst()
    
    def get_uptime(self, mode: str) -> int:
        started_at = self.simulation_started_at if mode == "simulation" else self.real_started_at
        if started_at:
            return int((now_kst() - started_at).total_seconds())
        return 0
    
    def get_last_check(self, mode: str) -> Optional[str]:
        last_check = self.simulation_last_check if mode == "simulation" else self.real_last_check
        return last_check.isoformat() if last_check else None


# Global state
bybit_bot_state = BybitBotState()


# ===================
# Bybit Settings
# ===================
BYBIT_LEVERAGE = 5
BYBIT_MARGIN_MODE = "isolated"
BYBIT_POSITION_RATIO = 0.30  # 30% per position
BYBIT_MAX_POSITIONS = 5
BYBIT_REBUY_COOLDOWN_HOURS = 4

# Stop-loss / Take-profit (업비트와 동일한 가격 기준, 레버리지 적용)
# 업비트: 가격 -5% 손절, +10% 익절
# Bybit 5x: 가격 -5% = 레버리지 수익률 -25%, 가격 +10% = 레버리지 수익률 +50%
BYBIT_STOP_LOSS_PERCENT = -25.0   # 가격 -5% (레버리지 적용)
BYBIT_TAKE_PROFIT_PERCENT = 50.0  # 가격 +10% (레버리지 적용)

# Trading Fees (simulated)
# Bybit VIP0 Taker fee: 0.055% (will use 0.06% to be conservative)
BYBIT_TRADING_FEE_RATE = 0.0006  # 0.06% per trade

# Funding Fee (average, varies by market conditions)
# Applied every 8 hours at 00:00, 08:00, 16:00 UTC
# Average funding rate is around 0.01% per 8 hours
BYBIT_FUNDING_FEE_RATE = 0.0001  # 0.01% per 8 hours

# ===================
# Candle Close Timing Settings (Bybit uses UTC)
# ===================
# Import shared candle close window logic
from utils.scheduler_common import is_within_candle_close_window, CANDLE_CLOSE_HOURS_4H, CANDLE_CLOSE_WINDOW_MINUTES


class BybitSchedulerService:
    """
    Bybit Scheduler Service
    
    Jobs:
    1. Whitelist refresh: Every hour
    2. Strategy check: Every 5 minutes
    3. Position monitoring: Every minute
    """
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=KST)
        self.order_manager = BybitOrderManager()
        # ★ 캔들 윈도우별 마지막 실행 시간 추적 (user_id:mode:timeframe -> datetime)
        self._last_execution_times: Dict[str, datetime] = {}
    
    def start(self):
        """Start the Bybit scheduler"""
        try:
            bybit_bot_state.restore_from_db()
            self._add_jobs()
            self.scheduler.start()
            logger.info("[Bybit] Scheduler started")
        except Exception as e:
            logger.error(f"[Bybit] Error starting scheduler: {e}")
    
    def shutdown(self):
        """Shutdown the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("[Bybit] Scheduler stopped")
    
    def _add_jobs(self):
        """Add scheduled jobs - Upbit과 다른 타이밍으로 분산 (매매 관련은 원래 빈도 유지)"""
        
        # === Bybit Scheduler Timing ===
        # 매매 관련: 원래 빈도 유지 (5분/1분)
        # 비매매: Upbit과 다른 타이밍으로 분산
        
        # Refresh whitelist - 매 5분, :02분에 실행 (Upbit은 :00)
        # misfire_grace_time=120: 2분 이내 지연 허용
        self.scheduler.add_job(
            self._job_refresh_whitelist,
            CronTrigger(minute='2,7,12,17,22,27,32,37,42,47,52,57'),
            id='bybit_refresh_whitelist',
            replace_existing=True,
            misfire_grace_time=120,
            coalesce=True
        )
        
        # [매매 관련] Check strategies - 매 5분 유지, :03분에 실행 (Upbit은 :01)
        # misfire_grace_time=120: 2분 이내 지연 허용 (중요 job)
        self.scheduler.add_job(
            self._job_check_strategies,
            CronTrigger(minute='3,8,13,18,23,28,33,38,43,48,53,58'),
            id='bybit_check_strategies',
            replace_existing=True,
            misfire_grace_time=120,
            coalesce=True
        )
        
        # Log strategy signals - 매 5분 (Upbit과 동일)
        # misfire_grace_time=300: 5분 이내 지연 허용 (UI 로그 표시용 - 중요)
        self.scheduler.add_job(
            self._job_log_signals,
            CronTrigger(minute='2,7,12,17,22,27,32,37,42,47,52,57'),
            id='bybit_log_signals',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True
        )
        
        # [매매 관련] Monitor positions - 매 1분 유지 (원래대로 - 손절/익절 중요)
        # misfire_grace_time=60: 60초 이내 지연은 허용하여 실행
        # coalesce=True: missed된 여러 실행은 하나로 합침
        self.scheduler.add_job(
            self._job_monitor_positions,
            'interval',
            minutes=1,
            id='bybit_monitor_positions',
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True
        )
        
        # ★ Phase 9: _job_check_expected_exits 제거됨 (SL/TP가 진입시 확정되므로 불필요)
        
        
        # Position sync - 매 5분, :04분에 실행 (Upbit은 :03)
        self.scheduler.add_job(
            self._job_sync_real_positions,
            CronTrigger(minute='4,9,14,19,24,29,34,39,44,49,54,59'),
            id='bybit_position_sync',
            replace_existing=True
        )
        
        # Log cleanup - daily at 00:05 (Upbit은 00:00)
        self.scheduler.add_job(
            self._job_cleanup_logs,
            CronTrigger(hour=0, minute=5),
            id='bybit_log_cleanup',
            replace_existing=True
        )
        
        # Buy preview alerts - 매 4시간 캔들 마감 10분 전
        # 00:50, 04:50, 08:50, 12:50, 16:50, 20:50
        self.scheduler.add_job(
            self._job_send_buy_preview_alerts,
            CronTrigger(hour='0,4,8,12,16,20', minute=50),
            id='bybit_buy_preview_alert',
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True
        )
        
        logger.info("[Bybit] Scheduled jobs added (staggered timing, trading jobs preserved)")
    
    async def _job_refresh_whitelist(self):
        """Refresh whitelist prices"""
        try:
            whitelist, added, removed = bybit_whitelist_service.refresh_prices()
            count = len(whitelist)
            
            # Log refresh status
            self._log_system("INFO", f"📊 [Bybit] 감시종목 갱신: {count}개 종목")
            
            # Log changes if any
            if added or removed:
                change_parts = []
                if added:
                    added_names = [s.replace("USDT", "") for s in added]
                    change_parts.append(f"추가: {', '.join(added_names)}")
                if removed:
                    removed_names = [s.replace("USDT", "") for s in removed]
                    change_parts.append(f"제거: {', '.join(removed_names)}")
                
                self._log_system("INFO", f"� [Bybit] 감시종목 변경 - {' / '.join(change_parts)}")
        except Exception as e:
            self._log_system("ERROR", f"[Bybit] 감시종목 갱신 실패: {e}")
    
    async def _job_check_strategies(self):
        """Check strategies for all active users - simulation and real in parallel"""
        try:
            import asyncio
            
            tasks = []
            
            # Check simulation mode
            if bybit_bot_state.is_running("simulation"):
                tasks.append(self._check_strategies_for_mode("simulation"))
            
            # Check real mode  
            if bybit_bot_state.is_running("real"):
                tasks.append(self._check_strategies_for_mode("real"))
            
            # Run both modes in parallel
            if tasks:
                await asyncio.gather(*tasks)
                
        except Exception as e:
            logger.error(f"[Bybit] Error in strategy check: {e}")
    
    async def _check_strategies_for_mode(self, mode: str):
        """Check strategies for a specific mode - process only ACTIVE users"""
        try:
            bybit_bot_state.set_last_check(mode)
            db = SessionLocal()
            
            # Get only ACTIVE users for this mode (like Upbit)
            if mode == "simulation":
                active_user_settings = db.query(UserSettings).filter(
                    UserSettings.bybit_bot_simulation_running == True
                ).all()
            else:
                active_user_settings = db.query(UserSettings).filter(
                    UserSettings.bybit_bot_real_running == True
                ).all()
            
            if not active_user_settings:
                db.close()
                return
            
            # Process each active user
            for user_settings in active_user_settings:
                try:
                    await self._check_strategies_for_user(db, user_settings, mode)
                except Exception as e:
                    logger.error(f"[Bybit][{mode}] Error processing user {user_settings.user_id}: {e}")
            
            db.close()
            
        except Exception as e:
            logger.error(f"[Bybit][{mode}] Error checking strategies: {e}")
    
    async def _check_strategies_for_user(self, db, user_settings: UserSettings, mode: str):
        """Check strategies for a specific user"""
        # Get strategy settings for Bybit
        strategy_settings = self._get_strategy_settings(user_settings)
        
        # Check if any strategy is enabled
        enabled_strategies = [k for k, v in strategy_settings.items() if v.get('enabled')]
        if not enabled_strategies:
            return
        
        # Get current positions for THIS user
        current_positions = db.query(Position).filter(
            Position.user_id == user_settings.user_id,
            Position.exchange == 'bybit',
            Position.mode == mode,
            Position.quantity > 0
        ).all()
        
        held_symbols = [p.coin for p in current_positions]
        
        if len(current_positions) >= BYBIT_MAX_POSITIONS:
            return
        
        # ★ 캔들 윈도우 중복 실행 방지
        is_1d_window, _ = is_within_candle_close_window("1D")
        is_4h_window, _ = is_within_candle_close_window("4H")
        
        if not is_1d_window and not is_4h_window:
            return  # 캔들 마감 윈도우가 아니면 매수 안함
        
        now = now_kst()
        window_key = f"{user_settings.user_id}:{mode}"
        
        # 활성 윈도우 타임프레임들 중 아직 실행 안된 것만 필터링
        available_timeframes = []
        if is_1d_window:
            exec_key = f"{window_key}:1D"
            last_exec = self._last_execution_times.get(exec_key)
            if not last_exec or (now - last_exec).total_seconds() >= 1800:
                available_timeframes.append("1D")
        if is_4h_window:
            exec_key = f"{window_key}:4H"
            last_exec = self._last_execution_times.get(exec_key)
            if not last_exec or (now - last_exec).total_seconds() >= 1800:
                available_timeframes.append("4H")
        
        if not available_timeframes:
            mode_label = "모의" if mode == "simulation" else "실전"
            logger.debug(f"[Bybit][{mode_label}] User {user_settings.user_id} 이미 이 캔들 윈도우에서 실행됨, 스킵")
            return
        
        # Get whitelist
        whitelist = bybit_whitelist_service.get_whitelist_symbols()
        
        # Collect buy candidates from all symbols
        buy_candidates = []
        
        # Check each symbol
        for symbol in whitelist:
            if symbol in held_symbols:
                continue
            
            # Check rebuy cooldown for THIS user
            if self._is_in_cooldown(db, symbol, mode, user_settings.user_id):
                continue
            
            # Run strategies and collect all signals (both long and short)
            signals = await self._check_strategies_for_symbol(symbol, strategy_settings)
            
            for signal in signals:
                direction = signal.get('direction', 'long')
                if direction == 'long':
                    buy_candidates.append({
                        "symbol": symbol,
                        "signal": signal,
                        "confidence": signal.get('confidence', 0.5),
                        "timeframe": signal.get('timeframe', '1D'),
                        "strategy": signal.get('strategy', 'divergence'),
                        "direction": "long",
                        "priority": self._get_strategy_priority(signal.get('strategy', 'divergence'))
                    })
                else:  # short
                    buy_candidates.append({
                        "symbol": symbol,
                        "signal": signal,
                        "confidence": signal.get('confidence', 0.5),
                        "timeframe": signal.get('timeframe', '1D'),
                        "strategy": signal.get('strategy', 'bearish_divergence'),
                        "direction": "short",
                        "priority": self._get_strategy_priority(signal.get('strategy', 'bearish_divergence'))
                    })
        
        # Sort by: confidence (desc) -> timeframe (1D first) -> priority (asc)
        buy_candidates.sort(key=lambda x: (
            -x["confidence"],
            0 if x["timeframe"] == "1D" else 1,
            x["priority"]
        ))
        
        # ★ PHASE 10: 균등 배분 로직
        # MAX_PER_EXECUTION: 한 캔들 마감에서 최대 3개 매수
        # MAX_POSITIONS: 계정당 최대 5개 보유
        MAX_PER_EXECUTION = 3
        MIN_ORDER_USD = 10.0
        
        # 추가 가능한 포지션 수 계산
        current_position_count = len(current_positions)
        available_slots = BYBIT_MAX_POSITIONS - current_position_count
        # ★ MAX_PER_EXECUTION 제한 적용
        max_buys_this_run = min(available_slots, MAX_PER_EXECUTION)
        
        # 상위 max_buys_this_run개만 선택 (MAX_PER_EXECUTION 적용)
        top_candidates = buy_candidates[:max_buys_this_run]
        if not top_candidates:
            return
        
        # ★ 이번에 매수하는 개수 기준 (보유 포지션과 무관)
        # 3개→각20%, 2개→각30%, 1개→50%
        n = len(top_candidates)
        if n >= 3:
            pct = 0.20
        elif n == 2:
            pct = 0.30
        else:
            pct = 0.50
        
        mode_label = "모의" if mode == "simulation" else "실전"
        logger.info(f"[Bybit][{mode_label}] User {user_settings.user_id} 매수 {n}개 신호, 각 {pct*100:.0f}% 배분")
        
        # Execute orders with equal sizing
        for candidate in top_candidates:
            if len(current_positions) >= BYBIT_MAX_POSITIONS:
                break
            
            # Check if current time is within candle close window for this timeframe
            is_within_window, timing_reason = is_within_candle_close_window(candidate["timeframe"])
            if not is_within_window:
                dir_text = "롱" if candidate["direction"] == "long" else "숏"
                logger.debug(f"[Bybit][{mode}] {candidate['symbol']} {dir_text} 스킵: {timing_reason}")
                continue
            
            if candidate["direction"] == "long":
                await self._execute_buy(db, user_settings, candidate["symbol"], candidate["signal"], mode, position_pct=pct, min_order_usd=MIN_ORDER_USD)
            else:
                await self._execute_short(db, user_settings, candidate["symbol"], candidate["signal"], mode, position_pct=pct, min_order_usd=MIN_ORDER_USD)
            current_positions.append(None)  # Increment count
        
        # ★ 실행 후 시간 기록 (캔들 윈도우 중복 방지)
        for tf in available_timeframes:
            exec_key = f"{user_settings.user_id}:{mode}:{tf}"
            self._last_execution_times[exec_key] = now_kst()
    
    def _get_strategy_priority(self, strategy: str) -> int:
        """Get priority for strategy sorting (lower = higher priority)"""
        priority_map = {
            # Long strategies
            "morning": 1,
            "inverted_hammer": 2,
            "squirrel": 3,
            "divergence": 4,
            "harmonic": 5,
            "leading_diagonal": 6,
            # Short strategies
            "evening_star": 7,
            "shooting_star": 8,
            "bearish_engulfing": 9,
            "bearish_divergence": 10,
            "leading_diagonal_breakdown": 11
        }
        return priority_map.get(strategy, 20)
    
    def _get_strategy_settings(self, user_settings: UserSettings) -> dict:
        """Get Bybit strategy settings"""
        try:
            if user_settings.bybit_strategy_settings:
                return json.loads(user_settings.bybit_strategy_settings)
        except:
            pass
        
        # Default settings - 모든 전략 활성화
        return {
            # Long strategies
            "squirrel": {"enabled": True},
            "morning": {"enabled": True},
            "inverted_hammer": {"enabled": True},
            "divergence": {"enabled": True},
            "harmonic": {"enabled": True},
            "leading_diagonal": {"enabled": True},
            # Short strategies
            "bearish_divergence": {"enabled": True},
            "evening_star": {"enabled": True},
            "shooting_star": {"enabled": True},
            "bearish_engulfing": {"enabled": True},
            "leading_diagonal_breakdown": {"enabled": True},
        }
    
    async def _check_strategies_for_symbol(self, symbol: str, settings: dict) -> List[Dict]:
        """Run all enabled strategies on a symbol, return list of signals (both long and short)"""
        # Map Bybit symbol to Upbit format for strategy analysis
        # BTCUSDT -> BTC (most strategies use this)
        base_symbol = symbol.replace("USDT", "")
        
        signals = []
        
        # Long strategies (롱 진입)
        long_strategies = [
            ("squirrel", squirrel_strategy, "1D", "long"),
            ("morning", morning_star_strategy, "1D", "long"),
            ("morning", morning_star_strategy, "4H", "long"),
            ("inverted_hammer", inverted_hammer_strategy, "1D", "long"),
            ("inverted_hammer", inverted_hammer_strategy, "4H", "long"),
            ("divergence", divergence_strategy, "1D", "long"),
            ("divergence", divergence_strategy, "4H", "long"),
            ("harmonic", harmonic_strategy, "1D", "long"),
            ("harmonic", harmonic_strategy, "4H", "long"),
            ("leading_diagonal", leading_diagonal_strategy, "1D", "long"),
            ("leading_diagonal", leading_diagonal_strategy, "4H", "long"),
        ]
        
        # Short strategies (숏 진입)
        short_strategies = [
            ("bearish_divergence", bearish_divergence_strategy, "1D", "short"),
            ("bearish_divergence", bearish_divergence_strategy, "4H", "short"),
            ("evening_star", evening_star_strategy, "1D", "short"),
            ("shooting_star", shooting_star_strategy, "1D", "short"),
            ("bearish_engulfing", bearish_engulfing_strategy, "1D", "short"),
            ("leading_diagonal_breakdown", leading_diagonal_breakdown_strategy, "1D", "short"),
        ]
        
        all_strategies = long_strategies + short_strategies
        
        for strategy_id, strategy, timeframe, direction in all_strategies:
            config = settings.get(strategy_id, {})
            if not config.get("enabled", False):
                continue
            
            # ★ Phase 5: min_confidence 체크 제거
            
            try:
                # Analyze using Bybit data
                signal = await self._analyze_bybit_symbol(strategy, symbol, timeframe, direction)
                
                # ★ Phase 5: 신호만 확인, 신뢰도 체크 제거
                if signal and signal.get("action") in ["BUY", "SHORT"]:
                    confidence = signal.get("confidence", 0)
                    signals.append({
                            "strategy": strategy_id,
                            "confidence": confidence,
                            "reason": signal.get("reason", ""),
                            "timeframe": timeframe,
                            "direction": direction,
                            "reference_data": signal.get("reference_data", {})
                        })
            except Exception as e:
                logger.debug(f"[Bybit] Strategy {strategy_id} error for {symbol}: {e}")
        
        return signals
    
    async def _analyze_bybit_symbol(self, strategy, symbol: str, timeframe: str, direction: str = "long") -> Optional[Dict]:
        """Analyze Bybit symbol using strategy (supports both long and short)"""
        try:
            import pandas as pd
            from pybit.unified_trading import HTTP
            
            # Get candles from Bybit
            client = HTTP()
            
            # Map timeframe
            interval_map = {
                "1D": "D",
                "4H": "240",
                "1H": "60",
            }
            interval = interval_map.get(timeframe, "D")
            
            response = client.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=100
            )
            
            if response['retCode'] != 0 or not response['result']['list']:
                return None
            
            # Convert Bybit data to both formats (candles list and DataFrame)
            data = list(reversed(response['result']['list']))
            
            # Create candles list format
            candles = []
            for item in data:
                candles.append({
                    'timestamp': int(item[0]),
                    'open': float(item[1]),
                    'high': float(item[2]),
                    'low': float(item[3]),
                    'close': float(item[4]),
                    'volume': float(item[5]),
                })
            
            if len(candles) < 20:
                return None
            
            # Create DataFrame for strategies that need it
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            
            # Run strategy analysis based on strategy type
            strategy_name = getattr(strategy, 'name', '')
            strategy_direction = getattr(strategy, 'direction', 'long')
            
            # Short strategies (all use DataFrame with analyze() method)
            # 한글 이름: 하락 다이버전스, 석양형, 유성형, 하락장악형, 리딩다이아 하단이탈
            if strategy_direction == "short" or strategy_name in [
                '하락 다이버전스', '석양형', '유성형', 
                '하락장악형', '리딩다이아 하단이탈'
            ]:
                result = strategy.analyze(df)
                if result and len(result) >= 2:
                    should_short, confidence = result[0], result[1]
                    extra_info = result[2] if len(result) > 2 else {}
                    if should_short and confidence > 0:
                        return {
                            "action": "SHORT",
                            "confidence": confidence,
                            "reason": extra_info.get("reason", f"{strategy_name} 숏 신호"),
                            "reference_data": extra_info
                        }
                return None
            
            # Long strategies that use DataFrame with analyze() method
            # 한글 이름: 상승 다이버전스, 하모닉 패턴, 리딩 다이아고날
            if strategy_name in ['상승 다이버전스', '하모닉 패턴', '리딩 다이아고날']:
                result = strategy.analyze(df)
                if result and len(result) >= 2:
                    should_buy, confidence = result[0], result[1]
                    extra_info = result[2] if len(result) > 2 else {}
                    if should_buy and confidence > 0:
                        return {
                            "action": "BUY",
                            "confidence": confidence,
                            "reason": extra_info.get("reason", f"{strategy_name} signal"),
                            "reference_data": extra_info
                        }
                return None
            
            # squirrel, morning, inverted_hammer - use analyze_df with Bybit data
            if hasattr(strategy, 'analyze_df'):
                result = strategy.analyze_df(df, symbol)
                return result  # Returns dict or None
            else:
                # Fallback (shouldn't happen)
                logger.warning(f"[Bybit] Strategy {strategy_name} has no analyze_df method")
                return None
            
            return None
            
        except Exception as e:
            logger.debug(f"[Bybit] Error analyzing {symbol}: {e}")
            return None
    
    def _is_in_cooldown(self, db, symbol: str, mode: str, user_id: int = None) -> bool:
        """Check if symbol is in rebuy cooldown for a specific user"""
        cooldown_time = now_kst() - timedelta(hours=BYBIT_REBUY_COOLDOWN_HOURS)
        
        query = db.query(TradeLog).filter(
            TradeLog.exchange == 'bybit',
            TradeLog.coin == symbol,
            TradeLog.side.in_(['sell', 'long_close', 'short_close']),  # 모든 청산 체크
            TradeLog.mode == mode,
            TradeLog.created_at >= cooldown_time
        )
        
        if user_id:
            query = query.filter(TradeLog.user_id == user_id)
        
        recent_sell = query.first()
        
        return recent_sell is not None
    
    async def _execute_buy(self, db, user_settings: UserSettings, symbol: str, signal: dict, mode: str, position_pct: float = 0.30, min_order_usd: float = 10.0):
        """Execute a buy order with equal sizing"""
        try:
            # Calculate position size based on position_pct from caller
            if mode == "simulation":
                balance = user_settings.bybit_virtual_usdt_balance or 10000
            else:
                # Real mode: Get balance from Bybit API
                if not user_settings.bybit_api_key or not user_settings.bybit_api_secret:
                    logger.warning(f"[Bybit][real] User {user_settings.user_id} has no API keys configured")
                    return
                
                try:
                    from utils.encryption import encryptor
                    from services.bybit_client import BybitClient
                    
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    
                    bybit_client = BybitClient()
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    wallet = bybit_client.get_wallet_balance()
                    balance = wallet.get('available', 0)
                    
                    if balance <= 0:
                        logger.warning(f"[Bybit][real] User {user_settings.user_id} has no available balance")
                        return
                except Exception as e:
                    logger.error(f"[Bybit][real] Error getting balance: {e}")
                    return
            
            # ★ PHASE 10: 균등 배분 비율 사용 (기존: BYBIT_POSITION_RATIO 고정)
            # ★ 수정: 증거금(margin)에 레버리지를 적용하여 포지션 가치 계산
            margin = balance * position_pct  # 증거금 = 잔고 × 배분비율
            position_size = margin * BYBIT_LEVERAGE  # 포지션 가치 = 증거금 × 레버리지
            
            # ★ PHASE 10: 최소 금액 체크 (증거금 기준)
            if margin < min_order_usd:
                mode_label = "모의" if mode == "simulation" else "실전"
                logger.info(f"[Bybit][{mode_label}] {symbol} 최소 금액 미달 (margin ${margin:.2f} < ${min_order_usd}), 스킵")
                return
            
            # Get current price
            price = self._get_current_price(symbol)
            if not price:
                return
            
            quantity = position_size / price  # 수량 = 포지션 가치 / 가격
            
            # Log the trade
            log_msg = f"[Bybit][{mode}] 매수 신호: {symbol} | 전략: {signal['strategy']}"
            logger.info(log_msg)
            
            # Extract reference data for exit checks
            ref_data = signal.get('reference_data', {})
            
            # ★ STRICT: reference_data에서 직접 SL/TP를 가져옴 (Jan 2026 Redesign)
            # SL/TP가 없으면 매수 거부 (fallback 없음)
            stop_loss = ref_data.get('stop_loss')
            take_profit = ref_data.get('take_profit')
            
            # STRICT VALIDATION: SL/TP가 없으면 매수 거부
            if stop_loss is None or take_profit is None:
                strategy_name = signal['strategy']
                missing = []
                if stop_loss is None:
                    missing.append("stop_loss")
                if take_profit is None:
                    missing.append("take_profit")
                logger.error(f"[Bybit][STRICT] [{strategy_name}] 매수 거부: SL/TP 미설정 ({', '.join(missing)}) - ref_data: {ref_data}")
                return
            
            if mode == "simulation":
                # Create position with SL/TP (Phase 5)
                position = Position(
                    user_id=user_settings.user_id,
                    exchange='bybit',
                    coin=symbol,
                    quantity=quantity,
                    entry_price=price,
                    stop_loss=stop_loss,  # ★ Phase 5
                    take_profit=take_profit,  # ★ Phase 5
                    mode=mode,
                    strategy=signal['strategy'],
                    timeframe=signal.get('timeframe', '1D'),
                    confidence=signal.get('confidence'),
                    leverage=BYBIT_LEVERAGE,
                    direction='long',  # ★ 롱 포지션
                    reference_candle_open=ref_data.get('reference_candle_open'),
                    reference_candle_high=ref_data.get('reference_candle_high') or ref_data.get('resistance'),
                    reference_candle_low=ref_data.get('reference_candle_low') or ref_data.get('support') or ref_data.get('divergence_low'),
                    created_at=now_kst()
                )
                db.add(position)
                
                # Create trade log (SL/TP already calculated above)
                
                trade_log = TradeLog(
                    user_id=user_settings.user_id,
                    exchange='bybit',
                    coin=symbol,
                    side='long_open',  # 롱 진입
                    quantity=quantity,
                    price=price,
                    total_amount=position_size,
                    strategy=signal['strategy'],
                    timeframe=signal.get('timeframe', '1D'),
                    confidence=signal.get('confidence'),
                    reason=signal.get('reason', f"{signal['strategy']} 매수 신호"),
                    mode=mode,
                    leverage=BYBIT_LEVERAGE,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    created_at=now_kst()
                )
                db.add(trade_log)
                
                # Calculate trading fee
                trading_fee = position_size * BYBIT_TRADING_FEE_RATE
                
                # Update virtual balance (margin only, not full position_size)
                # margin = position_size / leverage (실제 증거금만 차감)
                margin_used = position_size / BYBIT_LEVERAGE
                user_settings.bybit_virtual_usdt_balance = balance - margin_used - trading_fee
                
                db.commit()
                
            else:
                # Real mode: Place order via Bybit API
                try:
                    from services.bybit_client import BybitClient
                    from utils.encryption import encryptor
                    
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    
                    bybit_client = BybitClient()
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    # Place market order
                    order_result = bybit_client.place_order(
                        symbol=symbol,
                        side="Buy",
                        qty=quantity,
                        leverage=BYBIT_LEVERAGE
                    )
                    
                    if order_result.get('success'):
                        executed_price = order_result.get('price', price)
                        executed_qty = order_result.get('qty', quantity)
                        
                        # Create position record with SL/TP (Phase 5)
                        position = Position(
                            user_id=user_settings.user_id,
                            exchange='bybit',
                            coin=symbol,
                            quantity=executed_qty,
                            entry_price=executed_price,
                            stop_loss=stop_loss,  # ★ Phase 5: 이미 위에서 계산됨
                            take_profit=take_profit,  # ★ Phase 5
                            mode=mode,
                            strategy=signal['strategy'],
                            timeframe=signal.get('timeframe', '1D'),
                            confidence=signal.get('confidence'),
                            leverage=BYBIT_LEVERAGE,
                            direction='long',  # ★ 롱 포지션
                            reference_candle_open=ref_data.get('reference_candle_open'),
                            reference_candle_high=ref_data.get('reference_candle_high') or ref_data.get('resistance'),
                            reference_candle_low=ref_data.get('reference_candle_low') or ref_data.get('support'),
                            created_at=now_kst()
                        )
                        db.add(position)
                        
                        # Create trade log (SL/TP already calculated above)
                        
                        trade_log = TradeLog(
                            user_id=user_settings.user_id,
                            exchange='bybit',
                            coin=symbol,
                            side='long_open',  # ★ 롱 진입 (실전)
                            quantity=executed_qty,
                            price=executed_price,
                            total_amount=executed_price * executed_qty,
                            strategy=signal['strategy'],
                            timeframe=signal.get('timeframe', '1D'),
                            confidence=signal.get('confidence'),
                            reason=signal.get('reason', f"{signal['strategy']} 매수 신호"),
                            mode=mode,
                            leverage=BYBIT_LEVERAGE,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            created_at=now_kst()
                        )
                        db.add(trade_log)
                        db.commit()
                        
                        # ★ Phase 5: Set SL/TP on Bybit exchange
                        sltp_result = bybit_client.set_trading_stop(
                            symbol=symbol,
                            stop_loss=stop_loss,
                            take_profit=take_profit
                        )
                        if not sltp_result.get('success'):
                            logger.warning(f"[Bybit][real] Failed to set SL/TP: {sltp_result.get('error')}")
                        
                        price = executed_price
                        quantity = executed_qty
                    else:
                        logger.error(f"[Bybit][real] Order failed: {order_result.get('message')}")
                        return
                        
                except Exception as e:
                    logger.error(f"[Bybit][real] Error placing order: {e}")
                    return
            
            # Log to system
            trading_fee = position_size * BYBIT_TRADING_FEE_RATE
            self._log_system(
                "INFO",
                f"🟢 매수 체결: {symbol} | {quantity:.4f} @ ${price:.2f} | 전략: {signal['strategy']} | 수수료: ${trading_fee:.2f}",
                mode=mode,
                user_id=user_settings.user_id
            )
            
            # Send Telegram notification
            telegram_service.send_user_trade_alert(
                user_id=user_settings.user_id,
                side="buy",
                coin=symbol,
                price=price,
                quantity=quantity,
                strategy=signal['strategy'],
                confidence=signal.get('confidence', 0),
                mode=mode,
                exchange="bybit",
                leverage=BYBIT_LEVERAGE
            )
            
        except Exception as e:
            logger.error(f"[Bybit] Error executing buy: {e}")
    
    async def _execute_short(self, db, user_settings: UserSettings, symbol: str, signal: dict, mode: str, position_pct: float = 0.30, min_order_usd: float = 10.0):
        """Execute a short order (숏 진입) with equal sizing"""
        try:
            # Calculate position size based on position_pct from caller
            if mode == "simulation":
                balance = user_settings.bybit_virtual_usdt_balance or 10000
            else:
                # Real mode: Get balance from Bybit API
                if not user_settings.bybit_api_key or not user_settings.bybit_api_secret:
                    logger.warning(f"[Bybit][real] User {user_settings.user_id} has no API keys configured")
                    return
                
                try:
                    from utils.encryption import encryptor
                    from services.bybit_client import BybitClient
                    
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    
                    bybit_client = BybitClient()
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    wallet = bybit_client.get_wallet_balance()
                    balance = wallet.get('available', 0)
                    
                    if balance <= 0:
                        logger.warning(f"[Bybit][real] User {user_settings.user_id} has no available balance")
                        return
                except Exception as e:
                    logger.error(f"[Bybit][real] Error getting balance: {e}")
                    return
            
            # ★ PHASE 10: 균등 배분 비율 사용 (기존: BYBIT_POSITION_RATIO 고정)
            # ★ 수정: 증거금(margin)에 레버리지를 적용하여 포지션 가치 계산
            margin = balance * position_pct  # 증거금 = 잔고 × 배분비율
            position_size = margin * BYBIT_LEVERAGE  # 포지션 가치 = 증거금 × 레버리지
            
            # ★ PHASE 10: 최소 금액 체크 (증거금 기준)
            if margin < min_order_usd:
                mode_label = "모의" if mode == "simulation" else "실전"
                logger.info(f"[Bybit][{mode_label}] {symbol} 숏 최소 금액 미달 (margin ${margin:.2f} < ${min_order_usd}), 스킵")
                return
            
            # Get current price
            price = self._get_current_price(symbol)
            if not price:
                return
            
            quantity = position_size / price  # 수량 = 포지션 가치 / 가격
            
            # Log the trade
            log_msg = f"[Bybit][{mode}] 숏 신호: {symbol} | 전략: {signal['strategy']}"
            logger.info(log_msg)
            
            # Extract reference data for exit checks
            ref_data = signal.get('reference_data', {})
            
            # ★ STRICT: reference_data에서 직접 SL/TP를 가져옴 (Jan 2026 Redesign)
            # SL/TP가 없으면 숏 거부 (fallback 없음)
            stop_loss = ref_data.get('stop_loss')
            take_profit = ref_data.get('take_profit')
            
            # STRICT VALIDATION: SL/TP가 없으면 숏 거부
            if stop_loss is None or take_profit is None:
                strategy_name = signal['strategy']
                missing = []
                if stop_loss is None:
                    missing.append("stop_loss")
                if take_profit is None:
                    missing.append("take_profit")
                logger.error(f"[Bybit][STRICT] [{strategy_name}] 숏 거부: SL/TP 미설정 ({', '.join(missing)}) - ref_data: {ref_data}")
                return
            
            if mode == "simulation":
                # Create short position with direction
                position = Position(
                    user_id=user_settings.user_id,
                    exchange='bybit',
                    coin=symbol,
                    quantity=quantity,
                    entry_price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    mode=mode,
                    strategy=signal['strategy'],
                    timeframe=signal.get('timeframe', '1D'),
                    confidence=signal.get('confidence'),
                    leverage=BYBIT_LEVERAGE,
                    direction='short',  # 숏 포지션
                    reference_candle_high=ref_data.get('pattern_high') or ref_data.get('divergence_high') or ref_data.get('resistance'),
                    reference_candle_low=ref_data.get('support'),
                    created_at=now_kst()
                )
                db.add(position)
                
                # SL/TP는 이미 위에서 검증 및 설정됨 (strict validation)
                trade_log = TradeLog(
                    user_id=user_settings.user_id,
                    exchange='bybit',
                    coin=symbol,
                    side='short_open',  # 숏 진입
                    quantity=quantity,
                    price=price,
                    total_amount=position_size,
                    strategy=signal['strategy'],
                    timeframe=signal.get('timeframe', '1D'),
                    confidence=signal.get('confidence'),
                    reason=signal.get('reason', f"{signal['strategy']} 숏 진입 신호"),
                    mode=mode,
                    leverage=BYBIT_LEVERAGE,
                    direction='short',
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    created_at=now_kst()
                )
                db.add(trade_log)
                
                # Calculate trading fee
                trading_fee = position_size * BYBIT_TRADING_FEE_RATE
                
                # Update virtual balance (margin only, not full position_size)
                # margin = position_size / leverage (실제 증거금만 차감)
                margin_used = position_size / BYBIT_LEVERAGE
                user_settings.bybit_virtual_usdt_balance = balance - margin_used - trading_fee
                
                db.commit()
                
            else:
                # Real mode: Place short order via Bybit API
                try:
                    from services.bybit_client import BybitClient
                    from utils.encryption import encryptor
                    
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    
                    bybit_client = BybitClient()
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    # Place market order - Sell for short
                    order_result = bybit_client.place_order(
                        symbol=symbol,
                        side="Sell",  # 숏 진입
                        qty=quantity,
                        leverage=BYBIT_LEVERAGE
                    )
                    
                    if order_result.get('success'):
                        executed_price = order_result.get('price', price)
                        executed_qty = order_result.get('qty', quantity)
                        
                        # Create position record with direction
                        position = Position(
                            user_id=user_settings.user_id,
                            exchange='bybit',
                            coin=symbol,
                            quantity=executed_qty,
                            entry_price=executed_price,
                            stop_loss=stop_loss,  # ★ SL/TP는 위에서 이미 검증됨
                            take_profit=take_profit,
                            mode=mode,
                            strategy=signal['strategy'],
                            timeframe=signal.get('timeframe', '1D'),
                            confidence=signal.get('confidence'),
                            leverage=BYBIT_LEVERAGE,
                            direction='short',
                            reference_candle_high=ref_data.get('pattern_high') or ref_data.get('divergence_high'),
                            reference_candle_low=ref_data.get('support'),
                            created_at=now_kst()
                        )
                        db.add(position)
                        
                        # SL/TP는 이미 위에서 검증 및 설정됨 (strict validation)
                        trade_log = TradeLog(
                            user_id=user_settings.user_id,
                            exchange='bybit',
                            coin=symbol,
                            side='short_open',  # ★ 숏 진입 (실전)
                            quantity=executed_qty,
                            price=executed_price,
                            total_amount=executed_price * executed_qty,
                            strategy=signal['strategy'],
                            timeframe=signal.get('timeframe', '1D'),
                            confidence=signal.get('confidence'),
                            reason=signal.get('reason', f"{signal['strategy']} 숏 진입 신호"),
                            mode=mode,
                            leverage=BYBIT_LEVERAGE,
                            direction='short',
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            created_at=now_kst()
                        )
                        db.add(trade_log)
                        db.commit()
                        
                        # ★ Phase 5: Set SL/TP on Bybit exchange
                        sltp_result = bybit_client.set_trading_stop(
                            symbol=symbol,
                            stop_loss=stop_loss,
                            take_profit=take_profit
                        )
                        if not sltp_result.get('success'):
                            logger.warning(f"[Bybit][real] Failed to set SL/TP for short: {sltp_result.get('error')}")
                        
                        price = executed_price
                        quantity = executed_qty
                    else:
                        logger.error(f"[Bybit][real] Short order failed: {order_result.get('message')}")
                        return
                        
                except Exception as e:
                    logger.error(f"[Bybit][real] Error placing short order: {e}")
                    return
            
            # Log to system
            trading_fee = position_size * BYBIT_TRADING_FEE_RATE
            self._log_system(
                "INFO",
                f"🔴 숏 진입: {symbol} | {quantity:.4f} @ ${price:.2f} | 전략: {signal['strategy']} | 수수료: ${trading_fee:.2f}",
                mode=mode,
                user_id=user_settings.user_id
            )
            
            # Send Telegram notification
            telegram_service.send_user_trade_alert(
                user_id=user_settings.user_id,
                side="short",
                coin=symbol,
                price=price,
                quantity=quantity,
                strategy=signal['strategy'],
                confidence=signal.get('confidence', 0),
                mode=mode,
                exchange="bybit",
                leverage=BYBIT_LEVERAGE
            )
            
        except Exception as e:
            logger.error(f"[Bybit] Error executing short: {e}")

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price from Bybit"""
        try:
            from pybit.unified_trading import HTTP
            client = HTTP()
            
            response = client.get_tickers(
                category="linear",
                symbol=symbol
            )
            
            if response['retCode'] == 0 and response['result']['list']:
                return float(response['result']['list'][0]['lastPrice'])
        except Exception as e:
            logger.error(f"[Bybit] Error getting price for {symbol}: {e}")
        return None
    
    async def _job_monitor_positions(self):
        """Monitor positions for stop-loss and take-profit"""
        try:
            # Check for any active Bybit bots in DB (not in-memory state which may be stale)
            db = SessionLocal()
            
            # Simulation mode: check if any user has simulation bot running
            sim_users = db.query(UserSettings).filter(UserSettings.bybit_bot_simulation_running == True).count()
            if sim_users > 0:
                await self._monitor_positions_for_mode("simulation")
            
            # Real mode: check if any user has real bot running
            real_users = db.query(UserSettings).filter(UserSettings.bybit_bot_real_running == True).count()
            if real_users > 0:
                await self._monitor_positions_for_mode("real")
            
            db.close()
                
        except Exception as e:
            logger.error(f"[Bybit] Error in position monitoring: {e}")
    
    async def _monitor_positions_for_mode(self, mode: str):
        """Monitor positions for a specific mode using strategy-specific exit logic"""
        try:
            db = SessionLocal()
            
            # Get all active users for this mode
            mode_column = UserSettings.bybit_bot_simulation_running if mode == "simulation" else UserSettings.bybit_bot_real_running
            active_users = db.query(UserSettings).filter(mode_column == True).all()
            
            for user_settings in active_users:
                # Get positions for this user
                positions = db.query(Position).filter(
                    Position.user_id == user_settings.user_id,
                    Position.exchange == 'bybit',
                    Position.mode == mode,
                    Position.quantity > 0
                ).all()
                
                if not positions:
                    continue
                
                # ★ Phase 5: Real mode - check for auto-closed positions by Bybit
                if mode == "real" and user_settings.bybit_api_key and user_settings.bybit_api_secret:
                    try:
                        from services.bybit_client import BybitClient
                        from utils.encryption import encryptor
                        
                        api_key = encryptor.decrypt(user_settings.bybit_api_key)
                        api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                        
                        bybit_client = BybitClient()
                        bybit_client.set_credentials(api_key, api_secret)
                        
                        # Get closed PnL records from Bybit
                        closed_records = bybit_client.get_closed_pnl(limit=20)
                        
                        for position in positions:
                            # Check if this position was closed by Bybit
                            for record in closed_records:
                                if record['symbol'] == position.coin:
                                    # Position was auto-closed by Bybit SL/TP
                                    closed_time = record['updatedTime'] / 1000
                                    position_time = position.created_at.timestamp() if position.created_at else 0
                                    
                                    if closed_time > position_time:
                                        # Sync: Update DB with Bybit's execution
                                        exit_price = record['exitPrice']
                                        pnl = record['closedPnl']
                                        
                                        # Create trade log for the auto-close
                                        direction = getattr(position, 'direction', 'long') or 'long'
                                        side = 'long_close' if direction == 'long' else 'short_close'
                                        reason = f"거래소 자동청산 (PnL: ${pnl:.2f})"
                                        
                                        trade_log = TradeLog(
                                            user_id=user_settings.user_id,
                                            exchange='bybit',
                                            coin=position.coin,
                                            side=side,
                                            quantity=position.quantity,
                                            price=exit_price,
                                            total_amount=exit_price * position.quantity,
                                            strategy=position.strategy,
                                            timeframe=position.timeframe,
                                            reason=reason,
                                            mode=mode,
                                            leverage=BYBIT_LEVERAGE,
                                            direction=direction,
                                            profit_loss=pnl,
                                            created_at=now_kst()
                                        )
                                        db.add(trade_log)
                                        
                                        # Mark position as closed
                                        position.quantity = 0
                                        db.commit()
                                        
                                        logger.info(f"[Bybit][real] Auto-closed position synced: {position.coin} PnL=${pnl:.2f}")
                                        
                                        # Send notification
                                        exit_type = "익절" if pnl > 0 else "손절"
                                        self._log_system(
                                            "INFO",
                                            f"🔔 {exit_type}: {position.coin} | PnL: ${pnl:.2f} (거래소 자동청산)",
                                            mode=mode,
                                            user_id=user_settings.user_id
                                        )
                                        break
                    except Exception as e:
                        logger.error(f"[Bybit][real] Error syncing closed positions: {e}")
                
                logger.debug(f"[Bybit][{mode}] Monitoring {len(positions)} positions for user {user_settings.user_id}")
            
                for position in positions:
                    should_exit = False
                    reason = ""
                    
                    # Get current price
                    current_price = self._get_current_price(position.coin)
                    if not current_price:
                        continue
                    
                    # Get direction and SL/TP from position
                    direction = getattr(position, 'direction', 'long') or 'long'
                    stop_loss = position.stop_loss or position.reference_candle_low
                    take_profit = position.take_profit or position.reference_candle_high
                    
                    if not stop_loss or not take_profit:
                        logger.warning(f"[Bybit][{mode}] {position.coin} SL/TP 미설정, 스킵")
                        continue
                    
                    # Calculate PnL based on direction
                    if direction == 'short':
                        price_change_pct = ((position.entry_price - current_price) / position.entry_price) * 100
                    else:
                        price_change_pct = ((current_price - position.entry_price) / position.entry_price) * 100
                    
                    pnl_pct_with_leverage = price_change_pct * BYBIT_LEVERAGE
                    
                    # ★ Phase 5: 단순화된 SL/TP 체크
                    if direction == 'short':
                        # 숏 포지션: 가격 상승 시 손절, 가격 하락 시 익절
                        if current_price >= stop_loss:
                            should_exit = True
                            reason = f"손절: SL 도달 ({pnl_pct_with_leverage:+.1f}%)"
                        elif current_price <= take_profit:
                            should_exit = True
                            reason = f"익절: TP 도달 ({pnl_pct_with_leverage:+.1f}%)"
                    else:
                        # 롱 포지션: 가격 하락 시 손절, 가격 상승 시 익절
                        if current_price <= stop_loss:
                            should_exit = True
                            reason = f"손절: SL 도달 ({pnl_pct_with_leverage:+.1f}%)"
                        elif current_price >= take_profit:
                            should_exit = True
                            reason = f"익절: TP 도달 ({pnl_pct_with_leverage:+.1f}%)"
                    
                    # Execute exit if conditions met
                    if should_exit:
                        await self._execute_sell(db, position, current_price, mode, reason, pnl_pct_with_leverage)
            
            db.close()
            
        except Exception as e:
            logger.error(f"[Bybit][{mode}] Error monitoring positions: {e}")
    
    def _get_bybit_ohlcv(self, symbol: str, interval: str = "D", limit: int = 100):
        """Get OHLCV data from Bybit for strategy analysis"""
        try:
            from pybit.unified_trading import HTTP
            import pandas as pd
            
            client = HTTP()
            response = client.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            if response['retCode'] != 0 or not response['result']['list']:
                return None
            
            # Convert to DataFrame (Bybit returns newest first, so reverse)
            data = list(reversed(response['result']['list']))
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            
            return df
            
        except Exception as e:
            logger.debug(f"[Bybit] Error getting OHLCV for {symbol}: {e}")
            return None
    
    async def _execute_sell(self, db, position: Position, current_price: float, mode: str, reason: str, pnl_pct: float):
        """Execute a sell (close position) order - 100% close only (Phase 8)"""
        try:
            # ★ Phase 8: sell_ratio 제거, 100% 청산만
            sell_quantity = position.quantity
            total_value = sell_quantity * current_price
            
            # Calculate PnL based on direction
            direction = getattr(position, 'direction', 'long') or 'long'
            if direction == 'short':
                # Short: 진입가 - 현재가 = 수익 (가격 하락 시 수익)
                pnl = (position.entry_price - current_price) * sell_quantity
            else:
                # Long: 현재가 - 진입가 = 수익 (가격 상승 시 수익)
                pnl = (current_price - position.entry_price) * sell_quantity
            
            # Determine emoji based on profit/loss
            if pnl >= 0:
                reason_text = f"🟢 {reason}"
            else:
                reason_text = f"🔴 {reason}"
            
            exit_type = "숏 청산" if direction == 'short' else "청산"
            
            logger.info(f"[Bybit][{mode}] {exit_type}: {position.coin} | PnL: {pnl_pct:.1f}% | ${pnl:.2f}")
            
            # For simulation, update position and balance
            if mode == "simulation":
                user_settings = db.query(UserSettings).filter(
                    UserSettings.user_id == position.user_id
                ).first()
                
                # Calculate trading fee for sell
                trading_fee = total_value * BYBIT_TRADING_FEE_RATE
                
                # Calculate funding fees (based on holding time)
                # Funding is charged every 8 hours at 00:00, 08:00, 16:00 UTC
                holding_hours = 0
                if position.created_at:
                    # Handle timezone-aware vs naive datetime comparison
                    created_at = position.created_at
                    if created_at.tzinfo is None:
                        # Convert naive to KST using replace
                        created_at = created_at.replace(tzinfo=KST)
                    holding_hours = (now_kst() - created_at).total_seconds() / 3600
                funding_periods = int(holding_hours / 8)  # Number of 8-hour periods
                entry_value = position.entry_price * sell_quantity
                funding_fee = entry_value * BYBIT_FUNDING_FEE_RATE * funding_periods
                
                # Total fees
                total_fees = trading_fee + funding_fee
                
                # Net PnL after fees
                net_pnl = pnl - total_fees
                net_pnl_pct = pnl_pct - (total_fees / entry_value * 100 * BYBIT_LEVERAGE) if entry_value > 0 else pnl_pct
                
                # Create trade log with net values
                # Determine close side based on position direction
                close_side = 'long_close' if position.direction == 'long' else 'short_close'
                
                trade_log = TradeLog(
                    user_id=position.user_id,
                    exchange='bybit',
                    coin=position.coin,
                    side=close_side,
                    quantity=sell_quantity,
                    price=current_price,
                    total_amount=total_value,
                    pnl=net_pnl,
                    pnl_percent=net_pnl_pct,
                    funding_fee=funding_fee,
                    strategy=position.strategy,
                    timeframe=position.timeframe or "1D",
                    reason=reason,
                    mode=mode,
                    leverage=BYBIT_LEVERAGE,
                    created_at=now_kst()
                )
                db.add(trade_log)
                
                # Update virtual balance (margin return + PnL - fees)
                # 청산 시: 진입 시 사용한 마진을 반환 + 실현 손익 - 수수료
                if user_settings:
                    margin_return = entry_value / BYBIT_LEVERAGE
                    user_settings.bybit_virtual_usdt_balance = (user_settings.bybit_virtual_usdt_balance or 10000) + margin_return + pnl - total_fees
                
                # ★ Phase 6: 100% 청산만 - 포지션 삭제
                db.delete(position)
                
                # Log to system
                fee_info = f" | 수수료: ${trading_fee:.2f}"
                if funding_fee > 0:
                    fee_info += f" | 펀딩피: ${funding_fee:.2f}"
                    
                self._log_system(
                    "INFO",
                    f"{reason_text} [{exit_type}]: {position.coin} | PnL: {net_pnl_pct:.1f}% (${net_pnl:.2f}){fee_info}",
                    mode=mode,
                    user_id=position.user_id
                )
                
                # Send Telegram notification
                telegram_service.send_user_trade_alert(
                    user_id=position.user_id,
                    side="sell",
                    coin=position.coin,
                    price=current_price,
                    quantity=sell_quantity,
                    strategy=position.strategy or "divergence",
                    pnl_percent=net_pnl_pct,
                    mode=mode,
                    exchange="bybit",
                    leverage=BYBIT_LEVERAGE
                )
                
                # Commit all changes to DB
                db.commit()
            
            else:
                # Real mode: Close position via Bybit API
                try:
                    user_settings = db.query(UserSettings).filter(
                        UserSettings.user_id == position.user_id
                    ).first()
                    
                    if not user_settings or not user_settings.bybit_api_key:
                        logger.error(f"[Bybit][real] No API keys for user {position.user_id}")
                        return
                    
                    from services.bybit_client import BybitClient
                    from utils.encryption import encryptor
                    
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    
                    bybit_client = BybitClient()
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    # Close position
                    order_result = bybit_client.place_order(
                        symbol=position.coin,
                        side="Sell",
                        qty=sell_quantity,
                        reduce_only=True
                    )
                    
                    if order_result.get('success'):
                        executed_price = order_result.get('price', current_price)
                        executed_qty = order_result.get('qty', sell_quantity)
                        
                        # Recalculate with executed values
                        total_value = executed_qty * executed_price
                        pnl = (executed_price - position.entry_price) * executed_qty
                        trading_fee = total_value * BYBIT_TRADING_FEE_RATE
                        net_pnl = pnl - trading_fee
                        entry_value = position.entry_price * executed_qty
                        net_pnl_pct = (net_pnl / entry_value * 100 * BYBIT_LEVERAGE) if entry_value > 0 else 0
                        
                        # Create trade log
                        trade_log = TradeLog(
                            user_id=position.user_id,
                            exchange='bybit',
                            coin=position.coin,
                            side='sell',
                            quantity=executed_qty,
                            price=executed_price,
                            total_amount=total_value,
                            pnl=net_pnl,
                            pnl_percent=net_pnl_pct,
                            strategy=position.strategy,
                            timeframe=position.timeframe or "1D",
                            reason=reason,
                            mode=mode,
                            leverage=BYBIT_LEVERAGE,
                            created_at=now_kst()
                        )
                        db.add(trade_log)
                        
                        # Update or delete position (★ Phase 9: 100% 청산만, is_partial 분기 제거)
                        db.delete(position)
                        
                        db.commit()
                        
                        # Log to system (★ Phase 9: sell_ratio 제거, 100% 고정)
                        self._log_system(
                            "INFO",
                            f"{reason_text} [{exit_type}]: {position.coin} | PnL: {net_pnl_pct:.1f}% (${net_pnl:.2f}) [100%]",
                            mode=mode,
                            user_id=position.user_id
                        )
                        
                        # Send Telegram notification
                        telegram_service.send_user_trade_alert(
                            user_id=position.user_id,
                            side="sell",
                            coin=position.coin,
                            price=executed_price,
                            quantity=executed_qty,
                            strategy=position.strategy or "divergence",
                            pnl_percent=net_pnl_pct,
                            mode=mode,
                            exchange="bybit",
                            leverage=BYBIT_LEVERAGE
                        )
                    else:
                        logger.error(f"[Bybit][real] Close order failed: {order_result.get('message')}")
                        
                except Exception as e:
                    logger.error(f"[Bybit][real] Error closing position: {e}")
            
        except Exception as e:
            logger.error(f"[Bybit] Error executing sell: {e}")
    
    def _job_log_signals(self):
        """
        Log strategy signals per mode - runs at exact 5-minute intervals.
        Logs pure signals with timeframe info. Same structure as Upbit's _job_log_strategy_signals.
        """
        try:
            # Check which modes are active from DB
            db = SessionLocal()
            
            sim_active = db.query(UserSettings).filter(UserSettings.bybit_bot_simulation_running == True).count() > 0
            real_active = db.query(UserSettings).filter(UserSettings.bybit_bot_real_running == True).count() > 0
            
            user_settings = db.query(UserSettings).first()
            db.close()
            
            if not sim_active and not real_active:
                return
            
            # Determine which modes to log for
            active_modes = []
            if sim_active:
                active_modes.append("simulation")
            if real_active:
                active_modes.append("real")
            
            whitelist = bybit_whitelist_service.get_whitelist_symbols()
            
            strategy_settings = self._get_strategy_settings(user_settings) if user_settings else {}
            squirrel_config = strategy_settings.get("squirrel", {"enabled": True})
            morning_config = strategy_settings.get("morning", {"enabled": True})
            inverted_hammer_config = strategy_settings.get("inverted_hammer", {"enabled": True})
            divergence_config = strategy_settings.get("divergence", {"enabled": True})
            harmonic_config = strategy_settings.get("harmonic", {"enabled": True})
            leading_diagonal_config = strategy_settings.get("leading_diagonal", {"enabled": True})
            
            # Log current candle timing status (once)
            now = now_kst()
            hour = now.hour
            minute = now.minute
            
            # Bybit uses UTC candles - 1D closes at 00:00 UTC (09:00 KST)
            # 4H closes at 01:00, 05:00, 09:00, 13:00, 17:00, 21:00 KST
            CANDLE_CLOSE_HOURS_4H = [1, 5, 9, 13, 17, 21]
            CANDLE_CLOSE_WINDOW = 30  # minutes
            
            is_1d_window = (hour == 9 and minute < CANDLE_CLOSE_WINDOW)
            is_4h_window = any(hour == h and minute < CANDLE_CLOSE_WINDOW for h in CANDLE_CLOSE_HOURS_4H)
            
            timing_status = []
            if is_1d_window:
                timing_status.append("1D✅")
            if is_4h_window:
                timing_status.append("4H✅")
            if not timing_status:
                next_4h = min([h for h in CANDLE_CLOSE_HOURS_4H if h > hour] or [CANDLE_CLOSE_HOURS_4H[0] + 24]) % 24
                timing_status.append(f"매수대기(4H→{next_4h:02d}:00, 1D→09:00)")
            
            # Log for each active mode
            for mode in active_modes:
                self._log_system("INFO", f"⏰ [Bybit] 캔들 마감 체크: {' '.join(timing_status)}", mode=mode)
            
            # Collect all signals for TOP 5 logging
            all_signals = []  # (symbol, strategy, confidence, signal_type)
            
            # ★ 보유/쿨다운 상태는 사용자별로 다르므로 공통 로그에서 제거
            # 이 정보는 _execute_trades_for_mode에서 사용자별로 확인됨
            
            for symbol in whitelist:
                try:
                    # Get OHLCV data for this symbol (1D timeframe)
                    df = self._get_bybit_ohlcv_sync(symbol, "D", 100)
                    if df is None or len(df) < 30:
                        continue
                    
                    # Analyze with Squirrel strategy (1D)
                    if squirrel_config.get("enabled", True):
                        try:
                            signal = squirrel_strategy.analyze_df(df)
                            if signal and signal.confidence >= 0.01:
                                all_signals.append((
                                    symbol, "다람쥐(1D)",
                                    signal.confidence,
                                    signal.signal_type
                                ))
                        except:
                            pass
                    
                    # Analyze with Morning Star strategy - 1D
                    if morning_config.get("enabled", True):
                        try:
                            signal = morning_star_strategy_daily.analyze_df(df)
                            if signal and signal.confidence >= 0.01:
                                all_signals.append((
                                    symbol, "샛별형(1D)",
                                    signal.confidence,
                                    signal.signal_type
                                ))
                        except:
                            pass
                    
                    # Analyze with Inverted Hammer strategy - 1D
                    if inverted_hammer_config.get("enabled", True):
                        try:
                            signal = inverted_hammer_strategy.analyze_df(df)
                            if signal and signal.confidence >= 0.01:
                                all_signals.append((
                                    symbol, "윗꼬리양봉(1D)",
                                    signal.confidence,
                                    signal.signal_type
                                ))
                        except:
                            pass
                    
                    # === 4H 타임프레임 분석 ===
                    df_4h = self._get_bybit_ohlcv_sync(symbol, "240", 100)  # 240 = 4H
                    if df_4h is not None and len(df_4h) >= 30:
                        # Analyze with Morning Star strategy - 4H
                        if morning_config.get("enabled", True):
                            try:
                                signal = morning_star_strategy.analyze_df(df_4h)
                                if signal and signal.confidence >= 0.01:
                                    all_signals.append((
                                        symbol, "샛별형(4H)",
                                        signal.confidence,
                                        signal.signal_type
                                    ))
                            except:
                                pass
                        
                        # Analyze with Inverted Hammer strategy - 4H
                        if inverted_hammer_config.get("enabled", True):
                            try:
                                signal = inverted_hammer_strategy_4h.analyze_df(df_4h)
                                if signal and signal.confidence >= 0.01:
                                    all_signals.append((
                                        symbol, "윗꼬리양봉(4H)",
                                        signal.confidence,
                                        signal.signal_type
                                    ))
                            except:
                                pass
                    
                    # === 신규 전략 분석 (1D) ===
                    # Analyze with Divergence strategy - 1D
                    if divergence_config.get("enabled", True):
                        try:
                            is_signal, confidence, info = divergence_strategy.analyze(df)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((symbol, "다이버전스(1D)", confidence, signal_type))
                        except:
                            pass
                    
                    # Analyze with Harmonic strategy - 1D
                    if harmonic_config.get("enabled", True):
                        try:
                            is_signal, confidence, info = harmonic_strategy.analyze(df)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((symbol, "하모닉(1D)", confidence, signal_type))
                        except:
                            pass
                    
                    # Analyze with Leading Diagonal strategy - 1D
                    if leading_diagonal_config.get("enabled", True):
                        try:
                            is_signal, confidence, info = leading_diagonal_strategy.analyze(df)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((symbol, "리딩다이아(1D)", confidence, signal_type))
                        except:
                            pass
                    
                    # === 신규 전략 분석 (4H) ===
                    if df_4h is not None and len(df_4h) >= 30:
                        # Analyze with Divergence strategy - 4H
                        if divergence_config.get("enabled", True):
                            try:
                                is_signal, confidence, info = divergence_strategy.analyze(df_4h)
                                if confidence >= 0.01:
                                    signal_type = "buy" if is_signal else "none"
                                    all_signals.append((symbol, "다이버전스(4H)", confidence, signal_type))
                            except:
                                pass
                        
                        # Analyze with Harmonic strategy - 4H
                        if harmonic_config.get("enabled", True):
                            try:
                                is_signal, confidence, info = harmonic_strategy.analyze(df_4h)
                                if confidence >= 0.01:
                                    signal_type = "buy" if is_signal else "none"
                                    all_signals.append((symbol, "하모닉(4H)", confidence, signal_type))
                            except:
                                pass
                        
                        # Analyze with Leading Diagonal strategy - 4H
                        if leading_diagonal_config.get("enabled", True):
                            try:
                                is_signal, confidence, info = leading_diagonal_strategy.analyze(df_4h)
                                if confidence >= 0.01:
                                    signal_type = "buy" if is_signal else "none"
                                    all_signals.append((symbol, "리딩다이아(4H)", confidence, signal_type))
                            except:
                                pass
                    
                    # === 숏 전략 분석 (1D) ===
                    # 하락 다이버전스
                    try:
                        from services.strategy_bearish_divergence import bearish_divergence_strategy
                        is_signal, confidence, info = bearish_divergence_strategy.analyze(df)
                        if confidence >= 0.01:
                            signal_type = "short" if is_signal else "none"
                            all_signals.append((symbol, "하락다이버전스(1D)", confidence, signal_type))
                    except:
                        pass
                    
                    # 석양형
                    try:
                        from services.strategy_evening_star import evening_star_strategy
                        is_signal, confidence, info = evening_star_strategy.analyze(df)
                        if confidence >= 0.01:
                            signal_type = "short" if is_signal else "none"
                            all_signals.append((symbol, "석양형(1D)", confidence, signal_type))
                    except:
                        pass
                    
                    # 유성형
                    try:
                        from services.strategy_shooting_star import shooting_star_strategy
                        is_signal, confidence, info = shooting_star_strategy.analyze(df)
                        if confidence >= 0.01:
                            signal_type = "short" if is_signal else "none"
                            all_signals.append((symbol, "유성형(1D)", confidence, signal_type))
                    except:
                        pass
                    
                    # 하락장악형
                    try:
                        from services.strategy_bearish_engulfing import bearish_engulfing_strategy
                        is_signal, confidence, info = bearish_engulfing_strategy.analyze(df)
                        if confidence >= 0.01:
                            signal_type = "short" if is_signal else "none"
                            all_signals.append((symbol, "하락장악형(1D)", confidence, signal_type))
                    except:
                        pass
                    
                    # 리딩다이아 하단이탈
                    try:
                        from services.strategy_leading_diagonal_breakdown import leading_diagonal_breakdown_strategy
                        is_signal, confidence, info = leading_diagonal_breakdown_strategy.analyze(df)
                        if confidence >= 0.01:
                            signal_type = "short" if is_signal else "none"
                            all_signals.append((symbol, "리딩다이아이탈(1D)", confidence, signal_type))
                    except:
                        pass
                            
                except Exception as e:
                    logger.debug(f"[Bybit][{symbol}] 전략 분석 오류: {e}")
            
            # === 전략별 TOP 5 로깅 ===
            strategy_groups = {
                # 롱 전략
                "다람쥐": {"name": "다람쥐", "threshold": 0, "signals": [], "direction": "long"},
                "샛별형": {"name": "샛별형", "threshold": 0, "signals": [], "direction": "long"},
                "윗꼬리양봉": {"name": "윗꼬리양봉", "threshold": 0, "signals": [], "direction": "long"},
                "다이버전스": {"name": "다이버전스", "threshold": 0, "signals": [], "direction": "long"},
                "하모닉": {"name": "하모닉", "threshold": 0, "signals": [], "direction": "long"},
                "리딩다이아": {"name": "리딩다이아", "threshold": 0, "signals": [], "direction": "long"},
                # 숏 전략
                "하락다이버전스": {"name": "하락다이버전스", "threshold": 0.5, "signals": [], "direction": "short"},
                "석양형": {"name": "석양형", "threshold": 0.5, "signals": [], "direction": "short"},
                "유성형": {"name": "유성형", "threshold": 0.5, "signals": [], "direction": "short"},
                "하락장악형": {"name": "하락장악형", "threshold": 0.5, "signals": [], "direction": "short"},
                "리딩다이아이탈": {"name": "리딩다이아이탈", "threshold": 0.5, "signals": [], "direction": "short"},
            }
            
            # 신호를 전략별로 그룹화
            for symbol, strategy, confidence, signal_type in all_signals:
                for key in strategy_groups.keys():
                    if key in strategy:
                        strategy_groups[key]["signals"].append((symbol, strategy, confidence, signal_type))
                        break
            
            # 전략별 TOP 5 로깅 - 각 전략을 한 줄로 표시
            # "롱" 전략은 ⭐, "숏" 전략은 🔻 마커 사용
            for key, group in strategy_groups.items():
                signals = group["signals"]
                
                if not signals:
                    self._log_system("INFO", f"🎯 [Bybit][{group['name']}] 신호 없음")
                    continue
                
                # 신뢰도 순으로 정렬 후 TOP 5
                sorted_signals = sorted(signals, key=lambda x: x[2], reverse=True)
                top5 = sorted_signals[:5]
                
                threshold = group["threshold"]
                is_short_strategy = group.get("direction") == "short"
                
                # 한 줄로 압축: 🎯 [Bybit][샛별형] BTC(4H)⭐ ETH(1D)
                # ★ 신뢰도 % 제거 - 모든 조건 충족 시에만 신호 발생
                items = []
                for symbol, strategy_name, confidence, signal_type in top5:
                    coin_name = symbol.replace('USDT', '')
                    
                    # 롱 전략: buy 신호면 ⭐, 숏 전략: short 신호면 🔻
                    if is_short_strategy:
                        is_signal = signal_type == "short"
                        marker = "🔻" if is_signal else ""
                    else:
                        is_signal = signal_type == "buy"
                        marker = "⭐" if is_signal else ""
                    
                    # 타임프레임 정보
                    tf_info = ""
                    if "4H" in strategy_name:
                        tf_info = "(4H)"
                    elif "1D" in strategy_name:
                        tf_info = "(1D)"
                    
                    # ★ 보유/쿨다운 상태는 사용자별로 다르므로 공통 로그에서 생략
                    items.append(f"{coin_name}{tf_info}{marker}")
                
                direction_label = "숏" if is_short_strategy else "롱"
                log_line = f"🎯 [Bybit][{group['name']}][{direction_label}] {' '.join(items)}"
                self._log_system("INFO", log_line)
                
        except Exception as e:
            logger.error(f"[Bybit] Error logging signals: {e}")
    
    def _get_bybit_ohlcv_sync(self, symbol: str, interval: str, limit: int = 100):
        """Get OHLCV data from Bybit synchronously"""
        try:
            from pybit.unified_trading import HTTP
            import pandas as pd
            
            session = HTTP(testnet=False)
            response = session.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            if response["retCode"] != 0:
                return None
            
            klines = response["result"]["list"]
            if not klines:
                return None
            
            # Bybit returns data in reverse order (newest first)
            klines = list(reversed(klines))
            
            df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            df.set_index("timestamp", inplace=True)
            
            return df
            
        except Exception as e:
            logger.debug(f"[Bybit] Failed to get OHLCV for {symbol}: {e}")
            return None
    
    # ==================
    # Bot Control Methods
    # ==================
    
    def start_bot(self, mode: str = "simulation", user_id: int = None):
        """Start the trading bot"""
        bybit_bot_state.start(mode, user_id)
        self._log_system("INFO", f"🚀 Bybit {mode} 봇 시작", mode=mode)
        return {"success": True, "message": f"Bybit {mode} bot started"}
    
    def stop_bot(self, mode: str = "simulation", user_id: int = None):
        """Stop the trading bot"""
        bybit_bot_state.stop(mode, user_id)
        self._log_system("INFO", f"🛑 Bybit {mode} 봇 정지", mode=mode)
        return {"success": True, "message": f"Bybit {mode} bot stopped"}
    
    def get_status(self, mode: str = None, user_id: int = None) -> dict:
        """Get bot status - check DB state for specific user"""
        # DB에서 사용자별 상태 확인
        db_sim_running = False
        db_real_running = False
        
        try:
            db = SessionLocal()
            if user_id:
                user_settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
                if user_settings:
                    db_sim_running = user_settings.bybit_bot_simulation_running or False
                    db_real_running = user_settings.bybit_bot_real_running or False
            db.close()
        except Exception as e:
            logger.error(f"[Bybit] Error getting user settings: {e}")
        
        if mode:
            is_running = db_sim_running if mode == "simulation" else db_real_running
            return {
                "mode": mode,
                "running": is_running,
                "is_running": is_running,  # 프론트엔드 호환성
                "uptime": bybit_bot_state.get_uptime(mode),
                "last_check": bybit_bot_state.get_last_check(mode),
            }
        
        return {
            "simulation_running": db_sim_running,
            "real_running": db_real_running,
            "simulation_uptime": bybit_bot_state.get_uptime("simulation"),
            "real_uptime": bybit_bot_state.get_uptime("real"),
            "simulation_last_check": bybit_bot_state.get_last_check("simulation"),
            "real_last_check": bybit_bot_state.get_last_check("real"),
        }
    
    async def _job_sync_real_positions(self):
        """
        Sync DB positions with actual exchange holdings for real mode.
        Removes positions from DB that no longer exist on the exchange
        (e.g., manually closed positions).
        """
        if not bybit_bot_state.is_running("real"):
            return
        
        try:
            db = SessionLocal()
            
            # Get all users with real mode running
            active_users = db.query(UserSettings).filter(
                UserSettings.bybit_bot_real_running == True
            ).all()
            
            for user_settings in active_users:
                if not user_settings.bybit_api_key:
                    continue
                
                try:
                    from utils.encryption import encryptor
                    from services.bybit_client import BybitClient
                    
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    
                    bybit_client = BybitClient()
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    # Get actual positions from exchange
                    actual_positions = bybit_client.get_positions()
                    actual_symbols = set()
                    for pos in actual_positions:
                        if float(pos.get('size', 0)) > 0:
                            actual_symbols.add(pos['symbol'])
                    
                    # Get DB positions for this user
                    db_positions = db.query(Position).filter(
                        Position.user_id == user_settings.user_id,
                        Position.exchange == 'bybit',
                        Position.mode == 'real',
                        Position.quantity > 0
                    ).all()
                    
                    # Find orphan positions (in DB but not on exchange)
                    orphan_count = 0
                    
                    # ★ Bybit API에서 closedPnl 조회 (수수료+펀딩비 포함된 실제 수익)
                    closed_pnl_map = {}
                    try:
                        closed_pnl_result = bybit_client.client.get_closed_pnl(category='linear', limit=50)
                        if closed_pnl_result.get('retCode') == 0:
                            for item in closed_pnl_result.get('result', {}).get('list', []):
                                symbol = item.get('symbol')
                                if symbol not in closed_pnl_map:
                                    closed_pnl_map[symbol] = {
                                        'closedPnl': float(item.get('closedPnl', 0)),
                                        'avgExitPrice': float(item.get('avgExitPrice', 0)),
                                        'side': item.get('side'),
                                        'updatedTime': item.get('updatedTime')
                                    }
                    except Exception as e:
                        logger.warning(f"[Bybit][Sync] Failed to get closedPnl: {e}")
                    
                    for position in db_positions:
                        if position.coin not in actual_symbols:
                            logger.info(f"[Bybit][Sync] Removing orphan position: {position.coin} (user {user_settings.user_id})")
                            
                            # ★ 거래 로그 생성 (SL/TP 또는 수동 청산)
                            try:
                                # ★ Bybit closedPnl API에서 실제 수익 가져오기 (수수료+펀딩비 포함)
                                api_pnl_data = closed_pnl_map.get(position.coin)
                                
                                if api_pnl_data:
                                    # ★ API에서 가져온 실제 PnL 사용 (수수료+펀딩비 포함)
                                    pnl = api_pnl_data['closedPnl']
                                    current_price = api_pnl_data['avgExitPrice']
                                    
                                    # 마진 대비 수익률 계산
                                    margin = (position.entry_price * position.quantity) / (position.leverage or BYBIT_LEVERAGE)
                                    pnl_percent = (pnl / margin * 100) if margin > 0 else 0
                                else:
                                    # Fallback: 직접 계산 (수수료 미포함)
                                    current_price = self._get_current_price(position.coin)
                                    if current_price is None:
                                        current_price = position.entry_price
                                    
                                    if position.direction == 'short':
                                        pnl = (position.entry_price - current_price) * position.quantity
                                    else:
                                        pnl = (current_price - position.entry_price) * position.quantity
                                    
                                    pnl_percent = ((pnl / (position.entry_price * position.quantity)) * 100 * (position.leverage or BYBIT_LEVERAGE))
                                
                                # Determine close side
                                close_side = 'short_close' if position.direction == 'short' else 'long_close'
                                
                                # Determine reason (SL/TP hit or manual)
                                reason = "거래소에서 청산됨 (SL/TP 또는 수동)"
                                if position.stop_loss and position.take_profit:
                                    if position.direction == 'short':
                                        if current_price >= position.stop_loss:
                                            reason = "손절"
                                        elif current_price <= position.take_profit:
                                            reason = "익절"
                                    else:
                                        if current_price <= position.stop_loss:
                                            reason = "손절"
                                        elif current_price >= position.take_profit:
                                            reason = "익절"
                                
                                trade_log = TradeLog(
                                    user_id=user_settings.user_id,
                                    exchange='bybit',
                                    coin=position.coin,
                                    side=close_side,
                                    quantity=position.quantity,
                                    price=current_price,
                                    total_amount=current_price * position.quantity,
                                    strategy=position.strategy,
                                    timeframe=position.timeframe,
                                    pnl=pnl,
                                    pnl_percent=pnl_percent,
                                    reason=reason,
                                    mode='real',
                                    leverage=position.leverage or BYBIT_LEVERAGE,
                                    direction=position.direction,
                                    created_at=now_kst()
                                )
                                db.add(trade_log)
                                logger.info(f"[Bybit][Sync] Created trade log for {position.coin}: {reason} (PnL: ${pnl:.2f})")
                            except Exception as log_err:
                                logger.error(f"[Bybit][Sync] Failed to create trade log: {log_err}")
                            
                            db.delete(position)
                            orphan_count += 1
                    
                    if orphan_count > 0:
                        db.commit()
                        self._log_system(
                            "INFO",
                            f"[포지션동기화] {orphan_count}개 포지션 청산 감지 및 거래 로그 생성",
                            mode="real",
                            user_id=user_settings.user_id
                        )
                        
                except Exception as e:
                    logger.error(f"[Bybit][Sync] Error syncing positions for user {user_settings.user_id}: {e}")
            
            db.close()
            
        except Exception as e:
            logger.error(f"[Bybit][Sync] Position sync failed: {e}")

    def _log_system(self, level: str, message: str, mode: str = None, user_id: int = None):
        """Log message to database and console"""
        logger.info(f"[Bybit][{level}] {message}")
        try:
            db = SessionLocal()
            log = SystemLog(
                user_id=user_id,
                exchange='bybit',
                level=level,
                message=message,
                mode=mode,
                created_at=now_kst()
            )
            db.add(log)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to log to DB: {e}")
    
    # ★ Phase 9: _job_check_expected_exits 함수 삭제됨 (SL/TP가 진입시 확정되므로 불필요)
    
    async def _job_cleanup_logs(self):
        """Delete system logs older than 24 hours"""
        try:
            db = SessionLocal()
            cutoff = now_kst() - timedelta(hours=24)
            
            deleted = db.query(SystemLog).filter(
                SystemLog.exchange == 'bybit',
                SystemLog.created_at < cutoff
            ).delete()
            
            db.commit()
            db.close()
            
            if deleted > 0:
                self._log_system("INFO", f"[Bybit] {deleted}개 오래된 로그 삭제됨")
                
        except Exception as e:
            logger.error(f"[Bybit] Log cleanup failed: {e}")
    
    async def _job_send_buy_preview_alerts(self):
        """
        Send buy preview alerts for Bybit to users with Telegram enabled.
        Runs at 00:50, 04:50, 08:50, 12:50, 16:50, 20:50 (10 minutes before candle close)
        """
        try:
            from services.telegram_service import telegram_service
            
            now = now_kst()
            next_candle_close = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            preview_time_str = next_candle_close.strftime("%Y-%m-%d %H:%M")
            
            # Determine timeframe based on current hour
            current_hour = now.hour
            upcoming_hour = (current_hour + 1) % 24
            is_daily_candle = (upcoming_hour == 9)  # 1D candle closes at 09:00 KST
            timeframe = "1D" if is_daily_candle else "4H"
            
            logger.info(f"[Bybit BuyPreview] Starting buy preview alerts for {timeframe} candle at {preview_time_str}")
            
            db = SessionLocal()
            
            # Get all users with Telegram enabled and Bybit bot running
            telegram_users = db.query(UserSettings).filter(
                UserSettings.telegram_enabled == True,
                UserSettings.telegram_token != None,
                UserSettings.telegram_chat_id != None
            ).all()
            
            if not telegram_users:
                logger.info("[Bybit BuyPreview] No users with Telegram enabled")
                db.close()
                return
            
            logger.info(f"[Bybit BuyPreview] Found {len(telegram_users)} users with Telegram enabled")
            
            for user_settings in telegram_users:
                try:
                    user_id = user_settings.user_id
                    
                    # Check which modes are active for Bybit
                    modes_to_check = []
                    if user_settings.bybit_bot_simulation_running:
                        modes_to_check.append("simulation")
                    if user_settings.bybit_bot_real_running:
                        modes_to_check.append("real")
                    
                    if not modes_to_check:
                        continue
                    
                    # Get user's Bybit strategy settings
                    strategy_settings = self._get_strategy_settings(user_settings)
                    
                    # Get user's current Bybit positions
                    user_positions = db.query(Position).filter(
                        Position.user_id == user_id,
                        Position.exchange == "bybit"
                    ).all()
                    
                    # Get recent sell trades (for cooldown check)
                    cooldown_cutoff = now - timedelta(hours=REBUY_COOLDOWN_HOURS)
                    recent_sells = db.query(TradeLog).filter(
                        TradeLog.user_id == user_id,
                        TradeLog.exchange == "bybit",
                        TradeLog.side == "sell",
                        TradeLog.created_at >= cooldown_cutoff
                    ).all()
                    cooldown_symbols = {s.coin for s in recent_sells}
                    
                    for mode in modes_to_check:
                        # Filter positions for this mode
                        mode_positions = {
                            p.coin for p in user_positions 
                            if p.mode == mode
                        }
                        
                        buy_signals = []
                        
                        # Check each strategy
                        for strategy_name, settings in strategy_settings.items():
                            if not settings.get('enabled', False):
                                continue
                            
                            
                            # Check entry conditions for each symbol
                            for symbol in BYBIT_SYMBOLS:
                                # Skip if already holding
                                if symbol in mode_positions:
                                    continue
                                
                                # Skip if in cooldown
                                if symbol in cooldown_symbols:
                                    continue
                                
                                try:
                                    # Analyze strategy signal (★ Phase 9: min_confidence 제거)
                                    signal_result = await self._analyze_bybit_signal(
                                        strategy_name, symbol, timeframe
                                    )
                                    
                                    if signal_result and signal_result.get('is_buy'):
                                        buy_signals.append({
                                            'strategy': strategy_name,
                                            'coin': symbol,
                                            'confidence': signal_result.get('confidence', 0),
                                            'entry_price': signal_result.get('entry_price', 0),
                                            'stop_loss': signal_result.get('stop_loss', 0),
                                            'take_profit_1': signal_result.get('take_profit_1', 0)
                                        })
                                except Exception as e:
                                    logger.debug(f"[Bybit BuyPreview] Error analyzing {symbol} for {strategy_name}: {e}")
                        
                        # Sort by confidence (highest first) and limit to top 10
                        buy_signals.sort(key=lambda x: x.get('confidence', 0), reverse=True)
                        buy_signals = buy_signals[:10]
                        
                        # Send alert
                        telegram_service.send_buy_preview_alert(
                            user_id=user_id,
                            exchange="bybit",
                            mode=mode,
                            timeframe=timeframe,
                            preview_time=preview_time_str,
                            buy_signals=buy_signals
                        )
                        
                except Exception as e:
                    logger.error(f"[Bybit BuyPreview] Error processing user {user_settings.user_id}: {e}")
            
            db.close()
            logger.info(f"[Bybit BuyPreview] Completed buy preview alerts")
            
        except Exception as e:
            logger.error(f"[Bybit BuyPreview] Error in buy preview job: {e}")
    
    async def _analyze_bybit_signal(self, strategy_name: str, symbol: str, timeframe: str) -> dict:
        """Analyze a single strategy signal for a Bybit symbol"""
        try:
            result = {'is_buy': False}
            
            # Get current price
            current_price = BybitClient.get_ticker_price(symbol)
            if not current_price:
                return result
            
            # Analyze using the strategy
            signal = await self._analyze_bybit_symbol_for_preview(strategy_name, symbol, timeframe)
            
            # ★ Phase 5: 신뢰도 체크 제거 - 신호만 확인
            if signal and signal.get('signal_type') == 'buy':
                stop_loss = signal.get('stop_loss') or (current_price * 0.95)
                take_profit = signal.get('take_profit') or (current_price * 1.05)
                
                return {
                    'is_buy': True,
                    'confidence': signal.get('confidence', 0),
                    'entry_price': current_price,
                    'stop_loss': stop_loss,
                    'take_profit_1': take_profit
                }
            
            return result
            
        except Exception as e:
            logger.debug(f"[Bybit BuyPreview] _analyze_bybit_signal error: {e}")
            return {'is_buy': False}
    
    async def _analyze_bybit_symbol_for_preview(self, strategy_name: str, symbol: str, timeframe: str) -> dict:
        """Analyze Bybit symbol for preview alert"""
        try:
            interval = "240" if timeframe == "4H" else "D"
            df = BybitClient.get_kline(symbol, interval=interval, limit=100)
            
            if df is None or len(df) < 30:
                return None
            
            # Rename columns if needed
            if 'startTime' in df.columns:
                df = df.rename(columns={
                    'startTime': 'timestamp',
                    'openPrice': 'open',
                    'highPrice': 'high',
                    'lowPrice': 'low',
                    'closePrice': 'close',
                    'volume': 'volume'
                })
            
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = df[col].astype(float)
            
            # min_confidence removed
            
            if strategy_name == "divergence":
                is_buy, confidence, info = divergence_strategy.analyze(df)
                if is_buy:
                    return {
                        'signal_type': 'buy',
                        'confidence': confidence,
                        'stop_loss': info.get('divergence_low') if info else None,
                        'take_profit': None
                    }
            
            elif strategy_name == "harmonic":
                is_buy, confidence, info = harmonic_strategy.analyze(df)
                if is_buy:
                    return {
                        'signal_type': 'buy',
                        'confidence': confidence,
                        'stop_loss': info.get('stop_loss') if info else None,
                        'take_profit': info.get('A_point') if info else None
                    }
            
            elif strategy_name == "leading_diagonal":
                is_buy, confidence, info = leading_diagonal_strategy.analyze(df)
                if is_buy:
                    return {
                        'signal_type': 'buy',
                        'confidence': confidence,
                        'stop_loss': info.get('support') if info else None,
                        'take_profit': info.get('resistance') if info else None
                    }
            
            return None
            
        except Exception as e:
            logger.debug(f"[Bybit BuyPreview] _analyze_bybit_symbol_for_preview error: {e}")
            return None


# Global instance
bybit_scheduler_service = BybitSchedulerService()
