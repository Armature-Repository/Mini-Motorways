"""
The road graph itself: RoadTile/RoadNetwork (the graph), Pathfinder
(shortest path over it), and RoadRenderer (drawing it). Depends on
grid.py and board.py only.
"""

import heapq
import math
import pygame as pg

from grid import Direction, TileType


class RoadSpeed:
    """Named speed multipliers for road tiles. NORMAL is the plain road;
    MOTORWAY tiles are meaningfully faster, which is what actually makes
    the motorway system worth building rather than just a re-skinned
    road. Every RoadTile carries a `speed`, so the pathfinder is already
    weight-aware and needed no changes to support this."""
    NORMAL = 1.0
    MOTORWAY = 2.5


class RoadTile:
    def __init__(self, x, y, fixed=False, speed=RoadSpeed.NORMAL):
        self.x = x
        self.y = y
        self.connections = Direction.NONE
        self.fixed = fixed
        self.speed = speed
        self.in_use = 0
        self.ghosted = False
        self.connectivity_version = 0   # bumped every time connections change

    def add_connection(self, direction):
        self.connections |= direction
        self.connectivity_version += 1

    def remove_connection(self, direction):
        self.connections &= ~direction
        self.connectivity_version += 1

    def has_connection(self, direction):
        return bool(self.connections & direction)

    def connected_directions(self):
        return [d for d in Direction.ALL if self.has_connection(d)]

    def is_straight_through(self):
        """True for a tile with exactly two connections that are a
        straight-line opposite pair (e.g. N+S, or NE+SW) — a plain
        pass-through segment with no turn and no merge."""
        dirs = self.connected_directions()
        if len(dirs) != 2:
            return False
        return Direction.OPPOSITE[dirs[0]] == dirs[1]

    def is_intersection(self):
        """True only for a genuine multi-way intersection (3 or more
        connections). A bare stub (1 connection), a straight run (2
        opposite), and a plain turn/corner (2 non-opposite) are all just
        a single lane passing through and never need cars to wait on
        each other there — matches the real game."""
        return len(self.connected_directions()) >= 3


