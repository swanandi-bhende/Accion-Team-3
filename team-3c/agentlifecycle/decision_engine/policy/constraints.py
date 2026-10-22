# Decision Engine policy constants.
MIN_MATCH_SCORE = 0.70
DEFAULT_MAX_CONCURRENT_TASKS = 3
CONTEXT_WINDOW_SIZE = 20

# Bid bounds (Requirement 2.3: 50%-95% of bounty) are NOT used by the
# Decision Engine. They belong to the Bidding Engine (WP3), which owns bid
# calculation; kept here only as the documented reference values.
MIN_BID_RATIO = 0.50
MAX_BID_RATIO = 0.95
