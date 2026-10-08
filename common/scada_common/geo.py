"""WGS84 <-> local plan metres.

The plan's (0, 0) sits at (origin_lat, origin_lon); the plan's y axis is rotated
`rotation_deg` clockwise from true north. An equirectangular approximation is
accurate to centimetres over a few kilometres, far below GNSS noise.
"""

import math
from dataclasses import dataclass

M_PER_DEG_LAT = 111_132.954  # mean metres per degree of latitude


@dataclass(frozen=True)
class Georef:
    origin_lat: float
    origin_lon: float
    rotation_deg: float = 0.0

    @classmethod
    def from_layout(cls, layout: dict) -> "Georef":
        return cls(**layout["metadata"]["georef"])

    @property
    def _m_per_deg_lon(self) -> float:
        return M_PER_DEG_LAT * math.cos(math.radians(self.origin_lat))

    def to_local(self, lat: float, lon: float) -> tuple[float, float]:
        east = (lon - self.origin_lon) * self._m_per_deg_lon
        north = (lat - self.origin_lat) * M_PER_DEG_LAT
        r = math.radians(self.rotation_deg)
        return east * math.cos(r) - north * math.sin(r), east * math.sin(r) + north * math.cos(r)

    def to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        r = math.radians(self.rotation_deg)
        east = x * math.cos(r) + y * math.sin(r)
        north = -x * math.sin(r) + y * math.cos(r)
        return self.origin_lat + north / M_PER_DEG_LAT, self.origin_lon + east / self._m_per_deg_lon


def heading_deg(dx: float, dy: float) -> float:
    """Compass heading of a plan-space vector: 0 = plan north (+y), 90 = plan east (+x)."""
    return math.degrees(math.atan2(dx, dy)) % 360
