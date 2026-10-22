"""Mock-chain event listener with durable progress and idempotent dispatch."""

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from common.models import InferenceRequest

logger = logging.getLogger(__name__)


class EventListener:
    """Follow TaskCreated and TaskFunded events and dispatch each task once both are seen.

    Progress is persisted in Redis per block and per task, so polling resumes from the
    last processed block after a restart. A task moves through three states:

    - undispatched, with no flag;
    - attempted, persisted before the dispatch callable runs. A crash in between leaves
      it here, so a restart never dispatches it twice;
    - dispatched, persisted only after the dispatch target accepted it.

    An error from the dispatch target returns the task to undispatched, so it is retried
    on the next pass. Without a dispatch target the listener only tracks tasks and never
    marks one dispatched.

    The progress belongs to one chain, identified by its genesis block hash. A reset node
    has a different genesis, so before polling the listener clears the cursor and task
    records saved for the old chain. Block numbers and task ids mean nothing across chains.
    """

    LAST_BLOCK_KEY = "event_listener:last_block"
    CHAIN_KEY = "event_listener:chain"
    TASK_STATE_PREFIX = "event_listener:task:"
    RECONNECT_BASE = 1.0
    RECONNECT_MAX = 60.0
    PENDING_TTL_SECONDS = 3600.0

    def __init__(
        self,
        web3: Any,
        task_manager: Any,
        escrow: Any,
        redis_client: Any,
        inference_engine: Any = None,
        dispatch: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        self.web3 = web3
        self.task_manager = task_manager
        self.escrow = escrow
        self.redis = redis_client
        self.inference_engine = inference_engine
        self.dispatch = dispatch or (self._dispatch_to_engine if inference_engine is not None else None)
        self.clock = clock
        self.sleep = sleep
        self._task_events: dict[str, dict[str, Any]] = {}
        self._pending_since: dict[str, float] = {}
        self._connection_state = "disconnected"
        self._state_restored = False
        self._stop_requested = False

    def stop(self) -> None:
        """Ask the poll loop to finish its current pass and return from ``start``."""
        self._stop_requested = True

    @staticmethod
    def _task_id(value: Any) -> str:
        return str(value.hex()) if hasattr(value, "hex") else str(value)

    @staticmethod
    def _event_amount(args: dict[str, Any]) -> int:
        """Read the funded amount under either field name.

        Team A's ``EscrowContract`` emits ``TaskFunded(taskId, funder, amount)``;
        our earlier mock named the same field ``fundedAmount``. Accepting both
        keeps an in-flight stack and the real deployment readable by one binary.
        """
        for name in ("amount", "fundedAmount"):
            if name in args:
                return int(args[name])
        raise KeyError("TaskFunded has neither an 'amount' nor a 'fundedAmount' field")

    @staticmethod
    def _event_args(event: Any) -> dict[str, Any]:
        args = getattr(event, "args", None)
        if args is None and isinstance(event, dict):
            args = event.get("args", event)
        return dict(args or {})

    @staticmethod
    def _block_number(event: Any) -> int:
        if isinstance(event, dict):
            return int(event.get("blockNumber", 0))
        return int(getattr(event, "blockNumber", 0))

    async def _get_last_processed_block(self) -> int:
        value = await self.redis.get(self.LAST_BLOCK_KEY)
        return int(value.decode() if isinstance(value, bytes) else value) if value else 0

    async def _persist_block_number(self, block_number: int) -> None:
        await self.redis.set(self.LAST_BLOCK_KEY, str(block_number))

    async def _persist_task_state(self, task_id: str) -> None:
        task = self._task_events.get(task_id)
        if task is None:
            await self.redis.delete(f"{self.TASK_STATE_PREFIX}{task_id}")
            return
        await self.redis.set(f"{self.TASK_STATE_PREFIX}{task_id}", json.dumps(task, default=self._json_value))

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"__bytes__": value.hex()}
        raise TypeError(f"Unsupported task state value: {type(value)!r}")

    @staticmethod
    def _restore_value(value: Any) -> Any:
        if isinstance(value, dict) and set(value) == {"__bytes__"}:
            return bytes.fromhex(value["__bytes__"])
        if isinstance(value, dict):
            return {key: EventListener._restore_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [EventListener._restore_value(item) for item in value]
        return value

    async def _restore_task_states(self) -> None:
        if self._state_restored:
            return
        keys = await self.redis.keys(f"{self.TASK_STATE_PREFIX}*")
        for key in keys:
            raw = await self.redis.get(key)
            if raw is None:
                continue
            key_text = key.decode() if isinstance(key, bytes) else key
            task_id = key_text.removeprefix(self.TASK_STATE_PREFIX)
            state = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            self._task_events[task_id] = self._restore_value(state)
            self._pending_since.setdefault(task_id, self.clock())
        self._state_restored = True

    def _expired(self, deadline: int) -> bool:
        return deadline <= self.clock()

    async def _process_created(self, event: Any) -> None:
        args = self._event_args(event)
        task_id = self._task_id(args["taskId"])
        deadline = int(args["deadline"])
        if self._expired(deadline):
            logger.warning("Skipping expired task_id=%s deadline=%s", task_id, deadline)
            return
        self._task_events.setdefault(task_id, {})["created"] = {
            "task_id": task_id,
            "creator": args["creator"],
            "description_hash": args["descriptionHash"],
            "deadline": deadline,
            "bounty_amount": int(args["bountyAmount"]),
            "block_number": self._block_number(event),
        }
        self._pending_since.setdefault(task_id, self.clock())
        await self._persist_task_state(task_id)
        await self._try_dispatch(task_id)

    async def _process_funded(self, event: Any) -> None:
        args = self._event_args(event)
        task_id = self._task_id(args["taskId"])
        self._task_events.setdefault(task_id, {})["funded"] = {
            "task_id": task_id,
            "funder": args["funder"],
            "funded_amount": self._event_amount(args),
            "block_number": self._block_number(event),
        }
        self._pending_since.setdefault(task_id, self.clock())
        await self._persist_task_state(task_id)
        await self._try_dispatch(task_id)

    async def _process_events(self, from_block: int, to_block: int | str = "latest") -> None:
        await self._restore_task_states()
        # One eth_getLogs per event type. A filter would stay installed on the node after
        # every poll, which is what leaked thousands of filters on the local chain.
        created_events = self.task_manager.events.TaskCreated.get_logs(from_block=from_block, to_block=to_block)
        funded_events = self.escrow.events.TaskFunded.get_logs(from_block=from_block, to_block=to_block)
        events = [(self._block_number(event), 0, event) for event in created_events]
        events.extend((self._block_number(event), 1, event) for event in funded_events)
        for block_number, event_type, event in sorted(events, key=lambda item: (item[0], item[1])):
            if event_type == 0:
                await self._process_created(event)
            else:
                await self._process_funded(event)
            await self._persist_block_number(block_number)
        # Retry tasks whose earlier dispatch failed. Without a dispatch target there is nothing to retry.
        if self.dispatch is not None:
            for task_id in list(self._task_events):
                await self._try_dispatch(task_id)

    async def _genesis_hash(self) -> str:
        raw = self.web3.eth.get_block(0)["hash"]
        text = raw.hex() if isinstance(raw, bytes) else str(raw)
        return "0x" + text.removeprefix("0x")

    async def _align_with_chain(self) -> None:
        """Clear the progress saved for a different chain.

        A reset node reuses block numbers and task ids, so state from the old chain would
        hide new events below the saved cursor and could mark a new task as dispatched.
        """
        genesis = await self._genesis_hash()
        stored = await self.redis.get(self.CHAIN_KEY)
        stored_text = stored.decode() if isinstance(stored, bytes) else stored
        if stored_text == genesis:
            return
        if stored_text is None:
            # Progress saved before chain identity was recorded. Keep it unless the cursor
            # is past this chain's head, which only another chain can produce.
            if await self._get_last_processed_block() <= self.web3.eth.block_number:
                await self.redis.set(self.CHAIN_KEY, genesis)
                return
        logger.warning(
            "CHAIN_CHANGED genesis=%s previous=%s; clearing progress saved for the previous chain",
            genesis,
            stored_text,
        )
        await self._clear_progress()
        await self.redis.set(self.CHAIN_KEY, genesis)

    async def _clear_progress(self) -> None:
        keys = await self.redis.keys(f"{self.TASK_STATE_PREFIX}*")
        if keys:
            await self.redis.delete(*keys)
        await self.redis.delete(self.LAST_BLOCK_KEY)
        self._task_events.clear()
        self._pending_since.clear()

    async def _try_dispatch(self, task_id: str) -> bool:
        task = self._task_events.get(task_id)
        if not task or task.get("dispatched") or task.get("attempted") or "created" not in task or "funded" not in task:
            return False
        if self._expired(task["created"]["deadline"]):
            logger.warning("Skipping expired task_id=%s deadline=%s", task_id, task["created"]["deadline"])
            self._task_events.pop(task_id, None)
            self._pending_since.pop(task_id, None)
            return False
        if self.dispatch is None:
            logger.info("TASK_READY task_id=%s no dispatch target configured; not dispatched", task_id)
            return False
        task["attempted"] = True
        await self._persist_task_state(task_id)
        try:
            result = self.dispatch(task)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # The target did not accept the task, so it goes back to undispatched and the
            # next pass retries it.
            task.pop("attempted", None)
            await self._persist_task_state(task_id)
            logger.exception("Dispatch failed task_id=%s; will retry", task_id)
            return False
        task["dispatched"] = True
        await self._persist_task_state(task_id)
        logger.info("Dispatched task_id=%s block_number=%s", task_id, task["created"]["block_number"])
        return True

    async def _dispatch_to_engine(self, task: dict[str, Any]) -> Any:
        request = InferenceRequest(
            model_id=task["created"]["description_hash"].hex(),
            input_data=b"",
            task_id=task["created"]["task_id"],
        )
        result = self.inference_engine.execute(request)
        return await result if inspect.isawaitable(result) else result

    async def cleanup_pending(self) -> int:
        """Forget tasks tracked for longer than PENDING_TTL_SECONDS.

        Removes them from memory and from Redis. Returns the number removed.
        """
        cutoff = self.clock() - self.PENDING_TTL_SECONDS
        abandoned = [task_id for task_id, started in self._pending_since.items() if started < cutoff]
        for task_id in abandoned:
            self._pending_since.pop(task_id, None)
            self._task_events.pop(task_id, None)
            await self._persist_task_state(task_id)
        return len(abandoned)

    async def _reconnect_with_backoff(self, connect: Callable[[], Awaitable[Any] | Any], max_attempts: int | None = None) -> Any:
        delay = self.RECONNECT_BASE
        attempts = 0
        while max_attempts is None or attempts < max_attempts:
            attempts += 1
            logger.warning("Reconnect attempt=%s delay=%s", attempts, delay)
            try:
                result = connect()
                result = await result if inspect.isawaitable(result) else result
                self._connection_state = "connected"
                return result
            except (ConnectionError, OSError):
                self._connection_state = "disconnected"
                if max_attempts is not None and attempts >= max_attempts:
                    raise
                await self.sleep(delay)
                delay = min(delay * 2, self.RECONNECT_MAX)
        return None

    async def start(self, poll_interval: float = 1.0) -> None:
        """Poll the chain for new events until stop() is called.

        Connection errors are retried rather than raised.

        Args:
            poll_interval: Seconds to wait between polls.
        """
        await self._restore_task_states()
        self._connection_state = "connected"
        while not self._stop_requested:
            try:
                # Checked on every pass, so a chain reset while the listener runs is caught too.
                await self._align_with_chain()
                from_block = await self._get_last_processed_block() + 1
                await self._process_events(from_block)
            except (ConnectionError, OSError):
                pass
            try:
                await self.sleep(poll_interval)
            except asyncio.CancelledError:
                logger.warning("event-listener: poll cancelled (shutdown in progress)")
                break
            except (ConnectionError, OSError):
                self._connection_state = "disconnected"
                connect = getattr(self.web3.provider, "connect", None)
                if connect is None:
                    # A pooled HTTP transport has nothing to reopen, so back off
                    # explicitly instead of spinning this loop against a dead RPC.
                    await self.sleep(self.RECONNECT_BASE)
                else:
                    await self._reconnect_with_backoff(connect)

    async def status(self) -> dict[str, Any]:
        """Return the last processed block, the number of tracked tasks and the connection state."""
        return {
            "last_processed_block": await self._get_last_processed_block(),
            "pending_tasks": len(self._task_events),
            "connection_state": self._connection_state,
        }
