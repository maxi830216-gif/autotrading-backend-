"""
Scheduler Service
Background tasks using APScheduler
"""
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from models.database import SessionLocal, SystemLog, Position, UserSettings, User, TradeLog, get_setting
from services.whitelist_service import whitelist_service
from services.strategy_squirrel import squirrel_strategy
from services.strategy_morning import morning_star_strategy, morning_star_strategy_daily
from services.strategy_inverted_hammer import inverted_hammer_strategy, inverted_hammer_strategy_4h
from services.strategy_divergence import divergence_strategy
from services.strategy_harmonic import harmonic_strategy
from services.strategy_leading_diagonal import leading_diagonal_strategy
from services.order_manager import order_manager, get_default_user_id
from services.upbit_client import upbit_client, UpbitClient
from services.telegram_service import telegram_service
from utils.logger import setup_logger
from utils.encryption import encryptor
from utils.timezone import now_kst, KST

logger = setup_logger(__name__)


class BotState:
    """Global bot state - separate for simulation and real modes"""
    def __init__(self):
        # Simulation mode state
        self.simulation_running = False
        self.simulation_started_at: Optional[datetime] = None
        self.simulation_last_check: Optional[datetime] = None
        
        # Real mode state
        self.real_running = False
        self.real_started_at: Optional[datetime] = None
        self.real_last_check: Optional[datetime] = None
    
    def restore_from_db(self):
        """Restore bot states from database after server restart"""
        try:
            db = SessionLocal()
            # Get all user settings with running bots
            user_settings_list = db.query(UserSettings).filter(
                (UserSettings.bot_simulation_running == True) | 
                (UserSettings.bot_real_running == True)
            ).all()
            
            for user_settings in user_settings_list:
                if user_settings.bot_simulation_running:
                    self.simulation_running = True
                    self.simulation_started_at = datetime.utcnow()
                    logger.info(f"🔄 User {user_settings.user_id}: 모의투자 봇 상태 복원됨")
                
                if user_settings.bot_real_running:
                    self.real_running = True
                    self.real_started_at = datetime.utcnow()
                    logger.info(f"🔄 User {user_settings.user_id}: 실전투자 봇 상태 복원됨")
            
            db.close()
            
            if self.simulation_running or self.real_running:
                logger.info("✅ 봇 상태가 DB에서 복원되었습니다")
            else:
                logger.info("ℹ️ 복원할 봇 상태가 없습니다")
                
        except Exception as e:
            logger.error(f"Failed to restore bot state from DB: {e}")
    
    def save_to_db(self, mode: str, running: bool):
        """Save bot state to database"""
        try:
            db = SessionLocal()
            # For now, update first user's settings (can be extended for multi-user)
            user_settings = db.query(UserSettings).first()
            
            if user_settings:
                if mode == "simulation":
                    user_settings.bot_simulation_running = running
                else:
                    user_settings.bot_real_running = running
                db.commit()
                logger.info(f"💾 Bot state saved to DB: {mode}={running}")
            else:
                logger.warning("No user settings found to save bot state")
            
            db.close()
        except Exception as e:
            logger.error(f"Failed to save bot state to DB: {e}")
    
    def is_running(self, mode: str) -> bool:
        """Check if specific mode is running"""
        if mode == "simulation":
            return self.simulation_running
        return self.real_running
    
    def start(self, mode: str):
        """Start specific mode"""
        if mode == "simulation":
            self.simulation_running = True
            self.simulation_started_at = datetime.utcnow()
        else:
            self.real_running = True
            self.real_started_at = datetime.utcnow()
        
        # Persist to DB
        self.save_to_db(mode, True)
    
    def stop(self, mode: str):
        """Stop specific mode"""
        if mode == "simulation":
            self.simulation_running = False
        else:
            self.real_running = False
        
        # Persist to DB
        self.save_to_db(mode, False)
    
    def set_last_check(self, mode: str):
        """Update last check time for mode"""
        now = datetime.utcnow()
        if mode == "simulation":
            self.simulation_last_check = now
        else:
            self.real_last_check = now
    
    def get_uptime(self, mode: str) -> Optional[int]:
        """Get uptime in seconds for mode"""
        if mode == "simulation":
            if self.simulation_started_at and self.simulation_running:
                return int((datetime.utcnow() - self.simulation_started_at).total_seconds())
        else:
            if self.real_started_at and self.real_running:
                return int((datetime.utcnow() - self.real_started_at).total_seconds())
        return None
    
    def get_last_check(self, mode: str) -> Optional[str]:
        """Get last check time as ISO string"""
        if mode == "simulation":
            return self.simulation_last_check.isoformat() if self.simulation_last_check else None
        return self.real_last_check.isoformat() if self.real_last_check else None


# ===================
# Rebuy Cooldown Settings
# ===================
# After selling, prevent immediate rebuy to avoid transaction fee waste
# All strategies use the same cooldown period (4 hours)
REBUY_COOLDOWN = 14400  # 4 hours - minimum wait after any sell, regardless of strategy

# ===================
# Candle Close Timing Settings
# ===================
# Import shared candle close window logic
from utils.scheduler_common import is_within_candle_close_window, CANDLE_CLOSE_HOURS_4H, CANDLE_CLOSE_WINDOW_MINUTES