class RoadNetwork:
    def __init__(self, occupancy):
        self.tiles = {}  # (x, y) -> RoadTile
        self.occupancy = occupancy

    def has_road(self, x, y):
        return (x, y) in self.tiles

    def get_tile(self, x, y):
        return self.tiles.get((x, y))
    
    def placed_count(self):
        """Number of road tiles counted against the player's budget —
        every non-fixed tile. Ghosted tiles still count (they're still
        physically on the board until the last car clears)."""
        return sum(1 for t in self.tiles.values() if not t.fixed)
    
    def create_road(self, x, y, fixed=False, speed=RoadSpeed.NORMAL):
            if not self.has_road(x, y):
                if self.occupancy.get(x, y) == TileType.BUILDING:
                    return None
                self.tiles[(x, y)] = RoadTile(x, y, speed=speed)
                self.occupancy.set(x, y, TileType.ROAD)
            tile = self.tiles[(x, y)]
            if tile.ghosted:
                # The player is redrawing over a tile that was marked for
                # deletion but is still standing (a car's mid-trip). Bring
                # it back to life instead of treating it as a fresh tile —
                # the car relying on it, and its in_use count, are untouched.
                tile.ghosted = False
            if fixed:
                tile.fixed = True
            return tile

    def connect_tiles(self, pos_a, pos_b):
        """Create an explicit bidirectional connection between two
        adjacent tiles. This is the ONLY way connections get made — the
        graph's edges are exactly what this method has been called with,
        nothing inferred from position."""
        ax, ay = pos_a
        bx, by = pos_b
        dx, dy = bx - ax, by - ay

        direction_a_to_b = self._direction_for_offset(dx, dy)
        if direction_a_to_b is None:
            raise ValueError(f"{pos_a} and {pos_b} are not adjacent")

        tile_a = self.get_tile(ax, ay)
        tile_b = self.get_tile(bx, by)
        if not tile_a or not tile_b:
            raise ValueError("both tiles must exist before connecting them")

        tile_a.add_connection(direction_a_to_b)
        tile_b.add_connection(Direction.OPPOSITE[direction_a_to_b])

    def _direction_for_offset(self, dx, dy):
        for direction, (odx, ody) in Direction.OFFSETS.items():
            if (odx, ody) == (dx, dy):
                return direction
        return None

    def remove_road(self, x, y):
            tile = self.get_tile(x, y)
            if not tile or tile.fixed:
                return None
            if tile.in_use > 0:
                tile.ghosted = True
                return []
            return self._remove_road_cascade(tile)

    def _remove_road_cascade(self, tile):
        """Remove `tile`, then keep removing outward... Returns the list
        of (x, y) positions actually deleted, so the caller can refund
        inventory for every tile the cascade ate — not just the one the
        player clicked on."""
        removed = []
        queue = [tile]
        while queue:
            t = queue.pop()
            if (t.x, t.y) not in self.tiles:
                continue
            removed.append((t.x, t.y))
            queue.extend(self._remove_road_unchecked(t))
        return removed

    def release_path(self, path):
        for (x, y) in path:
            tile = self.get_tile(x, y)
            if not tile:
                continue
            tile.in_use = max(0, tile.in_use - 1)
            if tile.in_use == 0 and tile.ghosted:
                self._remove_road_cascade(tile)   # this return value is fine to ignore — cars finishing a trip don't refund inventory

    def force_remove_road(self, x, y):
        """Bypasses the fixed check. Low-level escape hatch — never wire
        this to user input directly."""
        tile = self.get_tile(x, y)
        if tile:
            self._remove_road_unchecked(tile)

    def _remove_road_unchecked(self, tile):
        x, y = tile.x, tile.y
        self.tiles.pop((x, y), None)
        self.occupancy.clear(x, y)
        orphans = []
        for direction in tile.connected_directions():
            dx, dy = Direction.OFFSETS[direction]
            neighbor = self.get_tile(x + dx, y + dy)
            if neighbor:
                neighbor.remove_connection(Direction.OPPOSITE[direction])
                if (not neighbor.fixed and not neighbor.connected_directions()
                        and neighbor.in_use == 0):
                    orphans.append(neighbor)
        return orphans

    def mark_path_in_use(self, path):
        for (x, y) in path:
            tile = self.get_tile(x, y)
            if tile:
                tile.in_use += 1

    def neighbors_of(self, x, y):
        tile = self.get_tile(x, y)
        if not tile:
            return []
        result = []
        for direction in tile.connected_directions():
            dx, dy = Direction.OFFSETS[direction]
            neighbor = self.get_tile(x + dx, y + dy)
            if neighbor:
                result.append(neighbor)
        return result


class Pathfinder:
    """Shortest-path search over a RoadNetwork's graph, with an optional
    MotorwaySystem plugged in. Uses Dijkstra (not plain BFS) because edge
    cost isn't just "1 tile" — diagonal steps cost sqrt(2), a slower/
    faster tile changes the cost of any edge touching it, and a motorway
    pair is a same-cost-as-adjacent teleport between two possibly distant
    tiles."""

    def __init__(self, road_network, motorway_system=None):
        self.road_network = road_network
        self.motorway_system = motorway_system

    def shortest_path_cost(self, start, goal):
        """Returns (cost, path) as a list of (x, y) tiles from start to
        goal, or (None, None) if unreachable."""
        if not self.road_network.has_road(*start) or not self.road_network.has_road(*goal):
            return None, None
        if start == goal:
            return 0.0, [start]

        frontier = [(0.0, start)]
        best_cost = {start: 0.0}
        came_from = {start: None}

        while frontier:
            cost, current = heapq.heappop(frontier)
            if cost > best_cost.get(current, math.inf):
                continue
            if current == goal:
                break

            for neighbor_pos, step_cost in self._edges_from(current):
                new_cost = cost + step_cost
                if new_cost < best_cost.get(neighbor_pos, math.inf):
                    best_cost[neighbor_pos] = new_cost
                    came_from[neighbor_pos] = current
                    heapq.heappush(frontier, (new_cost, neighbor_pos))

        if goal not in came_from:
            return None, None

        path = []
        node = goal
        while node is not None:
            path.append(node)
            node = came_from[node]
        path.reverse()
        return best_cost[goal], path

    def _edges_from(self, current):
        """Yields (neighbor_pos, cost) pairs reachable from `current`:
        ordinary connected road tiles, plus — if `current` is a motorway
        endpoint — its paired endpoint at a small fixed cost (faster than
        any ordinary detour, but not literally free, so a motorway is a
        genuine shortcut rather than a magic zero-cost wormhole)."""
        tile = self.road_network.get_tile(*current)
        for direction in tile.connected_directions():
            dx, dy = Direction.OFFSETS[direction]
            neighbor_pos = (current[0] + dx, current[1] + dy)
            neighbor = self.road_network.get_tile(*neighbor_pos)
            if not neighbor or neighbor.ghosted:
                continue
            step_distance = math.hypot(dx, dy)
            step_speed = min(tile.speed, neighbor.speed)
            yield neighbor_pos, step_distance / step_speed

        if self.motorway_system is not None and self.motorway_system.is_endpoint(current):
            other_end = self.motorway_system.other_end(current)
            if other_end is not None:
                yield other_end, self.motorway_system.TRAVEL_COST


