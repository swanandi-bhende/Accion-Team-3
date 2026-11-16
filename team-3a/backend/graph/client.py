"""Legal knowledge graph (Requirement 10).

`InMemoryGraph` implements the same surface as `NebulaGraphClient` over dicts. It backs
the tests and lets the pipeline run without a NebulaGraph cluster; the auto-linking
logic in Req 10.5 lives in the shared base so both backends share one implementation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.config import settings
from backend.legal_agent.models import ParsedContract

VERTEX_TYPES = ("contract", "clause", "legal_entity")
EDGE_TYPES = ("contains", "references", "depends_on", "conflicts_with", "supersedes", "party_to")


@dataclass
class Edge:
    src: str
    dst: str
    edge_type: str


@dataclass
class GraphState:
    vertices: dict[str, str] = field(default_factory=dict)  # id -> vertex type
    edges: list[Edge] = field(default_factory=list)

    def add_vertex(self, vertex_id: str, vertex_type: str) -> None:
        if vertex_type not in VERTEX_TYPES:
            raise ValueError(f"unknown vertex type {vertex_type!r}")
        self.vertices[vertex_id] = vertex_type

    def add_edge(self, src: str, dst: str, edge_type: str) -> None:
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"unknown edge type {edge_type!r}")
        self.edges.append(Edge(src, dst, edge_type))

    def neighbors(self, vertex_id: str, edge_type: str | None = None) -> list[str]:
        return [
            e.dst
            for e in self.edges
            if e.src == vertex_id and (edge_type is None or e.edge_type == edge_type)
        ]


class InMemoryGraph:
    """Reference implementation of the graph surface."""

    def __init__(self) -> None:
        self.state = GraphState()
        # entity name -> contracts that named it, for auto-linking.
        self._entity_index: dict[str, list[str]] = {}

    def add_contract(self, parsed: ParsedContract) -> dict[str, int]:
        """Req 10.1, 10.2, 10.5. Returns counts so callers can assert on them."""
        contract_id = parsed.contract_id
        self.state.add_vertex(contract_id, "contract")

        new_edges = 0
        for i, clause in enumerate(parsed.clauses):
            clause_id = f"{contract_id}::clause::{i}"
            self.state.add_vertex(clause_id, "clause")
            self.state.add_edge(contract_id, clause_id, "contains")
            new_edges += 1

        # Req 10.2: depends_on / conflicts_with between clauses of this contract.
        clause_edges: list[tuple[str, str, str]] = []
        for src_i, dst_i, edge_type in parsed.relationships:
            if src_i < len(parsed.clauses) and dst_i < len(parsed.clauses):
                src, dst = f"{contract_id}::clause::{src_i}", f"{contract_id}::clause::{dst_i}"
                self.state.add_edge(src, dst, edge_type)
                clause_edges.append((src, dst, edge_type))
                new_edges += 1

        # Entities shared with earlier contracts, counted before this contract is indexed.
        shared: dict[str, int] = {}
        for entity in parsed.entities:
            for other in self._entity_index.get(entity, []):
                if other != contract_id:
                    shared[other] = shared.get(other, 0) + 1

        for entity in parsed.entities:
            entity_id = f"entity::{entity}"
            self.state.add_vertex(entity_id, "legal_entity")
            self.state.add_edge(contract_id, entity_id, "party_to")
            new_edges += 1

            # Req 10.5: a shared entity links this contract to every earlier one
            # that named the same party, in both directions.
            for other in self._entity_index.get(entity, []):
                if other != contract_id:
                    self.state.add_edge(contract_id, other, "references")
                    self.state.add_edge(other, contract_id, "references")
                    new_edges += 2
            self._entity_index.setdefault(entity, []).append(contract_id)

        # Req 10.2 `supersedes`: a contract that says it replaces earlier agreements
        # supersedes the earlier contract it shares the most parties with (latest on ties).
        superseded: list[str] = []
        if parsed.supersedes_prior and shared:
            order = {cid: n for n, cid in enumerate(self.state.vertices)}
            target = max(shared, key=lambda c: (shared[c], order.get(c, 0)))
            self.state.add_edge(contract_id, target, "supersedes")
            superseded.append(target)
            new_edges += 1

        self._last_clause_edges = clause_edges
        self._last_superseded = superseded
        return {
            "clauses": len(parsed.clauses),
            "entities": len(parsed.entities),
            "edges_added": new_edges,
        }

    # --- traversal (Req 10.3, 10.4) ---------------------------------------------

    def find_paths(
        self, src: str, dst: str, max_depth: int = 4, edge_types: tuple[str, ...] | None = None
    ) -> list[list[str]]:
        """All simple paths from src to dst up to max_depth edges."""
        paths: list[list[str]] = []

        def walk(node: str, trail: list[str]) -> None:
            if len(trail) - 1 > max_depth:
                return
            if node == dst and len(trail) > 1:
                paths.append(list(trail))
                return
            for e in self.state.edges:
                if e.src == node and e.dst not in trail and (
                    edge_types is None or e.edge_type in edge_types
                ):
                    walk(e.dst, trail + [e.dst])

        walk(src, [src])
        return paths

    _NODE_RE = re.compile(r'^\(\s*(\w+)?(?::(\w+))?\s*\)')
    _EDGE_RE = re.compile(
        r'^(?:'
        r'(?:-\s*\[\s*(?:(\w+))?(?::(\w+))?(?:\*(\d+)?(?:\.\.(\d+)?)?)?\s*\]\s*->)|'
        r'(?:<-\s*\[\s*(?:(\w+))?(?::(\w+))?(?:\*(\d+)?(?:\.\.(\d+)?)?)?\s*\]\s*-)|'
        r'(?:-\s*\[\s*(?:(\w+))?(?::(\w+))?(?:\*(\d+)?(?:\.\.(\d+)?)?)?\s*\]\s*-)'
        r')'
    )

    def query(self, statement: str) -> list[dict]:
        """Cypher-style pattern matching and path traversal (Req 10.4).

        Supports:
        - Single-hop: MATCH (a[:tag])-[:edge]->(b[:tag]) [WHERE id(a) == "x"] RETURN id(a), id(b)
        - Multi-hop: MATCH (a)-[:edge1]->(b)-[:edge2]->(c) RETURN id(a), id(b), id(c)
        - Path traversal: MATCH [p =] (a)-[:edge*1..3]->(b) RETURN p (or id(a), id(b))
        - Bi-directional / undirected edge patterns: ->, <-, -
        """
        st = statement.strip()
        m_start = re.match(r'^MATCH\s+', st, re.IGNORECASE)
        if not m_start:
            raise ValueError('unsupported query; expected MATCH pattern [WHERE ...] RETURN ...')
        rest = st[m_start.end():]

        path_var = None
        m_p = re.match(r'^(\w+)\s*=\s*', rest)
        if m_p:
            path_var = m_p.group(1)
            rest = rest[m_p.end():]

        m_ret = re.search(r'\s+RETURN\s+(.+?)\s*;?$', rest, re.IGNORECASE)
        if not m_ret:
            raise ValueError('query must contain RETURN clause')
        return_str = m_ret.group(1)
        body = rest[:m_ret.start()].strip()

        where_conds: dict[str, str] = {}
        m_where = re.search(r'\s+WHERE\s+(.+)$', body, re.IGNORECASE)
        if m_where:
            where_str = m_where.group(1)
            body = body[:m_where.start()].strip()
            for cond in re.split(r'\s+AND\s+', where_str, flags=re.IGNORECASE):
                m_c = re.match(r'id\(\s*(\w+)\s*\)\s*(?:==|=)\s*"([^"]*)"', cond.strip())
                if m_c:
                    where_conds[m_c.group(1)] = m_c.group(2)
                else:
                    raise ValueError(f'unsupported WHERE condition: {cond}')

        nodes: list[dict[str, str | None]] = []
        edges: list[dict] = []
        cur = body
        m_n = self._NODE_RE.match(cur)
        if not m_n:
            raise ValueError(f'expected node pattern at: {cur}')
        nodes.append({"var": m_n.group(1), "tag": m_n.group(2)})
        cur = cur[m_n.end():].strip()

        while cur:
            m_e = self._EDGE_RE.match(cur)
            if not m_e:
                raise ValueError(f'expected edge pattern at: {cur}')
            matched_text = m_e.group(0)
            is_forward = matched_text.endswith("->")
            is_backward = matched_text.startswith("<-")

            groups = m_e.groups()
            if groups[0] or groups[1] or groups[2] or groups[3]:
                var, edge_type, min_h, max_h = groups[0:4]
            elif groups[4] or groups[5] or groups[6] or groups[7]:
                var, edge_type, min_h, max_h = groups[4:8]
            else:
                var, edge_type, min_h, max_h = groups[8:12]

            if edge_type and edge_type not in EDGE_TYPES:
                raise ValueError(f'unknown edge type {edge_type!r}')

            has_dots = ".." in matched_text
            min_hops = int(min_h) if min_h else 1
            max_hops = int(max_h) if max_h else (min_hops if (min_h and not has_dots) else (4 if has_dots else 1))

            edges.append({
                "var": var,
                "type": edge_type,
                "forward": is_forward,
                "backward": is_backward,
                "undirected": not is_forward and not is_backward,
                "min_hops": min_hops,
                "max_hops": max_hops,
            })
            cur = cur[m_e.end():].strip()

            m_n2 = self._NODE_RE.match(cur)
            if not m_n2:
                raise ValueError(f'expected node pattern after edge at: {cur}')
            nodes.append({"var": m_n2.group(1), "tag": m_n2.group(2)})
            cur = cur[m_n2.end():].strip()

        returns = [r.strip() for r in return_str.split(",")]
        node_vars = {n["var"]: i for i, n in enumerate(nodes) if n["var"]}
        for expr in returns:
            m_id = re.match(r'^id\(\s*(\w+)\s*\)$', expr)
            if m_id:
                vname = m_id.group(1)
                if vname not in node_vars:
                    raise ValueError(f'unknown variable in RETURN: {vname}')
            elif expr == path_var:
                pass
            elif expr in node_vars:
                pass
            else:
                raise ValueError(f'unsupported RETURN expression {expr!r}')

        def get_neighbors(u: str, edge_spec):
            etype = edge_spec["type"]
            fwd = edge_spec["forward"]
            bwd = edge_spec["backward"]
            und = edge_spec["undirected"]
            neighs = set()
            for e in self.state.edges:
                if etype is not None and e.edge_type != etype:
                    continue
                if (fwd or und) and e.src == u:
                    neighs.add(e.dst)
                if (bwd or und) and e.dst == u:
                    neighs.add(e.src)
            return neighs

        def traverse_edge(u: str, edge_spec):
            min_h = edge_spec["min_hops"]
            max_h = edge_spec["max_hops"]
            res = []
            queue = [(u, [u])]
            while queue:
                curr, path = queue.pop(0)
                depth = len(path) - 1
                if min_h <= depth <= max_h and depth > 0:
                    res.append((curr, path))
                if depth < max_h:
                    for nxt in get_neighbors(curr, edge_spec):
                        if nxt not in path:
                            queue.append((nxt, path + [nxt]))
            return res

        matches: list[tuple[list[str], list[str]]] = []
        tag0 = nodes[0]["tag"]
        var0 = nodes[0]["var"]
        start_nodes = [
            vid for vid, vtype in self.state.vertices.items()
            if (not tag0 or vtype == tag0) and (not var0 or var0 not in where_conds or where_conds[var0] == vid)
        ]

        def search_step(step: int, current_nodes: list[str], full_path: list[str]):
            if step == len(edges):
                matches.append((current_nodes, full_path))
                return
            u = current_nodes[-1]
            edge_spec = edges[step]
            next_node = nodes[step + 1]
            next_tag = next_node["tag"]
            next_var = next_node["var"]

            for end_v, subpath in traverse_edge(u, edge_spec):
                if next_tag and self.state.vertices.get(end_v) != next_tag:
                    continue
                if next_var and next_var in where_conds and where_conds[next_var] != end_v:
                    continue
                search_step(step + 1, current_nodes + [end_v], full_path + subpath[1:])

        for s in start_nodes:
            search_step(0, [s], [s])

        rows: list[dict] = []
        for node_list, p in matches:
            var_to_id = {}
            for i, n in enumerate(nodes):
                if n["var"]:
                    var_to_id[n["var"]] = node_list[i]
                    var_to_id[f"id({n['var']})"] = node_list[i]
            row = {}
            for expr in returns:
                if expr == path_var:
                    row[expr] = p
                elif expr in var_to_id:
                    row[expr] = var_to_id[expr]
                else:
                    m_id = re.match(r'^id\(\s*(\w+)\s*\)$', expr)
                    if m_id and m_id.group(1) in var_to_id:
                        row[expr] = var_to_id[m_id.group(1)]
            rows.append(row)
        return rows

    def linked_contracts(self, contract_id: str) -> set[str]:
        return set(self.state.neighbors(contract_id, "references"))

    def clause_count(self, contract_id: str) -> int:
        return len(self.state.neighbors(contract_id, "contains"))


SCHEMA_STATEMENTS = [
    f"CREATE SPACE IF NOT EXISTS {settings.nebula_space} "
    "(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(128));",
    "CREATE TAG IF NOT EXISTS contract(contract_id string, jurisdiction string);",
    "CREATE TAG IF NOT EXISTS clause(clause_type string, text string, confidence double);",
    "CREATE TAG IF NOT EXISTS legal_entity(name string);",
    *[f"CREATE EDGE IF NOT EXISTS {name}();" for name in EDGE_TYPES],
]


def ngql_str(value: object) -> str:
    """Escapes a value for use inside a double-quoted nGQL string literal.

    Contract text, IDs and jurisdictions are untrusted input; interpolating them raw
    would let a crafted clause break out of the string and inject statements.
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


