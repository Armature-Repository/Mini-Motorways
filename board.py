"""
The physical grid: tile sizing/layout math, pixel<->tile conversion, and
the drag-target heuristic used for both road-dragging and house rotation.
Depends only on grid.py.
"""

import math
import pygame as pg

from grid import Direction, TileType, OccupancyGrid


def compute_tile_layout(play_area, cols, rows):
    tile_size = min(play_area.width // cols, play_area.height // rows)
    grid_width = tile_size * cols
    grid_height = tile_size * rows
    offset_x = play_area.x + (play_area.width - grid_width) // 2
    offset_y = play_area.y + (play_area.height - grid_height) // 2
    return tile_size, offset_x, offset_y


class Tile:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class Board:
    # How much a diagonal neighbor's distance is discounted when picking a
    # drag target. <1.0 makes diagonals easier to reach without needing
    # pixel-perfect corner precision; 1.0 would be a plain nearest-center
    # (voronoi) hit test.
    DIAGONAL_DRAG_BIAS = 0.7
    MIN_DRAG_TRIGGER_RATIO = 0.15  # fraction of tile_size of pointer slack

    def __init__(self, cols, rows, play_area):
        self.cols = cols
        self.rows = rows
        self.play_area = play_area
        self.tile_size, self.offset_x, self.offset_y = compute_tile_layout(play_area, cols, rows)
        self.tiles = []
        self.occupancy = OccupancyGrid()

    def build(self):
        for x in range(self.cols):
            for y in range(self.rows):
                self.tiles.append(Tile(x, y))

    def in_bounds(self, x, y):
        return 0 <= x < self.cols and 0 <= y < self.rows

    def tile_rect(self, x, y):
        return pg.Rect(
            self.offset_x + x * self.tile_size,
            self.offset_y + y * self.tile_size,
            self.tile_size,
            self.tile_size)

    def tile_center(self, x, y):
        rect = self.tile_rect(x, y)
        return rect.centerx, rect.centery

    def tile_at_pixel(self, pos):
        """Pixel coords -> (x, y) grid coords, or None if outside the grid."""
        px, py = pos
        gx = (px - self.offset_x) // self.tile_size
        gy = (py - self.offset_y) // self.tile_size
        if self.in_bounds(gx, gy):
            return int(gx), int(gy)
        return None

    def drag_target_tile(self, pos, origin, skip_buildings=True):
        """Given the tile a drag currently sits on, pick the best tile
        among it and its 8 neighbors for the pointer's current position.

        Uses nearest-tile-center rather than raw floor division, with a
        bias that shrinks the effective distance to diagonal neighbors.
        This is what makes diagonal drags forgiving: you don't need to
        land the pointer exactly in the sliver nearest a corner, just
        noticeably closer to that neighbor's center than to the others.
        """
        ox, oy = origin
        candidates = [(ox, oy)]
        for odx, ody in Direction.OFFSETS.values():
            candidates.append((ox + odx, oy + ody))

        best_tile, best_dist = None, None
        for (cx, cy) in candidates:
            if not self.in_bounds(cx, cy):
                continue
            if skip_buildings and self.occupancy.get(cx, cy) == TileType.BUILDING:
                continue
            center = self.tile_center(cx, cy)
            dist = math.hypot(pos[0] - center[0], pos[1] - center[1])
            is_diagonal = (cx != ox and cy != oy)
            if is_diagonal:
                dist *= self.DIAGONAL_DRAG_BIAS
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_tile = (cx, cy)
        return best_tile

    def draw(self, screen):
        for tile in self.tiles:
            rect = self.tile_rect(tile.x, tile.y)
            pg.draw.rect(screen, (200, 200, 200), rect)
            pg.draw.rect(screen, (0, 0, 0), rect, 1)
