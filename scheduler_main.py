"""
Standalone Scheduler Runner
- Runs Upbit and Bybit schedulers independently from the API server
- Prevents duplicate execution with lock file
"""
import os
import sys
import signal
import asyncio
import atexit
from pathlib import Path

from models.database import init_db
from services.scheduler_service import SchedulerService
from services.bybit_scheduler import bybit_scheduler_service
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Lock file to prevent duplicate scheduler execution
LOCK_FILE = Path(__file__).parent / ".scheduler.lock"

# Global scheduler references for graceful shutdown
upbit_scheduler = None
bybit_scheduler = None
shutdown_event = None


def create_lock():
    """Create lock file to prevent duplicate execution"""
    if LOCK_FILE.exists():
        # Check if the process is still running
        try:
            with open(LOCK_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            # Check if process exists
            os.kill(old_pid, 0)
            logger.error(f"❌ Scheduler already running with PID {old_pid}")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            # Process not running, remove stale lock
            logger.warning("🔓 Removing stale lock file")
            LOCK_FILE.unlink()
    
    # Create new lock file with current PID
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    logger.info(f"🔒 Lock file created (PID: {os.getpid()})")


def remove_lock():
    """Remove lock file on exit"""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
            logger.info("🔓 Lock file removed")
    except Exception as e:
        logger.error(f"Failed to remove lock file: {e}")


def graceful_shutdown(signum, frame):
    """Handle graceful shutdown"""
    global upbit_scheduler, bybit_scheduler, shutdown_event
    
    logger.info("🛑 Received shutdown signal, stopping schedulers...")
    
    if upbit_scheduler:
        upbit_scheduler.shutdown()
    if bybit_scheduler:
        bybit_scheduler.shutdown()
    
    if shutdown_event:
        shutdown_event.set()
    
    remove_lock()
    logger.info("👋 Scheduler shutdown complete")


async def run_schedulers():
    """Run schedulers with asyncio event loop"""
    global upbit_scheduler, bybit_scheduler, shutdown_event
    
    shutdown_event = asyncio.Event()
    
    logger.info("=" * 50)
    logger.info("🚀 Starting Standalone Scheduler...")
    logger.info("=" * 50)
    
    # Create lock file
    create_lock()
    
    # Register cleanup on exit
    atexit.register(remove_lock)
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)
    
    try:
        # Initialize database
        logger.info("📦 Initializing database...")
        init_db()
        
        # Start Upbit scheduler
        logger.info("📈 Starting Upbit Scheduler...")
        upbit_scheduler = SchedulerService()
        upbit_scheduler.start()
        
        # Start Bybit scheduler
        logger.info("📊 Starting Bybit Scheduler...")
        bybit_scheduler = bybit_scheduler_service
        bybit_scheduler.start()
        
        logger.info("✅ All schedulers started successfully!")
        logger.info("   - Upbit: Running")
        logger.info("   - Bybit: Running")
        logger.info("=" * 50)
        
        # Keep the process running with asyncio
        while not shutdown_event.is_set():
            await asyncio.sleep(60)
            logger.debug("💓 Scheduler heartbeat")
            
    except asyncio.CancelledError:
        logger.info("🛑 Scheduler cancelled")
    except Exception as e:
        logger.error(f"❌ Scheduler error: {e}")
        raise
    finally:
        remove_lock()


def main():
    """Main entry point for scheduler"""
    try:
        asyncio.run(run_schedulers())
    except KeyboardInterrupt:
        logger.info("🛑 Keyboard interrupt received")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        remove_lock()
        sys.exit(1)


if __name__ == "__main__":
    main()

