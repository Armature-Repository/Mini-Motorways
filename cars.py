"""
Car: a single vehicle's round trip (house -> shop -> parked -> home).
Dispatcher: matches shop demand to houses with spare car capacity.

Both take an explicit GameContext instead of reaching for module-level
globals, which is what makes this file testable/importable on its own.
"""

import math
import pygame as pg

from grid import Direction
from roads import Pathfinder


class Dispatcher:
    """Matches shop demand ('pings') to houses with spare car capacity.

    For a shop needing N cars: find every house that can currently reach
    it, ranked by travel time (why Pathfinder is weighted). Pull cars
    from the fastest house first; if it can't fully cover the need, pull
    the remainder from the next-fastest, and so on.
    """

    def __init__(self, road_network, houses, motorway_system=None):
        self.pathfinder = Pathfinder(road_network, motorway_system=motorway_system)
        self.houses = houses

    def _cost_and_path_to_shop(self, house, shop):
        best_cost, best_path = None, None
        for entrance in shop.entrance_tiles:
            cost, path = self.pathfinder.shortest_path_cost(house.entrance_tile, entrance)
            if cost is not None and (best_cost is None or cost < best_cost):
                best_cost, best_path = cost, path
        return best_cost, best_path

    def fulfill_demand(self, shop, num_needed=None):
        """Returns a list of (house, cars_taken, path) triples, and
        updates shop.pending_pings bookkeeping is left to the caller —
        this only decides who's dispatched, not what's delivered."""
        if num_needed is None:
            num_needed = getattr(shop, 'pending_pings', 0)

        reachable = []
        for house in self.houses:
            if shop.color_id is not None and house.color_id != shop.color_id:
                continue
            cost, path = self._cost_and_path_to_shop(house, shop)
            if cost is not None:
                reachable.append((cost, house, path))
        reachable.sort(key=lambda item: item[0])

        remaining = num_needed
        dispatched = []
        for _cost, house, path in reachable:
            if remaining <= 0:
                break
            take = min(house.available_cars(), remaining)
            if take > 0:
                house.dispatch(take)
                dispatched.append((house, take, path))
                remaining -= take

        return dispatched