class NebulaGraphClient(InMemoryGraph):
    """NebulaGraph-backed client.

    Inherits the auto-linking and relationship logic and overrides only transport, so
    graph semantics cannot drift between the tested in-memory path and the deployed one.
    NOT yet run against a live cluster (see README, "What has and has not been run live").
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        super().__init__()
        self.host = host or settings.nebula_host
        self.port = port or settings.nebula_port
        self._session = None

    def connect(self):  # pragma: no cover - requires a running cluster
        import os
        import time

        from nebula3.Config import Config
        from nebula3.gclient.net import ConnectionPool

        config = Config()
        config.timeout = int(settings.graph_query_timeout_seconds * 1000)  # Req 10.3: 3s
        pool = ConnectionPool()
        if not pool.init([(self.host, self.port)], config):
            raise ConnectionError(f"cannot reach NebulaGraph at {self.host}:{self.port}")
        self._session = pool.get_session(settings.nebula_user, settings.nebula_password)

        # A raw docker-compose cluster has no storage hosts registered, so CREATE SPACE
        # fails until storaged is added. Already-registered is not an error.
        storaged = os.getenv("NEBULA_STORAGED_HOST")
        if storaged:
            self._session.execute(f'ADD HOSTS "{storaged}":{os.getenv("NEBULA_STORAGED_PORT", "9779")};')
            time.sleep(10)  # hosts become ONLINE on the next metad heartbeat

        for _ in range(30):
            if self._session.execute(SCHEMA_STATEMENTS[0]).is_succeeded():
                break
            time.sleep(2)
        else:
            raise ConnectionError("could not create the NebulaGraph space (are storage hosts online?)")
        # A new space is not visible to graphd until the next heartbeat, so USE retries.
        for _ in range(30):
            if self._session.execute(f"USE {settings.nebula_space};").is_succeeded():
                break
            time.sleep(1)
        else:
            raise ConnectionError(f"NebulaGraph space {settings.nebula_space!r} never became ready")
        for statement in SCHEMA_STATEMENTS[1:]:
            self._session.execute(statement)
        time.sleep(11)  # tags/edges likewise need a heartbeat before they accept writes
        return self._session

    def _exec(self, statement: str):  # pragma: no cover - requires a running cluster
        if self._session is None:
            self.connect()
        result = self._session.execute(statement)
        if not result.is_succeeded():
            raise RuntimeError(f"nGQL failed: {result.error_msg()} :: {statement[:120]}")
        return result

    def query(self, statement: str):  # pragma: no cover - requires a running cluster
        """Runs an openCypher/nGQL statement (Req 10.4) and returns rows as dicts."""
        return self._exec(statement).as_primitive()

    def find_paths(self, src, dst, max_depth=4, edge_types=None):  # pragma: no cover
        over = ", ".join(edge_types) if edge_types else "*"
        result = self._exec(
            f'FIND ALL PATH FROM "{ngql_str(src)}" TO "{ngql_str(dst)}" OVER {over} '
            f"UPTO {int(max_depth)} STEPS YIELD path AS p;"
        )
        return [list(row.values()) for row in result.as_primitive()]

    def add_contract(self, parsed: ParsedContract) -> dict[str, int]:  # pragma: no cover
        """Writes graph nodes and edges to NebulaGraph."""
        stats = super().add_contract(parsed)

        contract_id = ngql_str(parsed.contract_id)
        jurisdiction = ngql_str(parsed.jurisdiction or "Unknown")
        self._exec(
            f'INSERT VERTEX contract(contract_id, jurisdiction) '
            f'VALUES "{contract_id}":("{contract_id}", "{jurisdiction}");'
        )

        for i, clause in enumerate(parsed.clauses):
            clause_id = f"{contract_id}::clause::{i}"
            self._exec(
                f'INSERT VERTEX clause(clause_type, text, confidence) VALUES '
                f'"{clause_id}":("{clause.clause_type.value}", "{ngql_str(clause.text)}", '
                f'{float(clause.confidence)});'
            )
            self._exec(f'INSERT EDGE contains() VALUES "{contract_id}"->"{clause_id}":();')

        # depends_on / conflicts_with between this contract's clauses (Req 10.2).
        for src, dst, edge_type in self._last_clause_edges:
            self._exec(
                f'INSERT EDGE {edge_type}() VALUES "{ngql_str(src)}"->"{ngql_str(dst)}":();'
            )

        for entity in parsed.entities:
            entity_id = f"entity::{ngql_str(entity)}"
            self._exec(f'INSERT VERTEX legal_entity(name) VALUES "{entity_id}":("{ngql_str(entity)}");')
            self._exec(f'INSERT EDGE party_to() VALUES "{contract_id}"->"{entity_id}":();')

            for other in self._entity_index.get(entity, []):
                if other != parsed.contract_id:
                    safe_other = ngql_str(other)
                    self._exec(f'INSERT EDGE references() VALUES "{contract_id}"->"{safe_other}":();')
                    self._exec(f'INSERT EDGE references() VALUES "{safe_other}"->"{contract_id}":();')

        for target in self._last_superseded:
            self._exec(f'INSERT EDGE supersedes() VALUES "{contract_id}"->"{ngql_str(target)}":();')

        return stats
