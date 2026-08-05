"""
Every widget that draws to the screen but isn't a board/road/car/building:
the bottom toolbar, the weekly-upgrade modal, the sandbox toggle, and the
small HUD overlays (score, clock, pause/game-over banners). Depends on
progression.py for the model it reads; has no gameplay logic of its own.
"""

import math
import pygame as pg


# ---------------------------------------------------------------------------
# Toolbar
# ---------------------------------------------------------------------------

class Toolbar:
    """Bottom placement toolbar. Reads whatever is registered in the
    Inventory, so a newly-registered placeable type appears automatically
    with zero bespoke UI code."""

    SLOT_SIZE = 64
    PADDING = 12

    def __init__(self, inventory, screen_width, screen_height):
        self.inventory = inventory
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active_tool = None
        self._icon_font = pg.font.SysFont(None, 30)
        self._qty_font = pg.font.SysFont(None, 20)

    def _slot_rects(self):
        items = self.inventory.ordered_items()
        if not items:
            return []
        total_width = len(items) * self.SLOT_SIZE + (len(items) - 1) * self.PADDING
        start_x = (self.screen_width - total_width) // 2
        y = self.screen_height - self.SLOT_SIZE - self.PADDING
        rects = []
        for i, item in enumerate(items):
            x = start_x + i * (self.SLOT_SIZE + self.PADDING)
            rects.append((item, pg.Rect(x, y, self.SLOT_SIZE, self.SLOT_SIZE)))
        return rects

    def handle_click(self, pos):
        """Returns True (and sets active_tool) if the click hit a slot."""
        for item, rect in self._slot_rects():
            if rect.collidepoint(pos):
                self.active_tool = item.item_type
                return True
        return False

    def draw(self, screen):
        for item, rect in self._slot_rects():
            selected = item.item_type == self.active_tool
            bg_color = (255, 235, 170) if selected else (255, 255, 255)
            pg.draw.rect(screen, bg_color, rect, border_radius=10)
            pg.draw.rect(screen, (50, 50, 50), rect, 2, border_radius=10)

            if item.icon is not None:
                icon_rect = item.icon.get_rect(center=(rect.centerx, rect.centery - 8))
                screen.blit(item.icon, icon_rect)
            else:
                label = self._icon_font.render(item.item_type[:2].upper(), True, (40, 40, 40))
                screen.blit(label, label.get_rect(center=(rect.centerx, rect.centery - 8)))

            qty_text = "\u221e" if item.unlimited else str(item.quantity)
            qty_label = self._qty_font.render(qty_text, True, (30, 30, 30))
            screen.blit(qty_label, qty_label.get_rect(center=(rect.centerx, rect.bottom - 12)))


# ---------------------------------------------------------------------------
# Weekly upgrade modal + sandbox toggle
# ---------------------------------------------------------------------------

