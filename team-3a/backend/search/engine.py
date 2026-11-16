"""Semantic search over contract clauses (Requirement 11).

`InMemorySearchEngine` scores with token-overlap cosine similarity rather than real
embeddings. That keeps Properties 20-22 testable offline; `WeaviateSearchEngine`
overrides only `_search_raw`, so ordering, threshold and filter semantics are shared.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from backend.config import settings
from backend.legal_agent.models import Clause, ParsedContract

CLAUSE_CLASS_SCHEMA = {
    "class": "ContractClause",
    "vectorizer": "text2vec-transformers",
    "properties": [
        {"name": "text", "dataType": ["text"]},
        {"name": "clauseType", "dataType": ["text"], "tokenization": "field"},
        {"name": "contractDate", "dataType": ["date"]},
        {"name": "jurisdiction", "dataType": ["text"], "tokenization": "field"},
        {"name": "contractId", "dataType": ["text"], "tokenization": "field"},
        {"name": "confidence", "dataType": ["number"]},
    ],
}


@dataclass
class SearchResult:
    contract_id: str
    clause_type: str
    text: str
    relevance: float
    jurisdiction: str | None = None
    contract_date: date | None = None


@dataclass
class SearchResponse:
    results: list[SearchResult]
    message: str | None = None


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    numerator = sum(a[t] * b[t] for t in shared)
    denom = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return numerator / denom if denom else 0.0


class InMemorySearchEngine:
    def __init__(self, threshold: float | None = None):
        self.threshold = threshold if threshold is not None else settings.relevance_threshold
        self._docs: list[dict] = []

    def index_clauses(self, parsed: ParsedContract, contract_date: date | None = None) -> int:
        """Req 11.5. Returns how many clauses were indexed."""
        for clause in parsed.clauses:
            self._docs.append(
                {
                    "contract_id": parsed.contract_id,
                    "clause_type": clause.clause_type.value,
                    "text": clause.text,
                    "jurisdiction": parsed.jurisdiction,
                    "contract_date": contract_date,
                    "tokens": Counter(tokenize(clause.text)),
                }
            )
        return len(parsed.clauses)

    def _search_raw(self, query: str) -> list[tuple[dict, float]]:
        q = Counter(tokenize(query))
        return [(doc, cosine(q, doc["tokens"])) for doc in self._docs]

    def search(
        self,
        query: str,
        clause_type: str | None = None,
        date_range: tuple[date, date] | None = None,
        jurisdiction: str | None = None,
        limit: int = 10,
    ) -> SearchResponse:
        """Req 11.1-11.4, 11.6.

        Filters are applied before the threshold so a filtered-out high scorer cannot
        keep a legitimate result out of the response.
        """
        scored = self._search_raw(query)
        kept: list[SearchResult] = []

        for doc, score in scored:
            if clause_type is not None and doc["clause_type"] != clause_type:
                continue
            if jurisdiction is not None and doc["jurisdiction"] != jurisdiction:
                continue
            if date_range is not None:
                doc_date = doc["contract_date"]
                if doc_date is None or not (date_range[0] <= doc_date <= date_range[1]):
                    continue
            if score < self.threshold:
                continue
            kept.append(
                SearchResult(
                    contract_id=doc["contract_id"],
                    clause_type=doc["clause_type"],
                    text=doc["text"],
                    relevance=round(min(1.0, max(0.0, score)), 4),
                    jurisdiction=doc["jurisdiction"],
                    contract_date=doc["contract_date"],
                )
            )

        kept.sort(key=lambda r: r.relevance, reverse=True)
        kept = kept[:limit]

        if not kept:
            return SearchResponse(
                results=[],
                message=(
                    "no matches met the minimum relevance threshold of "
                    f"{self.threshold}"
                ),
            )
        return SearchResponse(results=kept)


class WeaviateSearchEngine(InMemorySearchEngine):
    """Weaviate-backed engine; overrides transport only."""

    def __init__(self, url: str | None = None, threshold: float | None = None):
        super().__init__(threshold=threshold)
        self.url = url or settings.weaviate_url
        self._client = None

    def connect(self):  # pragma: no cover - requires a running Weaviate
        import weaviate

        from urllib.parse import urlparse

        from weaviate.classes.init import AdditionalConfig, Timeout

        parsed = urlparse(self.url if "//" in self.url else f"http://{self.url}")
        host = parsed.hostname or "127.0.0.1"
        secure = parsed.scheme == "https"
        self._client = weaviate.connect_to_custom(
            http_host=host,
            http_port=parsed.port or (443 if secure else 80),
            http_secure=secure,
            grpc_host=host,
            grpc_port=int(os.getenv("WEAVIATE_GRPC_PORT", "50051")),
            grpc_secure=secure,
            additional_config=AdditionalConfig(
                # Req 11.2: results within 2 seconds.
                timeout=Timeout(init=30, query=settings.search_query_timeout_seconds, insert=60)
            ),
        )
        if not self._client.collections.exists("ContractClause"):
            self._client.collections.create_from_dict(CLAUSE_CLASS_SCHEMA)
        return self._client

    def index_clauses(self, parsed: ParsedContract, contract_date: date | None = None) -> int:  # pragma: no cover
        super().index_clauses(parsed, contract_date)
        if self._client is None:
            self.connect()
            
        collection = self._client.collections.get("ContractClause")
        with collection.batch.dynamic() as batch:
            for clause in parsed.clauses:
                batch.add_object({
                    "text": clause.text,
                    "clauseType": clause.clause_type.value,
                    "contractDate": contract_date.isoformat() + "T00:00:00Z" if contract_date else None,
                    "jurisdiction": parsed.jurisdiction,
                    "contractId": parsed.contract_id,
                    "confidence": clause.confidence
                })
        return len(parsed.clauses)

    def search(self, query: str, clause_type: str | None = None, date_range: tuple[date, date] | None = None, jurisdiction: str | None = None, limit: int = 10) -> SearchResponse:  # pragma: no cover
        if self._client is None:
            self.connect()
            
        import weaviate.classes.query as wq
        
        filters = []
        if clause_type:
            filters.append(wq.Filter.by_property("clauseType").equal(clause_type))
        if jurisdiction:
            filters.append(wq.Filter.by_property("jurisdiction").equal(jurisdiction))
        if date_range:
            filters.append(wq.Filter.by_property("contractDate").greater_or_equal(date_range[0].isoformat() + "T00:00:00Z"))
            filters.append(wq.Filter.by_property("contractDate").less_or_equal(date_range[1].isoformat() + "T23:59:59Z"))
            
        final_filter = None
        if filters:
            final_filter = filters[0]
            for f in filters[1:]:
                final_filter = final_filter & f

        collection = self._client.collections.get("ContractClause")
        response = collection.query.near_text(
            query=query,
            limit=limit,
            filters=final_filter,
            return_metadata=wq.MetadataQuery(certainty=True)
        )
        
        kept = []
        for obj in response.objects:
            score = obj.metadata.certainty or 0.0
            if score >= self.threshold:
                kept.append(SearchResult(
                    contract_id=obj.properties.get("contractId"),
                    clause_type=obj.properties.get("clauseType"),
                    text=obj.properties.get("text"),
                    relevance=round(score, 4),
                    jurisdiction=obj.properties.get("jurisdiction")
                ))
                
        if not kept:
            return SearchResponse(results=[], message=f"no matches met the minimum relevance threshold of {self.threshold}")
        return SearchResponse(results=kept)
