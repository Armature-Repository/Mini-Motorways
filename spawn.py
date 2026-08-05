"""
SpawnManager: procedurally introduces new houses and shops onto the
board over time. Depends on grid.py, board.py, buildings.py.
"""

import random

from grid import Direction, TileType
from buildings import Shop, House, shop_footprint_tiles


def find_shop_spot(board, attempts=80):
    """Random-search for a free spot for a new shop (2x2 core + one
    parking side + its entrance tile). A spot is valid only if every
    tile it would occupy is currently EMPTY."""
    sides = list(Shop.SIDES)
    for _ in range(attempts):
        x = random.randint(0, board.cols - 2)
        y = random.randint(0, board.rows - 2)
        side = random.choice(sides)
        tiles = shop_footprint_tiles(x, y, side)
        if all(board.in_bounds(tx, ty) and board.occupancy.get(tx, ty) == TileType.EMPTY
               for tx, ty in tiles):
            return (x, y), side
    return None, None


def find_house_spot(board, near=None, radius=3, attempts=60):
    """Random-search for a free single tile for a new house, with a free
    neighbor to use as its entrance. If `near` is given, candidates are
    sampled within `radius` tiles of it first — produces same-color
    houses clustering together."""
    candidates = []
    if near is not None:
        nx, ny = near
        for _ in range(attempts):
            candidates.append((nx + random.randint(-radius, radius),
                                ny + random.randint(-radius, radius)))
    else:
        for _ in range(attempts):
            candidates.append((random.randint(0, board.cols - 1),
                                random.randint(0, board.rows - 1)))

    dirs = list(Direction.ALL)
    for (x, y) in candidates:
        if not board.in_bounds(x, y):
            continue
        if board.occupancy.get(x, y) != TileType.EMPTY:
            continue
        random.shuffle(dirs)
        for d in dirs:
            dx, dy = Direction.OFFSETS[d]
            ex, ey = x + dx, y + dy
            if board.in_bounds(ex, ey) and board.occupancy.get(ex, ey) == TileType.EMPTY:
                return (x, y), d
    return None, None


class SpawnManager:
    """Procedurally introduces new houses and shops over time — the
    game's core-loop escalation.

    - Colors are introduced one at a time, spaced by NEW_COLOR_INTERVAL.
    - New tier-1 shops of already-introduced colors spawn periodically.
    - Tier-1 shops can upgrade to tier-2 after they've existed long
      enough, with a random chance each check.
    - Houses cluster into several distinct groups per color rather than
      one ever-growing blob, via a handful of "cluster anchors" per
      color.
    - Right after a color gets a new shop, that color's houses are
      prioritized for the next few house-spawn attempts.
    """

    COLORS = ['red', 'blue', 'yellow', 'green']

    NEW_COLOR_INTERVAL = 25.0
    HOUSE_SPAWN_INTERVAL = 7.0
    SHOP_SPAWN_INTERVAL = 32.0

    TIER2_SPAWN_CHANCE = 0.15

    UPGRADE_CHECK_INTERVAL = 10.0
    UPGRADE_MIN_AGE = 40.0
    UPGRADE_CHANCE_PER_CHECK = 0.15

    MAX_CLUSTER_ANCHORS = 4
    NEW_CLUSTER_CHANCE = 0.3
    CLUSTER_RADIUS = 3

    BOOST_HOUSES_PER_NEW_SHOP = 3

    def __init__(self, board, road_network, houses, shops, demand_system, initial_colors=()):
        self.board = board
        self.road_network = road_network
        self.houses = houses
        self.shops = shops
        self.demand_system = demand_system
        self.introduced_colors = list(initial_colors)
        self.cluster_anchors = {color: [] for color in initial_colors}
        for house in houses:
            if house.color_id in self.cluster_anchors:
                self.cluster_anchors[house.color_id].append((house.x, house.y))
        self.pending_color_boosts = []

        self.color_timer = self.NEW_COLOR_INTERVAL
        self.house_timer = self.HOUSE_SPAWN_INTERVAL
        self.shop_timer = self.SHOP_SPAWN_INTERVAL
        self.upgrade_timer = self.UPGRADE_CHECK_INTERVAL

    def update(self, dt):
        self.color_timer -= dt
        if self.color_timer <= 0 and len(self.introduced_colors) < len(self.COLORS):
            self.color_timer = self.NEW_COLOR_INTERVAL
            self._introduce_next_color()

        self.house_timer -= dt
        if self.house_timer <= 0:
            self.house_timer = self.HOUSE_SPAWN_INTERVAL
            if self.introduced_colors:
                self._spawn_house()

        self.shop_timer -= dt
        if self.shop_timer <= 0:
            self.shop_timer = self.SHOP_SPAWN_INTERVAL
            if self.introduced_colors:
                self._spawn_shop(random.choice(self.introduced_colors))

        self.upgrade_timer -= dt
        if self.upgrade_timer <= 0:
            self.upgrade_timer = self.UPGRADE_CHECK_INTERVAL
            self._maybe_upgrade_shops()

    def _introduce_next_color(self):
        color = self.COLORS[len(self.introduced_colors)]
        self.introduced_colors.append(color)
        self.cluster_anchors.setdefault(color, [])
        self._spawn_shop(color)
        self._spawn_house(color=color)

    def _spawn_shop(self, color):
        spot, side = find_shop_spot(self.board)
        if spot is None:
            return None
        x, y = spot
        tier = 2 if random.random() < self.TIER2_SPAWN_CHANCE else 1
        shop = Shop(x, y, self.board, side, tier=tier, color_id=color)
        shop.build_connector_road(self.road_network)
        self.shops.append(shop)
        self.demand_system.add_shop(shop)
        self.pending_color_boosts.extend([color] * self.BOOST_HOUSES_PER_NEW_SHOP)
        return shop

    def _maybe_upgrade_shops(self):
        for shop in self.shops:
            if shop.tier == 1 and shop.age >= self.UPGRADE_MIN_AGE:
                if random.random() < self.UPGRADE_CHANCE_PER_CHECK:
                    shop.tier = 2

    def _spawn_house(self, color=None):
        if color is None:
            if self.pending_color_boosts:
                color = self.pending_color_boosts.pop(0)
            else:
                color = random.choice(self.introduced_colors)

        anchors = self.cluster_anchors.setdefault(color, [])
        near = None
        if anchors and random.random() >= self.NEW_CLUSTER_CHANCE:
            near = random.choice(anchors)

        spot, entrance_dir = find_house_spot(self.board, near=near, radius=self.CLUSTER_RADIUS)
        if spot is None:
            return None
        x, y = spot
        house = House(x, y, self.board, entrance_dir, color_id=color)
        house.build_entrance_road(self.road_network)
        self.houses.append(house)

        if len(anchors) < self.MAX_CLUSTER_ANCHORS:
            anchors.append((x, y))
        return house