class UpgradeChoiceUI:
    """Reusable "choose one of N cards" modal. Not hardwired to the
    weekly-upgrade pool specifically — any list of objects exposing
    `.label` can be shown, so future one-off reward events can reuse
    this same widget."""

    CARD_WIDTH = 300
    CARD_HEIGHT = 180
    GAP = 40

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._title_font = pg.font.SysFont(None, 42)
        self._body_font = pg.font.SysFont(None, 26)

    def _card_rects(self, choices):
        total_w = len(choices) * self.CARD_WIDTH + (len(choices) - 1) * self.GAP
        start_x = (self.screen_width - total_w) // 2
        y = (self.screen_height - self.CARD_HEIGHT) // 2
        return [pg.Rect(start_x + i * (self.CARD_WIDTH + self.GAP), y,
                         self.CARD_WIDTH, self.CARD_HEIGHT)
                for i in range(len(choices))]

    def handle_click(self, pos, choices, on_choose):
        for rect, option in zip(self._card_rects(choices), choices):
            if rect.collidepoint(pos):
                on_choose(option)
                return True
        return False

    def draw(self, screen, choices, title="Weekly Upgrade \u2014 choose one"):
        overlay = pg.Surface((self.screen_width, self.screen_height), pg.SRCALPHA)
        overlay.fill((0, 0, 0, 165))
        screen.blit(overlay, (0, 0))

        title_label = self._title_font.render(title, True, (255, 255, 255))
        screen.blit(title_label, title_label.get_rect(center=(self.screen_width // 2, 130)))

        for rect, option in zip(self._card_rects(choices), choices):
            pg.draw.rect(screen, (250, 250, 250), rect, border_radius=16)
            pg.draw.rect(screen, (40, 40, 40), rect, 2, border_radius=16)
            for i, line in enumerate(option.label.split(" + ")):
                line_label = self._body_font.render(line, True, (30, 30, 30))
                screen.blit(line_label, line_label.get_rect(
                    center=(rect.centerx, rect.centery - 14 + i * 30)))


class SandboxToggleButton:
    """Obvious, always-visible "Sandbox / OP Mode" toggle button."""

    def __init__(self, mode_manager, rect):
        self.mode_manager = mode_manager
        self.rect = rect
        self._font = pg.font.SysFont(None, 22)

    def handle_click(self, pos):
        if self.rect.collidepoint(pos):
            self.mode_manager.toggle_sandbox()
            return True
        return False

    def draw(self, screen):
        sandbox = self.mode_manager.is_sandbox
        bg = (250, 205, 80) if sandbox else (225, 225, 225)
        pg.draw.rect(screen, bg, self.rect, border_radius=8)
        pg.draw.rect(screen, (40, 40, 40), self.rect, 2, border_radius=8)
        text = "Sandbox / OP Mode: ON" if sandbox else "Sandbox / OP Mode: OFF"
        label = self._font.render(text, True, (20, 20, 20))
        screen.blit(label, label.get_rect(center=self.rect.center))


class SpeedToggleButton:
    """Toggles the simulation between 1x and 2x speed. Only affects how
    fast game-logic dt advances — real-time event handling and rendering
    are untouched, so input still feels responsive at 2x."""

    def __init__(self, rect):
        self.rect = rect
        self.multiplier = 1
        self._font = pg.font.SysFont(None, 22)

    def handle_click(self, pos):
        if self.rect.collidepoint(pos):
            self.multiplier = 2 if self.multiplier == 1 else 1
            return True
        return False

    def draw(self, screen):
        active = self.multiplier == 2
        bg = (140, 200, 250) if active else (225, 225, 225)
        pg.draw.rect(screen, bg, self.rect, border_radius=8)
        pg.draw.rect(screen, (40, 40, 40), self.rect, 2, border_radius=8)
        text = f"{self.multiplier}x Speed"
        label = self._font.render(text, True, (20, 20, 20))
        screen.blit(label, label.get_rect(center=self.rect.center))

# ---------------------------------------------------------------------------
# Small HUD overlays
# ---------------------------------------------------------------------------

PIN_COLOR = (250, 250, 250)
PIN_OUTLINE = (40, 40, 40)
PIN_TIMER_BG = (60, 60, 60)
PIN_TIMER_FILL = (220, 60, 60)


def draw_shop_demand(screen, shop, board):
    """Draws the shop's pending pins as a tight vertical stack over the
    shop, plus a filling red arc once its timeout timer is active."""
    if shop.pending_pings <= 0 and shop.timeout_timer <= 0:
        return

    rects = [board.tile_rect(tx, ty) for tx, ty in shop.color_tiles]
    bound = rects[0].unionall(rects[1:])
    base_x, base_y = bound.centerx, bound.centery + 8

    pin_radius = 7
    stack_spacing = 6
    count = min(shop.pending_pings, 6)

    for i in range(count):
        py = base_y - i * stack_spacing
        pg.draw.circle(screen, PIN_COLOR, (int(base_x), int(py)), pin_radius)
        pg.draw.circle(screen, PIN_OUTLINE, (int(base_x), int(py)), pin_radius, 1)

    if shop.timeout_timer > 0:
        stack_top = base_y - max(count - 1, 0) * stack_spacing
        timer_center = (int(base_x), int(stack_top - 18))
        timer_radius = 10
        pg.draw.circle(screen, PIN_TIMER_BG, timer_center, timer_radius)
        start_angle = -math.pi / 2
        end_angle = start_angle + shop.timeout_timer * 2 * math.pi
        rect = pg.Rect(timer_center[0] - timer_radius, timer_center[1] - timer_radius,
                        timer_radius * 2, timer_radius * 2)
        if shop.timeout_timer > 0.01:
            pg.draw.arc(screen, PIN_TIMER_FILL, rect, -end_angle, -start_angle, width=4)


def draw_score(screen, score, pos):
    font = pg.font.SysFont(None, 40)
    label = font.render(f"Score: {score}", True, (40, 40, 40))
    screen.blit(label, pos)


def draw_game_over(screen, width, height):
    overlay = pg.Surface((width, height), pg.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    screen.blit(overlay, (0, 0))
    font = pg.font.SysFont(None, 96)
    label = font.render("GAME OVER", True, (255, 255, 255))
    rect = label.get_rect(center=(width // 2, height // 2))
    screen.blit(label, rect)


def draw_paused_banner(screen, width):
    """Small non-blocking 'PAUSED' banner — the player still needs to see
    and interact with the board while paused."""
    font = pg.font.SysFont(None, 48)
    label = font.render("PAUSED", True, (255, 255, 255))
    padding_x, padding_y = 18, 8
    bg_rect = pg.Rect(0, 0, label.get_width() + padding_x * 2, label.get_height() + padding_y * 2)
    bg_rect.midtop = (width // 2, 16)
    bg = pg.Surface(bg_rect.size, pg.SRCALPHA)
    bg.fill((0, 0, 0, 170))
    screen.blit(bg, bg_rect.topleft)
    label_rect = label.get_rect(center=bg_rect.center)
    screen.blit(label, label_rect)


def draw_clock_widget(screen, game_clock, center, radius):
    """Minimal ring-fills-clockwise-over-the-day widget with the day
    abbreviation in the middle."""
    BG_COLOR = (250, 250, 250)
    RING_BG_COLOR = (210, 210, 210)
    RING_FILL_COLOR = (90, 160, 90)
    TEXT_COLOR = (40, 40, 40)

    pg.draw.circle(screen, BG_COLOR, center, radius)
    pg.draw.circle(screen, RING_BG_COLOR, center, radius, width=4)

    start_angle = -math.pi / 2
    end_angle = start_angle + game_clock.day_progress * 2 * math.pi
    if game_clock.day_progress > 0:
        rect = pg.Rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
        pg.draw.arc(screen, RING_FILL_COLOR, rect, -end_angle, -start_angle, width=6)

    font = pg.font.SysFont(None, max(18, radius // 2))
    label = font.render(game_clock.day_label, True, TEXT_COLOR)
    label_rect = label.get_rect(center=center)
    screen.blit(label, label_rect)
