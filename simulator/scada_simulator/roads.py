"""Road graph built from the plan's road centrelines, with shortest paths for vehicles."""

import heapq
import math
from dataclasses import dataclass

from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import unary_union

Node = tuple[float, float]


def _key(p) -> Node:
    return round(p[0], 2), round(p[1], 2)


@dataclass(frozen=True)
class Road:
    id: str
    coords: list[Node]
    speed_limit_kmh: float


class RoadGraph:
    def __init__(self, roads: list[Road]) -> None:
        lines = [LineString(r.coords) for r in roads]
        noded = unary_union(lines)  # splits lines at every intersection
        parts = list(noded.geoms) if isinstance(noded, MultiLineString) else [noded]
        self.edges: dict[Node, dict[Node, tuple[float, float]]] = {}
        for part in parts:
            pts = [_key(c) for c in part.coords]
            limit = self._limit_at(roads, lines, part.interpolate(0.5, normalized=True))
            for a, b in zip(pts, pts[1:]):
                d = math.dist(a, b)
                self.edges.setdefault(a, {})[b] = (d, limit)
                self.edges.setdefault(b, {})[a] = (d, limit)

    @staticmethod
    def _limit_at(roads: list[Road], lines: list[LineString], p: Point) -> float:
        hits = [r.speed_limit_kmh for r, line in zip(roads, lines) if line.distance(p) < 0.5]
        return min(hits) if hits else 20.0

    @property
    def nodes(self) -> list[Node]:
        return list(self.edges)

    def nearest(self, x: float, y: float) -> Node:
        return min(self.edges, key=lambda n: math.dist(n, (x, y)))

    def path(self, start: Node, goal: Node) -> list[tuple[Node, float]]:
        """Dijkstra. Returns [(point, speed_limit_of_segment_ending_here)], starting at `start`."""
        start, goal = self.nearest(*start), self.nearest(*goal)
        dist = {start: 0.0}
        prev: dict[Node, Node] = {}
        queue = [(0.0, start)]
        while queue:
            d, node = heapq.heappop(queue)
            if node == goal:
                break
            if d > dist[node]:
                continue
            for nxt, (length, _) in self.edges[node].items():
                nd = d + length
                if nd < dist.get(nxt, math.inf):
                    dist[nxt], prev[nxt] = nd, node
                    heapq.heappush(queue, (nd, nxt))
        if goal not in dist:
            raise ValueError(f"no road from {start} to {goal}")
        chain = [goal]
        while chain[-1] != start:
            chain.append(prev[chain[-1]])
        chain.reverse()
        return [(chain[0], 0.0)] + [(b, self.edges[a][b][1]) for a, b in zip(chain, chain[1:])]
