"""
Placeable destinations: House and Shop. Depends on grid.py, board.py,
and roads.py (for driveway-rendering colors and building the fixed
entrance/connector road stubs).
"""

import pygame as pg

from grid import TileType
from roads import RoadRenderer

GROUND_COLOR = (170, 190, 150)

# Shared house/shop color palette, keyed by color_id. Both House and Shop
# look themselves up here so "red house" and "red shop" always render as
# the same RGB, however many colors get added later.
COLOR_PALETTE = {
    'red': (200, 70, 70),
    'blue': (70, 110, 200),
    'yellow': (220, 190, 60),
    'green': (80, 170, 100),
}


class Building:
    color = (0, 0, 0)
    # tier 1 = square (low tier), tier 2 = circle (high tier). The real
    # game gives tier-2 buildings a bigger pin capacity/warn threshold too
    # (DemandSystem keys off this same attribute), so shape and capacity
    # stay linked through one number instead of two.
    tier = 1
    color_id = None  # e.g. 'red', 'blue' — used to match houses to shops

    def __init__(self, x, y, board, orientation):
        self.x = x
        self.y = y
        self.board = board
        self.orientation = orientation
        self.ground_tiles, self.color_tiles = self.compute_footprint(x, y, orientation)
        blocked_tiles = getattr(self, 'blocked_tiles', None)
        if blocked_tiles is None:
            blocked_tiles = self.color_tiles
        for tx, ty in blocked_tiles:
            self.board.occupancy.set(tx, ty, TileType.BUILDING)

    def compute_footprint(self, x, y, orientation):
        raise NotImplementedError

    def draw(self, screen):
        board = self.board
        for tx, ty in self.ground_tiles:
            pg.draw.rect(screen, GROUND_COLOR, board.tile_rect(tx, ty))
        if self.tier >= 2:
            rects = [board.tile_rect(tx, ty) for tx, ty in self.color_tiles]
            bound = rects[0].unionall(rects[1:])
            radius = min(bound.width, bound.height) / 2
            pg.draw.circle(screen, self.color, bound.center, radius)
        else:
            for tx, ty in self.color_tiles:
                pg.draw.rect(screen, self.color, board.tile_rect(tx, ty))

    def contains_point(self, pos):
        board = self.board
        for tx, ty in self.color_tiles:
            if board.tile_rect(tx, ty).collidepoint(pos):
                return True
        return False

    @property
    def px(self):
        cx, cy = self.board.tile_center(self.x, self.y)
        return cx

    @property
    def py(self):
        cx, cy = self.board.tile_center(self.x, self.y)
        return cy


class Shop(Building):
    """A 2x2 shop with a 2-tile parking pad on one or more sides. The
    parking pad is NOT a road and can never be connected to — the only
    way in or out is a single fixed entrance tile, perpendicular to the
    pad, rounding the whole footprint out to a clean 3x3."""

    color = (200, 80, 60)
    SIDES = ('top', 'bottom', 'left', 'right')
    OUTWARD = {
        'top': (0, -1),
        'bottom': (0, 1),
        'left': (-1, 0),
        'right': (1, 0),
    }
    ENTRANCE_PERP_OFFSET = {
        'top': (-1, 0),
        'bottom': (-1, 0),
        'left': (0, -1),
        'right': (0, -1),
    }

    def __init__(self, x, y, board, orientations, tier=1, color_id=None):
        if isinstance(orientations, str):
            orientations = [orientations]
        self.tier = tier
        self.color_id = color_id
        self.pending_pings = 0
        self.timeout_timer = 0.0
        self.cars_en_route = 0
        self.age = 0.0
        if color_id in COLOR_PALETTE:
            self.color = COLOR_PALETTE[color_id]
        super().__init__(x, y, board, orientations)

    def compute_footprint(self, x, y, orientations):
        building = [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]

        self.sides = []
        ground_tiles = list(building)
        blocked_tiles = list(building)

        for side in orientations:
            if side not in self.SIDES:
                raise ValueError(f"bad shop orientation: {side}")

            if side in ('top', 'bottom'):
                py = y - 1 if side == 'top' else y + 2
                parking = [(x, py), (x + 1, py)]
            else:
                px = x - 1 if side == 'left' else x + 2
                parking = [(px, y), (px, y + 1)]

            perp_dx, perp_dy = self.ENTRANCE_PERP_OFFSET[side]
            anchor = parking[0]
            entrance = (anchor[0] + perp_dx, anchor[1] + perp_dy)

            self.sides.append({'side': side, 'parking': parking, 'entrance': entrance})
            ground_tiles += parking + [entrance]
            blocked_tiles += parking

        self.blocked_tiles = blocked_tiles
        color_tiles = building
        self.parking_status = {tile: None for s in self.sides for tile in s['parking']}
        return ground_tiles, color_tiles

    def reserve_parking(self, entrance_tile):
        side = next((s for s in self.sides if s['entrance'] == entrance_tile), None)
        if side is None:
            return None
        for tile in side['parking']:
            if self.parking_status.get(tile) is None:
                self.parking_status[tile] = True
                return tile
        return None

    def release_parking(self, tile):
        if tile in self.parking_status:
            self.parking_status[tile] = None

    @property
    def entrance_tiles(self):
        return [s['entrance'] for s in self.sides]

    def build_connector_road(self, road_network):
        for s in self.sides:
            road_network.create_road(*s['entrance'], fixed=True)


