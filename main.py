"""
Entry point. Owns pygame setup and the frame loop; every actual system
lives in its own module and is just wired together here.
"""

import pygame as pg

from grid import Direction, TileType
from board import Board
from roads import RoadNetwork, RoadRenderer, Pathfinder
from buildings import House, Shop
from cars import Car, Dispatcher
from demand import DemandSystem
from spawn import SpawnManager
from clock import GameClock
from context import GameContext
from progression import GameMode, ModeManager, Inventory, ItemType, WeeklyUpgradeSystem
from traffic_control import TrafficLightManager, TrafficLightRenderer, RoundaboutManager
from motorways import MotorwaySystem
from ui import (
    Toolbar, UpgradeChoiceUI, SandboxToggleButton, SpeedToggleButton,
    draw_shop_demand, draw_score, draw_game_over, draw_paused_banner, draw_clock_widget,
)
from input import InputController

WIDTH = 1600
HEIGHT = 900
WHITE = (255, 255, 255)
FPS = 60

CLOCK_WIDGET_CENTER = (WIDTH - 90, 70)
CLOCK_WIDGET_RADIUS = 45
SCORE_POS = (40, 40)
SANDBOX_BUTTON_RECT = pg.Rect(20, 90, 220, 40)
SPEED_BUTTON_RECT = pg.Rect(WIDTH - 90 - 60, 120, 120, 32) 

STARTING_ROAD_STOCK = 40
CAR_DISPATCH_INTERVAL = 0.5
CAR_DEPART_STAGGER = 1.0


def build_world():
    """Constructs every system for a fresh game and returns them as a
    dict — kept as one function so main() itself stays a thin loop."""
    play_area = pg.Rect(187, 20, 1225, 700)
    board = Board(28, 16, play_area)
    board.build()

    road_network = RoadNetwork(board.occupancy)
    road_renderer = RoadRenderer(board)

    houses = [
        House(3, 3, board, Direction.SE, color_id='red'),
        House(20, 12, board, Direction.W, color_id='blue'),
    ]
    shops = [
        Shop(10, 5, board, 'bottom', tier=1, color_id='red'),
        Shop(15, 10, board, 'top', tier=2, color_id='blue'),
    ]
    for house in houses:
        house.build_entrance_road(road_network)
    for shop in shops:
        shop.build_connector_road(road_network)

    demand_system = DemandSystem(shops)
    spawn_manager = SpawnManager(board, road_network, houses, shops, demand_system,
                                  initial_colors=['red', 'blue'])
    game_clock = GameClock()

    mode_manager = ModeManager(GameMode.NORMAL)
    inventory = Inventory(mode_manager, road_network)
    inventory.register(ItemType.ROAD, starting_quantity=STARTING_ROAD_STOCK)
    inventory.register(ItemType.ROUNDABOUT, starting_quantity=0)
    inventory.register(ItemType.TRAFFIC_LIGHT, starting_quantity=0)
    inventory.register(ItemType.MOTORWAY, starting_quantity=0)

    weekly_upgrades = WeeklyUpgradeSystem(inventory, mode_manager)
    toolbar = Toolbar(inventory, WIDTH, HEIGHT)
    toolbar.active_tool = ItemType.ROAD
    upgrade_ui = UpgradeChoiceUI(WIDTH, HEIGHT)
    sandbox_button = SandboxToggleButton(mode_manager, SANDBOX_BUTTON_RECT)
    speed_button = SpeedToggleButton(SPEED_BUTTON_RECT)

    traffic_light_manager = TrafficLightManager(inventory)
    traffic_light_renderer = TrafficLightRenderer(board, Direction.OFFSETS)
    roundabout_manager = RoundaboutManager(inventory)
    motorway_system = MotorwaySystem(inventory, max_pairs=None)

    context = GameContext(board, road_network, demand_system,
                           traffic_light_manager=traffic_light_manager,
                           roundabout_manager=roundabout_manager,
                           motorway_system=motorway_system)

    dispatcher = Dispatcher(road_network, houses, motorway_system=motorway_system)

    input_controller = InputController(
        board, road_network, houses, inventory, toolbar,
        traffic_light_manager, roundabout_manager, motorway_system)

    return dict(
        board=board, road_network=road_network, road_renderer=road_renderer,
        houses=houses, shops=shops, demand_system=demand_system,
        spawn_manager=spawn_manager, game_clock=game_clock,
        mode_manager=mode_manager, inventory=inventory,
        weekly_upgrades=weekly_upgrades, toolbar=toolbar, upgrade_ui=upgrade_ui,
        sandbox_button=sandbox_button, speed_button=speed_button,
        traffic_light_manager=traffic_light_manager,
        traffic_light_renderer=traffic_light_renderer,
        roundabout_manager=roundabout_manager, motorway_system=motorway_system,
        context=context, dispatcher=dispatcher, input_controller=input_controller,
        cars=[], pending_spawns=[],
    )


def try_dispatch_cars(world):
    """For every shop with unclaimed pending pins, ask the Dispatcher to
    send cars from matching-color houses with spare capacity, then queue
    a Car departure per house/count returned, staggered a second apart
    per house."""
    for shop in world['shops']:
        unclaimed = shop.pending_pings - shop.cars_en_route
        if unclaimed <= 0:
            continue
        dispatched = world['dispatcher'].fulfill_demand(shop, num_needed=unclaimed)
        for house, count, path in dispatched:
            if not path:
                continue
            for i in range(count):
                world['pending_spawns'].append({
                    'delay': i * CAR_DEPART_STAGGER,
                    'house': house,
                    'shop': shop,
                    'tile_path': path,
                })
            shop.cars_en_route += count


