"""
Logger utility with database logging support (KST timezone)
"""
import logging
from datetime import datetime
from typing import Optional

# KST timezone formatter
from utils.timezone import now_kst, KST


class KSTFormatter(logging.Formatter):
    """Custom formatter that uses KST timezone"""
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, tz=KST)
        if datefmt:
            return ct.strftime(datefmt)
        return ct.strftime('%Y-%m-%d %H:%M:%S')


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Setup and return a logger instance with KST timezone"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = KSTFormatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger


class SystemLogger:
    """System logger that also stores logs in database"""
    
    def __init__(self):
        self.logger = setup_logger("SystemLogger")
        self._db_session = None
    
    def set_db_session(self, session):
        """Set database session for log persistence"""
        self._db_session = session
    
    def _store_log(self, level: str, message: str):
        """Store log entry in database"""
        if self._db_session:
            try:
                from models.database import SystemLog
                log_entry = SystemLog(
                    level=level,
                    message=message,
                    created_at=now_kst()
                )
                self._db_session.add(log_entry)
                self._db_session.commit()
            except Exception as e:
                self.logger.error(f"Failed to store log in DB: {e}")
    
    def info(self, message: str):
        self.logger.info(message)
        self._store_log("INFO", message)
    
    def error(self, message: str):
        self.logger.error(message)
        self._store_log("ERROR", message)
    
    def warning(self, message: str):
        self.logger.warning(message)
        self._store_log("WARNING", message)
    
    def debug(self, message: str):
        self.logger.debug(message)


# Global system logger instance
system_logger = SystemLogger()