class RoadRenderer:
    ROAD_COLOR = (60, 60, 60)
    GHOST_ROAD_COLOR = (170, 170, 170)
    EDGE_COLOR = (255, 255, 255)
    ROAD_WIDTH_RATIO = 0.4
    EDGE_THICKNESS = 2

    def __init__(self, board):
        self.board = board

    def draw(self, screen, road_network):
        for tile in road_network.tiles.values():
            self._draw_tile(screen, tile)

    def _road_color(self, tile):
        return self.GHOST_ROAD_COLOR if tile.ghosted else self.ROAD_COLOR

    def _draw_tile(self, screen, tile):
        board = self.board
        cx, cy = board.tile_center(tile.x, tile.y)
        half = board.tile_size / 2
        road_half_width = board.tile_size * self.ROAD_WIDTH_RATIO / 2
        color = self._road_color(tile)

        directions = tile.connected_directions()

        if not directions:
            pg.draw.circle(screen, color, (cx, cy), road_half_width)
        elif len(directions) == 2 and not self._is_straight_through(directions):
            d1, d2 = directions
            p1 = self._edge_point(cx, cy, d1, half)
            p2 = self._edge_point(cx, cy, d2, half)
            self._draw_curve(screen, p1, (cx, cy), p2, road_half_width, color)
        else:
            for direction in directions:
                target = self._edge_point(cx, cy, direction, half)
                self._draw_segment(screen, (cx, cy), target, road_half_width, color)

        self._draw_edge_markings(screen, tile, cx, cy, half)

    def _is_straight_through(self, directions):
        d1, d2 = directions
        return Direction.OPPOSITE[d1] == d2

    def _edge_point(self, cx, cy, direction, half):
        dx, dy = Direction.OFFSETS[direction]
        return (cx + dx * half, cy + dy * half)

    def _draw_segment(self, screen, start, end, half_width, color):
        pg.draw.line(screen, color, start, end, int(half_width * 2))

    def _draw_curve(self, screen, p1, control, p2, half_width, color):
        steps = 14
        points = []
        for i in range(steps + 1):
            t = i / steps
            x = (1 - t) ** 2 * p1[0] + 2 * (1 - t) * t * control[0] + t ** 2 * p2[0]
            y = (1 - t) ** 2 * p1[1] + 2 * (1 - t) * t * control[1] + t ** 2 * p2[1]
            points.append((x, y))
        width = int(half_width * 2)
        for i in range(len(points) - 1):
            pg.draw.line(screen, color, points[i], points[i + 1], width)
        for p in points:
            pg.draw.circle(screen, color, (int(p[0]), int(p[1])), int(half_width))

    def _draw_edge_markings(self, screen, tile, cx, cy, half):
        cardinal_sides = {
            Direction.N: ((cx - half, cy - half), (cx + half, cy - half)),
            Direction.S: ((cx - half, cy + half), (cx + half, cy + half)),
            Direction.E: ((cx + half, cy - half), (cx + half, cy + half)),
            Direction.W: ((cx - half, cy - half), (cx - half, cy + half)),
        }
        for direction, (p1, p2) in cardinal_sides.items():
            if tile.has_connection(direction):
                continue
            dx, dy = Direction.OFFSETS[direction]
            if self.board.occupancy.get(tile.x + dx, tile.y + dy) == TileType.BUILDING:
                continue
            pg.draw.line(screen, self.EDGE_COLOR, p1, p2, self.EDGE_THICKNESS)
