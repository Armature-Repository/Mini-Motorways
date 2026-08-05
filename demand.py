"""
DemandSystem: generates and tracks per-shop demand pins. Depends on
nothing but plain shop objects (duck-typed: color_id, tier, pending_pings,
timeout_timer, age).
"""


class DemandSystem:
    """Matches the real Mini Motorways rules as closely as a text
    description allows:

    - Each color has its own recurring "pip timer". When it elapses, one
      pin is added to a shop of that color, chosen round-robin so pins
      spread across same-color shops. If the shop up next is already at
      its pin cap, the pin rolls to the next same-color shop instead.
    - The pip interval shrinks over time (an in-game "stage" ramps up).
    - Square (tier 1) buildings warn at 7 pins and cap at 10; circular
      (tier 2) buildings warn at 10 and cap higher.
    - Once a shop is over its warn threshold, a closing timer fills over
      TIMER_FILL_SECONDS; it drains once back under threshold. Filling
      completely ends the game.
    - A car arriving resolves exactly one pin and nudges the timer down.
    """

    WARN_THRESHOLD = {1: 7, 2: 10}
    MAX_PINS = {1: 10, 2: 14}
    TIMER_FILL_SECONDS = 18.0
    ARRIVAL_TIMER_RELIEF = 0.06

    BASE_PIP_INTERVAL = 9.0
    MIN_PIP_INTERVAL = 2.5
    STAGE_DURATION_SECONDS = 45.0
    STAGE_SPEEDUP = 0.2

    TIER_WEIGHT = {1: 1.0, 2: 1.9}

    def __init__(self, shops):
        self.shops_by_color = {}
        for shop in shops:
            self.shops_by_color.setdefault(shop.color_id, []).append(shop)
        self.color_pip_timer = {color: self._pip_interval(0, shop_list)
                                 for color, shop_list in self.shops_by_color.items()}
        self.round_robin_index = {color: 0 for color in self.shops_by_color}
        self.elapsed = 0.0
        self.game_over = False

    def _stage(self):
        return self.elapsed / self.STAGE_DURATION_SECONDS

    def _pip_interval(self, stage, shop_list):
        base = self.BASE_PIP_INTERVAL / (1 + stage * self.STAGE_SPEEDUP)
        weight = sum(self.TIER_WEIGHT.get(s.tier, 1.0) for s in shop_list) or 1.0
        return max(self.MIN_PIP_INTERVAL, base / weight)

    def add_shop(self, shop):
        shop_list = self.shops_by_color.setdefault(shop.color_id, [])
        shop_list.append(shop)
        if shop.color_id not in self.color_pip_timer:
            self.color_pip_timer[shop.color_id] = self._pip_interval(self._stage(), shop_list)
            self.round_robin_index[shop.color_id] = 0

    def update(self, dt):
        if self.game_over:
            return
        self.elapsed += dt
        stage = self._stage()

        for color, shop_list in self.shops_by_color.items():
            self.color_pip_timer[color] -= dt
            if self.color_pip_timer[color] <= 0:
                self.color_pip_timer[color] += self._pip_interval(stage, shop_list)
                self._add_pip(color, shop_list)

        for shop_list in self.shops_by_color.values():
            for shop in shop_list:
                shop.age += dt
                self._update_timeout_timer(shop, dt)

    def _add_pip(self, color, shop_list):
        n = len(shop_list)
        start = self.round_robin_index[color]
        for i in range(n):
            idx = (start + i) % n
            shop = shop_list[idx]
            if shop.pending_pings < self.MAX_PINS[shop.tier]:
                shop.pending_pings += 1
                self.round_robin_index[color] = (idx + 1) % n
                return

    def _update_timeout_timer(self, shop, dt):
        warn = self.WARN_THRESHOLD[shop.tier]
        if shop.pending_pings > warn:
            shop.timeout_timer = min(1.0, shop.timeout_timer + dt / self.TIMER_FILL_SECONDS)
            if shop.timeout_timer >= 1.0:
                self.game_over = True
        else:
            shop.timeout_timer = max(0.0, shop.timeout_timer - dt / self.TIMER_FILL_SECONDS)

    def car_arrived(self, shop):
        shop.pending_pings = max(0, shop.pending_pings - 1)
        shop.timeout_timer = max(0.0, shop.timeout_timer - self.ARRIVAL_TIMER_RELIEF)