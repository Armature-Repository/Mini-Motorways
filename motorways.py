"""
Motorways: a transportation layer independent of the standard road graph.
Depends on progression.py only, for inventory-gated placement.
"""

from progression import ItemType


class MotorwaySystem:
    """Connects two entrance tiles as a paired fast link: a car whose
    route passes through one endpoint can hop straight to the other at a
    small fixed cost (TRAVEL_COST), rather than pathing through whatever
    lies between them. This mirrors Mini Motorways, where the motorway is
    a distinct system layered on top of (not a variant of) ordinary
    roads — see Pathfinder._edges_from in roads.py for how it plugs in.

    Endpoints still need a normal road tile to connect INTO so ordinary
    cars can path onto them; travel BETWEEN endpoints is handled entirely
    here as an O(1) lookup rather than a graph walk.
    """

    # Small non-zero cost so a motorway is a genuine shortcut rather than
    # a literal zero-cost wormhole (which would make distance meaningless
    # for anything beyond "is a motorway available").
    TRAVEL_COST = 0.5

    def __init__(self, inventory, max_pairs=None):
        self.inventory = inventory
        self.max_pairs = max_pairs
        self.pairs = {}              # pair_id -> (tile_a, tile_b)
        self._endpoint_to_pair = {}  # tile_pos -> pair_id
        self._next_pair_id = 0

    def can_add_pair(self):
        if self.max_pairs is not None and len(self.pairs) >= self.max_pairs:
            return False
        return self.inventory.can_place(ItemType.MOTORWAY)

    def add_pair(self, tile_a, tile_b):
        """Consumes one unit of motorway inventory per pair placed.
        Refuses if either tile is already part of another pair."""
        if tile_a in self._endpoint_to_pair or tile_b in self._endpoint_to_pair:
            return None
        if not self.can_add_pair():
            return None
        if not self.inventory.consume(ItemType.MOTORWAY):
            return None
        pair_id = self._next_pair_id
        self._next_pair_id += 1
        self.pairs[pair_id] = (tile_a, tile_b)
        self._endpoint_to_pair[tile_a] = pair_id
        self._endpoint_to_pair[tile_b] = pair_id
        return pair_id

    def remove_pair(self, pair_id, refund=True):
        pair = self.pairs.pop(pair_id, None)
        if pair is None:
            return None
        for tile in pair:
            self._endpoint_to_pair.pop(tile, None)
        if refund:
            self.inventory.refund(ItemType.MOTORWAY)
        return pair

    def is_endpoint(self, tile_pos):
        return tile_pos in self._endpoint_to_pair

    def other_end(self, tile_pos):
        pair_id = self._endpoint_to_pair.get(tile_pos)
        if pair_id is None:
            return None
        a, b = self.pairs[pair_id]
        return b if tile_pos == a else a