class House(Building):
    """Single-tile building. entrance_direction is a Direction flag
    pointing at the adjacent tile where its road belongs. The entrance
    road is fixed but can be relocated by rotating the house."""
    color = (60, 120, 200)
    MIN_ROTATE_DRAG_RATIO = 0.3
    CAR_CAPACITY = 2
    SIZE_RATIO = 0.62

    def __init__(self, x, y, board, entrance_direction, color_id=None):
        self.entrance_direction = entrance_direction
        self.color_id = color_id
        if color_id in COLOR_PALETTE:
            self.color = COLOR_PALETTE[color_id]
        self.dispatched_cars = 0
        super().__init__(x, y, board, entrance_direction)

    def available_cars(self):
        return self.CAR_CAPACITY - self.dispatched_cars

    def dispatch(self, count=1):
        self.dispatched_cars = min(self.CAR_CAPACITY, self.dispatched_cars + count)

    def return_car(self, count=1):
        self.dispatched_cars = max(0, self.dispatched_cars - count)

    def compute_footprint(self, x, y, orientation):
        tile = (x, y)
        return [tile], [tile]

    def draw(self, screen):
        board = self.board
        rect = board.tile_rect(self.x, self.y)
        pg.draw.rect(screen, GROUND_COLOR, rect)
        shrink_x = rect.width * (1 - self.SIZE_RATIO)
        shrink_y = rect.height * (1 - self.SIZE_RATIO)
        house_rect = rect.inflate(-shrink_x, -shrink_y)
        pg.draw.rect(screen, self.color, house_rect,
                      border_radius=max(2, int(house_rect.width * 0.18)))

    def draw_driveway(self, screen):
        """Pavement-colored line from the house's center to the center of
        its entrance road tile. Must be called AFTER RoadRenderer.draw()
        so it draws on top of whatever the entrance tile itself rendered."""
        board = self.board
        house_center = board.tile_center(self.x, self.y)
        entrance_center = board.tile_center(*self.entrance_tile)
        road_half_width = board.tile_size * RoadRenderer.ROAD_WIDTH_RATIO / 2
        pg.draw.line(screen, RoadRenderer.ROAD_COLOR, house_center, entrance_center,
                      int(road_half_width * 2))

    @property
    def entrance_tile(self):
        from grid import Direction
        dx, dy = Direction.OFFSETS[self.entrance_direction]
        return (self.x + dx, self.y + dy)

    def has_road_access(self, road_network):
        ex, ey = self.entrance_tile
        return road_network.has_road(ex, ey)

    def build_entrance_road(self, road_network):
        road_network.create_road(*self.entrance_tile, fixed=True)

    def rotate_to(self, new_direction, road_network):
        """Move the house's fixed entrance road to point in a new
        direction. The old entrance tile is only kept (demoted from fixed
        to a normal, player-removable road) if the player actually built
        onto it; otherwise it's deleted outright rather than left behind
        as an orphaned freebie road tile."""
        from grid import Direction
        if new_direction is None or new_direction == self.entrance_direction:
            return False

        dx, dy = Direction.OFFSETS[new_direction]
        new_tile = (self.x + dx, self.y + dy)
        board = self.board
        if not board.in_bounds(*new_tile):
            return False
        if board.occupancy.get(*new_tile) == TileType.BUILDING:
            return False

        old_tile = self.entrance_tile
        old_road = road_network.get_tile(*old_tile)
        if old_road is not None:
            old_road.fixed = False
            if not old_road.connected_directions() and old_road.in_use == 0:
                road_network._remove_road_cascade(old_road)
        self.entrance_direction = new_direction
        road_network.create_road(*new_tile, fixed=True)
        return True


def shop_footprint_tiles(x, y, side):
    """Every tile a Shop(x, y, ..., side) would occupy or block, computed
    without constructing the Shop — used to test candidate spawn spots
    before committing to them. Mirrors Shop.compute_footprint exactly."""
    core = [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
    if side in ('top', 'bottom'):
        py = y - 1 if side == 'top' else y + 2
        parking = [(x, py), (x + 1, py)]
    else:
        px = x - 1 if side == 'left' else x + 2
        parking = [(px, y), (px, y + 1)]
    perp_dx, perp_dy = Shop.ENTRANCE_PERP_OFFSET[side]
    anchor = parking[0]
    entrance = (anchor[0] + perp_dx, anchor[1] + perp_dy)
    return core + parking + [entrance]