class Car:
    """A single car's round trip: house -> shop's entrance -> a parked
    spot at the shop -> back to the entrance -> back home.

    Two movement modes:
    - On the road, movement is tile-based, with tile-locking enforced
      only at genuine intersections (3+ connections) — matching how the
      real game doesn't even treat a driveway joining a road as an
      intersection.
    - Off the road (entering/exiting a parking spot), the car just
      drives in a straight line — parking pads are tiny and not part of
      the shared road graph, so full tile-locking isn't needed there.

    Roundabout and traffic-light integration point: when the tile a car
    is about to enter has a roundabout or traffic light registered in
    the GameContext, that controller is consulted instead of (or in
    addition to) the plain tile_occupants check — see
    `_can_enter_next_tile`.
    """

    SPEED = 420.0  # pixels per second
    PARK_SECONDS = 2.5

    STATE_TO_SHOP = 'to_shop'
    STATE_ENTER_PARK = 'enter_park'
    STATE_PARKED = 'parked'
    STATE_EXIT_PARK = 'exit_park'
    STATE_RETURNING = 'returning'

    INTERSECTION_SPEED_FACTOR = 0.55

    def __init__(self, house, shop, tile_path, color, context):
        self.house = house
        self.shop = shop
        self.color = color
        self.context = context
        board = context.board
        road_network = context.road_network

        self.tile_path = tile_path
        self.outbound_route = tile_path
        self.inbound_route = list(reversed(tile_path))
        self.route = self.outbound_route
        self.route_index = 0

        self._locks = {}
        self._chokepoint_tiles = {house.entrance_tile, *shop.entrance_tiles}
        self.shop_entrance_tile = self.outbound_route[-1]

        self.current_tile = self.route[0]
        context.tile_occupants[self.current_tile] = self
        self._locks[self.current_tile] = ('tile', None)
        self.pos = list(board.tile_center(*self.current_tile))
        self.state = Car.STATE_TO_SHOP
        self.done = False

        self._transiting = False
        self._transit_from = None
        self._transit_to = None
        self._seg_dx = self._seg_dy = self._seg_dist = self._seg_progress = 0.0

        self.parking_tile = None
        self._park_timer = 0.0
        
    def update(self, dt):
        if self.done:
            return
        if self.state in (Car.STATE_TO_SHOP, Car.STATE_RETURNING):
            self._update_road_travel(dt)
        elif self.state == Car.STATE_ENTER_PARK:
            target = self.shop.parking_position(self.parking_tile)
            self._update_direct(dt, target, self._on_parked)
        elif self.state == Car.STATE_PARKED:
            self._park_timer -= dt
            if self._park_timer <= 0:
                self._try_begin_exit()
        elif self.state == Car.STATE_EXIT_PARK:
            board = self.context.board
            self._update_direct(dt, board.tile_center(*self.current_tile), self._on_exit_complete)

    # -- on-road movement, tile-locked only at genuine intersections ------

    def _tile_needs_lock(self, tile):
        if tile is None:
            return True
        return tile.is_intersection()

    def _segment_speed(self):
        from_tile = self.context.road_network.get_tile(*self._transit_from)
        to_tile = self.context.road_network.get_tile(*self._transit_to)
        if (from_tile is not None and from_tile.is_intersection()) or \
           (to_tile is not None and to_tile.is_intersection()):
            return self.SPEED * self.INTERSECTION_SPEED_FACTOR
        return self.SPEED

    def _direction_from_offset(self, from_pos, to_pos):
        dx = to_pos[0] - from_pos[0]
        dy = to_pos[1] - from_pos[1]
        for direction, (odx, ody) in Direction.OFFSETS.items():
            if (odx, ody) == (dx, dy):
                return direction
        return None

    def _can_enter_next_tile(self, nxt, nxt_tile):
        """Every tile — including intersections — is lane-keyed by
        direction of travel: a car only ever queues behind another car
        going the SAME way through that tile. Cross traffic and opposing
        traffic never block each other here at all; that's entirely the
        traffic light's/roundabout's job to arbitrate. The one exception
        is a chokepoint tile (a house/shop entrance), which is a literal
        single point a car enters/exits parking through and stays
        whole-tile locked regardless of direction."""
        is_intersection = nxt_tile is not None and nxt_tile.is_intersection()
        is_chokepoint = nxt in self._chokepoint_tiles

        if is_intersection:
            traffic_light_manager = self.context.traffic_light_manager
            if traffic_light_manager is not None:
                light = traffic_light_manager.get(nxt)
                if light is not None:
                    direction = self._direction_from_offset(nxt, self.current_tile)
                    if direction is None or not light.can_enter_from(direction):
                        return False

            roundabout_manager = self.context.roundabout_manager
            if roundabout_manager is not None:
                roundabout = roundabout_manager.get(nxt)
                if roundabout is not None:
                    if roundabout.request_enter(self):
                        self._locks[nxt] = ('roundabout', None)
                        return True
                    return False

        if is_chokepoint:
            if (self.state == Car.STATE_TO_SHOP and nxt == self.shop_entrance_tile
                    and self.parking_tile is None):
                self.parking_tile = self.shop.reserve_parking(nxt)
                if self.parking_tile is None:
                    return False  # lot's full — wait back on the road, not on the entrance
            occupant = self.context.tile_occupants.get(nxt)
            if occupant is not None and occupant is not self:
                return False
            self.context.tile_occupants[nxt] = self
            self._locks[nxt] = ('tile', None)
            return True
        
        travel_direction = self._direction_from_offset(self.current_tile, nxt)
        lane_key = (nxt, travel_direction)
        occupant = self.context.lane_occupants.get(lane_key)
        if occupant is not None and occupant is not self:
            return False
        self.context.lane_occupants[lane_key] = self
        self._locks[nxt] = ('lane', travel_direction)
        return True

    def _release_tile(self, tile_pos):
        lock = self._locks.pop(tile_pos, None)
        if lock is None:
            return
        lock_type, key = lock
        if lock_type == 'tile':
            if self.context.tile_occupants.get(tile_pos) is self:
                del self.context.tile_occupants[tile_pos]
        elif lock_type == 'lane':
            lane_key = (tile_pos, key)
            if self.context.lane_occupants.get(lane_key) is self:
                del self.context.lane_occupants[lane_key]
        elif lock_type == 'roundabout':
            roundabout_manager = self.context.roundabout_manager
            if roundabout_manager is not None:
                roundabout = roundabout_manager.get(tile_pos)
                if roundabout is not None:
                    roundabout.exit(self)

    def _update_road_travel(self, dt):
        board = self.context.board
        road_network = self.context.road_network

        if not self._transiting:
            if self.route_index >= len(self.route) - 1:
                self._on_route_complete()
                return
            nxt = self.route[self.route_index + 1]
            nxt_tile = road_network.get_tile(*nxt)
            if not self._can_enter_next_tile(nxt, nxt_tile):
                return  # yield and wait here

            self._transiting = True
            self._transit_from = self.route[self.route_index]
            self._transit_to = nxt
            fx, fy = board.tile_center(*self._transit_from)
            tx, ty = board.tile_center(*nxt)
            self._seg_dx, self._seg_dy = tx - fx, ty - fy
            self._seg_dist = math.hypot(self._seg_dx, self._seg_dy)
            self._seg_progress = 0.0

        self._seg_progress += self._segment_speed() * dt
        if self._seg_dist == 0 or self._seg_progress >= self._seg_dist:
            self.pos = list(board.tile_center(*self._transit_to))
            self._release_tile(self._transit_from)
            self.current_tile = self._transit_to
            self.route_index += 1
            self._transiting = False
        else:
            t = self._seg_progress / self._seg_dist
            fx, fy = board.tile_center(*self._transit_from)
            self.pos = [fx + self._seg_dx * t, fy + self._seg_dy * t]

    def _on_route_complete(self):
        if self.state == Car.STATE_TO_SHOP:
            if self.parking_tile is None:
                # Only hit when the route was length 1 (house entrance ==
                # shop entrance), so _can_enter_next_tile's reservation
                # never ran. Reserve here as a fallback.
                self.parking_tile = self.shop.reserve_parking(self.current_tile)
                if self.parking_tile is None:
                    return  # lot's full — hold at the entrance and keep checking
            self._release_tile(self.current_tile)
            self.state = Car.STATE_ENTER_PARK
        elif self.state == Car.STATE_RETURNING:
            self._release_tile(self.current_tile)
            self.done = True

    # -- off-road parking legs ---------------------------------------------

    def _update_direct(self, dt, target, on_arrive):
        dx, dy = target[0] - self.pos[0], target[1] - self.pos[1]
        dist = math.hypot(dx, dy)
        step = self.SPEED * dt
        if dist <= step or dist == 0:
            self.pos = [target[0], target[1]]
            on_arrive()
        else:
            self.pos[0] += dx / dist * step
            self.pos[1] += dy / dist * step

    def _on_parked(self):
        # Score here, on parking — same as the real game resolving a
        # trip's pin the moment the car reaches its destination.
        self.context.scoreboard.add(1)
        self.context.demand_system.car_arrived(self.shop)
        self.shop.cars_en_route = max(0, self.shop.cars_en_route - 1)
        self.state = Car.STATE_PARKED
        self._park_timer = self.PARK_SECONDS

    def _try_begin_exit(self):
        entrance_tile = self.current_tile
        occupant = self.context.tile_occupants.get(entrance_tile)
        if occupant is not None and occupant is not self:
            return  # entrance's busy — stay parked a little longer
        self.context.tile_occupants[entrance_tile] = self
        self._locks[entrance_tile] = ('tile', None)
        self.shop.release_parking(self.parking_tile)
        self.state = Car.STATE_EXIT_PARK

    def _on_exit_complete(self):
        self.state = Car.STATE_RETURNING
        self.route = self.inbound_route
        self.route_index = 0
        self._transiting = False

    def draw(self, screen):
        pg.draw.circle(screen, self.color, (int(self.pos[0]), int(self.pos[1])), 6)
        pg.draw.circle(screen, (30, 30, 30), (int(self.pos[0]), int(self.pos[1])), 6, 1)
