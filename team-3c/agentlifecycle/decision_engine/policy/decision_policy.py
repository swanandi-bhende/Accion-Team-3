from .constraints import MIN_MATCH_SCORE, MIN_BID_RATIO, MAX_BID_RATIO

class DecisionPolicy:
    def validate(self, match_score: float, bid_ratio: float | None,
                 active_task_count: int, max_concurrent_tasks: int):
        suitable = match_score > MIN_MATCH_SCORE
        if active_task_count >= max_concurrent_tasks:
            suitable = False
        if bid_ratio is not None:
            bid_ratio = max(MIN_BID_RATIO, min(MAX_BID_RATIO, bid_ratio))
        return suitable, bid_ratio

    @staticmethod
    def calculate_bid(bounty: float, bid_ratio: float | None):
        return None if bid_ratio is None else bounty * bid_ratio
