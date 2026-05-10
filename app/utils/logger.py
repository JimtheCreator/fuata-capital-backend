import logging
import sys
import structlog
from pathlib import Path
from concurrent_log_handler import ConcurrentRotatingFileHandler

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def configure_logging():
    """
    Configures standard logging to use ConcurrentRotatingFileHandler, 
    then wraps it with structlog for JSON/KV support.
    """
    
    # 1. Setup the Standard Logging handlers
    shared_handlers = [
        # Process-safe file rotation
        ConcurrentRotatingFileHandler(
            LOG_DIR / "app.log",
            maxBytes=10*1024*1024,  # 10 MB
            backupCount=5,
            encoding='utf-8'
        ),
        # Console output (This is your 'stream')
        logging.StreamHandler(sys.stdout)
    ]

    # 2. Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.dict_tracebacks,
            structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 3. Connect Standard Logging (FIXED HERE)
    logging.basicConfig(
        level=logging.INFO,
        handlers=shared_handlers  # REMOVED stream=sys.stdout from here
    )

    # Global exception hook
    def exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        # Use structlog to log the uncaught error with full context
        structlog.get_logger("sys.excepthook").error(
            "uncaught_exception",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = exception_handler

    return structlog.get_logger()

# Export a default logger instance
logger = configure_logging()