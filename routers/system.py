"""
System Control API Router
Bot status, start/stop, mode switching, panic sell
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from models.database import get_db, User, Position
from models.schemas import BotStatus, PanicSellResponse
from services.order_manager import OrderManager
from routers.auth import get_current_user
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter()


@router.get("/status")
async def get_bot_status(
    request: Request,
    mode: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current bot status for the authenticated user"""
    try:
        from models.database import UserSettings
        from datetime import datetime
        from services.whitelist_service import whitelist_service
        
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        # Get counts for status
        whitelist_count = len(whitelist_service.get_whitelist_markets())
        
        if mode:
            active_positions = db.query(Position).filter(
                Position.user_id == current_user.id,
                Position.mode == mode
            ).count()
        else:
            active_positions = db.query(Position).filter(
                Position.user_id == current_user.id
            ).count()
        
        if mode:
            # Return status for specific mode
            if mode == "simulation":
                is_running = user_settings.bot_simulation_running if user_settings else False
            else:
                is_running = user_settings.bot_real_running if user_settings else False
            
            return {
                "mode": mode,
                "is_running": is_running,
                "whitelist_count": whitelist_count,
                "active_positions": active_positions,
                "updated_at": datetime.now().isoformat()
            }
        else:
            # Return status for all modes
            return {
                "simulation": {
                    "is_running": user_settings.bot_simulation_running if user_settings else False
                },
                "real": {
                    "is_running": user_settings.bot_real_running if user_settings else False
                },
                "whitelist_count": whitelist_count,
                "active_positions": active_positions,
                "updated_at": datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Error getting bot status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start")
async def start_bot(
    request: Request,
    mode: str = Query("simulation"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start the trading bot for the authenticated user"""
    try:
        if mode not in ["simulation", "real"]:
            raise HTTPException(status_code=400, detail="Invalid mode. Use 'simulation' or 'real'")
        
        from models.database import UserSettings
        
        # Update this user's bot state in DB
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if not user_settings:
            # Create settings if not exists
            user_settings = UserSettings(user_id=current_user.id)
            db.add(user_settings)
        
        if mode == "simulation":
            user_settings.bot_simulation_running = True
        else:
            user_settings.bot_real_running = True
        
        db.commit()
        
        # Log start message for this user
        scheduler = request.app.state.scheduler
        scheduler._log_system("INFO", f"🚀 [{mode}] 트레이딩 봇 시작", mode=mode, user_id=current_user.id)
        
        return {"success": True, "message": f"{mode} bot started", "mode": mode}
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_bot(
    request: Request,
    mode: str = Query("simulation"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Stop the trading bot for the authenticated user"""
    try:
        if mode not in ["simulation", "real"]:
            raise HTTPException(status_code=400, detail="Invalid mode. Use 'simulation' or 'real'")
        
        from models.database import UserSettings
        
        # Update this user's bot state in DB
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if user_settings:
            if mode == "simulation":
                user_settings.bot_simulation_running = False
            else:
                user_settings.bot_real_running = False
            db.commit()
        
        # Log stop message for this user
        scheduler = request.app.state.scheduler
        scheduler._log_system("INFO", f"🛑 [{mode}] 트레이딩 봇 정지", mode=mode, user_id=current_user.id)
        
        return {"success": True, "message": f"{mode} bot stopped", "mode": mode}
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/panic-sell")
async def panic_sell(
    request: Request,
    mode: str = Query("simulation"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Emergency sell all positions at market price for the authenticated user"""
    try:
        if mode not in ["simulation", "real"]:
            raise HTTPException(status_code=400, detail="Invalid mode")
        
        from models.database import UserSettings
        from services.order_manager import order_manager
        
        # Stop the bot for this user (update DB)
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if user_settings:
            if mode == "simulation":
                user_settings.bot_simulation_running = False
            else:
                user_settings.bot_real_running = False
            db.commit()
        
        # Execute panic sell for this user's positions
        is_simulation = (mode == "simulation")
        positions = db.query(Position).filter(
            Position.user_id == current_user.id,
            Position.mode == mode
        ).all()
        
        sold_positions = []
        for position in positions:
            result = order_manager.execute_sell(
                market=position.coin,
                quantity=position.quantity,
                reason="panic_sell",
                is_simulation=is_simulation,
                user_id=current_user.id,
                user_settings=user_settings
            )
            if result.success:
                sold_positions.append(position.coin)
                db.delete(position)
        
        db.commit()
        
        return {
            "success": True,
            "mode": mode,
            "message": f"Panic sell executed. {len(sold_positions)} positions liquidated.",
            "sold_positions": sold_positions
        }
    except Exception as e:
        logger.error(f"Error during panic sell: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel-orders")
async def cancel_all_orders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cancel all pending orders for the authenticated user"""
    try:
        order_manager = OrderManager(db, user_id=current_user.id)
        cancelled = order_manager.cancel_stale_orders()
        return {
            "success": True,
            "cancelled_count": cancelled,
            "message": f"{cancelled} orders cancelled"
        }
    except Exception as e:
        logger.error(f"Error cancelling orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell-position")
async def sell_position(
    request: Request,
    market: str = Query(..., description="Market to sell (e.g., KRW-BTC)"),
    mode: str = Query("simulation", description="Trading mode: simulation or real"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Sell a specific position at market price for the authenticated user"""
    try:
        if mode not in ["simulation", "real"]:
            raise HTTPException(status_code=400, detail="Invalid mode. Use 'simulation' or 'real'")
        
        from models.database import UserSettings
        from services.order_manager import order_manager
        
        # Get user settings
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        # Find the position for this user
        position = db.query(Position).filter(
            Position.user_id == current_user.id,
            Position.mode == mode,
            Position.coin == market
        ).first()
        
        if not position:
            # For real mode, try to sell from Upbit directly even if no DB position
            if mode == "real" and user_settings and user_settings.upbit_access_key:
                from utils.encryption import encryptor
                import pyupbit
                
                access_key = encryptor.decrypt(user_settings.upbit_access_key)
                secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                user_upbit = pyupbit.Upbit(access_key, secret_key)
                
                # Get balance for this coin
                currency = market.replace("KRW-", "")
                balance = user_upbit.get_balance(currency)
                
                if balance and float(balance) > 0:
                    result = order_manager.execute_sell(
                        market=market,
                        quantity=float(balance),
                        reason="manual_sell",
                        is_simulation=False,
                        user_id=current_user.id,
                        user_settings=user_settings
                    )
                    
                    if result.success:
                        # Log the manual sell
                        scheduler = request.app.state.scheduler
                        scheduler._log_system(
                            "INFO", 
                            f"👤 [{mode}] {market.replace('KRW-', '')} 수동 청산 완료", 
                            mode=mode, 
                            user_id=current_user.id
                        )
                        
                        return {
                            "success": True,
                            "mode": mode,
                            "market": market,
                            "quantity": float(balance),
                            "executed_price": result.executed_price,
                            "message": f"{market} 수동 청산 완료"
                        }
                    else:
                        raise HTTPException(status_code=500, detail=f"매도 실패: {result.error}")
                else:
                    raise HTTPException(status_code=404, detail=f"보유 중인 {market} 포지션을 찾을 수 없습니다")
            else:
                raise HTTPException(status_code=404, detail=f"보유 중인 {market} 포지션을 찾을 수 없습니다")
        
        # Execute sell
        is_simulation = (mode == "simulation")
        result = order_manager.execute_sell(
            market=position.coin,
            quantity=position.quantity,
            reason="manual_sell",
            is_simulation=is_simulation,
            user_id=current_user.id,
            user_settings=user_settings
        )
        
        if result.success:
            # Delete the position from DB
            db.delete(position)
            db.commit()
            
            # Log the manual sell
            scheduler = request.app.state.scheduler
            scheduler._log_system(
                "INFO", 
                f"👤 [{mode}] {market.replace('KRW-', '')} 수동 청산 완료 @ {result.executed_price:,.0f}원", 
                mode=mode, 
                user_id=current_user.id
            )
            
            return {
                "success": True,
                "mode": mode,
                "market": market,
                "quantity": position.quantity,
                "executed_price": result.executed_price,
                "message": f"{market} 수동 청산 완료"
            }
        else:
            raise HTTPException(status_code=500, detail=f"매도 실패: {result.error}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error selling position: {e}")
        raise HTTPException(status_code=500, detail=str(e))
