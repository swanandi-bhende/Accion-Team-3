"""Safe local capability handlers used by the agent runtime."""


async def analyze_text(payload: object) -> dict[str, int]:
    """Return basic text statistics without external side effects."""
    if not isinstance(payload, str):
        raise TypeError("Text analysis expects a string payload")

    return {
        "character_count": len(payload),
        "word_count": len(payload.split()),
    }