class SchedulerService:
    """
    Background task scheduler
    
    Jobs:
    1. Whitelist refresh: Every hour
    2. Strategy check: Every 5 minutes
    3. Order timeout check: Every minute
    4. Log cleanup: Daily at midnight
    5. Exit condition check: Every 5 minutes
    
    Rebuy Cooldown Rules:
    - After selling a coin, no rebuy for 1 hour (all strategies)
    - Same strategy rebuy: wait 24 hours
    - Different strategy rebuy: wait 1 hour
    """
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.state = BotState()
        # ★ 캔들 윈도우별 마지막 실행 시간 추적 (user_id:mode:timeframe -> datetime)
        self._last_execution_times: Dict[str, datetime] = {}
    
    def start(self):
        """Start the scheduler"""
        try:
            # Initialize services
            self._initialize_services()
            
            # Restore bot states from DB (for server restart recovery)
            self.state.restore_from_db()
            
            # Log restored state to system logs
            if self.state.simulation_running:
                self._log_system("INFO", "🔄 [모의투자] 서버 재시작 - 봇 상태 자동 복원됨", mode="simulation")
            if self.state.real_running:
                self._log_system("INFO", "🔄 [실전투자] 서버 재시작 - 봇 상태 자동 복원됨", mode="real")
            
            # Add jobs
            self._add_jobs()
            
            # Start scheduler
            self.scheduler.start()
            logger.info("Scheduler started")
            
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
    
    def shutdown(self):
        """Shutdown the scheduler"""
        try:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler shutdown")
        except Exception as e:
            logger.error(f"Error during scheduler shutdown: {e}")
    
    def _initialize_services(self):
        """Initialize all services with stored credentials"""
        try:
            db = SessionLocal()
            
            # Initialize Upbit client
            access_key_enc = get_setting(db, "upbit_access_key")
            secret_key_enc = get_setting(db, "upbit_secret_key")
            
            if access_key_enc and secret_key_enc:
                access_key = encryptor.decrypt(access_key_enc)
                secret_key = encryptor.decrypt(secret_key_enc)
                upbit_client.set_credentials(access_key, secret_key)
            
            # Initialize Telegram
            telegram_service.initialize()
            
            # Load bot mode
            mode = get_setting(db, "bot_mode")
            self.state.mode = mode if mode else "simulation"
            
            db.close()
            
        except Exception as e:
            logger.error(f"Failed to initialize services: {e}")
    
    def _add_jobs(self):
        """Add scheduled jobs - 타이밍 분산으로 CPU 부하 방지 (매매 관련은 5분 유지)"""
        
        # === Upbit Scheduler Timing (분 단위로 분산) ===
        # 매매 관련: 5분마다 (원래 빈도 유지)
        # 비매매: 타이밍 분산
        
        # Whitelist refresh - 매 5분 (:00, :05, :10, ...)
        # misfire_grace_time=120: 2분 이내 지연은 허용
        self.scheduler.add_job(
            self._job_refresh_whitelist,
            CronTrigger(minute='0,5,10,15,20,25,30,35,40,45,50,55'),
            id="whitelist_refresh",
            name="Refresh Whitelist",
            replace_existing=True,
            misfire_grace_time=120,
            coalesce=True
        )
        
        # [매매 관련] Strategy check - 매 5분 유지, :01분에 실행 (1, 6, 11, ...)
        # misfire_grace_time=120: 2분 이내 지연은 허용 (중요 job)
        self.scheduler.add_job(
            self._job_check_strategies,
            CronTrigger(minute='1,6,11,16,21,26,31,36,41,46,51,56'),
            id="strategy_check",
            name="Check Trading Strategies",
            replace_existing=True,
            misfire_grace_time=120,
            coalesce=True
        )
        
        # Strategy signal logging - 매 5분, :00분에 실행 (Bybit과 동일)
        # misfire_grace_time=300: 5분 이내 지연 허용 (UI 로그 표시용 - 중요)
        self.scheduler.add_job(
            self._job_log_strategy_signals,
            CronTrigger(minute='0,5,10,15,20,25,30,35,40,45,50,55'),
            id="strategy_signal_log",
            name="Log Strategy Signals",
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True
        )
        
        # [매매 관련] Exit condition check - 매 5분 유지, :02분에 실행 (2, 7, 12, ...)
        # misfire_grace_time=120: 2분 이내 지연은 허용 (중요 job)
        self.scheduler.add_job(
            self._job_check_exits,
            CronTrigger(minute='2,7,12,17,22,27,32,37,42,47,52,57'),
            id="exit_check",
            name="Check Exit Conditions",
            replace_existing=True,
            misfire_grace_time=120,
            coalesce=True
        )
        
        # ★ Phase 9: _job_check_expected_exits 제거됨 (SL/TP가 진입시 확정되므로 불필요)
        
        
        # Order timeout check - 매 1분 유지 (원래대로)
        # misfire_grace_time=60: 60초 이내 지연은 허용하여 실행
        self.scheduler.add_job(
            self._job_check_order_timeouts,
            IntervalTrigger(minutes=1),
            id="order_timeout",
            name="Check Order Timeouts",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True
        )
        
        # Log cleanup - daily at midnight
        self.scheduler.add_job(
            self._job_cleanup_logs,
            CronTrigger(hour=0, minute=0),
            id="log_cleanup",
            name="Cleanup Old Logs",
            replace_existing=True
        )
        
        # Position sync - 매 5분, :03분에 실행 (3, 8, 13, ...)
        self.scheduler.add_job(
            self._job_sync_real_positions,
            CronTrigger(minute='3,8,13,18,23,28,33,38,43,48,53,58'),
            id="position_sync",
            name="Sync Real Mode Positions",
            replace_existing=True
        )
        
        # Buy preview alerts - 매 4시간 캔들 마감 10분 전
        # 00:50, 04:50, 08:50, 12:50, 16:50, 20:50
        self.scheduler.add_job(
            self._job_send_buy_preview_alerts,
            CronTrigger(hour='0,4,8,12,16,20', minute=50),
            id="buy_preview_alert",
            name="Send Buy Preview Alerts",
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True
        )
    
    async def _job_refresh_whitelist(self):
        """Refresh whitelist of top 20 coins (global data, shared by all users)"""
        try:
            whitelist, added, removed = whitelist_service.refresh_whitelist()
            
            # Log as system log (user_id=None) - all users can see this
            self._log_system("INFO", f"📊 감시종목 갱신: {len(whitelist)}개 종목")
            
            # Log changes if any
            if added or removed:
                change_parts = []
                if added:
                    added_names = [m.replace("KRW-", "") for m in added]
                    change_parts.append(f"추가: {', '.join(added_names)}")
                if removed:
                    removed_names = [m.replace("KRW-", "") for m in removed]
                    change_parts.append(f"제거: {', '.join(removed_names)}")
                
                self._log_system("INFO", f"📋 감시종목 변경 - {' / '.join(change_parts)}")
                    
        except Exception as e:
            self._log_system("ERROR", f"감시종목 갱신 실패: {e}")
    
    def _get_active_users(self) -> List[Tuple[int, UserSettings]]:
        """Get all users with active trading (simulation or real)"""
        db = SessionLocal()
        try:
            active_users = db.query(UserSettings).filter(
                (UserSettings.bot_simulation_running == True) | 
                (UserSettings.bot_real_running == True)
            ).all()
            # Return list of (user_id, user_settings) tuples
            return [(us.user_id, us) for us in active_users]
        except Exception as e:
            logger.error(f"Error getting active users: {e}")
            return []
        finally:
            db.close()
    
    async def _job_check_strategies(self):
        """Check entry signals for all strategies - runs for each active user and mode"""
        from concurrent.futures import ThreadPoolExecutor
        import asyncio
        
        # Get all users with active trading
        active_users = self._get_active_users()
        
        # Debug: log active user count
        sim_count = sum(1 for _, u in active_users if u.bot_simulation_running)
        real_count = sum(1 for _, u in active_users if u.bot_real_running)
        logger.info(f"[Upbit] 전략 체크 시작: {len(active_users)}명 (sim={sim_count}, real={real_count})")
        
        for user_id, user_settings in active_users:
            # Collect tasks for this user
            tasks = []
            
            # Check simulation mode for this user
            if user_settings.bot_simulation_running:
                tasks.append(("simulation", user_id, user_settings))
            
            # Check real mode for this user
            if user_settings.bot_real_running:
                logger.info(f"[Upbit] User {user_id} real 모드 전략 체크 시작")
                tasks.append(("real", user_id, user_settings))
            
            # Run simulation and real in parallel using ThreadPoolExecutor
            if tasks:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(self._check_strategies_for_user, uid, settings, mode)
                        for mode, uid, settings in tasks
                    ]
                    # Wait for all tasks to complete
                    for future in futures:
                        try:
                            future.result()
                        except Exception as e:
                            logger.error(f"[Upbit] Error in parallel strategy check: {e}")
    
    async def _job_log_strategy_signals(self):
        """
        Log strategy signals ONCE (not per user) - runs at exact 5-minute intervals.
        Logs pure signals with timeframe info. "(보유중)" labels are added dynamically by API based on each user's positions.
        """
        # Check if any mode is running
        if not (self.state.simulation_running or self.state.real_running):
            return
        
        try:
            markets = whitelist_service.get_whitelist_markets()
            
            # Get default strategy settings (from first active user or defaults)
            db = SessionLocal()
            first_user_settings = db.query(UserSettings).first()
            db.close()
            strategy_settings = self._get_user_strategy_settings(first_user_settings)
            squirrel_config = strategy_settings.get("squirrel", {"enabled": True})
            morning_config = strategy_settings.get("morning", {"enabled": True})
            inverted_hammer_config = strategy_settings.get("inverted_hammer", {"enabled": True})
            divergence_config = strategy_settings.get("divergence", {"enabled": True})
            harmonic_config = strategy_settings.get("harmonic", {"enabled": True})
            leading_diagonal_config = strategy_settings.get("leading_diagonal", {"enabled": True})
            
            # Log current candle timing status (once)
            is_1d_window, reason_1d = is_within_candle_close_window("1D")
            is_4h_window, reason_4h = is_within_candle_close_window("4H")
            timing_status = []
            if is_1d_window:
                timing_status.append("1D✅")
            if is_4h_window:
                timing_status.append("4H✅")
            if not timing_status:
                now = now_kst()
                hour = now.hour
                next_4h = min([h for h in CANDLE_CLOSE_HOURS_4H if h > hour] or [CANDLE_CLOSE_HOURS_4H[0] + 24]) % 24
                timing_status.append(f"매수대기(4H→{next_4h:02d}:00, 1D→09:00)")
            
            self._log_system("INFO", f"⏰ [Upbit] 캔들 마감 체크: {' '.join(timing_status)}")
            
            # Collect all signals for TOP 5 logging
            all_signals: List[Tuple[str, str, float, str]] = []  # (market, strategy, confidence, signal_type)
            
            # ★ 보유/쿨다운 상태는 사용자별로 다르므로 공통 로그에서 제거
            # 이 정보는 _check_strategies_for_all_users에서 사용자별로 확인됨

            
            for market in markets:
                # Analyze with Squirrel strategy (1D)
                if squirrel_config.get("enabled", True):
                    squirrel_signal = squirrel_strategy.analyze(market)
                    if squirrel_signal.confidence >= 0.01:
                        all_signals.append((
                            market, "다람쥐(1D)", 
                            squirrel_signal.confidence,
                            squirrel_signal.signal_type
                        ))
                
                # Analyze with Morning Star strategy - 4H
                if morning_config.get("enabled", True):
                    morning_signal = morning_star_strategy.analyze(market)
                    if morning_signal.confidence >= 0.01:
                        all_signals.append((
                            market, "샛별형(4H)", 
                            morning_signal.confidence,
                            morning_signal.signal_type
                        ))
                
                # Analyze with Morning Star strategy - Daily
                if morning_config.get("enabled", True):
                    morning_signal_daily = morning_star_strategy_daily.analyze(market)
                    if morning_signal_daily.confidence >= 0.01:
                        all_signals.append((
                            market, "샛별형(1D)", 
                            morning_signal_daily.confidence,
                            morning_signal_daily.signal_type
                        ))
                
                # Analyze with Inverted Hammer strategy - Daily
                if inverted_hammer_config.get("enabled", True):
                    hammer_signal = inverted_hammer_strategy.analyze(market)
                    if hammer_signal.confidence >= 0.01:
                        all_signals.append((
                            market, "윗꼬리양봉(1D)", 
                            hammer_signal.confidence,
                            hammer_signal.signal_type
                        ))
                
                # Analyze with Inverted Hammer strategy - 4H
                if inverted_hammer_config.get("enabled", True):
                    hammer_signal_4h = inverted_hammer_strategy_4h.analyze(market)
                    if hammer_signal_4h.confidence >= 0.01:
                        all_signals.append((
                            market, "윗꼬리양봉(4H)", 
                            hammer_signal_4h.confidence,
                            hammer_signal_4h.signal_type
                        ))
                
                # === 신규 전략 분석 (1D) ===
                try:
                    df = UpbitClient.get_ohlcv(market, interval="day", count=100)
                    if df is not None and len(df) >= 30:
                        
                        # Analyze with Divergence strategy - 1D
                        if divergence_config.get("enabled", True):
                            is_signal, confidence, info = divergence_strategy.analyze(df)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((market, "다이버전스(1D)", confidence, signal_type))
                        
                        # Analyze with Harmonic strategy - 1D
                        if harmonic_config.get("enabled", True):
                            is_signal, confidence, info = harmonic_strategy.analyze(df)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((market, "하모닉(1D)", confidence, signal_type))
                        
                        # Analyze with Leading Diagonal strategy - 1D
                        if leading_diagonal_config.get("enabled", True):
                            is_signal, confidence, info = leading_diagonal_strategy.analyze(df)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((market, "리딩다이아(1D)", confidence, signal_type))
                except Exception as e:
                    logger.debug(f"[{market}] 신규 전략 1D 분석 오류: {e}")
                
                # === 신규 전략 분석 (4H) ===
                try:
                    df_4h = UpbitClient.get_ohlcv(market, interval="minute240", count=100)
                    if df_4h is not None and len(df_4h) >= 30:
                        
                        # Analyze with Divergence strategy - 4H
                        if divergence_config.get("enabled", True):
                            is_signal, confidence, info = divergence_strategy.analyze(df_4h)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((market, "다이버전스(4H)", confidence, signal_type))
                        
                        # Analyze with Harmonic strategy - 4H
                        if harmonic_config.get("enabled", True):
                            is_signal, confidence, info = harmonic_strategy.analyze(df_4h)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((market, "하모닉(4H)", confidence, signal_type))
                        
                        # Analyze with Leading Diagonal strategy - 4H
                        if leading_diagonal_config.get("enabled", True):
                            is_signal, confidence, info = leading_diagonal_strategy.analyze(df_4h)
                            if confidence >= 0.01:
                                signal_type = "buy" if is_signal else "none"
                                all_signals.append((market, "리딩다이아(4H)", confidence, signal_type))
                except Exception as e:
                    logger.debug(f"[{market}] 신규 전략 4H 분석 오류: {e}")
            
            # === 전략별 TOP 5 로깅 ===
            strategy_groups = {
                "다람쥐": {"name": "다람쥐", "threshold": 0, "signals": []},
                "샛별형": {"name": "샛별형", "threshold": 0, "signals": []},
                "윗꼬리양봉": {"name": "윗꼬리양봉", "threshold": 0, "signals": []},
                "다이버전스": {"name": "다이버전스", "threshold": 0, "signals": []},
                "하모닉": {"name": "하모닉", "threshold": 0, "signals": []},
                "리딩다이아": {"name": "리딩다이아", "threshold": 0, "signals": []},
            }
            
            # 신호를 전략별로 그룹화
            for market, strategy, confidence, signal_type in all_signals:
                for key in strategy_groups.keys():
                    if key in strategy:
                        strategy_groups[key]["signals"].append((market, strategy, confidence, signal_type))
                        break
            
            # 전략별 TOP 5 로깅 - 각 전략을 한 줄로 표시
            for key, group in strategy_groups.items():
                signals = group["signals"]
                
                if not signals:
                    self._log_system("INFO", f"🎯 [Upbit][{group['name']}] 신호 없음")
                    continue
                
                # 신뢰도 순으로 정렬 후 TOP 5
                signals.sort(key=lambda x: x[2], reverse=True)
                top5 = signals[:5]
                
                threshold = group["threshold"]
                
                # 한 줄로 압축: 🎯 [샛별형] ENA(4H)⭐ MNT(1D) POL(1D)⭐(보유중)
                items = []
                for market, strategy_name, confidence, signal_type in top5:
                    coin_name = market.replace('KRW-', '')
                    is_buy = signal_type == "buy"
                    
                    # 타임프레임 정보
                    tf_info = ""
                    if "4H" in strategy_name:
                        tf_info = "(4H)"
                    elif "1D" in strategy_name:
                        tf_info = "(1D)"
                    
                    marker = "⭐" if is_buy else ""
                    
                    # ★ 보유/쿨다운 상태는 사용자별로 다르므로 공통 로그에서 생략
                    items.append(f"{coin_name}{tf_info}{marker}")
                
                log_line = f"🎯 [Upbit][{group['name']}] {' '.join(items)}"
                self._log_system("INFO", log_line)
                
        except Exception as e:
            self._log_system("ERROR", f"전략 신호 로깅 오류: {e}")
    
    
    def _get_user_strategy_settings(self, user_settings: UserSettings) -> dict:
        """Get strategy settings from user's settings"""
        try:
            if user_settings and user_settings.strategy_settings:
                return json.loads(user_settings.strategy_settings)
        except Exception as e:
            logger.error(f"Error parsing user strategy settings: {e}")
        
        # Default settings
        return {
            "squirrel": {"enabled": True},
            "morning": {"enabled": True},
            "inverted_hammer": {"enabled": True},
            "divergence": {"enabled": True},
            "harmonic": {"enabled": True},
            "leading_diagonal": {"enabled": True}
        }
    
    def _check_rebuy_cooldown(self, user_id: int, coin: str, strategy: str, mode: str) -> Tuple[bool, str]:
        """
        Check if a coin is on rebuy cooldown.
        
        Rules:
        - After any sell: 1 hour cooldown for all strategies
        - Same strategy: 24 hour cooldown
        - Different strategy: 1 hour cooldown
        
        Args:
            user_id: User ID
            coin: Coin to check (e.g., 'KRW-BTC')
            strategy: Strategy attempting to buy (e.g., 'squirrel')
            mode: Trading mode ('simulation' or 'real')
            
        Returns:
            Tuple of (can_buy: bool, reason: str)
            - can_buy: True if no cooldown, False if on cooldown
            - reason: Empty if can buy, cooldown reason if blocked
        """
        try:
            db = SessionLocal()
            now = now_kst()
            
            # Find the most recent SELL trade for this user, coin, and mode
            recent_sell = db.query(TradeLog).filter(
                TradeLog.user_id == user_id,
                TradeLog.coin == coin,
                TradeLog.mode == mode,
                TradeLog.side == "sell"
            ).order_by(TradeLog.created_at.desc()).first()
            
            db.close()
            
            if not recent_sell:
                # No sell history for this coin - OK to buy
                return True, ""
            
            # Calculate time since last sell
            sell_time = recent_sell.created_at
            
            # Fix timezone issue: DB datetime might be naive, treat it as KST
            if sell_time.tzinfo is None:
                sell_time = sell_time.replace(tzinfo=KST)
                
            elapsed_seconds = (now - sell_time).total_seconds()
            
            # Check cooldown (4 hours for ALL strategies)
            if elapsed_seconds < REBUY_COOLDOWN:
                remaining = REBUY_COOLDOWN - elapsed_seconds
                remaining_hours = int(remaining / 3600)
                remaining_min = int((remaining % 3600) / 60)
                return False, f"쿨다운: 매도 후 4시간 대기 필요 ({remaining_hours}시간 {remaining_min}분 남음)"
            
            # Cooldown passed - OK to buy
            return True, ""
            
        except Exception as e:
            logger.error(f"Error checking rebuy cooldown: {e}")
            # On error, allow the trade (fail-open)
            return True, ""
    
    def _collect_and_execute_batch_buys(self, user_id: int, user_settings, mode: str, markets: list, 
                                         strategy_settings: dict, squirrel_config: dict, morning_config: dict, 
                                         inverted_hammer_config: dict) -> None:
        """
        ★ PHASE 10: 균등 포지션 배분 로직
        - 모든 마켓의 신호를 수집 → 상위 3개 선택 → 균등 비율로 실행
        - MAX_PER_EXECUTION: 한 캔들 마감에서 최대 3개 매수
        - MAX_POSITIONS: 계정당 최대 5개 보유
        - 캔들 윈도우당 1회만 실행 (중복 방지)
        """
        MAX_POSITIONS = 5       # 계정당 최대 포지션 수
        MAX_PER_EXECUTION = 3   # 한 캔들 마감에서 최대 매수 개수
        MIN_ORDER_KRW = 10000
        
        is_simulation = (mode == "simulation")
        mode_label = "모의" if is_simulation else "실전"
        
        # Get positions for filtering
        positions = order_manager.get_open_positions(mode=mode, user_id=user_id)
        owned_coins = {p['coin'] for p in positions}
        current_position_count = len(positions)
        
        # ★ MAX_POSITIONS 체크: 이미 최대 포지션 보유 시 스킵
        if current_position_count >= MAX_POSITIONS:
            logger.debug(f"[{mode_label}] User {user_id} 이미 {current_position_count}개 포지션 보유 (MAX={MAX_POSITIONS}), 매수 스킵")
            return
        
        # 추가 가능한 포지션 수 계산 (MAX_POSITIONS 기준)
        available_slots = MAX_POSITIONS - current_position_count
        # ★ MAX_PER_EXECUTION 제한 적용
        max_buys_this_run = min(available_slots, MAX_PER_EXECUTION)
        
        # ★ 캔들 윈도우 중복 실행 방지
        # 1D, 4H 윈도우 체크
        is_1d_window, _ = is_within_candle_close_window("1D")
        is_4h_window, _ = is_within_candle_close_window("4H")
        
        if not is_1d_window and not is_4h_window:
            # 캔들 마감 윈도우가 아니면 매수 안함
            return
        
        # 현재 윈도우 식별자 생성
        now = now_kst()
        window_key = f"{user_id}:{mode}"
        
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
        
        # 모든 활성 타임프레임에서 이미 실행됐으면 스킵
        if not available_timeframes:
            logger.debug(f"[{mode_label}] User {user_id} 이미 이 캔들 윈도우에서 실행됨, 스킵")
            return
        
        # ========== PHASE 1: 모든 마켓에서 신호 수집 ==========
        all_candidates = []
        
        for market in markets:
            if market in owned_coins:
                continue
            
            buy_candidates = []
            
            # Squirrel strategy (1D only)
            if squirrel_config.get("enabled", True):
                squirrel_signal = squirrel_strategy.analyze(market)
                if squirrel_signal.signal_type == "buy":
                    buy_candidates.append({
                        "market": market, "strategy": "squirrel", "strategy_name": "다람쥐",
                        "timeframe": "1D", "confidence": squirrel_signal.confidence, "priority": 3,
                        "reference_data": {
                            "reference_candle_open": squirrel_signal.reference_candle_open,
                            "reference_candle_high": squirrel_signal.reference_candle_high,
                            "stop_loss": squirrel_signal.stop_loss,
                            "take_profit": squirrel_signal.take_profit,
                            "atr": squirrel_signal.atr
                        }
                    })
            
            # Morning Star - Daily
            if morning_config.get("enabled", True):
                morning_signal_daily = morning_star_strategy_daily.analyze(market)
                if morning_signal_daily.signal_type == "buy":
                    buy_candidates.append({
                        "market": market, "strategy": "morning", "strategy_name": "샛별형(1D)",
                        "timeframe": "1D", "confidence": morning_signal_daily.confidence, "priority": 1,
                        "reference_data": {
                            "pattern_low": morning_signal_daily.pattern_low,
                            "pattern_high": morning_signal_daily.pattern_high,
                            "stop_loss": morning_signal_daily.stop_loss,
                            "take_profit": morning_signal_daily.take_profit,
                            "atr": morning_signal_daily.atr
                        }
                    })
            
            # Morning Star - 4H
            if morning_config.get("enabled", True):
                morning_signal = morning_star_strategy.analyze(market)
                if morning_signal.signal_type == "buy":
                    buy_candidates.append({
                        "market": market, "strategy": "morning", "strategy_name": "샛별형(4H)",
                        "timeframe": "4H", "confidence": morning_signal.confidence, "priority": 1,
                        "reference_data": {
                            "pattern_low": morning_signal.pattern_low, "pattern_high": morning_signal.pattern_high,
                            "stop_loss": morning_signal.stop_loss, "take_profit": morning_signal.take_profit,
                            "atr": morning_signal.atr
                        }
                    })
            
            # Inverted Hammer - Daily
            if inverted_hammer_config.get("enabled", True):
                hammer_signal = inverted_hammer_strategy.analyze(market)
                if hammer_signal.signal_type == "buy":
                    buy_candidates.append({
                        "market": market, "strategy": "inverted_hammer", "strategy_name": "윗꼬리양봉(1D)",
                        "timeframe": "1D", "confidence": hammer_signal.confidence, "priority": 2,
                        "reference_data": {
                            "pattern_high": hammer_signal.pattern_high, "pattern_low": hammer_signal.pattern_low,
                            "stop_loss": hammer_signal.stop_loss, "take_profit": hammer_signal.take_profit,
                            "atr": hammer_signal.atr
                        }
                    })
            
            # Inverted Hammer - 4H
            if inverted_hammer_config.get("enabled", True):
                hammer_signal_4h = inverted_hammer_strategy_4h.analyze(market)
                if hammer_signal_4h.signal_type == "buy":
                    buy_candidates.append({
                        "market": market, "strategy": "inverted_hammer", "strategy_name": "윗꼬리양봉(4H)",
                        "timeframe": "4H", "confidence": hammer_signal_4h.confidence, "priority": 2,
                        "reference_data": {
                            "pattern_high": hammer_signal_4h.pattern_high, "pattern_low": hammer_signal_4h.pattern_low,
                            "stop_loss": hammer_signal_4h.stop_loss, "take_profit": hammer_signal_4h.take_profit,
                            "atr": hammer_signal_4h.atr
                        }
                    })
            
            # 신규 전략들 (Divergence, Harmonic, Leading Diagonal)
            try:
                divergence_config = strategy_settings.get("divergence", {"enabled": True})
                harmonic_config = strategy_settings.get("harmonic", {"enabled": True})
                leading_config = strategy_settings.get("leading_diagonal", {"enabled": True})
                
                df_1d = UpbitClient.get_ohlcv(market, interval="day", count=100)
                if df_1d is not None and len(df_1d) >= 30:
                    if divergence_config.get("enabled", True):
                        is_signal, confidence, info = divergence_strategy.analyze(df_1d)
                        if is_signal:
                            buy_candidates.append({
                                "market": market, "strategy": "divergence", "strategy_name": "다이버전스(1D)",
                                "timeframe": "1D", "confidence": confidence, "priority": 1,
                                "reference_data": {"stop_loss": info.get("stop_loss"), "take_profit": info.get("take_profit")}
                            })
                    if harmonic_config.get("enabled", True):
                        is_signal, confidence, info = harmonic_strategy.analyze(df_1d)
                        if is_signal:
                            buy_candidates.append({
                                "market": market, "strategy": "harmonic", "strategy_name": "하모닉(1D)",
                                "timeframe": "1D", "confidence": confidence, "priority": 2,
                                "reference_data": {"stop_loss": info.get("stop_loss"), "take_profit": info.get("take_profit")}
                            })
                    if leading_config.get("enabled", True):
                        is_signal, confidence, info = leading_diagonal_strategy.analyze(df_1d)
                        if is_signal:
                            buy_candidates.append({
                                "market": market, "strategy": "leading_diagonal", "strategy_name": "리딩다이아(1D)",
                                "timeframe": "1D", "confidence": confidence, "priority": 3,
                                "reference_data": {"stop_loss": info.get("stop_loss"), "take_profit": info.get("take_profit")}
                            })
                
                df_4h = UpbitClient.get_ohlcv(market, interval="minute240", count=100)
                if df_4h is not None and len(df_4h) >= 30:
                    if divergence_config.get("enabled", True):
                        is_signal, confidence, info = divergence_strategy.analyze(df_4h)
                        if is_signal:
                            buy_candidates.append({
                                "market": market, "strategy": "divergence", "strategy_name": "다이버전스(4H)",
                                "timeframe": "4H", "confidence": confidence, "priority": 1,
                                "reference_data": {"stop_loss": info.get("stop_loss"), "take_profit": info.get("take_profit")}
                            })
                    if harmonic_config.get("enabled", True):
                        is_signal, confidence, info = harmonic_strategy.analyze(df_4h)
                        if is_signal:
                            buy_candidates.append({
                                "market": market, "strategy": "harmonic", "strategy_name": "하모닉(4H)",
                                "timeframe": "4H", "confidence": confidence, "priority": 2,
                                "reference_data": {"stop_loss": info.get("stop_loss"), "take_profit": info.get("take_profit")}
                            })
                    if leading_config.get("enabled", True):
                        is_signal, confidence, info = leading_diagonal_strategy.analyze(df_4h)
                        if is_signal:
                            buy_candidates.append({
                                "market": market, "strategy": "leading_diagonal", "strategy_name": "리딩다이아(4H)",
                                "timeframe": "4H", "confidence": confidence, "priority": 3,
                                "reference_data": {"stop_loss": info.get("stop_loss"), "take_profit": info.get("take_profit")}
                            })
            except Exception as e:
                logger.debug(f"[{market}] 신규 전략 분석 오류: {e}")
            
            # 마켓별 최고 후보 하나 선택 (쿨다운 + 캔들 타이밍 체크)
            if buy_candidates:
                buy_candidates.sort(key=lambda x: (-x["confidence"], 0 if x["timeframe"] == "1D" else 1, x["priority"]))
                for candidate in buy_candidates:
                    can_buy, _ = self._check_rebuy_cooldown(user_id=user_id, coin=market, strategy=candidate["strategy"], mode=mode)
                    if can_buy:
                        is_within_window, _ = is_within_candle_close_window(candidate["timeframe"])
                        if is_within_window:
                            all_candidates.append(candidate)
                        break
        
        # ========== PHASE 2: 상위 N개 선택 (max_buys_this_run 만큼) ==========
        if not all_candidates:
            return
        
        all_candidates.sort(key=lambda x: (-x["confidence"], 0 if x["timeframe"] == "1D" else 1, x["priority"]))
        # ★ max_buys_this_run 만큼만 선택 (MAX_PER_EXECUTION 적용)
        top_candidates = all_candidates[:max_buys_this_run]
        
        # ========== PHASE 3: 균등 배분 비율 ==========
        # ★ 이번에 매수하는 개수 기준 (보유 포지션과 무관)
        # 3개→각20%, 2개→각30%, 1개→50%
        n = len(top_candidates)
        if n >= 3:
            pct = 0.20
        elif n == 2:
            pct = 0.30
        else:
            pct = 0.50
        logger.info(f"[{mode_label}] User {user_id} 매수 {n}개 신호, 각 {pct*100:.0f}% 배분")
        
        # ========== PHASE 4: 일괄 실행 ==========
        # ★ 버그 수정: 루프 전에 잔고와 배분금액을 미리 계산 (순차 감소 방지)
        initial_balance = order_manager.get_balance_for_user(is_simulation, user_settings)
        order_amount_per_coin = initial_balance * pct
        
        logger.info(f"[{mode_label}] User {user_id} 초기잔고: ₩{initial_balance:,.0f}, 종목당 배분: ₩{order_amount_per_coin:,.0f}")
        
        for candidate in top_candidates:
            market = candidate["market"]
            
            # ★ 미리 계산된 금액 사용 (잔고 재조회 안함)
            if order_amount_per_coin < MIN_ORDER_KRW:
                logger.info(f"[{mode_label}] {market} 최소 금액 미달 (₩{order_amount_per_coin:,.0f} < ₩{MIN_ORDER_KRW:,}), 스킵")
                continue
            
            logger.info(f"[{mode_label}] User {user_id} {market} 매수 시도: {candidate['strategy_name']} (₩{order_amount_per_coin:,.0f})")
            
            result = order_manager.execute_buy(
                market=market, strategy=candidate["strategy"], timeframe=candidate["timeframe"],
                confidence=candidate["confidence"], reference_data=candidate["reference_data"],
                is_simulation=is_simulation, user_id=user_id, user_settings=user_settings,
                order_amount=order_amount_per_coin
            )
            
            if result.success:
                self._log_system("INFO", f"[{mode_label}][{candidate['strategy_name']}] {market} 매수 완료 @ {result.executed_price:,.0f}원", mode=mode, user_id=user_id)
                telegram_service.send_user_trade_alert(
                    user_id=user_id, side="buy", coin=market, price=result.executed_price,
                    quantity=result.executed_quantity, strategy=candidate['strategy'],
                    is_simulation=is_simulation, confidence=candidate['confidence'],
                    total_krw=result.executed_price * result.executed_quantity,
                    remaining_balance=order_manager.get_balance_for_user(is_simulation, user_settings)
                )
            else:
                self._log_system("ERROR", f"[{mode_label}][{candidate['strategy_name']}] {market} 매수 실패: {result.message}", mode=mode, user_id=user_id)
        
        # ★ 실행 후 시간 기록 (캔들 윈도우 중복 방지)
        for tf in available_timeframes:
            exec_key = f"{user_id}:{mode}:{tf}"
            self._last_execution_times[exec_key] = now_kst()
    
    def _check_strategies_for_user(self, user_id: int, user_settings: UserSettings, mode: str):
        """Check strategies for a specific user and mode - executes trades only (logging is done separately)"""
        try:
            self.state.set_last_check(mode)
            markets = whitelist_service.get_whitelist_markets()
            is_simulation = (mode == "simulation")
            mode_label = "모의" if is_simulation else "실전"
            
            # Get THIS USER's strategy settings
            strategy_settings = self._get_user_strategy_settings(user_settings)
            squirrel_config = strategy_settings.get("squirrel", {"enabled": True})
            morning_config = strategy_settings.get("morning", {"enabled": True})
            inverted_hammer_config = strategy_settings.get("inverted_hammer", {"enabled": True})
            
            # Check THIS USER's balance before attempting any trades
            current_balance = order_manager.get_balance_for_user(is_simulation, user_settings)
            if current_balance < order_manager.MIN_ORDER_AMOUNT:
                # Skip trading - not enough balance
                logger.info(f"[{mode_label}] User {user_id} 잔고 부족으로 스킵: {current_balance:,.0f}원")
                return
            
            # ★ PHASE 10: 새 배치 실행 로직 호출
            self._collect_and_execute_batch_buys(
                user_id=user_id,
                user_settings=user_settings,
                mode=mode,
                markets=markets,
                strategy_settings=strategy_settings,
                squirrel_config=squirrel_config,
                morning_config=morning_config,
                inverted_hammer_config=inverted_hammer_config
            )
        except Exception as e:
            self._log_system("ERROR", f"[{mode}] 전략 체크 오류: {e}", mode=mode, user_id=user_id)
    
    # ★ Phase 9: _job_check_expected_exits 함수 삭제됨 (SL/TP가 진입시 확정되므로 불필요)
    async def _job_check_exits(self):
        """Check exit conditions for open positions - runs if any mode is active"""
        # Run if at least one mode is running
        if not (self.state.simulation_running or self.state.real_running):
            return
        
        try:
            db = SessionLocal()
            positions = db.query(Position).all()
            
            for position in positions:
                should_exit = False
                reason = ""
                exit_type = None
                
                # Skip Bybit positions (handled by bybit scheduler)
                if position.coin.endswith("USDT"):
                    continue
                
                try:
                    # 현재가 조회
                    current_price = UpbitClient.get_current_price([position.coin]).get(position.coin, 0)
                    if current_price <= 0:
                        continue
                    
                    # ★ Phase 5: 단순화된 SL/TP 체크 (모든 전략 공통)
                    stop_loss = position.stop_loss or position.reference_candle_low
                    take_profit = position.take_profit or position.reference_candle_high
                    
                    if not stop_loss or not take_profit:
                        logger.warning(f"[{position.coin}] SL/TP 미설정, 스킵")
                        continue
                    
                    profit_pct = (current_price - position.entry_price) / position.entry_price
                    
                    # 롱 전략 (Upbit는 현물만 있으므로 모두 롱)
                    if current_price <= stop_loss:
                        should_exit = True
                        reason = f"손절: SL 도달 ({profit_pct*100:+.1f}%)"
                        exit_type = "stop_loss"
                        logger.info(f"[{position.coin}] SL 트리거: 현재가={current_price:.0f}, SL={stop_loss:.0f}, Entry={position.entry_price:.0f}")
                    elif current_price >= take_profit:
                        should_exit = True
                        reason = f"익절: TP 도달 ({profit_pct*100:+.1f}%)"
                        exit_type = "take_profit"
                    
                except Exception as e:
                    logger.error(f"[{position.coin}] 청산체크 오류: {e}")
                    continue
                
                if should_exit:
                    # ★ Phase 5: 항상 100% 청산 (분할청산 제거)
                    sell_quantity = position.quantity
                    
                    # Determine mode from position
                    is_simulation = position.mode == "simulation"
                    mode_label = "모의" if is_simulation else "실전"
                    
                    # Get user settings for this position's owner
                    pos_user_settings = db.query(UserSettings).filter(
                        UserSettings.user_id == position.user_id
                    ).first() if position.user_id else None
                    
                    # Log exit
                    self._log_system(
                        "INFO",
                        f"[{mode_label}][청산] {position.coin} - {reason}",
                        mode="simulation" if is_simulation else "real",
                        user_id=position.user_id
                    )
                    
                    result = order_manager.execute_sell(
                        market=position.coin,
                        quantity=sell_quantity,
                        reason=reason,
                        is_simulation=is_simulation,
                        user_id=position.user_id,
                        user_settings=pos_user_settings
                    )
                    
                    if result.success:
                        pnl_percent = ((result.executed_price - position.entry_price) / position.entry_price) * 100
                        
                        # ★ Phase 9: is_partial 분기 제거 - 100% 청산만 지원
                        
                        telegram_service.send_user_trade_alert(
                            user_id=position.user_id,
                            side="sell",
                            coin=position.coin,
                            price=result.executed_price,
                            quantity=result.executed_quantity,
                            strategy=position.strategy,
                            pnl_percent=pnl_percent,
                            is_simulation=is_simulation,
                            entry_price=position.entry_price,
                            remaining_balance=order_manager.get_balance_for_user(is_simulation, pos_user_settings)
                        )
                    else:
                        # ★ 청산 실행 실패 로그 추가
                        self._log_system(
                            "ERROR",
                            f"[{mode_label}][청산실패] {position.coin} - {result.message}",
                            mode="simulation" if is_simulation else "real",
                            user_id=position.user_id
                        )
            
            db.close()
            
        except Exception as e:
            self._log_system("ERROR", f"청산 체크 오류: {e}")
    
    async def _job_check_order_timeouts(self):
        """Cancel orders older than 5 minutes"""
        if not (self.state.simulation_running or self.state.real_running):
            return
        
        try:
            cancelled = order_manager.cancel_stale_orders()
            if cancelled > 0:
                self._log_system("INFO", f"{cancelled}개 미체결 주문 취소됨")
        except Exception as e:
            self._log_system("ERROR", f"주문 타임아웃 체크 오류: {e}")
    
    async def _job_cleanup_logs(self):
        """Delete system logs older than 24 hours"""
        try:
            db = SessionLocal()
            cutoff = datetime.utcnow() - timedelta(hours=24)
            
            deleted = db.query(SystemLog).filter(
                SystemLog.created_at < cutoff
            ).delete()
            
            db.commit()
            db.close()
            
            if deleted > 0:
                self._log_system("INFO", f"{deleted}개 오래된 로그 삭제됨")
                
        except Exception as e:
            logger.error(f"Log cleanup failed: {e}")
    
    async def _job_sync_real_positions(self):
        """
        Sync DB positions with actual exchange holdings for real mode.
        Removes positions from DB that no longer exist on the exchange
        (e.g., manually sold positions).
        """
        if not self.state.real_running:
            return
        
        try:
            db = SessionLocal()
            
            # Get all users with real mode running
            active_users = db.query(UserSettings).filter(
                UserSettings.bot_real_running == True
            ).all()
            
            for user_settings in active_users:
                if not user_settings.upbit_access_key:
                    continue
                
                try:
                    from utils.encryption import encryptor
                    from services.upbit_client import UpbitClient
                    
                    api_key = encryptor.decrypt(user_settings.upbit_access_key)
                    api_secret = encryptor.decrypt(user_settings.upbit_secret_key)
                    
                    upbit = UpbitClient(api_key, api_secret)
                    
                    # Get actual holdings from exchange
                    actual_holdings = upbit.get_balances()
                    actual_markets = set()
                    for balance in actual_holdings:
                        if float(balance.get('balance', 0)) > 0:
                            actual_markets.add(f"KRW-{balance['currency']}")
                    
                    # Get DB positions for this user
                    db_positions = db.query(Position).filter(
                        Position.user_id == user_settings.user_id,
                        Position.exchange == 'upbit',
                        Position.mode == 'real',
                        Position.quantity > 0
                    ).all()
                    
                    # Find orphan positions (in DB but not on exchange)
                    orphan_count = 0
                    for position in db_positions:
                        if position.coin not in actual_markets:
                            logger.info(f"[Sync] Removing orphan position: {position.coin} (user {user_settings.user_id})")
                            db.delete(position)
                            orphan_count += 1
                    
                    if orphan_count > 0:
                        db.commit()
                        self._log_system(
                            "INFO",
                            f"[포지션동기화] {orphan_count}개 고아 포지션 제거 (수동매도 감지)",
                            mode="real",
                            user_id=user_settings.user_id
                        )
                        
                except Exception as e:
                    logger.error(f"[Sync] Error syncing positions for user {user_settings.user_id}: {e}")
            
            db.close()
            
        except Exception as e:
            logger.error(f"[Sync] Position sync failed: {e}")
    
    def _log_system(self, level: str, message: str, mode: str = None, user_id: int = None):
        """Log message to database and console"""
        logger.info(f"[{level}] {message}")
        try:
            db = SessionLocal()
            log = SystemLog(
                user_id=user_id,  # 유저별 로그 저장 (None이면 시스템 로그)
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
    
    # ===================
    # Control Methods
    # ===================
    
    def start_bot(self, mode: str = "simulation"):
        """Start the trading bot for a specific mode"""
        self.state.start(mode)
        mode_label = "실전" if mode == "real" else "모의투자"
        self._log_system("INFO", f"🚀 [{mode_label}] 트레이딩 봇 시작", mode=mode)
        telegram_service.send_system_alert(
            "봇 시작",
            f"모드: {mode_label}",
            "success"
        )
    
    def stop_bot(self, mode: str = "simulation"):
        """Stop the trading bot for a specific mode"""
        self.state.stop(mode)
        mode_label = "실전" if mode == "real" else "모의투자"
        self._log_system("INFO", f"🛑 [{mode_label}] 트레이딩 봇 정지", mode=mode)
        telegram_service.send_system_alert("봇 정지", f"{mode_label} 봇이 정지되었습니다.", "info")
    
    def get_status(self, mode: str = None) -> dict:
        """Get current bot status for a specific mode or both"""
        positions = order_manager.get_open_positions()
        whitelist = whitelist_service.get_whitelist()
        
        if mode:
            # Return status for specific mode
            return {
                "is_running": self.state.is_running(mode),
                "mode": mode,
                "uptime_seconds": self.state.get_uptime(mode),
                "last_check": self.state.get_last_check(mode),
                "whitelist_count": len(whitelist),
                "active_positions": len(positions)
            }
        else:
            # Return status for both modes (legacy compatibility)
            return {
                "simulation_running": self.state.simulation_running,
                "real_running": self.state.real_running,
                "simulation_uptime": self.state.get_uptime("simulation"),
                "real_uptime": self.state.get_uptime("real"),
                "simulation_last_check": self.state.get_last_check("simulation"),
                "real_last_check": self.state.get_last_check("real"),
                "whitelist_count": len(whitelist),
                "active_positions": len(positions)
            }
    
    async def _job_send_buy_preview_alerts(self):
        """
        Send buy preview alerts to users with Telegram enabled.
        Runs at 00:50, 04:50, 08:50, 12:50, 16:50, 20:50 (10 minutes before candle close)
        """
        try:
            now = now_kst()
            next_candle_close = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            preview_time_str = next_candle_close.strftime("%Y-%m-%d %H:%M")
            
            # Determine timeframe based on current hour
            current_hour = now.hour
            # 4H candle closes at 01, 05, 09, 13, 17, 21
            # At :50, the upcoming hour will be candle close hour
            upcoming_hour = (current_hour + 1) % 24
            is_daily_candle = (upcoming_hour == 9)  # 1D candle closes at 09:00
            timeframe = "1D" if is_daily_candle else "4H"
            
            logger.info(f"[BuyPreview] Starting buy preview alerts for {timeframe} candle at {preview_time_str}")
            
            db = SessionLocal()
            
            # Get all users with Telegram enabled
            telegram_users = db.query(UserSettings).filter(
                UserSettings.telegram_enabled == True,
                UserSettings.telegram_token != None,
                UserSettings.telegram_chat_id != None
            ).all()
            
            if not telegram_users:
                logger.info("[BuyPreview] No users with Telegram enabled")
                db.close()
                return
            
            logger.info(f"[BuyPreview] Found {len(telegram_users)} users with Telegram enabled")
            
            # Get whitelist
            from services.upbit_client import UpbitClient
            whitelist = UpbitClient.get_whitelist()
            
            for user_settings in telegram_users:
                try:
                    user_id = user_settings.user_id
                    
                    # Check which modes are active
                    modes_to_check = []
                    if user_settings.bot_simulation_running:
                        modes_to_check.append("simulation")
                    if user_settings.bot_real_running:
                        modes_to_check.append("real")
                    
                    if not modes_to_check:
                        continue
                    
                    # Get user's strategy settings
                    strategy_settings = self._get_user_strategy_settings(user_settings)
                    
                    # Get user's current positions
                    user_positions = db.query(Position).filter(
                        Position.user_id == user_id
                    ).all()
                    
                    # Get recent sell trades (for cooldown check)
                    cooldown_cutoff = now - timedelta(hours=REBUY_COOLDOWN_HOURS)
                    recent_sells = db.query(TradeLog).filter(
                        TradeLog.user_id == user_id,
                        TradeLog.side == "sell",
                        TradeLog.created_at >= cooldown_cutoff
                    ).all()
                    cooldown_coins = {s.coin for s in recent_sells}
                    
                    for mode in modes_to_check:
                        # Filter positions for this mode
                        mode_positions = {
                            p.coin for p in user_positions 
                            if (p.mode == mode or p.mode is None) and 
                               (p.exchange == "upbit" or p.exchange is None)
                        }
                        
                        buy_signals = []
                        
                        # Check each strategy
                        for strategy_name, settings in strategy_settings.items():
                            if not settings.get('enabled', False):
                                continue
                            
                            
                            # Check entry conditions for each coin
                            for coin in whitelist:
                                # Skip if already holding
                                if coin in mode_positions:
                                    continue
                                
                                # Skip if in cooldown
                                if coin in cooldown_coins:
                                    continue
                                
                                try:
                                    # Analyze strategy signal (★ Phase 9: min_confidence 제거)
                                    signal_result = await self._analyze_strategy_signal(
                                        strategy_name, coin, timeframe
                                    )
                                    
                                    if signal_result and signal_result.get('is_buy'):
                                        buy_signals.append({
                                            'strategy': strategy_name,
                                            'coin': coin,
                                            'confidence': signal_result.get('confidence', 0),
                                            'entry_price': signal_result.get('entry_price', 0),
                                            'stop_loss': signal_result.get('stop_loss', 0),
                                            'take_profit_1': signal_result.get('take_profit_1', 0)
                                        })
                                except Exception as e:
                                    logger.debug(f"[BuyPreview] Error analyzing {coin} for {strategy_name}: {e}")
                        
                        # Sort by confidence (highest first) and limit to top 10
                        buy_signals.sort(key=lambda x: x.get('confidence', 0), reverse=True)
                        buy_signals = buy_signals[:10]
                        
                        # Send alert
                        telegram_service.send_buy_preview_alert(
                            user_id=user_id,
                            exchange="upbit",
                            mode=mode,
                            timeframe=timeframe,
                            preview_time=preview_time_str,
                            buy_signals=buy_signals
                        )
                        
                except Exception as e:
                    logger.error(f"[BuyPreview] Error processing user {user_settings.user_id}: {e}")
            
            db.close()
            logger.info(f"[BuyPreview] Completed buy preview alerts for {len(telegram_users)} users")
            
        except Exception as e:
            logger.error(f"[BuyPreview] Error in buy preview job: {e}")
    
    async def _analyze_strategy_signal(self, strategy_name: str, coin: str, timeframe: str) -> dict:
        """Analyze a single strategy signal for a coin"""
        try:
            result = {'is_buy': False}
            
            # Get current price
            from services.upbit_client import UpbitClient
            current_price = UpbitClient.get_ticker(coin)
            if not current_price:
                return result
            
            if strategy_name == "squirrel":
                signal = squirrel_strategy.analyze(coin)
                if signal.signal_type == "buy" and True:
                    stop_loss = signal.reference_candle_open or (current_price * 0.95)
                    return {
                        'is_buy': True,
                        'confidence': signal.confidence,
                        'entry_price': current_price,
                        'stop_loss': stop_loss,
                        'take_profit_1': current_price * 1.05
                    }
            
            elif strategy_name == "morning":
                strategy = morning_star_strategy if timeframe == "4H" else morning_star_strategy_daily
                signal = strategy.analyze(coin, timeframe="minute240" if timeframe == "4H" else "day")
                if signal.signal_type == "buy" and True:
                    stop_loss = signal.pattern_low or (current_price * 0.95)
                    return {
                        'is_buy': True,
                        'confidence': signal.confidence,
                        'entry_price': current_price,
                        'stop_loss': stop_loss,
                        'take_profit_1': current_price * 1.05
                    }
            
            elif strategy_name == "inverted_hammer":
                strategy = inverted_hammer_strategy if timeframe == "1D" else inverted_hammer_strategy_4h
                signal = strategy.analyze(coin, timeframe="day" if timeframe == "1D" else "minute240")
                if signal.signal_type == "buy" and True:
                    stop_loss = signal.pattern_low or (current_price * 0.95)
                    return {
                        'is_buy': True,
                        'confidence': signal.confidence,
                        'entry_price': current_price,
                        'stop_loss': stop_loss,
                        'take_profit_1': signal.pattern_high or (current_price * 1.05)
                    }
            
            elif strategy_name == "divergence":
                from services.upbit_client import UpbitClient
                interval = "minute240" if timeframe == "4H" else "day"
                df = UpbitClient.get_ohlcv(coin, interval=interval, count=100)
                if df is not None and len(df) >= 30:
                    is_buy, confidence, info = divergence_strategy.analyze(df)
                    if is_buy:
                        stop_loss = info.get('divergence_low', current_price * 0.95) if info else current_price * 0.95
                        return {
                            'is_buy': True,
                            'confidence': confidence,
                            'entry_price': current_price,
                            'stop_loss': stop_loss,
                            'take_profit_1': current_price * 1.05
                        }
            
            elif strategy_name == "harmonic":
                from services.upbit_client import UpbitClient
                interval = "minute240" if timeframe == "4H" else "day"
                df = UpbitClient.get_ohlcv(coin, interval=interval, count=100)
                if df is not None and len(df) >= 50:
                    is_buy, confidence, info = harmonic_strategy.analyze(df)
                    if is_buy:
                        stop_loss = info.get('stop_loss', current_price * 0.95) if info else current_price * 0.95
                        tp1 = info.get('A_point', current_price * 1.05) if info else current_price * 1.05
                        return {
                            'is_buy': True,
                            'confidence': confidence,
                            'entry_price': current_price,
                            'stop_loss': stop_loss,
                            'take_profit_1': tp1
                        }
            
            elif strategy_name == "leading_diagonal":
                from services.upbit_client import UpbitClient
                interval = "minute240" if timeframe == "4H" else "day"
                df = UpbitClient.get_ohlcv(coin, interval=interval, count=100)
                if df is not None and len(df) >= 30:
                    is_buy, confidence, info = leading_diagonal_strategy.analyze(df)
                    if is_buy:
                        stop_loss = info.get('support', current_price * 0.95) if info else current_price * 0.95
                        tp1 = info.get('resistance', current_price * 1.05) if info else current_price * 1.05
                        return {
                            'is_buy': True,
                            'confidence': confidence,
                            'entry_price': current_price,
                            'stop_loss': stop_loss,
                            'take_profit_1': tp1
                        }
            
            return result
            
        except Exception as e:
            logger.debug(f"[BuyPreview] _analyze_strategy_signal error: {e}")
            return {'is_buy': False}
