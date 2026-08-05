# File layout

Run with: `python3 main.py`

Each file owns one concern. Dependencies only flow "downward" — nothing
low-level imports something above it, so there are no import cycles:

```
grid.py            (Direction, TileType, OccupancyGrid — no game dependencies)
  |
board.py           (tile grid / pixel math)
  |
roads.py           (road graph, pathfinding, road rendering)
  |
buildings.py        (House, Shop)
  |
progression.py      (GameMode, Inventory, WeeklyUpgradeSystem — pure logic, no pygame drawing)
  |         \
traffic_control.py  motorways.py    (both just need progression.ItemType)
  |
clock.py            demand.py       spawn.py     (independent side-branches)
  |
context.py          (GameContext — bundles shared mutable state)
  |
cars.py             (Car, Dispatcher — use context + roads + grid)
  |
ui.py               (Toolbar, upgrade modal, sandbox button, HUD overlays)
  |
input.py            (mouse handling — reads/writes everything above)
  |
main.py             (wires it all together, runs the frame loop)
```

## Why it's split this way

- **grid / board / roads / buildings** are the "world model" — the stuff
  that would exist even in a version of this game with no upgrades,
  inventory, or modes at all.
- **progression.py has no pygame code in it on purpose.** It's pure
  Python data/logic (modes, inventory counts, upgrade choices). That
  means you could write unit tests for "does Sandbox mode make inventory
  unlimited" without ever opening a window. All the *drawing* for these
  systems (the toolbar, the upgrade cards, the sandbox button) lives in
  `ui.py` instead, which imports progression.py but not vice versa.
- **traffic_control.py and motorways.py** are the new upgrade mechanics.
  Each is a self-contained controller class (`TrafficLight`,
  `Roundabout`, `MotorwaySystem`) plus a manager that handles
  inventory-gated placement — same shape for all three, so a future
  bridge/tunnel system can follow the identical pattern.
- **context.py** exists solely so `Car` doesn't need module-level
  globals for `board`, `road_network`, `score`, etc. `GameContext` is
  just a bag of references passed in explicitly — this is the one file
  that's pure code-hygiene rather than gameplay.
- **input.py** is the only file that's allowed to touch almost
  everything, because interpreting a click legitimately needs to know
  about roads, buildings, the inventory, and the active toolbar tool all
  at once. Keeping that coupling contained to one file (instead of
  scattered across drag-handling code mixed into the main loop) is what
  makes the rest of the codebase decoupled.
- **main.py** does no gameplay logic itself — `build_world()` constructs
  every system, and the frame loop just calls `.update()` / `.draw()` on
  things in order. If you ever want a second "world" (e.g. a level
  select screen, or a test harness), you call `build_world()` again
  instead of duplicating setup code.

## Extending this later

- **Bridges/tunnels**: add a new file mirroring `motorways.py`'s shape
  (a `BridgeSystem` class + `ItemType.BRIDGE`), register it in
  `Inventory`, add an `UpgradeOption` in `progression.py`, and give
  `input.py`'s `_try_place_active_tool` a new branch.
- **Save/load**: `GameContext`, `RoadNetwork.tiles`, `Inventory.items`,
  and the houses/shops lists are the only stateful things that would
  need serializing — everything else derives from them.
- **Multiple maps**: `build_world()` is already the single seam where a
  map's initial houses/shops/board size would be swapped in.
