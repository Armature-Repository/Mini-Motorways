"""
GameContext bundles the handful of shared, mutable pieces of state that
several systems (mainly Car) need to read or update, so they can be
passed around explicitly instead of reached for as module-level globals.
This is the one part of the refactor that's purely about code hygiene —
none of it is gameplay logic.
"""


class Scoreboard:
    """A one-int object instead of a bare module-level `score` variable,
    so anything holding a reference to it sees updates immediately
    without needing `global`."""

    def __init__(self):
        self.value = 0

    def add(self, amount=1):
        self.value += amount


class GameContext:
    """Everything a Car (or other simulation entity) needs to interact
    with the wider world: the board/grid, the road graph, per-tile
    occupancy for intersection locking, the upgrade controllers, and the
    scoreboard/demand system for resolving a delivery."""

    def __init__(self, board, road_network, demand_system,
                 traffic_light_manager=None, roundabout_manager=None,
                 motorway_system=None, scoreboard=None):
        self.board = board
        self.road_network = road_network
        self.demand_system = demand_system
        self.traffic_light_manager = traffic_light_manager
        self.roundabout_manager = roundabout_manager
        self.motorway_system = motorway_system
        self.scoreboard = scoreboard if scoreboard is not None else Scoreboard()

        # (x, y) -> Car currently sitting in or transiting into that tile.
        # This is the collision/yielding mechanism at intersections: a car
        # may only move into a locked tile that's not in here (or that it
        # already owns) — see Car._tile_needs_lock in cars.py.
        self.tile_occupants = {}
