import logging
logger = logging.getLogger("team3c.decision_engine")

def log_decision(message: str, **fields):
    logger.info("%s | %s", message, fields)
