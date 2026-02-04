"""
Order Manager Service
Handles order execution, timeout management, and panic sell functionality
"""
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
import time
import pandas as pd

from models.database import SessionLocal, TradeLog, Position, User, UserSettings, CandleSnapshot, PositionHistory, get_setting, set_setting
from services.upbit_client import UpbitClient
from utils.logger import setup_logger
from utils.encryption import encryptor
from utils.timezone import now_kst

logger = setup_logger(__name__)

# Default virtual balance (10,000,000 KRW = 1천만원)
DEFAULT_VIRTUAL_BALANCE = 10_000_000

# Cached default user ID
_default_user_id: Optional[int] = None


def get_default_user_id() -> Optional[int]:
    """Get the default user ID (first user in the system)"""
    global _default_user_id
    if _default_user_id is not None:
        return _default_user_id
    
    try:
        db = SessionLocal()
        user = db.query(User).first()
        db.close()
        if user:
            _default_user_id = user.id
            return _default_user_id
    except Exception as e:
        logger.error(f"Error getting default user: {e}")
    return None


@dataclass
class OrderResult:
    """Result of an order execution"""
    success: bool
    order_id: Optional[str] = None
    executed_price: float = 0.0
    executed_quantity: float = 0.0
    message: str = ""


class OrderManager:
    """
    Order management with safety features
    
    Features:
    - Hard Cap (50% rule): No single order > 50% of available KRW
    - Confidence-based sizing: Order size = available * 0.5 * confidence
    - 5-minute timeout: Auto-cancel unfilled limit orders
    - Panic Sell: Market sell all positions immediately
    - Virtual Balance: Simulation without Upbit API
    - Trading Fee: 0.05% per trade (buy/sell)
    """
    
    HARD_CAP_RATIO = 0.5  # 50% max per order
    ORDER_TIMEOUT_SECONDS = 300  # 5 minutes
    TRADING_FEE_RATE = 0.0005  # 0.05% Upbit trading fee
    MIN_ORDER_AMOUNT = 10_000  # 최소 주문 금액 10,000 KRW (업비트 최소 5,000원이지만 여유있게 설정)
    
    def __init__(self, user_id: int = None):
        self._pending_orders: Dict[str, datetime] = {}  # order_id -> created_at
        self._is_panic_mode = False
        self._user_id = user_id
    
    def _get_user_id(self) -> Optional[int]:
        """Get user_id for DB operations"""
        if self._user_id:
            return self._user_id
        return get_default_user_id()
    
    def get_virtual_balance(self) -> float:
        """Get virtual KRW balance for simulation mode"""
        try:
            db = SessionLocal()
            balance_str = get_setting(db, "virtual_krw_balance")
            db.close()
            
            if balance_str:
                return float(balance_str)
            else:
                # Initialize virtual balance
                self.set_virtual_balance(DEFAULT_VIRTUAL_BALANCE)
                return DEFAULT_VIRTUAL_BALANCE
        except Exception as e:
            logger.error(f"Error getting virtual balance: {e}")
            return DEFAULT_VIRTUAL_BALANCE
    
    def set_virtual_balance(self, amount: float):
        """Set virtual KRW balance in both settings and user_settings tables"""
        try:
            db = SessionLocal()
            # Update legacy settings table
            set_setting(db, "virtual_krw_balance", str(amount))
            
            # Also update user_settings table for the current user
            user_id = self._get_user_id()
            if user_id:
                from models.database import UserSettings
                user_settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
                if user_settings:
                    user_settings.virtual_krw_balance = amount
                    db.commit()
            
            db.close()
        except Exception as e:
            logger.error(f"Error setting virtual balance: {e}")
    
    def reset_virtual_balance(self, amount: float = DEFAULT_VIRTUAL_BALANCE):
        """Reset virtual balance to initial amount"""
        self.set_virtual_balance(amount)
        logger.info(f"Virtual balance reset to {amount:,.0f} KRW")
    
    def get_balance(self, is_simulation: bool = True) -> float:
        """Get KRW balance based on mode"""
        if is_simulation:
            return self.get_virtual_balance()
        else:
            # Real mode - use user's API keys from UserSettings
            try:
                from models.database import UserSettings
                db = SessionLocal()
                user_id = get_default_user_id()
                user_settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
                
                if not user_settings or not user_settings.upbit_access_key or not user_settings.upbit_secret_key:
                    db.close()
                    logger.error("Real mode balance check failed: No API keys configured in UserSettings")
                    return 0.0
                
                # Decrypt API keys
                access_key = encryptor.decrypt(user_settings.upbit_access_key)
                secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                db.close()
                
                # Create authenticated client and get balance
                import pyupbit
                user_upbit = pyupbit.Upbit(access_key, secret_key)
                balance = user_upbit.get_balance("KRW")
                logger.info(f"Real mode KRW balance: {balance}")
                return float(balance) if balance else 0.0
            except Exception as e:
                logger.error(f"Error getting real balance: {e}")
                return 0.0
    
    def calculate_order_size(self, confidence: float, is_simulation: bool = True) -> float:
        """
        Calculate order size based on confidence score
        
        Args:
            confidence: Confidence score (0.2 ~ 1.0)
            is_simulation: Use virtual balance if True
            
        Returns:
            Order amount in KRW
        """
        krw_balance = self.get_balance(is_simulation)
        if krw_balance <= 0:
            return 0.0
        
        # Apply hard cap and confidence
        max_order = krw_balance * self.HARD_CAP_RATIO
        order_size = max_order * confidence
        
        # If calculated order is below minimum but balance allows minimum order
        # Try using minimum order amount instead
        if order_size < self.MIN_ORDER_AMOUNT:
            # Check if we can at least make a minimum order
            if krw_balance >= self.MIN_ORDER_AMOUNT:
                order_size = self.MIN_ORDER_AMOUNT
                logger.info(f"Low balance, using minimum order: {order_size:.0f}원")
            else:
                logger.warning(f"Insufficient balance: {krw_balance:.0f}원 < minimum {self.MIN_ORDER_AMOUNT}원")
                return 0.0
        
        return order_size
    
    def get_balance_for_user(self, is_simulation: bool, user_settings) -> float:
        """Get balance for a specific user"""
        if is_simulation:
            if user_settings:
                try:
                    # Force refresh from DB to get latest balance
                    db = SessionLocal()
                    us = db.query(UserSettings).filter(UserSettings.user_id == user_settings.user_id).first()
                    balance = float(us.virtual_krw_balance or 10000000)
                    db.close()
                    return balance
                except Exception as e:
                    logger.error(f"Failed to refresh balance: {e}")
                    return float(user_settings.virtual_krw_balance or 10000000)
            return self.get_virtual_balance()
        else:
            # Real mode - get from Upbit API using user's keys
            if user_settings and user_settings.upbit_access_key:
                try:
                    import pyupbit
                    access_key = encryptor.decrypt(user_settings.upbit_access_key)
                    secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                    user_upbit = pyupbit.Upbit(access_key, secret_key)
                    balance = user_upbit.get_balance("KRW")
                    return float(balance) if balance else 0.0
                except Exception as e:
                    logger.error(f"Failed to get real balance: {e}")
                    return 0.0
            return 0.0
    
    def calculate_order_size_for_user(self, confidence: float, is_simulation: bool, user_settings) -> float:
        """Calculate order size for a specific user"""
        krw_balance = self.get_balance_for_user(is_simulation, user_settings)
        if krw_balance <= 0:
            return 0.0
        
        # Apply hard cap and confidence
        max_order = krw_balance * self.HARD_CAP_RATIO
        order_size = max_order * confidence
        
        # If calculated order is below minimum but balance allows minimum order
        if order_size < self.MIN_ORDER_AMOUNT:
            if krw_balance >= self.MIN_ORDER_AMOUNT:
                order_size = self.MIN_ORDER_AMOUNT
            else:
                return 0.0
        
        return order_size
    
    def execute_buy(
        self,
        market: str,
        strategy: str,
        timeframe: str,
        confidence: float,
        reference_data: Dict[str, Any],
        is_simulation: bool = True,
        reason: str = None,
        user_id: int = None,
        user_settings = None,  # UserSettings object
        order_amount: float = None  # ★ PHASE 10: 지정 금액 (없으면 기존 로직 사용)
    ) -> OrderResult:
        """
        Execute a buy order with proper sizing
        
        Args:
            market: Market ticker (e.g., 'KRW-BTC')
            strategy: Strategy name ('squirrel' or 'morning')
            timeframe: Timeframe ('1D' or '4H')
            confidence: Confidence score (0.2 ~ 1.0)
            reference_data: Strategy-specific reference data
            is_simulation: Whether this is a simulation trade
            reason: Reason for the buy (e.g., 'entry_squirrel')
            user_id: User ID for multi-user support
            user_settings: UserSettings object for this user
            order_amount: Explicit order amount (if None, calculated by confidence)
            
        Returns:
            OrderResult with execution details
        """
        try:
            # ★ PHASE 10: 지정 금액이 있으면 사용, 없으면 기존 로직
            if order_amount is None:
                order_amount = self.calculate_order_size_for_user(confidence, is_simulation, user_settings)
            if order_amount <= 0:
                balance = self.get_balance_for_user(is_simulation, user_settings)
                return OrderResult(
                    success=False,
                    message=f"잔고 부족: {balance:.0f}원 (최소 {self.MIN_ORDER_AMOUNT}원 필요)"
                )
            
            # ========================================
            # 수동 보유 체크 (Real 모드에서만)
            # 업비트에 잔고가 있지만 Position에 없으면 수동 구매로 간주
            # ========================================
            if not is_simulation:
                try:
                    import pyupbit
                    
                    # Get user's API keys
                    if user_settings and user_settings.upbit_access_key and user_settings.upbit_secret_key:
                        access_key = encryptor.decrypt(user_settings.upbit_access_key)
                        secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                        user_upbit = pyupbit.Upbit(access_key, secret_key)
                        
                        # Check if user already holds this coin on Upbit
                        coin_ticker = market.replace('KRW-', '')
                        upbit_balance = user_upbit.get_balance(coin_ticker)
                        
                        # ★ 100원 이상만 보유로 판단 (dust 무시)
                        if upbit_balance and float(upbit_balance) > 0:
                            # Get current price to calculate value
                            coin_value = float(upbit_balance) * current_price
                            
                            if coin_value >= 100:  # 100원 이상만 체크
                                # Check if this coin exists in Position table for this user
                                db = SessionLocal()
                                existing_position = db.query(Position).filter(
                                    Position.coin == market,
                                    Position.user_id == user_id,
                                    Position.mode == "real"
                                ).first()
                                db.close()
                                
                                if not existing_position:
                                    # User manually holds this coin (not AI-traded)
                                    logger.info(f"[User {user_id}] Skipping {market}: 수동 보유분 감지 (₩{coin_value:.0f}, {upbit_balance} {coin_ticker})")
                                    return OrderResult(
                                        success=False,
                                        message=f"수동 보유 코인 - AI 매수 스킵 (₩{coin_value:.0f} 보유 중)"
                                    )
                            else:
                                logger.debug(f"[User {user_id}] {market}: dust 무시 (₩{coin_value:.0f} < 100원)")
                except Exception as e:
                    logger.warning(f"Manual holding check failed: {e}")
                    # Continue with buy if check fails
            
            current_price = UpbitClient.get_current_price([market]).get(market, 0)
            if current_price <= 0:
                return OrderResult(success=False, message="Failed to get current price")
            
            quantity = order_amount / current_price
            
            if is_simulation:
                # Simulation mode - deduct from virtual balance and log
                order_id = f"SIM-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                executed_price = current_price
                executed_quantity = quantity
                
                # Calculate trading fee (0.05%)
                trading_fee = order_amount * self.TRADING_FEE_RATE
                total_cost = order_amount + trading_fee
                
                # Deduct from user's virtual balance
                if user_settings:
                    # Update user's virtual balance in DB (Transactional update)
                    db = SessionLocal()
                    try:
                        us = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
                        if us:
                            current_db_balance = float(us.virtual_krw_balance or 10000000)
                            new_balance = current_db_balance - total_cost
                            us.virtual_krw_balance = new_balance
                            db.commit()
                            logger.info(f"[User {user_id}] Virtual balance: {current_db_balance:,.0f} -> {new_balance:,.0f} KRW")
                    except Exception as e:
                        logger.error(f"Failed to update balance: {e}")
                    finally:
                        db.close()
                else:
                    # Fallback to old method
                    current_balance = self.get_virtual_balance()
                    self.set_virtual_balance(current_balance - total_cost)
                    logger.info(f"Virtual balance: {current_balance:,.0f} -> {current_balance - total_cost:,.0f} KRW")
            else:
                # Real mode - execute market buy with user's API keys
                try:
                    # from models.database import UserSettings (Removed: Using global import)
                    
                    # Use provided user_settings, or fallback to fetching from DB
                    if not user_settings:
                        db = SessionLocal()
                        uid = user_id or get_default_user_id()
                        user_settings = db.query(UserSettings).filter(UserSettings.user_id == uid).first()
                        db.close()
                    
                    if not user_settings or not user_settings.upbit_access_key or not user_settings.upbit_secret_key:
                        logger.error(f"Real mode buy failed: No API keys configured for user {user_id}")
                        return OrderResult(success=False, message="API keys not configured for real trading")
                    
                    # Decrypt API keys
                    access_key = encryptor.decrypt(user_settings.upbit_access_key)
                    secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                    
                    # Create authenticated client
                    import pyupbit
                    user_upbit = pyupbit.Upbit(access_key, secret_key)
                    
                    # Execute buy order
                    result = user_upbit.buy_market_order(market, order_amount)
                    if not result or 'uuid' not in result:
                        logger.error(f"Real buy order failed: {result}")
                        return OrderResult(
                            success=False,
                            message=f"Order failed: {result}"
                        )
                    
                    order_id = result['uuid']
                    # Get actual execution details
                    time.sleep(1)  # Wait for order to process
                    order_detail = user_upbit.get_order(order_id)
                    executed_price = float(order_detail.get('trades', [{}])[0].get('price', current_price) if order_detail.get('trades') else current_price)
                    executed_quantity = float(order_detail.get('executed_volume', quantity))
                    
                    logger.info(f"Real buy executed: {market} @ {executed_price} x {executed_quantity}")
                    
                except Exception as e:
                    logger.error(f"Real mode buy error: {e}")
                    return OrderResult(success=False, message=f"Real buy error: {e}")
            
            # Generate buy reason if not provided
            if not reason:
                reason = f"entry_{strategy}"
            
            # Calculate stop_loss and take_profit from reference_data
            # ★ STRICT: reference_data에서 직접 SL/TP를 가져옴 (Jan 2026 Redesign)
            # SL/TP가 없으면 매수 거부 (fallback 없음)
            stop_loss = reference_data.get('stop_loss')
            take_profit = reference_data.get('take_profit')
            
            # STRICT VALIDATION: SL/TP가 없으면 매수 거부 (로그 남기고 실패 반환)
            if stop_loss is None or take_profit is None:
                missing = []
                if stop_loss is None:
                    missing.append("stop_loss")
                if take_profit is None:
                    missing.append("take_profit")
                error_msg = f"[{strategy}] 매수 거부: SL/TP 미설정 ({', '.join(missing)})"
                logger.error(f"[STRICT] {error_msg} - reference_data: {reference_data}")
                return OrderResult(
                    success=False,
                    message=error_msg
                )
            
            # ★ RR 조정: 손익비가 1.5 미만이면 TP 상향 조정 (TradeLog 저장 전에 수행)
            if stop_loss < executed_price:
                risk = executed_price - stop_loss
                reward = take_profit - executed_price
                if risk > 0 and reward / risk < 1.5:
                    original_tp = take_profit
                    take_profit = executed_price + (risk * 1.5)
                    logger.info(f"[{market}] RR 조정: TP {original_tp:.0f} → {take_profit:.0f} (RR 1.5 확보)")
            
            # Log the trade (조정된 SL/TP 저장)
            trade_log_id = self._log_trade(
                mode="simulation" if is_simulation else "real",
                strategy=strategy,
                timeframe=timeframe,
                coin=market,
                side="buy",
                price=executed_price,
                quantity=executed_quantity,
                confidence=confidence,
                reason=reason,
                order_id=order_id,
                user_id=user_id,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            
            # reference_data에도 조정된 값 반영 (Position 생성 시 사용)
            reference_data['stop_loss'] = stop_loss
            reference_data['take_profit'] = take_profit
            
            # Save candle snapshot for historical chart viewing
            if trade_log_id:
                self._save_candle_snapshot(
                    trade_log_id=trade_log_id,
                    exchange="upbit",
                    coin=market,
                    timeframe=timeframe
                )
            
            # Create position record
            position_id = self._create_position(
                coin=market,
                strategy=strategy,
                entry_price=executed_price,
                quantity=executed_quantity,
                reference_data=reference_data,
                confidence=confidence,
                mode="simulation" if is_simulation else "real",
                timeframe=timeframe,
                user_id=user_id  # 유저별 포지션 생성
            )
            
            # Save entry event for chart visualization
            if position_id:
                self._save_position_event(
                    position_id=position_id,
                    user_id=user_id,
                    exchange="upbit",
                    coin=market,
                    mode="simulation" if is_simulation else "real",
                    strategy=strategy,
                    timeframe=timeframe,
                    event_type="entry",
                    event_price=executed_price,
                    event_quantity=executed_quantity,
                    entry_price=executed_price,
                    stop_loss_price=stop_loss,
                    take_profit_price=take_profit,
                    save_snapshot=True
                )
            
            logger.info(f"Buy executed: {market} @ {executed_price} x {executed_quantity}")
            
            return OrderResult(
                success=True,
                order_id=order_id,
                executed_price=executed_price,
                executed_quantity=executed_quantity,
                message="Order executed successfully"
            )
            
        except Exception as e:
            logger.error(f"Error executing buy order: {e}")
            return OrderResult(success=False, message=str(e))
    
    def execute_sell(
        self,
        market: str,
        quantity: float,
        reason: str,
        is_simulation: bool = True,
        user_id: int = None,
        user_settings = None
    ) -> OrderResult:
        """
        Execute a sell order
        
        Args:
            market: Market ticker
            quantity: Quantity to sell
            reason: Sell reason ('take_profit', 'stop_loss', 'panic')
            is_simulation: Whether this is a simulation trade
            user_id: User ID for multi-user support
            user_settings: UserSettings object for this user
            
        Returns:
            OrderResult with execution details
        """
        try:
            current_price = UpbitClient.get_current_price([market]).get(market, 0)
            if current_price <= 0:
                return OrderResult(success=False, message="Failed to get current price")
            
            if is_simulation:
                order_id = f"SIM-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                executed_price = current_price
                executed_quantity = quantity
                
                # Calculate proceeds and trading fee (0.05%)
                gross_proceeds = executed_price * executed_quantity
                trading_fee = gross_proceeds * self.TRADING_FEE_RATE
                net_proceeds = gross_proceeds - trading_fee
                
                # Add net proceeds to user's virtual balance
                if user_settings and user_id:
                    current_balance = user_settings.virtual_krw_balance or 10000000
                    new_balance = current_balance + net_proceeds
                    # Update user's virtual balance in DB
                    db = SessionLocal()
                    us = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
                    if us:
                        us.virtual_krw_balance = new_balance
                        db.commit()
                    db.close()
                    logger.info(f"[User {user_id}] Sell: {gross_proceeds:,.0f} - fee {trading_fee:,.0f} = {net_proceeds:,.0f} KRW")
                    logger.info(f"[User {user_id}] Virtual balance: {current_balance:,.0f} -> {new_balance:,.0f} KRW")
                else:
                    current_balance = self.get_virtual_balance()
                    self.set_virtual_balance(current_balance + net_proceeds)
                    logger.info(f"Sell: {gross_proceeds:,.0f} - fee {trading_fee:,.0f} = {net_proceeds:,.0f} KRW")
                    logger.info(f"Virtual balance: {current_balance:,.0f} -> {current_balance + net_proceeds:,.0f} KRW")
            else:
                # Real mode sell with user's API keys
                try:
                    # from models.database import UserSettings (Removed: Using global import)
                    
                    # Use provided user_settings, or fallback to fetching from DB
                    if not user_settings:
                        db = SessionLocal()
                        uid = user_id or get_default_user_id()
                        user_settings = db.query(UserSettings).filter(UserSettings.user_id == uid).first()
                        db.close()
                    
                    if not user_settings or not user_settings.upbit_access_key or not user_settings.upbit_secret_key:
                        logger.error(f"Real mode sell failed: No API keys configured for user {user_id}")
                        return OrderResult(success=False, message="API keys not configured for real trading")
                    
                    # Decrypt API keys
                    access_key = encryptor.decrypt(user_settings.upbit_access_key)
                    secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                    
                    # Create authenticated client
                    import pyupbit
                    user_upbit = pyupbit.Upbit(access_key, secret_key)
                    
                    # 실제 보유량 조회 (전량 매도를 위해)
                    coin_ticker = market.replace('KRW-', '')
                    actual_balance = user_upbit.get_balance(coin_ticker)
                    if actual_balance and actual_balance > 0:
                        # 실제 보유량으로 매도 (1원 잔고 문제 해결)
                        sell_quantity = actual_balance
                        logger.info(f"Selling actual balance: {sell_quantity} {coin_ticker} (requested: {quantity})")
                    else:
                        sell_quantity = quantity
                    
                    result = user_upbit.sell_market_order(market, sell_quantity)
                    if not result or 'uuid' not in result:
                        logger.error(f"Real sell order failed: {result}")
                        return OrderResult(
                            success=False,
                            message=f"Order failed: {result}"
                        )
                    
                    order_id = result['uuid']
                    time.sleep(1)
                    order_detail = user_upbit.get_order(order_id)
                    executed_price = float(order_detail.get('trades', [{}])[0].get('price', current_price) if order_detail.get('trades') else current_price)
                    executed_quantity = float(order_detail.get('executed_volume', quantity))
                    
                    logger.info(f"Real sell executed: {market} @ {executed_price} x {executed_quantity}")
                    
                except Exception as e:
                    logger.error(f"Real mode sell error: {e}")
                    return OrderResult(success=False, message=f"Real sell error: {e}")
            
            # Get position for PnL calculation - must filter by user_id and mode!
            db = SessionLocal()
            mode_str = "simulation" if is_simulation else "real"
            position = db.query(Position).filter(
                Position.coin == market,
                Position.user_id == user_id,
                Position.mode == mode_str
            ).first()
            
            pnl = None
            pnl_percent = None
            strategy = "unknown"
            timeframe = "1D"
            
            if position:
                strategy = position.strategy
                timeframe = "1D" if strategy == "squirrel" else "4H"
                entry_price = position.entry_price
                pnl = (executed_price - entry_price) * executed_quantity
                pnl_percent = ((executed_price - entry_price) / entry_price) * 100
                
                # ★ Phase 6: 100% 청산만 지원 (분할청산 제거)
                # Remove position entirely
                db.delete(position)
                logger.info(f"Position {market} fully closed")
                
                db.commit()
            
            db.close()
            
            # Log the trade
            self._log_trade(
                mode="simulation" if is_simulation else "real",
                strategy=strategy,
                timeframe=timeframe,
                coin=market,
                side="sell",
                price=executed_price,
                quantity=executed_quantity,
                pnl=pnl,
                pnl_percent=pnl_percent,
                reason=reason,
                order_id=order_id,
                user_id=user_id  # Pass user_id for proper logging
            )
            
            # Save exit event for chart visualization
            if position:
                # ★ Phase 9: event_type 단순화 (invalidation 제거, SL/TP만 사용)
                event_type_map = {
                    'take_profit': 'take_profit',
                    'stop_loss': 'stop_loss',
                    'trailing_stop': 'trailing_stop',
                    'panic': 'panic_sell'
                }
                event_type = event_type_map.get(reason, 'exit')
                
                self._save_position_event(
                    position_id=position.id,
                    user_id=user_id,
                    exchange="upbit",
                    coin=market,
                    mode="simulation" if is_simulation else "real",
                    strategy=strategy,
                    timeframe=timeframe,
                    event_type=event_type,
                    event_price=executed_price,
                    event_quantity=executed_quantity,
                    event_reason=reason,
                    entry_price=entry_price,
                    stop_loss_price=position.reference_candle_low,
                    take_profit_price=position.reference_candle_high,
                    pnl_percent=pnl_percent,
                    save_snapshot=True
                )
            
            logger.info(f"Sell executed ({reason}): {market} @ {executed_price}, PnL: {pnl_percent:.2f}%")
            
            return OrderResult(
                success=True,
                order_id=order_id,
                executed_price=executed_price,
                executed_quantity=executed_quantity,
                message=f"Sold for {reason}"
            )
            
        except Exception as e:
            logger.error(f"Error executing sell order: {e}")
            return OrderResult(success=False, message=str(e))
    
    def panic_sell(self, is_simulation: bool = True) -> List[Dict]:
        """
        Emergency sell all positions at market price
        
        Returns:
            List of sold position details
        """
        self._is_panic_mode = True
        results = []
        
        try:
            db = SessionLocal()
            positions = db.query(Position).all()
            
            for position in positions:
                result = self.execute_sell(
                    market=position.coin,
                    quantity=position.quantity,
                    reason="panic",
                    is_simulation=is_simulation
                )
                
                results.append({
                    "market": position.coin,
                    "quantity": position.quantity,
                    "success": result.success,
                    "executed_price": result.executed_price,
                    "message": result.message
                })
            
            db.close()
            logger.info(f"Panic sell completed: {len(results)} positions sold")
            
        except Exception as e:
            logger.error(f"Error during panic sell: {e}")
        
        finally:
            self._is_panic_mode = False
        
        return results
    
    def cancel_stale_orders(self) -> int:
        """
        Cancel orders older than 5 minutes
        
        Returns:
            Number of orders cancelled
        """
        cancelled = 0
        now = datetime.now()
        stale_orders = []
        
        for order_id, created_at in self._pending_orders.items():
            if (now - created_at).total_seconds() > self.ORDER_TIMEOUT_SECONDS:
                stale_orders.append(order_id)
        
        if not stale_orders:
            return 0
        
        # Get user API keys for cancellation
        try:
            from models.database import UserSettings
            db = SessionLocal()
            user_id = get_default_user_id()
            user_settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
            
            if not user_settings or not user_settings.upbit_access_key or not user_settings.upbit_secret_key:
                db.close()
                logger.error("Cancel orders failed: No API keys configured in UserSettings")
                return 0
            
            access_key = encryptor.decrypt(user_settings.upbit_access_key)
            secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
            db.close()
            
            import pyupbit
            user_upbit = pyupbit.Upbit(access_key, secret_key)
            
            for order_id in stale_orders:
                result = user_upbit.cancel_order(order_id)
                if result:
                    cancelled += 1
                    logger.info(f"Cancelled stale order: {order_id}")
                del self._pending_orders[order_id]
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
        
        return cancelled
    
    def _log_trade(
        self,
        mode: str,
        strategy: str,
        timeframe: str,
        coin: str,
        side: str,
        price: float,
        quantity: float,
        confidence: float = None,
        pnl: float = None,
        pnl_percent: float = None,
        reason: str = None,
        order_id: str = None,
        user_id: int = None,
        stop_loss: float = None,
        take_profit: float = None
    ) -> Optional[int]:
        """Log trade to database and return trade_log_id for snapshot linking"""
        try:
            db = SessionLocal()
            # Use provided user_id ONLY - do not fallback to default for trades
            # This ensures each user's trades are logged to their own account
            uid = user_id
            if uid is None:
                logger.warning(f"_log_trade called without user_id for {coin} {side}! Using fallback.")
                uid = self._get_user_id()
            
            logger.info(f"[TradeLog] user_id={uid}, coin={coin}, side={side}")
            trade_log = TradeLog(
                user_id=uid,
                mode=mode,
                strategy=strategy,
                timeframe=timeframe,
                coin=coin,
                side=side,
                price=price,
                quantity=quantity,
                total_amount=price * quantity,
                pnl=pnl,
                pnl_percent=pnl_percent,
                confidence=confidence,
                reason=reason,
                order_id=order_id,
                stop_loss=stop_loss,
                take_profit=take_profit,
                created_at=now_kst()
            )
            db.add(trade_log)
            db.commit()
            trade_log_id = trade_log.id
            db.close()
            return trade_log_id
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
            return None
    
    def _save_candle_snapshot(
        self,
        trade_log_id: int,
        exchange: str,
        coin: str,
        timeframe: str
    ):
        """Save candlestick snapshot for historical chart viewing"""
        try:
            import json
            import ta
            
            # Get candle data from exchange
            if exchange == "upbit":
                if timeframe == "1D":
                    interval = "day"
                elif timeframe == "4H":
                    interval = "minute240"
                else:
                    interval = "minute240"
                
                df = UpbitClient.get_ohlcv(coin, interval=interval, count=60)
            else:
                # Bybit - handled separately
                return
            
            if df is None or len(df) < 10:
                logger.warning(f"Insufficient candle data for snapshot: {coin}")
                return
            
            # Convert candles to JSON format
            candles = []
            for idx, row in df.iterrows():
                timestamp = idx.timestamp() if hasattr(idx, 'timestamp') else 0
                candles.append({
                    'time': int(timestamp),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume'])
                })
            
            # Calculate indicators
            indicators = {}
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            indicators['rsi'] = [float(v) if not pd.isna(v) else 50 for v in df['rsi'].tolist()]
            
            df['ma5'] = df['close'].rolling(window=5).mean()
            indicators['ma5'] = [float(v) if not pd.isna(v) else None for v in df['ma5'].tolist()]
            
            df['ma20'] = df['close'].rolling(window=20).mean()
            indicators['ma20'] = [float(v) if not pd.isna(v) else None for v in df['ma20'].tolist()]
            
            # Save to database
            db = SessionLocal()
            snapshot = CandleSnapshot(
                trade_log_id=trade_log_id,
                exchange=exchange,
                coin=coin,
                timeframe=timeframe,
                candles_json=json.dumps(candles),
                indicators_json=json.dumps(indicators),
                created_at=now_kst()
            )
            db.add(snapshot)
            db.commit()
            db.close()
            
            logger.info(f"Candle snapshot saved for trade {trade_log_id}: {coin} ({timeframe})")
            
        except Exception as e:
            logger.error(f"Failed to save candle snapshot: {e}")
    
    def _save_position_event(
        self,
        position_id: int,
        user_id: int,
        exchange: str,
        coin: str,
        mode: str,
        strategy: str,
        timeframe: str,
        event_type: str,
        event_price: float,
        event_quantity: float = None,
        event_reason: str = None,
        entry_price: float = None,
        stop_loss_price: float = None,
        take_profit_price: float = None,
        pnl_percent: float = None,
        save_snapshot: bool = True
    ):
        """
        Save position event for historical chart visualization.
        
        event_type can be:
        - 'entry': Position opened
        - 'stop_loss': Stopped out
        - 'take_profit': Take profit triggered
        - 'trailing_stop': Trailing stop triggered
        - 'panic_sell': Emergency sell
        """
        try:
            import json
            import ta
            
            candles_json = None
            indicators_json = None
            
            # Save candle snapshot for this event
            if save_snapshot and exchange == "upbit":
                try:
                    if timeframe == "1D":
                        interval = "day"
                    elif timeframe == "4H":
                        interval = "minute240"
                    else:
                        interval = "minute240"
                    
                    df = UpbitClient.get_ohlcv(coin, interval=interval, count=60)
                    
                    if df is not None and len(df) >= 10:
                        # Convert candles to JSON
                        candles = []
                        for idx, row in df.iterrows():
                            timestamp = idx.timestamp() if hasattr(idx, 'timestamp') else 0
                            candles.append({
                                'time': int(timestamp),
                                'open': float(row['open']),
                                'high': float(row['high']),
                                'low': float(row['low']),
                                'close': float(row['close']),
                                'volume': float(row['volume'])
                            })
                        candles_json = json.dumps(candles)
                        
                        # Calculate indicators
                        indicators = {}
                        df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
                        indicators['rsi'] = [float(v) if not pd.isna(v) else 50 for v in df['rsi'].tolist()]
                        df['ma5'] = df['close'].rolling(window=5).mean()
                        indicators['ma5'] = [float(v) if not pd.isna(v) else None for v in df['ma5'].tolist()]
                        df['ma20'] = df['close'].rolling(window=20).mean()
                        indicators['ma20'] = [float(v) if not pd.isna(v) else None for v in df['ma20'].tolist()]
                        indicators_json = json.dumps(indicators)
                except Exception as snap_err:
                    logger.warning(f"Failed to save snapshot for event: {snap_err}")
            
            # Save to database
            db = SessionLocal()
            history = PositionHistory(
                position_id=position_id,
                user_id=user_id,
                exchange=exchange,
                coin=coin,
                mode=mode,
                strategy=strategy,
                timeframe=timeframe,
                event_type=event_type,
                event_price=event_price,
                event_quantity=event_quantity,
                event_reason=event_reason,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                candles_json=candles_json,
                indicators_json=indicators_json,
                pnl_percent=pnl_percent,
                created_at=now_kst()
            )
            db.add(history)
            db.commit()
            history_id = history.id
            db.close()
            
            logger.info(f"Position event saved: {event_type} for position {position_id}, price={event_price}")
            return history_id
            
        except Exception as e:
            logger.error(f"Failed to save position event: {e}")
            return None
    
    def _create_position(
        self,
        coin: str,
        strategy: str,
        entry_price: float,
        quantity: float,
        reference_data: Dict[str, Any],
        confidence: float,
        mode: str = "simulation",
        timeframe: str = "1D",
        user_id: int = None
    ) -> Optional[int]:
        """Create or update position record. Returns position_id."""
        try:
            db = SessionLocal()
            # Use provided user_id or fallback to default
            uid = user_id or self._get_user_id()
            
            # Check if position exists for this user AND mode
            existing = db.query(Position).filter(
                Position.coin == coin,
                Position.user_id == uid,
                Position.mode == mode  # 모드도 확인!
            ).first()
            
            position_id = None
            
            if existing:
                # Average up/down
                total_cost = (existing.entry_price * existing.quantity) + (entry_price * quantity)
                total_quantity = existing.quantity + quantity
                existing.entry_price = total_cost / total_quantity
                existing.quantity = total_quantity
                position_id = existing.id
                logger.info(f"Position updated: {coin} ({mode})")
            else:
                # ★ 명시적 SL/TP가 있으면 우선 사용 (ATR 버퍼 포함)
                ref_low = reference_data.get('stop_loss')
                ref_high = reference_data.get('take_profit')
                
                # 명시적 SL/TP가 없으면 전략별 매핑 사용 (레거시 호환)
                if ref_low is None:
                    if strategy == "divergence":
                        ref_low = reference_data.get('divergence_low')
                    elif strategy == "leading_diagonal":
                        ref_low = reference_data.get('support')
                    else:
                        ref_low = reference_data.get('pattern_low')
                
                if ref_high is None:
                    if strategy == "harmonic":
                        ref_high = reference_data.get('A_point')
                    elif strategy == "leading_diagonal":
                        ref_high = reference_data.get('resistance')
                    elif strategy == "divergence":
                        ref_high = reference_data.get('divergence_high')
                    else:
                        ref_high = reference_data.get('pattern_high')
                
                # ★ RR 조정은 execute_buy에서 이미 완료됨 (중복 제거)
                
                # ★ 손절가 유효성 검사: 진입가보다 높거나 None이면 진입가의 -3%로 대체
                if ref_low is None or ref_low >= entry_price:
                    logger.warning(f"Invalid stop_loss {ref_low} >= entry {entry_price}, using -3%")
                    ref_low = entry_price * 0.97
                
                # 2차 익절 목표 (Harmonic, LDB 등)
                ref_high_2 = None
                if strategy == "harmonic":
                    ref_high_2 = reference_data.get('C_point')  # 피보나치 0.618
                elif strategy == "leading_diagonal_breakdown":
                    ref_high_2 = reference_data.get('pattern_origin')  # 패턴 시작점
                
                position = Position(
                    user_id=uid,
                    coin=coin,
                    mode=mode,
                    strategy=strategy,
                    timeframe=timeframe,  # 매수 시 사용된 타임프레임 저장
                    entry_price=entry_price,
                    quantity=quantity,
                    stop_loss=ref_low,  # ★ Phase 5: 명시적 SL 저장
                    take_profit=ref_high,  # ★ Phase 5: 명시적 TP 저장
                    take_profit_2=ref_high_2,  # ★ Phase 5: 2차 TP 저장
                    reference_candle_open=reference_data.get('reference_candle_open'),
                    reference_candle_high=ref_high,
                    reference_candle_low=ref_low,
                    confidence=confidence,
                    partial_exit_stage=0,  # Initialize partial exit stage
                    created_at=now_kst()
                )
                db.add(position)
                db.commit()
                position_id = position.id
                logger.info(f"Position created: {coin} ({mode}) [{timeframe}] ref_low={ref_low}")
            
            db.commit()
            db.close()
            return position_id
        except Exception as e:
            logger.error(f"Failed to create position: {e}")
            return None
    
    def get_open_positions(self, mode: str = None, user_id: int = None) -> List[Dict]:
        """Get open positions, optionally filtered by mode and user_id"""
        try:
            db = SessionLocal()
            query = db.query(Position)
            if mode:
                query = query.filter(Position.mode == mode)
            if user_id:
                query = query.filter(Position.user_id == user_id)
            positions = query.all()
            result = [p.to_dict() for p in positions]
            db.close()
            return result
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []


# Global instance
order_manager = OrderManager()
