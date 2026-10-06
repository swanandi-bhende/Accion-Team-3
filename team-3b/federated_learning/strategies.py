"""Aggregation strategy registry and provenance-aware wrapper.

The registry is the single place that knows how to turn an ``FLTaskConfig`` into
a Flower ``Strategy``, so adding a new aggregation method never requires
touching ``run_training``. Robust/Byzantine-tolerant options (FedMedian,
FedTrimmedAvg, Krum) and the differential-privacy wrapper are wired here.

Security posture note: FedTrimmedAvg/Krum/FedMedian defend against a bounded
number of *malicious updates*; ``dp_fedavg`` adds output perturbation. None of
these is cryptographic *secure aggregation* — that is documented as future work
in ``docs/FEDERATED_LEARNING.md``.
"""

from __future__ import annotations

import time
from typing import Callable

from flwr.common import parameters_to_ndarrays
from flwr.server.strategy import (
    DPFedAvgFixed,
    FedAvg,
    FedMedian,
    FedTrimmedAvg,
    Krum,
    Strategy,
)

from common.models import FLTaskConfig, RoundProvenance
from federated_learning.weights import parameters_hash


def _common_kwargs(config: FLTaskConfig) -> dict:
    return {
        "min_fit_clients": 1,  # fault tolerance: proceed with a responding subset
        "min_evaluate_clients": 1,
        "min_available_clients": config.num_clients,
    }


def _fedavg(config: FLTaskConfig) -> Strategy:
    return FedAvg(**_common_kwargs(config))


def _fedmedian(config: FLTaskConfig) -> Strategy:
    return FedMedian(**_common_kwargs(config))


def _fedtrimmedavg(config: FLTaskConfig) -> Strategy:
    return FedTrimmedAvg(**_common_kwargs(config), beta=config.trim_fraction)


def _krum(config: FLTaskConfig) -> Strategy:
    return Krum(**_common_kwargs(config), num_malicious_clients=config.num_malicious_clients)


def _dp_fedavg(config: FLTaskConfig) -> Strategy:
    inner = FedAvg(**_common_kwargs(config))
    return DPFedAvgFixed(
        inner,
        num_sampled_clients=config.dp_num_sampled_clients or config.num_clients,
        clip_norm=config.dp_clip_norm,
        noise_multiplier=config.dp_noise_multiplier,
    )


STRATEGY_BUILDERS: dict[str, Callable[[FLTaskConfig], Strategy]] = {
    "fedavg": _fedavg,
    "fedmedian": _fedmedian,
    "fedtrimmedavg": _fedtrimmedavg,
    "krum": _krum,
    "dp_fedavg": _dp_fedavg,
}


def build_strategy(config: FLTaskConfig) -> Strategy:
    """Resolve the configured aggregation strategy, failing loudly on typos."""
    name = (config.aggregation_strategy or "").lower()
    builder = STRATEGY_BUILDERS.get(name)
    if builder is None:
        raise ValueError(
            f"Unknown aggregation strategy: {config.aggregation_strategy!r}. "
            f"Supported: {sorted(STRATEGY_BUILDERS)}"
        )
    return builder(config)


class ProvenanceStrategy:
    """Wrap a Flower strategy so every aggregated round is recorded.

    The network (SuperLink) path has the server drive the rounds, so provenance
    must be captured inside the strategy. The in-process orchestrator records
    provenance itself in ``run_training`` and does not use this wrapper. All
    other attributes are delegated to the wrapped strategy, so the wrapper can be
    handed to Flower anywhere a ``Strategy`` is accepted.
    """

    def __init__(
        self,
        inner: Strategy,
        strategy_name: str,
        on_record: Callable[[RoundProvenance], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._inner = inner
        self.strategy_name = strategy_name
        self.on_record = on_record
        self.clock = clock
        self.records: list[RoundProvenance] = []

    def __getattr__(self, name):
        # Guard against recursion during unpickling when ``_inner`` is unset.
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    def aggregate_fit(self, server_round, results, failures):
        started = self.clock()
        parameters, metrics = self._inner.aggregate_fit(server_round, results, failures)
        if parameters is not None:
            record = RoundProvenance(
                round_number=server_round,
                participating_clients=len(results),
                aggregation_strategy=self.strategy_name,
                round_duration=self.clock() - started,
                resulting_model_hash=parameters_hash(parameters_to_ndarrays(parameters)),
            )
            self.records.append(record)
            if self.on_record is not None:
                self.on_record(record)
        return parameters, metrics
