"""
Intersection upgrade controllers: TrafficLight and Roundabout. Both are
reusable controllers attached to a tile rather than special-cased logic
baked into RoadTile/RoadNetwork — tuning either later is a parameter
change, not a rewrite. Depends on progression.py only for the inventory
item type / consuming stock on placement; has no dependency on Car.
"""

import pygame as pg

from progression import ItemType
from grid import Direction

# ---------------------------------------------------------------------------
# Traffic lights
# ---------------------------------------------------------------------------

class TrafficLightPhase:
    """One step of a traffic light's cycle: which incoming directions are
    allowed to proceed, and how long the phase lasts."""

    def __init__(self, open_directions, duration):
        self.open_directions = set(open_directions)
        self.duration = duration


DEFAULT_PHASE_DURATION = 3.0

class TrafficLight:
    """A dedicated controller attached to a single intersection RoadTile.
    Simulation (update) and rendering (TrafficLightRenderer, below) are
    fully separate — this class has no drawing code at all.

    Extensibility:
        - Custom cycle lengths / phase counts: pass a custom `phases` list.
        - Adaptive timing: call `force_phase()` from an external system
          that inspects approaching traffic before each `update()`.
        - Pedestrian phases: add a phase with an empty `open_directions`
          (all traffic waits) — no core logic changes needed.
        - Synchronized corridors: build multiple lights with matching
          phase_duration and offset their initial `timer` to stay in sync.
    """
    def __init__(self, tile, phases=None, phase_duration=DEFAULT_PHASE_DURATION):
        self.tile = tile
        self._phase_duration = phase_duration
        self._last_version = tile.connectivity_version
        self.phases = phases if phases is not None else self._default_phases(
            tile.connected_directions(), phase_duration)
        self.phase_index = 0
        self.timer = self.phases[0].duration if self.phases else 0.0

    def refresh_for_connections(self):
        """Rebuilds phases only when the tile's connections have actually
        changed (a road was added/removed touching this intersection),
        detected via a cheap integer version check rather than rebuilding
        or comparing a set every frame for every light on the board."""
        if self.tile.connectivity_version == self._last_version:
            return
        self._last_version = self.tile.connectivity_version
        self.phases = self._default_phases(self.tile.connected_directions(), self._phase_duration)
        self.phase_index = 0
        self.timer = self.phases[0].duration if self.phases else 0.0

    @staticmethod
    def _default_phases(directions, duration):
        """Mirrors the real game: a straight-through pair (e.g. N+S) is
        one continuous flow and shares a single phase. Only a leg with
        no opposite partner present (a true dead-end branch into the
        intersection) gets its own solo phase."""
        if len(directions) < 3:
            return []
        dirs = set(directions)
        handled = set()
        phases = []
        for d in directions:
            if d in handled:
                continue
            opp = Direction.OPPOSITE[d]
            if opp in dirs:
                phases.append(TrafficLightPhase([d, opp], duration))
                handled.add(d)
                handled.add(opp)
            else:
                phases.append(TrafficLightPhase([d], duration))
                handled.add(d)
        return phases

    def update(self, dt):
        if not self.phases:
            return
        self.timer -= dt
        if self.timer <= 0:
            self.phase_index = (self.phase_index + 1) % len(self.phases)
            self.timer += self.phases[self.phase_index].duration

    def force_phase(self, index):
        if self.phases:
            self.phase_index = index % len(self.phases)
            self.timer = self.phases[self.phase_index].duration

    def can_enter_from(self, direction):
        if not self.phases:
            return True
        return direction in self.phases[self.phase_index].open_directions

    @property
    def current_phase(self):
        return self.phases[self.phase_index] if self.phases else None


