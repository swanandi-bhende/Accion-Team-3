"""In-memory registry for locally available capability handlers."""

from collections.abc import Awaitable, Callable

CapabilityHandler = Callable[[object], Awaitable[object]]


class InMemoryCapabilityRegistry:
    """Resolve declared capabilities only when a callable handler is registered."""

    def __init__(self) -> None:
        self._handlers: dict[str, CapabilityHandler] = {}

    def register(
        self,
        capability: str,
        handler: CapabilityHandler,
    ) -> None:
        if not capability.strip():
            raise ValueError("Capability name must not be empty")
        if not callable(handler):
            raise TypeError("Capability handler must be callable")
        self._handlers[capability] = handler

    async def is_capability_available(self, capability: str) -> bool:
        return callable(self._handlers.get(capability))

    async def invoke_capability(
        self,
        capability: str,
        payload: object,
    ) -> object:
        handler = self._handlers.get(capability)
        if handler is None:
            raise LookupError(
                f"No usable handler is registered for capability: {capability}"
            )
        return await handler(payload)