def update_spawn_queue(world, dt):
    context = world['context']
    still_pending = []
    for spawn in world['pending_spawns']:
        spawn['delay'] -= dt
        if spawn['delay'] <= 0:
            start_tile = spawn['tile_path'][0]
            if context.tile_occupants.get(start_tile) is not None:
                spawn['delay'] = 0
                still_pending.append(spawn)
                continue
            car = Car(spawn['house'], spawn['shop'], spawn['tile_path'],
                      spawn['house'].color, context)
            world['road_network'].mark_path_in_use(car.tile_path)
            world['cars'].append(car)
        else:
            still_pending.append(spawn)
    world['pending_spawns'][:] = still_pending


def update_cars(world, dt):
    road_network = world['road_network']
    for car in list(world['cars']):
        car.update(dt)
        if car.done:
            car.house.return_car(1)
            road_network.release_path(car.tile_path)
            world['cars'].remove(car)


def main():
    pg.init()
    screen = pg.display.set_mode((WIDTH, HEIGHT))
    clock = pg.time.Clock()

    world = build_world()
    board = world['board']
    road_network = world['road_network']
    weekly_upgrades = world['weekly_upgrades']
    toolbar = world['toolbar']
    upgrade_ui = world['upgrade_ui']
    sandbox_button = world['sandbox_button']
    speed_button = world['speed_button']
    input_controller = world['input_controller']
    game_clock = world['game_clock']
    demand_system = world['demand_system']
    spawn_manager = world['spawn_manager']
    traffic_light_manager = world['traffic_light_manager']
    context = world['context']

    # The weekly upgrade prompt pauses everything until a choice is made.
    paused = False
    running = True
    dispatch_accum = 0.0

    while running:
        dt = clock.tick(FPS) / 1000.0
        sim_dt = dt * speed_button.multiplier
        game_over = demand_system.game_over
        interactive_blocked = weekly_upgrades.is_awaiting_choice

        for event in pg.event.get():
            if event.type == pg.QUIT:
                running = False

            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_SPACE and not game_over and not interactive_blocked:
                    paused = not paused

            elif event.type == pg.MOUSEBUTTONDOWN:
                if interactive_blocked:
                    upgrade_ui.handle_click(event.pos, weekly_upgrades.pending_choices,
                                             weekly_upgrades.choose)
                    continue
                if sandbox_button.handle_click(event.pos):
                    continue
                if speed_button.handle_click(event.pos):
                    continue
                if not game_over:
                    input_controller.handle_mouse_down(event)

            elif event.type == pg.MOUSEBUTTONUP:
                input_controller.handle_mouse_up(event)

            elif event.type == pg.MOUSEMOTION:
                if not game_over and not interactive_blocked:
                    input_controller.handle_mouse_motion(event)

        if not game_over and not paused and not interactive_blocked:
            prev_day_index = game_clock.day_index
            game_clock.update(sim_dt)
            weekly_upgrades.check_week_boundary(prev_day_index, game_clock.day_index)

            demand_system.update(sim_dt)
            spawn_manager.update(sim_dt)
            traffic_light_manager.update(sim_dt)

            dispatch_accum += sim_dt
            if dispatch_accum >= CAR_DISPATCH_INTERVAL:
                dispatch_accum -= CAR_DISPATCH_INTERVAL
                try_dispatch_cars(world)

            update_spawn_queue(world, sim_dt)
            update_cars(world, sim_dt)
            game_over = demand_system.game_over

        screen.fill(WHITE)
        board.draw(screen)
        for building in world['houses'] + world['shops']:
            building.draw(screen)
        world['road_renderer'].draw(screen, road_network)
        for house in world['houses']:
            house.draw_driveway(screen)
        traffic_light_manager.draw(screen, world['traffic_light_renderer'])
        for car in world['cars']:
            car.draw(screen)
        for shop in world['shops']:
            draw_shop_demand(screen, shop, board)
        draw_clock_widget(screen, game_clock, CLOCK_WIDGET_CENTER, CLOCK_WIDGET_RADIUS)
        draw_score(screen, context.scoreboard.value, SCORE_POS)
        sandbox_button.draw(screen)
        speed_button.draw(screen)
        toolbar.draw(screen)

        preview = input_controller.drag_preview()
        if preview is not None:
            pixel_pos, snapped_tile = preview
            color = (250, 205, 80) if snapped_tile is not None else (180, 180, 180)
            if snapped_tile is not None:
                center = board.tile_center(*snapped_tile)
            else:
                center = pixel_pos
            pg.draw.circle(screen, color, (int(center[0]), int(center[1])), 7)
            pg.draw.circle(screen, (40, 40, 40), (int(center[0]), int(center[1])), 7, 2)

        if paused and not game_over and not interactive_blocked:
            draw_paused_banner(screen, WIDTH)

        if weekly_upgrades.is_awaiting_choice:
                    upgrade_ui.draw(screen, weekly_upgrades.pending_choices)

        if game_over:
            draw_game_over(screen, WIDTH, HEIGHT)

        pg.display.flip()

    pg.quit()


if __name__ == "__main__":
    main()