class TrafficLightRenderer:
    """Rendering only, no simulation logic. Draws a small colored dot per
    connected direction: green if that direction currently has right of
    way, red otherwise."""

    RADIUS = 4
    GREEN = (70, 200, 90)
    RED = (210, 60, 60)

    def __init__(self, board, direction_offsets):
        self.board = board
        self.direction_offsets = direction_offsets

    OUTLINE_COLOR = (40, 40, 40)
    OUTLINE_RADIUS_RATIO = 0.32

    def draw(self, screen, traffic_light):
        import math
        cx, cy = self.board.tile_center(traffic_light.tile.x, traffic_light.tile.y)
        half = self.board.tile_size / 2
        pg.draw.circle(screen, self.OUTLINE_COLOR, (int(cx), int(cy)),
                        int(half * self.OUTLINE_RADIUS_RATIO), 2)
        for direction in traffic_light.tile.connected_directions():
            dx, dy = self.direction_offsets[direction]
            mag = math.hypot(dx, dy) or 1
            px = cx + dx / mag * half * 0.6
            py = cy + dy / mag * half * 0.6
            color = self.GREEN if traffic_light.can_enter_from(direction) else self.RED
            pg.draw.circle(screen, color, (int(px), int(py)), self.RADIUS)


class TrafficLightManager:
    """Owns every TrafficLight: inventory-gated placement, per-frame
    updates, and lookup by tile — the single place other systems (Car,
    renderer) ask "is there a light here, and can I go?"."""

    def __init__(self, inventory):
        self.inventory = inventory
        self.lights = {}  # (x, y) -> TrafficLight

    def place(self, tile):
        pos = (tile.x, tile.y)
        if pos in self.lights:
            return None
        if not self.inventory.consume(ItemType.TRAFFIC_LIGHT):
            return None
        light = TrafficLight(tile)
        self.lights[pos] = light
        return light

    def remove(self, pos, refund=True):
        light = self.lights.pop(pos, None)
        if light is not None and refund:
            self.inventory.refund(ItemType.TRAFFIC_LIGHT)
        return light

    def get(self, pos):
        return self.lights.get(pos)

    def update(self, dt):
        for light in self.lights.values():
            light.refresh_for_connections()
            light.update(dt)
            
    def draw(self, screen, renderer):
        for light in self.lights.values():
            renderer.draw(screen, light)


# ---------------------------------------------------------------------------
# Roundabouts
# ---------------------------------------------------------------------------

class Roundabout:
    """A reusable circulation traffic-controller.

    Core rules (mirroring Mini Motorways):
        - A car outside must request entry and yield until a slot is
          free; a car already circulating always has priority.
        - Multiple cars may circulate at once, up to `capacity`.
        - A car leaves via whatever exit its route calls for; this
          controller only tracks how many cars are inside at once.

    No pygame/rendering code, and no dependency on the Car class — cars
    are tracked by opaque reference only.
    """

    DEFAULT_CAPACITY = 4

    def __init__(self, center_tile, capacity=DEFAULT_CAPACITY):
        self.center_tile = center_tile  # (x, y)
        self.capacity = capacity
        self._circulating = []

    def request_enter(self, car):
        if car in self._circulating:
            return True
        if len(self._circulating) < self.capacity:
            self._circulating.append(car)
            return True
        return False

    def exit(self, car):
        if car in self._circulating:
            self._circulating.remove(car)

    def is_circulating(self, car):
        return car in self._circulating

    @property
    def occupancy_count(self):
        return len(self._circulating)


class RoundaboutManager:
    """Owns every Roundabout: inventory-gated placement and lookup by
    tile position. Mirrors TrafficLightManager's shape on purpose."""

    def __init__(self, inventory):
        self.inventory = inventory
        self.roundabouts = {}  # (x, y) -> Roundabout

    def place(self, tile_pos, capacity=Roundabout.DEFAULT_CAPACITY):
        if tile_pos in self.roundabouts:
            return None
        if not self.inventory.consume(ItemType.ROUNDABOUT):
            return None
        roundabout = Roundabout(tile_pos, capacity=capacity)
        self.roundabouts[tile_pos] = roundabout
        return roundabout

    def remove(self, tile_pos, refund=True):
        roundabout = self.roundabouts.pop(tile_pos, None)
        if roundabout is not None and refund:
            self.inventory.refund(ItemType.ROUNDABOUT)
        return roundabout

    def get(self, tile_pos):
        return self.roundabouts.get(tile_pos)
