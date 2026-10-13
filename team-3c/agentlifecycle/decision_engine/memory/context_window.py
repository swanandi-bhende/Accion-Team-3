class ContextWindow:
    def __init__(self, size: int = 20):
        self.size = size

    def trim(self, items: list):
        return items[-self.size:]
