"""
Pure game-state/logic for progression: modes, inventory, and the weekly
upgrade system. Deliberately has NO pygame drawing in it — the
corresponding widgets (Toolbar, UpgradeChoiceUI, SandboxToggleButton)
live in ui.py and read this module's state. Keeping model and view in
separate files means this file could be unit-tested with no display at
all, and a future non-graphical mode (e.g. a headless simulation) could
reuse it untouched.
"""

import random


# ---------------------------------------------------------------------------
# Game modes
# ---------------------------------------------------------------------------

class GameMode:
    NORMAL = "normal"
    SANDBOX = "sandbox"


class ModeManager:
    """Single source of truth for which mode the game is in. Anything
    that needs to react to a mode switch (Inventory, WeeklyUpgradeSystem,
    UI) registers a callback here instead of polling — this is what lets
    mode switching update everything cleanly without a restart.

    Adding a future testing mode is just a new string constant plus
    whatever listeners want to special-case it; nothing here needs to
    change.
    """

    def __init__(self, mode=GameMode.NORMAL):
        self.mode = mode
        self._listeners = []

    def on_change(self, callback):
        """Register a callback(new_mode) invoked on every mode switch."""
        self._listeners.append(callback)

    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        for cb in self._listeners:
            cb(mode)

    def toggle_sandbox(self):
        self.set_mode(GameMode.SANDBOX if self.mode != GameMode.SANDBOX else GameMode.NORMAL)

    @property
    def is_sandbox(self):
        return self.mode == GameMode.SANDBOX


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class ItemType:
    """String constants for every placeable item type. Centralized so
    both the inventory and the upgrade pool reference the same keys —
    adding a future placeable (bridge, tunnel, ...) is one new constant."""
    ROAD = "road"
    ROUNDABOUT = "roundabout"
    TRAFFIC_LIGHT = "traffic_light"
    MOTORWAY = "motorway"


class InventoryItem:
    """Tracks stock for a single placeable type. `unlimited` is a real
    flag (not a huge number) so Sandbox is an explicit state the UI can
    check directly to draw the infinity symbol."""

    def __init__(self, item_type, icon=None, quantity=0, unlimited=False):
        self.item_type = item_type
        self.icon = icon
        self.quantity = quantity
        self.unlimited = unlimited

    def can_place(self, count=1):
        return self.unlimited or self.quantity >= count

    def consume(self, count=1):
        if self.unlimited:
            return True
        if self.quantity < count:
            return False
        self.quantity -= count
        return True

    def refund(self, count=1):
        if not self.unlimited:
            self.quantity += count

    def grant(self, count):
        if not self.unlimited:
            self.quantity += count


class Inventory:
    def __init__(self, mode_manager, road_network):
        self.mode_manager = mode_manager
        self.road_network = road_network
        self.items = {}
        self.road_budget = 0
        mode_manager.on_change(self._on_mode_change)

    def register(self, item_type, icon=None, starting_quantity=0):
        if item_type == ItemType.ROAD:
            self.road_budget = starting_quantity
            self.items[item_type] = InventoryItem(
                item_type, icon=icon, quantity=0,
                unlimited=self.mode_manager.is_sandbox)
            return
        self.items[item_type] = InventoryItem(
            item_type, icon=icon, quantity=starting_quantity,
            unlimited=self.mode_manager.is_sandbox)

    def _on_mode_change(self, mode):
        sandbox = (mode == GameMode.SANDBOX)
        for item in self.items.values():
            item.unlimited = sandbox

    def _sync_roads(self):
        """Roads have no stored ground truth — quantity is always
        recomputed from the board before anything reads it. Called at
        the top of every public accessor so there's no path that can
        see a stale number, not just get()."""
        item = self.items.get(ItemType.ROAD)
        if item is not None and not item.unlimited:
            item.quantity = max(0, self.road_budget - self.road_network.placed_count())

    def get(self, item_type):
        self._sync_roads()
        return self.items.get(item_type)

    def can_place(self, item_type, count=1):
        self._sync_roads()
        item = self.items.get(item_type)
        return item is not None and item.can_place(count)

    def consume(self, item_type, count=1):
        if item_type == ItemType.ROAD:
            return True
        item = self.get(item_type)
        return item.consume(count) if item is not None else False

    def refund(self, item_type, count=1):
        if item_type == ItemType.ROAD:
            return
        item = self.get(item_type)
        if item is not None:
            item.refund(count)

    def grant(self, item_type, count):
        if item_type == ItemType.ROAD:
            if ItemType.ROAD not in self.items:
                raise KeyError(f"Inventory has no registered item type {item_type!r}")
            self.road_budget += count
            return
        item = self.get(item_type)
        if item is None:
            raise KeyError(f"Inventory has no registered item type {item_type!r}")
        item.grant(count)

    def ordered_items(self):
        self._sync_roads()
        return list(self.items.values())


# ---------------------------------------------------------------------------
# Weekly upgrades (data-driven)
# ---------------------------------------------------------------------------

class UpgradeOption:
    """One weekly-reward choice. `rewards` is a plain dict of
    item_type -> quantity, so new combinations are just new dict
    literals — never hardcoded branching logic."""

    def __init__(self, key, label, rewards):
        self.key = key
        self.label = label
        self.rewards = rewards


# The pool this ships with. Extending it (bridges, tunnels, future
# upgrade types) means appending another UpgradeOption here — nothing
# else needs to change.
UPGRADE_POOL = [
    UpgradeOption("30_roads", "30 Roads", {ItemType.ROAD: 30}),
    UpgradeOption("20_roads_roundabout", "20 Roads + 1 Roundabout",
                  {ItemType.ROAD: 20, ItemType.ROUNDABOUT: 1}),
    UpgradeOption("20_roads_traffic_light", "20 Roads + 1 Traffic Light",
                  {ItemType.ROAD: 20, ItemType.TRAFFIC_LIGHT: 1}),
    UpgradeOption("10_roads_motorway", "10 Roads + 1 Motorway",
                  {ItemType.ROAD: 10, ItemType.MOTORWAY: 1}),
]


class WeeklyUpgradeSystem:
    """Drives the Sunday-midnight upgrade-choice flow.

    Does NOT own the game's pause state directly — it only exposes
    `is_awaiting_choice`, and the caller (main loop) is responsible for
    pausing simulation systems while that's true. Keeps this class
    reusable for contexts without a "pause the whole sim" concept.
    """

    def __init__(self, inventory, mode_manager, pool=None):
        self.inventory = inventory
        self.mode_manager = mode_manager
        self.pool = pool if pool is not None else UPGRADE_POOL
        self.pending_choices = None
        self.on_upgrade_ready = None  # optional callback(choices)

    @property
    def is_awaiting_choice(self):
        return self.pending_choices is not None

    def check_week_boundary(self, prev_day_index, new_day_index):
        """Call once per frame with the GameClock's day_index before and
        after its update(). Fires exactly on the SUN(6) -> MON(0)
        transition — "every Sunday at midnight". Disabled in Sandbox."""
        if self.mode_manager.is_sandbox:
            return
        if self.is_awaiting_choice:
            return
        if prev_day_index == 6 and new_day_index == 0:
            self.trigger()

    def trigger(self):
        count = min(2, len(self.pool))
        self.pending_choices = random.sample(self.pool, count)
        if self.on_upgrade_ready:
            self.on_upgrade_ready(self.pending_choices)

    def choose(self, option):
        for item_type, qty in option.rewards.items():
            self.inventory.grant(item_type, qty)
        self.pending_choices = None
