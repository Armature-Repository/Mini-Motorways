"""
All pointer/mouse handling lives here: dragging out roads, erasing them,
rotating houses, and using whichever tool is active on the Toolbar to
place roundabouts / traffic lights / motorway pairs. Depends on grid.py,
board.py, roads.py, buildings.py, and progression.py's ItemType.
"""

import math

from grid import Direction, TileType
from progression import ItemType


class DragState:
    NONE = None
    ROAD = 'road'
    ERASE = 'erase'
    ROTATE = 'rotate'
    PLACE_CONTROL = 'place_control'   # dragging a traffic light / roundabout out of the toolbar

    def __init__(self):
        self.mode = DragState.NONE
        self.last_tile = None
        self.house = None
        self.place_item_type = None   # which control is being dragged
        self.preview_pos = None       # current pixel position, for drawing the circle
        self.preview_tile = None      # snapped candidate tile, or None if none valid nearby

    def reset(self):
        self.mode = DragState.NONE
        self.last_tile = None
        self.house = None
        self.place_item_type = None
        self.preview_pos = None
        self.preview_tile = None


class InputController:
    """Owns the DragState and every piece of context needed to interpret
    a click/drag: the board, road network, houses, inventory, and the
    upgrade managers a non-road toolbar selection places into."""

    def __init__(self, board, road_network, houses, inventory,
                 toolbar, traffic_light_manager, roundabout_manager, motorway_system):
        self.board = board
        self.road_network = road_network
        self.houses = houses
        self.inventory = inventory
        self.toolbar = toolbar
        self.traffic_light_manager = traffic_light_manager
        self.roundabout_manager = roundabout_manager
        self.motorway_system = motorway_system

        self.drag = DragState()
        self._pending_motorway_start = None  # first-click tile while placing a motorway pair

    def house_at(self, pos):
        for house in self.houses:
            if house.contains_point(pos):
                return house
        return None

    # -- road placement, inventory-gated -----------------------------------

    def _place_road_tile(self, pos):
            tile = self.road_network.get_tile(*pos)
            if tile is not None and not tile.ghosted:
                return True
            if not self.inventory.can_place(ItemType.ROAD):
                return False
            if self.road_network.create_road(*pos) is None:
                return False
            return True

    def _erase_road_tile(self, pos):
        """Right-click priority: a traffic light or roundabout sitting on
        a tile is deleted on its own first — the road underneath survives.
        Only once neither control is present does erasing fall through to
        removing the road tile itself."""
        if self.traffic_light_manager.get(pos) is not None:
            self.traffic_light_manager.remove(pos)
            return
        if self.roundabout_manager.get(pos) is not None:
            self.roundabout_manager.remove(pos)
            return
        self.road_network.remove_road(*pos)
        
    # -- non-road tool placement ---------------------------------------------

    def _try_place_active_tool(self, tile_pos):
        tool = self.toolbar.active_tool
        if tool == ItemType.ROAD or tool is None:
            return False

        if tool == ItemType.TRAFFIC_LIGHT:
            tile = self.road_network.get_tile(*tile_pos)
            if tile is not None and tile.is_intersection():
                self.traffic_light_manager.place(tile)
            return True

        if tool == ItemType.ROUNDABOUT:
            tile = self.road_network.get_tile(*tile_pos)
            if tile is not None and tile.is_intersection():
                self.roundabout_manager.place(tile_pos)
            return True

        if tool == ItemType.MOTORWAY:
            if not self.road_network.has_road(*tile_pos):
                return True
            if self._pending_motorway_start is None:
                self._pending_motorway_start = tile_pos
            else:
                self.motorway_system.add_pair(self._pending_motorway_start, tile_pos)
                self._pending_motorway_start = None
            return True

        return False

    def _is_valid_control_tile(self, tile_pos):
        tile = self.road_network.get_tile(*tile_pos)
        return tile is not None and tile.is_intersection()

    def _nearest_valid_intersection(self, pixel_pos):
        """Snap a drag's cursor position to the nearest intersection tile
        within a couple tiles' reach, so the drop doesn't need pixel-
        perfect precision — matches the real game's forgiving placement
        feel. Returns None if nothing valid is nearby."""
        origin_tile = self.board.tile_at_pixel(pixel_pos)
        if origin_tile is None:
            return None
        ox, oy = origin_tile
        SEARCH_RADIUS = 2
        best_tile, best_dist = None, None
        for dx in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1):
            for dy in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1):
                candidate = (ox + dx, oy + dy)
                if not self._is_valid_control_tile(candidate):
                    continue
                cx, cy = self.board.tile_center(*candidate)
                dist = math.hypot(cx - pixel_pos[0], cy - pixel_pos[1])
                if best_dist is None or dist < best_dist:
                    best_tile, best_dist = candidate, dist
        return best_tile
    
    # -- event handlers -------------------------------------------------------

    def handle_mouse_down(self, event):
        if event.button == 1:
            house = self.house_at(event.pos)
            if house is not None:
                self.drag.mode = DragState.ROTATE
                self.drag.house = house
                return

            if self.toolbar.handle_click(event.pos):
                tool = self.toolbar.active_tool
                if tool in (ItemType.TRAFFIC_LIGHT, ItemType.ROUNDABOUT):
                    self.drag.mode = DragState.PLACE_CONTROL
                    self.drag.place_item_type = tool
                    self.drag.preview_pos = event.pos
                    self.drag.preview_tile = self._nearest_valid_intersection(event.pos)
                return

            tile_pos = self.board.tile_at_pixel(event.pos)
            if not tile_pos:
                return
            if self.board.occupancy.get(*tile_pos) == TileType.BUILDING:
                return

            if self._try_place_active_tool(tile_pos):
                return

            self.drag.mode = DragState.ROAD
            self.drag.last_tile = tile_pos

        elif event.button == 3:
            tile_pos = self.board.tile_at_pixel(event.pos)
            self.drag.mode = DragState.ERASE
            self.drag.last_tile = tile_pos
            if tile_pos:
                self._erase_road_tile(tile_pos)

    def handle_mouse_motion(self, event):
        drag = self.drag
        if drag.mode == DragState.ROAD:
            if drag.last_tile is None:
                return
            next_tile = self.board.drag_target_tile(event.pos, drag.last_tile)
            if not next_tile or next_tile == drag.last_tile:
                return

            if not self._place_road_tile(drag.last_tile):
                drag.reset()
                return

            if not self._place_road_tile(next_tile):
                return  # target has no stock or is a building; keep dragging

            self.road_network.connect_tiles(drag.last_tile, next_tile)
            drag.last_tile = next_tile

        elif drag.mode == DragState.PLACE_CONTROL:
            drag.preview_pos = event.pos
            drag.preview_tile = self._nearest_valid_intersection(event.pos)

        elif drag.mode == DragState.ERASE:
            tile_pos = self.board.tile_at_pixel(event.pos)
            if not tile_pos or tile_pos == drag.last_tile:
                return
            self._erase_road_tile(tile_pos)
            drag.last_tile = tile_pos

        elif drag.mode == DragState.ROTATE:
            house = drag.house
            cx, cy = self.board.tile_center(house.x, house.y)
            dx, dy = event.pos[0] - cx, event.pos[1] - cy
            if math.hypot(dx, dy) < self.board.tile_size * house.MIN_ROTATE_DRAG_RATIO:
                return
            new_direction = Direction.from_vector(dx, dy)
            house.rotate_to(new_direction, self.road_network)

    def handle_mouse_up(self, event):
        if event.button == 1 and self.drag.mode == DragState.PLACE_CONTROL:
            tile_pos = self.drag.preview_tile
            if tile_pos is not None:
                if self.drag.place_item_type == ItemType.TRAFFIC_LIGHT:
                    tile = self.road_network.get_tile(*tile_pos)
                    self.traffic_light_manager.place(tile)
                elif self.drag.place_item_type == ItemType.ROUNDABOUT:
                    self.roundabout_manager.place(tile_pos)
            self.drag.reset()
            return

        if event.button in (1, 3):
            self.drag.reset()

    def drag_preview(self):
        """(pixel_pos, snapped_tile_or_None) while a control is being
        dragged out of the toolbar, else None. Used by the render loop
        to draw the floating circle and its snap indicator."""
        if self.drag.mode != DragState.PLACE_CONTROL:
            return None
        return self.drag.preview_pos, self.drag.preview_tile