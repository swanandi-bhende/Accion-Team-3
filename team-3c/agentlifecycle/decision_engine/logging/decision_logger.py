import logging
logger = logging.getLogger("team3c.decision_engine")

def log_decision(message: str, **fields):
    """Log IDs, reasons and counts only - never API keys or task inputs."""
    logger.info("%s | %s", message, fields)


def log_decision_warning(message: str, **fields):
    """Same rules as log_decision, for anomalies such as unknown task IDs."""
    logger.warning("%s | %s", message, fields)
