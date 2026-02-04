"""
Upbit Algo-Trading System - FastAPI Main Application
NOTE: Schedulers are now run separately via scheduler_main.py
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from routers import system, trading, settings, auth, bybit, chart
from models.database import init_db
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Environment variable to optionally enable embedded scheduler (for development)
ENABLE_EMBEDDED_SCHEDULER = os.getenv("ENABLE_EMBEDDED_SCHEDULER", "false").lower() == "true"


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Middleware to prevent caching of API responses"""
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("🚀 Starting API Server...")
    init_db()
    
    # Only start embedded scheduler if explicitly enabled (for local development)
    if ENABLE_EMBEDDED_SCHEDULER:
        logger.info("📅 Starting embedded schedulers (development mode)...")
        from services.scheduler_service import SchedulerService
        from services.bybit_scheduler import bybit_scheduler_service
        
        scheduler = SchedulerService()
        scheduler.start()
        bybit_scheduler_service.start()
        app.state.scheduler = scheduler
        app.state.bybit_scheduler = bybit_scheduler_service
        logger.info("✅ Embedded schedulers started")
    else:
        logger.info("📅 Schedulers running in separate process (production mode)")
        app.state.scheduler = None
        app.state.bybit_scheduler = None
    
    logger.info("✅ API Server initialization complete")
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down API Server...")
    if ENABLE_EMBEDDED_SCHEDULER and app.state.scheduler:
        app.state.scheduler.shutdown()
        app.state.bybit_scheduler.shutdown()
    logger.info("👋 API Server shutdown complete")



app = FastAPI(
    title="Algo-Trading System",
    description="업비트/바이빗 자동매매 시스템",
    version="3.0.0",
    lifespan=lifespan
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://43.201.239.150:3000",  # EC2 Frontend
        "http://43.201.239.150",
        "https://r444874e8e3b55bb3301b9752dc982b75.apppaas.app",  # AppPaaS Frontend
        "https://autotrading-frontend.vercel.app",  # Vercel Frontend
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Cache Prevention Middleware
app.add_middleware(NoCacheMiddleware)

# Include Routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(trading.router, prefix="/api/trading", tags=["Trading (Upbit)"])
app.include_router(bybit.router, prefix="/api", tags=["Trading (Bybit)"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(chart.router, prefix="/api/chart", tags=["Chart"])


@app.get("/")
async def root():
    return {"message": "Upbit Algo-Trading System API", "status": "running", "version": "2.0.0"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}

