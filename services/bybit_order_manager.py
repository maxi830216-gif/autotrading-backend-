"""
Bybit Futures Order Manager
Handles order execution, position tracking, and P&L calculation for Bybit futures
"""
from typing import Optional, Dict, Tuple, Any, List
from datetime import datetime

from sqlalchemy.orm import Session
from models.database import SessionLocal, Position, TradeLog, UserSettings
from services.bybit_client import bybit_client, BybitClient
from services.telegram_service import telegram_service
from utils.timezone import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Constants
LEVERAGE = 5  # Fixed 5x leverage
MARGIN_TYPE = "isolated"
MIN_ORDER_USDT = 5  # Minimum order size
INVESTMENT_RATIO = 0.30  # 30% per position


class BybitOrderManager:
    """
    Manages Bybit futures orders and positions
    - Long only (no shorts)
    - Fixed 5x leverage
    - Isolated margin
    """
    
    def __init__(self):
        self.leverage = LEVERAGE
        self.margin_type = MARGIN_TYPE
    
    def get_available_balance(self, mode: str, user_id: int) -> float:
        """
        Get available USDT balance
        
        Args:
            mode: 'simulation' or 'real'
            user_id: User ID
        
        Returns:
            Available USDT balance
        """
        db = SessionLocal()
        try:
            if mode == "simulation":
                settings = db.query(UserSettings).filter(
                    UserSettings.user_id == user_id
                ).first()
                return settings.bybit_virtual_usdt_balance if settings else 10000
            else:
                # Real mode: Get from Bybit API
                settings = db.query(UserSettings).filter(
                    UserSettings.user_id == user_id
                ).first()
                
                if settings and settings.bybit_api_key and settings.bybit_api_secret:
                    from utils.encryption import encryptor
                    api_key = encryptor.decrypt(settings.bybit_api_key)
                    api_secret = encryptor.decrypt(settings.bybit_api_secret)
                    
                    bybit_client.set_credentials(api_key, api_secret)
                    balance = bybit_client.get_wallet_balance()
                    return balance.get('available', 0)
                return 0
        finally:
            db.close()
    
    def calculate_position_size(
        self,
        balance: float,
        current_price: float,
        leverage: int = LEVERAGE
    ) -> Tuple[float, float]:
        """
        Calculate position size based on 30% investment ratio
        
        Args:
            balance: Available USDT balance
            current_price: Current price of the asset
            leverage: Leverage to use
        
        Returns:
            (quantity, margin_used)
        """
        # 30% of balance
        margin = balance * INVESTMENT_RATIO
        
        if margin < MIN_ORDER_USDT:
            return 0, 0
        
        # Position value = margin * leverage
        position_value = margin * leverage
        
        # Quantity = position_value / price
        quantity = position_value / current_price
        
        return quantity, margin
    
    def calculate_liquidation_price(
        self,
        entry_price: float,
        leverage: int = LEVERAGE,
        is_long: bool = True
    ) -> float:
        """
        Calculate liquidation price for isolated margin
        
        For long positions:
        Liq Price = Entry Price * (1 - 1/leverage + maintenance_margin_rate)
        Simplified: Entry Price * (1 - 1/leverage)
        """
        if is_long:
            # Simplified calculation (actual has maintenance margin)
            return entry_price * (1 - 1/leverage * 0.95)  # ~5% buffer
        else:
            return entry_price * (1 + 1/leverage * 0.95)
    
    def open_long(
        self,
        symbol: str,
        mode: str,
        user_id: int,
        strategy: str,
        timeframe: str,
        confidence: float,
        reference_candle: Dict = None
    ) -> Dict[str, Any]:
        """
        Open a long position
        
        Args:
            symbol: Trading pair e.g., 'BTCUSDT'
            mode: 'simulation' or 'real'
            user_id: User ID
            strategy: Strategy name
            timeframe: '1D' or '4H'
            confidence: Signal confidence
            reference_candle: Dict with open, high, low for exit logic
        
        Returns:
            Dict with success status and details
        """
        db = SessionLocal()
        try:
            # Check if already have a position in this symbol
            existing = db.query(Position).filter(
                Position.user_id == user_id,
                Position.exchange == "bybit",
                Position.coin == symbol,
                Position.mode == mode
            ).first()
            
            if existing:
                return {"success": False, "reason": "이미 포지션 보유 중"}
            
            # Get current price
            prices = BybitClient.get_current_price([symbol])
            current_price = prices.get(symbol, 0)
            
            if current_price <= 0:
                return {"success": False, "reason": "가격 조회 실패"}
            
            # Get available balance
            balance = self.get_available_balance(mode, user_id)
            
            # Calculate position size
            quantity, margin_used = self.calculate_position_size(balance, current_price)
            
            if quantity <= 0:
                return {"success": False, "reason": f"잔고 부족 (최소 {MIN_ORDER_USDT} USDT 필요)"}
            
            # Calculate position value and liquidation price
            position_value = margin_used * self.leverage
            liq_price = self.calculate_liquidation_price(current_price)
            
            order_id = None
            
            if mode == "real":
                # Execute real order
                result = bybit_client.place_order(
                    symbol=symbol,
                    side="Buy",
                    qty=quantity,
                    leverage=self.leverage
                )
                
                if not result['success']:
                    return {"success": False, "reason": result.get('error', '주문 실패')}
                
                order_id = result.get('order_id')
            
            # Create position record
            position = Position(
                user_id=user_id,
                exchange="bybit",
                coin=symbol,
                mode=mode,
                strategy=strategy,
                timeframe=timeframe,
                entry_price=current_price,
                quantity=quantity,
                confidence=confidence,
                leverage=self.leverage,
                margin_type=self.margin_type,
                liquidation_price=liq_price,
                reference_candle_open=reference_candle.get('open') if reference_candle else None,
                reference_candle_high=reference_candle.get('high') if reference_candle else None,
                reference_candle_low=reference_candle.get('low') if reference_candle else None,
            )
            db.add(position)
            
            # Create trade log
            trade_log = TradeLog(
                user_id=user_id,
                exchange="bybit",
                mode=mode,
                strategy=strategy,
                timeframe=timeframe,
                coin=symbol,
                side="buy",
                price=current_price,
                quantity=quantity,
                total_amount=margin_used,
                confidence=confidence,
                reason="롱 진입",
                order_id=order_id,
                leverage=self.leverage
            )
            db.add(trade_log)
            
            # Update balance (simulation)
            if mode == "simulation":
                settings = db.query(UserSettings).filter(
                    UserSettings.user_id == user_id
                ).first()
                if settings:
                    settings.bybit_virtual_usdt_balance -= margin_used
            
            db.commit()
            
            # Send telegram notification
            coin_name = symbol.replace('USDT', '')
            mode_label = "모의" if mode == "simulation" else "실전"
            message = f"""🟢 [Bybit {mode_label}] 롱 진입
━━━━━━━━━━━━━━━
💰 {symbol}
📈 전략: {strategy} ({timeframe})
⚡ 레버리지: {self.leverage}x
💵 포지션: {position_value:,.2f} USDT
📊 진입가: {current_price:,.2f} USDT
🎯 투자금: {margin_used:,.2f} USDT (30%)
📈 신뢰도: {int(confidence * 100)}%
⚠️ 청산가: {liq_price:,.2f} USDT (-{int((1 - liq_price/current_price) * 100)}%)
━━━━━━━━━━━━━━━"""
            telegram_service.send_message_to_user(user_id, message)
            
            return {
                "success": True,
                "symbol": symbol,
                "entry_price": current_price,
                "quantity": quantity,
                "margin": margin_used,
                "leverage": self.leverage,
                "liquidation_price": liq_price
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error opening long: {e}")
            return {"success": False, "reason": str(e)}
        finally:
            db.close()
    
    def close_long(
        self,
        position_id: int,
        reason: str = "수동 청산"
        # ★ Phase 6: partial_ratio 제거 (100% 청산만)
    ) -> Dict[str, Any]:
        """
        Close a long position (full close only)
        
        Args:
            position_id: Position ID
            reason: Close reason
        
        Returns:
            Dict with success status and P&L
        """
        db = SessionLocal()
        try:
            position = db.query(Position).filter(
                Position.id == position_id,
                Position.exchange == "bybit"
            ).first()
            
            if not position:
                return {"success": False, "reason": "포지션 없음"}
            
            # Get current price
            prices = BybitClient.get_current_price([position.coin])
            current_price = prices.get(position.coin, 0)
            
            if current_price <= 0:
                return {"success": False, "reason": "가격 조회 실패"}
            
            # ★ Phase 6: 100% 청산만 지원
            close_qty = position.quantity
            
            # Calculate P&L based on position direction
            entry_value = position.entry_price * close_qty
            exit_value = current_price * close_qty
            
            # ★ 숏/롱에 따라 PnL 계산 방식 구분
            direction = getattr(position, 'direction', 'long') or 'long'
            if direction == 'short':
                # 숏: 가격 하락 시 수익 (진입가 - 청산가)
                pnl = (entry_value - exit_value)
                pnl_percent = ((position.entry_price - current_price) / position.entry_price) * 100 * self.leverage
            else:
                # 롱: 가격 상승 시 수익 (청산가 - 진입가)
                pnl = (exit_value - entry_value)
                pnl_percent = ((current_price - position.entry_price) / position.entry_price) * 100 * self.leverage
            
            # Calculate margin return
            margin_per_unit = position.entry_price / self.leverage
            margin_return = margin_per_unit * close_qty + pnl
            
            order_id = None
            
            if position.mode == "real":
                # 숏 청산은 Buy, 롱 청산은 Sell
                close_side = "Buy" if direction == 'short' else "Sell"
                result = bybit_client.place_order(
                    symbol=position.coin,
                    side=close_side,
                    qty=close_qty,
                    leverage=self.leverage
                )
                
                if not result['success']:
                    return {"success": False, "reason": result.get('error', '청산 실패')}
                
                order_id = result.get('order_id')
            
            # ★ 구분: short_close 또는 long_close
            trade_side = "short_close" if direction == 'short' else "long_close"
            
            # Create trade log
            trade_log = TradeLog(
                user_id=position.user_id,
                exchange="bybit",
                mode=position.mode,
                strategy=position.strategy,
                timeframe=position.timeframe,
                coin=position.coin,
                side=trade_side,  # ★ 수정: sell -> short_close/long_close
                price=current_price,
                quantity=close_qty,
                total_amount=exit_value,
                pnl=pnl,
                pnl_percent=pnl_percent,
                reason=reason,
                order_id=order_id,
                leverage=self.leverage
            )
            db.add(trade_log)
            
            # Update balance (simulation)
            if position.mode == "simulation":
                settings = db.query(UserSettings).filter(
                    UserSettings.user_id == position.user_id
                ).first()
                if settings:
                    settings.bybit_virtual_usdt_balance += margin_return
            
            # ★ Phase 6: 100% 청산만 - 포지션 삭제
            db.delete(position)
            
            db.commit()
            
            # Send telegram notification
            mode_label = "모의" if position.mode == "simulation" else "실전"
            position_type = "숏" if direction == 'short' else "롱"
            pnl_emoji = "💚" if pnl >= 0 else "💔"
            pnl_sign = "+" if pnl >= 0 else ""
            
            message = f"""🔴 [Bybit {mode_label}] {position_type} 청산
━━━━━━━━━━━━━━━
💰 {position.coin}
📈 전략: {position.strategy} ({position.timeframe})
📊 진입가: {position.entry_price:,.2f} USDT
📊 청산가: {current_price:,.2f} USDT
{pnl_emoji} 수익: {pnl_sign}{pnl:,.2f} USDT
📈 수익률: {pnl_sign}{pnl_percent:.1f}% (레버리지 적용)
📝 사유: {reason}
━━━━━━━━━━━━━━━"""
            telegram_service.send_message_to_user(position.user_id, message)
            
            return {
                "success": True,
                "symbol": position.coin,
                "exit_price": current_price,
                "quantity": close_qty,
                "pnl": pnl,
                "pnl_percent": pnl_percent
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error closing long: {e}")
            return {"success": False, "reason": str(e)}
        finally:
            db.close()
    
    def get_open_positions(
        self,
        mode: str,
        user_id: int
    ) -> List[Position]:
        """Get all open Bybit positions for a user"""
        db = SessionLocal()
        try:
            positions = db.query(Position).filter(
                Position.user_id == user_id,
                Position.exchange == "bybit",
                Position.mode == mode
            ).all()
            return positions
        finally:
            db.close()
    
    def process_funding_fee(
        self,
        position_id: int,
        funding_rate: float
    ) -> Dict[str, Any]:
        """
        Process funding fee for a position
        
        Args:
            position_id: Position ID
            funding_rate: Funding rate as decimal (e.g., 0.0001 = 0.01%)
        
        Returns:
            Dict with funding fee details
        """
        db = SessionLocal()
        try:
            position = db.query(Position).filter(
                Position.id == position_id,
                Position.exchange == "bybit"
            ).first()
            
            if not position:
                return {"success": False, "reason": "포지션 없음"}
            
            # Calculate funding fee
            # For long: positive rate = pay, negative rate = receive
            prices = BybitClient.get_current_price([position.coin])
            current_price = prices.get(position.coin, position.entry_price)
            
            position_value = current_price * position.quantity
            funding_fee = position_value * funding_rate  # Positive = pay
            
            # Update balance (simulation)
            if position.mode == "simulation":
                settings = db.query(UserSettings).filter(
                    UserSettings.user_id == position.user_id
                ).first()
                if settings:
                    settings.bybit_virtual_usdt_balance -= funding_fee
            
            # Create funding fee log
            trade_log = TradeLog(
                user_id=position.user_id,
                exchange="bybit",
                mode=position.mode,
                strategy="funding",
                timeframe="8H",
                coin=position.coin,
                side="funding",
                price=current_price,
                quantity=position.quantity,
                total_amount=position_value,
                funding_fee=funding_fee,
                reason=f"펀딩비 ({funding_rate * 100:.4f}%)"
            )
            db.add(trade_log)
            db.commit()
            
            return {
                "success": True,
                "symbol": position.coin,
                "position_value": position_value,
                "funding_rate": funding_rate,
                "funding_fee": funding_fee
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error processing funding fee: {e}")
            return {"success": False, "reason": str(e)}
        finally:
            db.close()


# Global instance
bybit_order_manager = BybitOrderManager()
