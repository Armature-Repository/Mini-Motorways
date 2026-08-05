"""
Lowest-level shared vocabulary: the 8-direction system every other module
speaks, and the occupancy grid that keeps roads and buildings from ever
overlapping. Nothing in this file depends on anything else in the game —
it's the foundation everything else is built on.
"""

import math


class Direction:
    """Bitflags for the 8 connection directions. A tile's connections are
    just an int — this makes storage, comparison, and serialization trivial
    (a RoadTile's whole connection state is one number)."""
    NONE = 0
    N  = 1 << 0
    S  = 1 << 1
    E  = 1 << 2
    W  = 1 << 3
    NE = 1 << 4
    NW = 1 << 5
    SE = 1 << 6
    SW = 1 << 7

    ALL = (N, S, E, W, NE, NW, SE, SW)

    OFFSETS = {
        N:  (0, -1),
        S:  (0, 1),
        E:  (1, 0),
        W:  (-1, 0),
        NE: (1, -1),
        NW: (-1, -1),
        SE: (1, 1),
        SW: (-1, 1),
    }

    OPPOSITE = {
        N: S, S: N, E: W, W: E,
        NE: SW, SW: NE, NW: SE, SE: NW,
    }

    @staticmethod
    def from_vector(dx, dy):
        """Snap an arbitrary (dx, dy) vector to the nearest of the 8
        directions using cosine similarity. Used for house-rotation drags —
        robust to any drag angle/magnitude, no wraparound edge cases."""
        if dx == 0 and dy == 0:
            return None
        mag = math.hypot(dx, dy)
        best_dir, best_score = None, -2.0
        for direction, (odx, ody) in Direction.OFFSETS.items():
            omag = math.hypot(odx, ody)
            score = (dx * odx + dy * ody) / (mag * omag)
            if score > best_score:
                best_score = score
                best_dir = direction
        return best_dir


class TileType:
    """What (if anything) occupies a grid cell. This is the single source
    of truth both RoadNetwork and Buildings check against, so 'a road tile
    and a building tile can never be the same tile' is enforced in one
    place instead of scattered validation."""
    EMPTY = 0
    ROAD = 1
    BUILDING = 2


class OccupancyGrid:
    """Tracks what occupies every (x, y) grid cell across the whole board.
    Shared by RoadNetwork and Building so placement rules stay consistent
    as more occupant types (bridges, traffic lights, etc.) get added later."""

    def __init__(self):
        self._cells = {}  # (x, y) -> TileType

    def get(self, x, y):
        return self._cells.get((x, y), TileType.EMPTY)

    def is_empty(self, x, y):
        return self.get(x, y) == TileType.EMPTY

    def set(self, x, y, tile_type):
        self._cells[(x, y)] = tile_type

    def clear(self, x, y):
        self._cells.pop((x, y), None)
