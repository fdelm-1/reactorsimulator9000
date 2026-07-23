"""Reactor Simulator 9000 - a pygame control panel for a point-kinetics reactor model."""

import time
import math
import threading
from os import environ
import csv

import config
import diagrams
from point_kinetics import PointKinetics
from temperature_model import TemperatureModel

environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame  # noqa: E402  (must import after PYGAME_HIDE_SUPPORT_PROMPT is set)




WIDTH, HEIGHT = 1920, 1080
WHITE = config.WHITE
BLACK = config.BLACK
FONT_PATH = "./fonts/retro.ttf"

RAW_SCORES_PATH = "raw_scores.csv"

# Popups (name entry, quit/restart instructions) live in the strip above the graph
# (which starts at diagrams.GRAPH_ORIGIN_PX[1]) instead of screen-centre, so they
# never block the game view.
POPUP_WIDTH, POPUP_HEIGHT = 800, 170
POPUP_TOP_MARGIN = 20


class System:
    """Drives the reactor point-kinetics model and the pygame control-panel UI."""

    TARGET_POWER_MW = config.TARGET_POWER_MW
    TARGET_POWER_TOLERANCE_MW = config.TARGET_TOLERANCE_MW
    TARGET_POWER_LOWER_MW = TARGET_POWER_MW - TARGET_POWER_TOLERANCE_MW
    TARGET_POWER_UPPER_MW = TARGET_POWER_MW + TARGET_POWER_TOLERANCE_MW
    TARGET_HOLD_TIME_S = config.TARGET_HOLD_TIME_S
    FAILURE_POWER_MW = config.FAILURE_POWER_MW
    FAILURE_ZONE_TOP_MW = 500  # how far up the graph's red danger band is drawn
    # displayed/plotted power never reads below this - just enough to avoid a literal
    # "0.000 MW" before the reactor starts producing power. Kept small on purpose: at
    # 10 the reactor's real exponential rise (rate set by k_eff) stayed under this
    # floor for a visible stretch at the start of every run, which read as a startup
    # power cap that lasted longer the lower k_eff was - not an intended feature.
    MIN_DISPLAY_POWER_MW = 1

    # k_eff with all levers fully up (neutral - no lever contributes anything).
    BASE_K_EFF = config.BASE_K_EFF

    # How much each lever (left, middle, right) subtracts from BASE_K_EFF when pushed
    # all the way down; 0 when pushed all the way up (no effect at maximum), linear
    # in between. Levers only ever pull k_eff down from the base, never push it above.
    LEVER_MIN_EFFECT = config.LEVER_MIN_EFFECT

    MIN_ALLOWABLE_K_EFF = BASE_K_EFF + sum(m for m in LEVER_MIN_EFFECT)

    # The last LEVER_DEADZONE_FRACTION of travel at each end of a lever snaps to the
    # extreme (0 or 1), so a lever nudged almost-but-not-quite to an end still reads as
    # fully there rather than leaving a sliver of residual effect.
    LEVER_DEADZONE_FRACTION = config.LEVER_DEADZONE_FRACTION

    # Each lever's effect (on k_eff and on the diagram) chases its target position
    # rather than snapping, taking this many seconds to cross the full range - so a
    # lever move plays out gradually, like a real rod drive / boron change. Safety
    # slowest, shim fastest. See _advance_effective_levers.
    LEVER_EFFECT_DELAY_S = config.LEVER_EFFECT_DELAY_S

    # SCRAM behaviour (manual or automatic - see _trigger_scram). Both immediately
    # subtract SCRAM_K_EFF_DROP from k_eff and lock it there; an automatic SCRAM
    # (triggered by exceeding FAILURE_POWER_MW) holds the lock
    # SCRAM_AUTO_LOCK_MULTIPLIER times longer than a manual one, since it represents
    # a more severe, unplanned trip.
    SCRAM_K_EFF_DROP = config.SCRAM_K_EFF_DROP
    SCRAM_LOCK_DURATION_S = config.SCRAM_LOCK_DURATION_S
    SCRAM_AUTO_LOCK_MULTIPLIER = config.SCRAM_AUTO_LOCK_MULTIPLIER

    # How long (seconds) the scram rods take to fully lower/raise - see
    # _advance_scram_rods.
    SCRAM_ROD_TRAVEL_TIME_S = config.SCRAM_ROD_TRAVEL_TIME_S

    # How much each kg/s of coolant mass flow subtracts from k_eff - see
    # _current_mass_flow_rate and where self.k_eff is finalised in _game_loop.
    MASS_FLOW_K_EFF_COEFFICIENT = config.MASS_FLOW_K_EFF_COEFFICIENT

    # How long (seconds) the left button (pumps) must be held before the pumps
    # count as spun up - see the startup-sequence handling in _game_loop.
    LEFT_BUTTON_HOLD_TO_START_S = config.LEFT_BUTTON_HOLD_TO_START_S

    # Yellow LED window: a lever's own k_eff contribution counts as "neutral" (not
    # positive/negative) within +-0.0005 of 1.0, rather than requiring it to land on
    # exactly 1.0 - potentiometer/reading inaccuracy meant that never actually happened.
    LEVER_LED_NEUTRAL_TOLERANCE = 0.0005

    # Whether the control-rod levers drive k_eff by default ('8' toggles this in-game).
    # update_pygame_keff_from_levers() sets k_eff purely from the current lever position,
    # so it must be off wherever there's no real lever - otherwise it overwrites the
    # keyboard w/s increments back to neutral every frame.
    USE_LEVERS_BY_DEFAULT = True

    MAX_NAME_LENGTH = 12

    def __init__(self, framerate=30, pk_n_animation=False, complexity_level=1) -> None:
        self.frame_rate = framerate
        self.frame_time = 1 / framerate

        self.pk = PointKinetics()
        self.k_eff = self.BASE_K_EFF

        self.temperature_model = TemperatureModel()
        self.temperature = config.STARTING_TEMPERATURE_C

        self.pk_n_animation = pk_n_animation
        self.complexity_level = complexity_level

        self.running = False

        self.panel_states = self._create_panel_states()
        self.lever_sign_states = [0, 0, 0]

    def _create_panel_states(self):
        """Hook so platforms without the physical control panel can substitute a stand-in.

        Imported lazily because control_panel_states.py pulls in RPi.GPIO/gpiozero/smbus,
        which only exist on a Raspberry Pi - importing it at module level would make merely
        importing this file fail on any other platform.
        """
        from control_panel_states import MyControlPanelStates

        return MyControlPanelStates()

    def main(self):
        self.pk_thread = threading.Thread(target=self.run_pk)
        return self.run_pygame()

    def start_simulation(self):
        self.pk_thread.start()

    def update_pygame_keff_from_levers(self, lever_current_rel_pos, lever_origin_rel_pos=(0.75, 0.75, 0.75)):
        """Each lever contributes linearly across its full travel (LEVER_MIN_EFFECT all
        the way down to no effect all the way up), with no flat/dead band - the
        physical lever is a plain slider potentiometer, so its software response
        should track it continuously rather than pinning to a value near the median.
        k_eff is BASE_K_EFF when all three levers are all the way up, and
        BASE_K_EFF + sum(LEVER_MIN_EFFECT) when all three are all the way down.
        """
        temp_k_eff = self.BASE_K_EFF

        for i, rel_pos in enumerate(lever_current_rel_pos):
            # This hardware reports a higher rel_pos the further DOWN the lever is
            # pushed, so convert to "how far up" before applying the linear response.
            up_fraction = 1.0 - rel_pos
            lever_value = self.LEVER_MIN_EFFECT[i] * (1.0 - up_fraction)
            temp_k_eff += lever_value

            # LED colour: green when this lever's own contribution is positive,
            # yellow within LEVER_LED_NEUTRAL_TOLERANCE of zero, red when negative.
            if lever_value > self.LEVER_LED_NEUTRAL_TOLERANCE:
                self.lever_sign_states[i] = -1
            elif lever_value < -self.LEVER_LED_NEUTRAL_TOLERANCE:
                self.lever_sign_states[i] = 1
            else:
                self.lever_sign_states[i] = 0

        self.pygame_k_eff = temp_k_eff

    @classmethod
    def _apply_lever_deadzone(cls, pos):
        """Snap the last LEVER_DEADZONE_FRACTION of travel at each end to 0 / 1."""
        if pos <= cls.LEVER_DEADZONE_FRACTION:
            return 0.0
        if pos >= 1.0 - cls.LEVER_DEADZONE_FRACTION:
            return 1.0
        return pos

    def _advance_effective_levers(self, lever_rel_pos, dt):
        """Move each lever's "effective" position - the one that actually drives k_eff
        and the diagram - toward its (deadzoned) real position, at a rate that crosses
        the full 0..1 range in LEVER_EFFECT_DELAY_S[i] seconds. This draws each lever's
        effect out over time (safety slowest, shim fastest) instead of applying it
        instantly, simulating the gradual physical response.
        """
        for i in range(len(self.effective_lever_pos)):
            target = self._apply_lever_deadzone(lever_rel_pos[i])
            delay = self.LEVER_EFFECT_DELAY_S[i]
            if delay <= 0:
                self.effective_lever_pos[i] = target
                continue
            max_step = dt / delay
            diff = target - self.effective_lever_pos[i]
            if abs(diff) <= max_step:
                self.effective_lever_pos[i] = target
            else:
                self.effective_lever_pos[i] += math.copysign(max_step, diff)

    def _trigger_scram(self, automatic):
        """SCRAM: immediately drop k_eff by SCRAM_K_EFF_DROP and lock it there,
        ignoring lever/rod input, until the lock expires (see the per-frame handling
        in _game_loop) - the resulting power drop plays out through the point-
        kinetics model itself rather than being forced directly. Also engages the
        scram rods (see _advance_scram_rods), which stay down even after the lock
        expires until the operator confirms safe by fully lowering every lever.
        Guarded by self.scramming so re-triggering (e.g. holding SPACE, or staying
        above FAILURE_POWER_MW for multiple frames before the drop takes effect)
        doesn't restack the lock or repeatedly drop k_eff further.
        """
        if self.scramming:
            return
        self.scramming = True
        self.scram_rods_engaged = True
        self.scram_locked_k_eff = self.pygame_k_eff - self.SCRAM_K_EFF_DROP
        self.pygame_k_eff = self.scram_locked_k_eff
        multiplier = self.SCRAM_AUTO_LOCK_MULTIPLIER if automatic else 1
        self.scram_lock_remaining_s = self.SCRAM_LOCK_DURATION_S * multiplier

    def _advance_scram_rods(self, dt):
        """Scram rods drop the instant a SCRAM triggers and stay down - even after
        the SCRAM's k_eff lock/cooldown ends - until the operator has manually
        pushed every lever fully down, confirming the reactor is safe to bring the
        rods back out. Both the drop and the raise are drawn out over
        SCRAM_ROD_TRAVEL_TIME_S rather than snapping instantly, same technique as
        _advance_effective_levers.
        """
        if self.scram_rods_engaged and not self.scramming:
            if all(pos >= 1.0 for pos in self.effective_lever_pos):
                self.scram_rods_engaged = False

        target = 1.0 if self.scram_rods_engaged else 0.0
        delay = self.SCRAM_ROD_TRAVEL_TIME_S
        if delay <= 0:
            self.scram_rod_insertion = target
            return
        max_step = dt / delay
        diff = target - self.scram_rod_insertion
        if abs(diff) <= max_step:
            self.scram_rod_insertion = target
        else:
            self.scram_rod_insertion += math.copysign(max_step, diff)

    def _current_mass_flow_rate(self):
        """Coolant mass flow rate (kg/s): a base amount plus a bump per switch that's
        currently on. Shared by the temperature model and the k_eff mass-flow penalty
        (see where self.k_eff is finalised in _game_loop).
        """
        switches_on = sum(1 for on in self.panel_states.switch_states.values() if on)
        return config.BASE_MASS_FLOW_RATE + config.FLOW_RATE_PER_SWITCH * switches_on

    # -- Setup -----------------------------------------------------------

    def _init_display(self):
        pygame.init()
        # Reuse the existing window across restarts (a new System is created each
        # restart) instead of tearing it down and recreating it via set_mode() again.
        self.screen = pygame.display.get_surface()
        if self.screen is None:
            self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
            pygame.display.set_caption("Reactor Simulator 9000")
        self.clock = pygame.time.Clock()
        self.fps_font = pygame.font.Font(FONT_PATH, 20)

        self.pygame_k_eff = self.BASE_K_EFF
        self.inc = 0.00005 * 30 / self.frame_rate
        self.lifting_rod = False
        self.lowering_rod = False
        self.scramming = False
        self.scram_lock_remaining_s = 0.0
        self.scram_locked_k_eff = self.BASE_K_EFF
        self.scram_rods_engaged = False
        self.scram_rod_insertion = 0.0

        # Startup sequence: hold the left button (pumps) for LEFT_BUTTON_HOLD_TO_START_S
        # to spin the pumps up, then press right to start the reactor. Pumps stay
        # "on" once activated even if left is released.
        self.left_button_press_time = None
        self.pumps_activated = False

        # Tied to the levers' real combined ceiling so pygame_k_eff is never clamped
        # short of what the levers can actually produce, and "MAXIMUM!" can display.
        self.max_allowable_k_eff = self.BASE_K_EFF

        self.running = False
        self.time_at_target_condition = 0.0
        self.temperature = config.STARTING_TEMPERATURE_C

        # Seed each lever's effective (drawn-out) position at its current deadzoned
        # reading so the diagram/k_eff start settled rather than ramping in from 0.
        self.effective_lever_pos = [
            self._apply_lever_deadzone(pos)
            for pos in self.panel_states.control_rod_lever_rel_pos.values()
        ]

    # -- Graph, reactor vessel, thermometer, pump panel, leaderboard --------
    # All actual drawing lives in diagrams.py; these are thin methods that build
    # the diagrams.py renderer objects once (the _init_* methods) and, each frame,
    # hand them whatever plain game-state values they need to draw (no renderer
    # ever reaches into self directly).

    def _init_graph(self):
        self.graph = diagrams.GraphRenderer(self.TARGET_POWER_LOWER_MW, self.TARGET_POWER_UPPER_MW,
                                             self.FAILURE_POWER_MW, self.FAILURE_ZONE_TOP_MW)
        self.leaderboard = diagrams.LeaderboardRenderer()
        self.graph_start_time = time.time()
        self._load_leaderboard()
        self._update_graph()

    def _display_power(self):
        """Reactor power for the graph/HUD only - never the raw game-logic value."""
        return max(self.pk.n, self.MIN_DISPLAY_POWER_MW)

    def _record_history_sample(self):
        elapsed = time.time() - self.graph_start_time
        self.graph.record_sample(elapsed, self._display_power())

    def _update_graph(self):
        elapsed = time.time() - self.graph_start_time
        # "MAXIMUM!" reflects the lever/keyboard input being pinned at its own ceiling
        # (pygame_k_eff), not the final k_eff - which, with the mass-flow penalty now
        # part of it, has no single fixed maximum (it depends on how many pumps are on).
        is_max_k_eff = self.pygame_k_eff == self.max_allowable_k_eff
        self.graph.render_live(elapsed, self._display_power(), self.k_eff, is_max_k_eff,
                                self.time_at_target_condition)
        self.graph.blit_to(self.screen)

    def _draw_final_graph(self):
        is_max_k_eff = self.pygame_k_eff == self.max_allowable_k_eff
        self.graph.render_final(self.final_elapsed_time, self._display_power(), self.k_eff,
                                 is_max_k_eff, self.time_at_target_condition)
        self.graph.blit_to(self.screen)

    def _init_reactor_vessel(self):
        self.vessel = diagrams.ReactorVesselRenderer()

    def _draw_reactor_vessel(self, rod_insertions, power_fraction, shim_fraction):
        self.vessel.draw(self.screen, rod_insertions, power_fraction, shim_fraction)

    def _init_pump_panel(self):
        self.pump_panel = diagrams.PumpPanelRenderer()

    def _draw_pump_panel(self):
        # The panel only shows a switch as "on" once the pumps have actually spun
        # up (left button held for LEFT_BUTTON_HOLD_TO_START_S) - before that, every
        # box reads off regardless of the physical switch position.
        if self.pumps_activated:
            display_states = self.panel_states.switch_states
        else:
            display_states = {name: False for name in self.panel_states.switch_states}
        self.pump_panel.draw(self.screen, display_states)

    def _init_thermometer(self):
        self.thermometer = diagrams.ThermometerRenderer()

    def _draw_thermometer(self, temp):
        self.thermometer.draw(self.screen, temp)

    def _load_leaderboard(self):
        entries = []
        with open(RAW_SCORES_PATH, "r") as raw_scores:
            reader = csv.reader(raw_scores)
            for row in reader:
                try:
                    elapsed_time = float(row[0])
                    name = row[1]
                    entries.append((elapsed_time, name))
                except ValueError:
                    continue

        entries.sort(key=lambda entry: entry[0])
        self.leaderboard.set_entries(entries[:diagrams.LEADERBOARD_MAX_ENTRIES])

    def _clear_leaderboard(self):
        open(RAW_SCORES_PATH, "w").close()
        if self.pk_n_animation:
            self._load_leaderboard()

    def _draw_leaderboard(self):
        self.leaderboard.draw(self.screen)

    def _draw_popup(self, message):
        # popup_surface is a fixed POPUP_WIDTH x POPUP_HEIGHT box, fully repainted and
        # blitted at the same fixed screen position every call, so a shorter message
        # (e.g. after backspacing) always overwrites the previous, longer one instead
        # of leaving stray glyphs from earlier frames on screen.
        popup_surface = pygame.Surface((POPUP_WIDTH, POPUP_HEIGHT), pygame.SRCALPHA)
        popup_surface.fill((0, 0, 0, 230))

        font = pygame.font.Font(FONT_PATH, 24)
        rendered_lines = [font.render(line, True, WHITE) for line in message.split("\n")]
        total_height = sum(line.get_height() for line in rendered_lines)

        y = (POPUP_HEIGHT - total_height) // 2
        for rendered_line in rendered_lines:
            x = (POPUP_WIDTH - rendered_line.get_width()) // 2
            popup_surface.blit(rendered_line, (x, y))
            y += rendered_line.get_height()

        self.screen.blit(popup_surface, (WIDTH // 2 - POPUP_WIDTH // 2, POPUP_TOP_MARGIN))
        pygame.display.flip()

    def _draw_fps(self):
        fps_surface = self.fps_font.render(f"FPS: {self.clock.get_fps():.1f}", True, WHITE)
        self.screen.blit(fps_surface, (10, 10))

    def _prompt_for_name(self):
        """Modal text-entry loop shown after a win, drawn on top of the frozen final graph."""
        name = ""
        pygame.key.start_text_input()
        entering = True
        while entering:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    entering = False
                elif event.type == pygame.TEXTINPUT:
                    if len(name) < self.MAX_NAME_LENGTH:
                        name += event.text
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_RETURN:
                        entering = False
                    elif event.key == pygame.K_BACKSPACE:
                        name = name[:-1]
                    elif event.key == pygame.K_ESCAPE:
                        name = ""
                        entering = False

            self._draw_popup(f"You win!\nEnter your name and press Enter:\n{name}_")
            self.clock.tick(self.frame_rate)

        pygame.key.stop_text_input()
        return name.strip() or "Anonymous"

    def _update_leds(self, scramming, at_target):
        led_names = list(self.panel_states.LED_strips.keys())

        if not self.running:
            for name in led_names:
                self.panel_states.LED_strips[name].set_colour("r")
            return

        for name in led_names:
            if "lever" in name:
                if "left" in name:
                    state = self.lever_sign_states[0]
                elif "middle" in name:
                    state = self.lever_sign_states[1]
                else:
                    state = self.lever_sign_states[2]
                self.panel_states.LED_strips[name].set_color(["g", "y", "r"][state + 1])

            elif "switch" in name:
                self.panel_states.LED_strips[name].set_colour("g")

            elif "reactor" in name:
                if scramming:
                    self.panel_states.LED_strips[name].set_color("r")
                elif at_target:
                    self.panel_states.LED_strips[name].set_color("g")
                else:
                    self.panel_states.LED_strips[name].set_color("y")

            elif "right_button" in name:
                self.panel_states.LED_strips[name].set_colour("g")



    def _end_game(self):
        self.running = False
        self.panel_states.turn_off_all_leds()
        if self.pk_thread.is_alive():
            self.pk_thread.join()
        self.pk.reset_sol()

    def _record_score(self, name):
        with open(RAW_SCORES_PATH, "a") as raw_scores:
            score_writer = csv.writer(raw_scores)
            score_writer.writerow([f"{self.final_elapsed_time:.3f}", name])
        if self.pk_n_animation:
            self._load_leaderboard()

    # -- Main loop ----------------------------------------------------------

    def run_pygame(self):
        self._init_display()
        if self.pk_n_animation:
            self._init_graph()
            self._init_reactor_vessel()
            self._init_pump_panel()
            self._init_thermometer()
        return self._game_loop()

    def _game_loop(self):
        lever_origin_rel_pos = list(self.panel_states.control_rod_lever_rel_pos.values())
        use_levers_flag = self.USE_LEVERS_BY_DEFAULT
        show_quit_popup = False
        restart_flag = False
        quit_restart_message = "Press 3D/B to quit\nor 1D/4 to restart.\nAny other key to continue"

        victory_flag = False
        at_target = False
        self.panel_states.turn_off_all_leds()

        if self.pk_n_animation:
            self._update_graph()

        pygame_running = True
        while pygame_running:
            ##-- Handle events

            ##!! Figure out what the physical inputs from the control panel are
            self.panel_states.update_state()
            self._update_leds(self.scramming, at_target)
            lever_rel_pos = list(self.panel_states.control_rod_lever_rel_pos.values())

            # Draw each lever's effect out over time (and apply the end deadzones):
            # effective_lever_pos lags the real levers and is what actually drives
            # k_eff and the rod/shim diagram. clock.get_time() is the previous frame's
            # real duration in ms (see the time_at_target comment further down).
            frame_dt = self.clock.get_time() / 1000.0
            self._advance_effective_levers(lever_rel_pos, frame_dt)
            self._advance_scram_rods(frame_dt)

            ##!! Startup sequence: hold left (pumps) for LEFT_BUTTON_HOLD_TO_START_S,
            ## then press right (reactor) to start. Pumps latch "on" once activated -
            ## releasing left afterwards doesn't undo it.
            if self.panel_states.button_states["left_button"]:
                if self.left_button_press_time is None:
                    self.left_button_press_time = time.time()
                elif time.time() - self.left_button_press_time >= self.LEFT_BUTTON_HOLD_TO_START_S:
                    self.pumps_activated = True
            else:
                self.left_button_press_time = None

            if not self.running and self.pumps_activated and self.panel_states.button_states["right_button"]:
                self.screen.fill(BLACK)
                self.running = True
                self.start_simulation()

            if not self.running and not victory_flag:
                self.graph_start_time = time.time()
                self.temperature = config.STARTING_TEMPERATURE_C
                if self.pk_n_animation:
                    self._update_graph()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame_running = False

                elif event.type == pygame.KEYDOWN:
                    if event.key not in (pygame.K_4, pygame.K_b) and show_quit_popup:
                        ##!! Cancel the popup
                        show_quit_popup = False

                    if event.key in (pygame.K_q, pygame.K_b):
                        self.running = False
                        pygame_running = False
                        restart_flag = False

                    if event.key == pygame.K_1 and not self.running:
                        self.screen.fill((252, 186, 3))

                    if event.key in (pygame.K_SPACE, pygame.K_0):
                        if self.running:
                            self._trigger_scram(automatic=False)

                    if event.key in (pygame.K_w, pygame.K_UP, pygame.K_2):
                        self.lifting_rod = True

                    if event.key in (pygame.K_s, pygame.K_DOWN, pygame.K_6):
                        self.lowering_rod = True

                    if event.key == pygame.K_8:
                        ##!! Toggle using the levers, instead just use keypad
                        use_levers_flag = not use_levers_flag

                    if event.key == pygame.K_4:
                        ##!! RESTART
                        restart_flag = True
                        self.running = False
                        pygame_running = False

                    if event.key == pygame.K_d:
                        ##!! Clear the leaderboard
                        self._clear_leaderboard()

                elif event.type == pygame.KEYUP:
                    if event.key == pygame.K_1 and not self.running:
                        ##!! Start the game
                        self.screen.fill((0, 50, 0))
                        self.running = True
                        self.start_simulation()

                    if event.key in (pygame.K_w, pygame.K_UP, pygame.K_2):
                        self.lifting_rod = False

                    if event.key in (pygame.K_s, pygame.K_DOWN, pygame.K_6):
                        self.lowering_rod = False

            ##--Apply updates
            if self.running:
                if self.TARGET_POWER_LOWER_MW < self.pk.n < self.TARGET_POWER_UPPER_MW:
                    at_target = True
                    # clock.get_time() is the actual duration of the previous frame, in ms.
                    # Using the fixed nominal frame_time here instead would undercount
                    # whenever the real frame rate drops below target (e.g. on the Pi),
                    # since each frame would still only add 1/frame_rate regardless of how
                    # long it actually took.
                    self.time_at_target_condition += self.clock.get_time() / 1000.0
                else:
                    at_target = False
                    self.time_at_target_condition = 0.0

                # Fuel temperature: integrate the model's dT/dt each tick. Coolant mass
                # flow is a base plus a bump per switch that's on; more flow cools the
                # fuel faster (at the cost of some reactivity - see where self.k_eff is
                # finalised below). Power is MW in the game but the model works in SI, so
                # it's converted to watts. Overheating past the scram temp trips an
                # auto-SCRAM.
                temp_rate = self.temperature_model.fuel_temperature_rate(
                    self._current_mass_flow_rate(), self.pk.n * 1e6, self.temperature)
                self.temperature += temp_rate * (self.clock.get_time() / 1000.0)
                if self.temperature > config.SCRAM_TEMPERATURE_C:
                    self._trigger_scram(automatic=True)

                if self.pk.n > self.FAILURE_POWER_MW:
                    self._trigger_scram(automatic=True)

                elif self.time_at_target_condition >= self.TARGET_HOLD_TIME_S:
                    self._end_game()
                    victory_flag = True
                    self.final_elapsed_time = time.time() - self.graph_start_time
                    if self.pk_n_animation:
                        self._draw_final_graph()
                    name = self._prompt_for_name()
                    if name != "Anonymous":
                        self._record_score(name)
                    # Replace the (now stale) name-entry popup with the existing
                    # quit/restart instructions rather than leaving it on screen.
                    self._draw_popup(quit_restart_message)

                ##!! Update the k_eff value from the levers' effective (drawn-out) positions
                if not self.scramming and use_levers_flag:
                    self.update_pygame_keff_from_levers(self.effective_lever_pos, lever_origin_rel_pos)

            if self.running:
                self.screen.fill((0, 50, 0))
            if self.scramming:
                self.screen.fill((50, 0, 0))

            if self.pk_n_animation and self.running:
                self._record_history_sample()
                self._update_graph()

            if show_quit_popup:
                self._draw_popup(quit_restart_message)

            if self.scramming:
                # clock.get_time() is the actual duration of the previous frame, in
                # ms - see the time_at_target_condition comment above for why the
                # nominal frame_time isn't used instead.
                self.scram_lock_remaining_s -= self.clock.get_time() / 1000.0
                self.pygame_k_eff = self.scram_locked_k_eff
                if self.scram_lock_remaining_s <= 0.0:
                    self.scramming = False
            else:
                self.pygame_k_eff += self.inc if self.lifting_rod else 0
                self.pygame_k_eff -= self.inc if self.lowering_rod else 0
                self.pygame_k_eff = min(max(self.MIN_ALLOWABLE_K_EFF, self.pygame_k_eff), self.max_allowable_k_eff)

            # k_eff is the lever/keyboard-driven value (pygame_k_eff, already dropped by
            # SCRAM_K_EFF_DROP and locked for the duration of a SCRAM above) further
            # reduced by the coolant mass flow rate - pushing more coolant through costs
            # some reactivity. Applied after pygame_k_eff's own clamping, so - like the
            # SCRAM drop - it can legitimately push k_eff below MIN_ALLOWABLE_K_EFF.
            self.k_eff = self.pygame_k_eff - self.MASS_FLOW_K_EFF_COEFFICIENT * self._current_mass_flow_rate()

            self._draw_fps()
            if self.pk_n_animation:
                # The right column shows the coolant-pump panel and the live fuel-
                # temperature dial during play; the leaderboard only takes over that
                # whole column once the game has been won.
                if victory_flag:
                    self._draw_leaderboard()
                else:
                    self._draw_pump_panel()
                    self._draw_thermometer(self.temperature)
                # Safety (left lever) and regulating (mid lever) rods track their
                # levers' effective (drawn-out) positions and are unaffected by a SCRAM.
                # The scram rods track scram_rod_insertion, which drops on a SCRAM and
                # only raises again once _advance_scram_rods' reset conditions are met
                # (see that method) - both drawn out over SCRAM_ROD_TRAVEL_TIME_S rather
                # than snapping instantly. The right lever is now chemical shim: its
                # effective position reddens the coolant.
                eff = self.effective_lever_pos
                rod_insertions = {
                    "safety": min(max(eff[0], 0.0), 1.0),
                    "regulating": min(max(eff[1], 0.0), 1.0),
                    "scram": self.scram_rod_insertion,
                }
                shim_fraction = min(max(eff[2], 0.0), 1.0)
                power_fraction = min(max(self.pk.n / self.FAILURE_POWER_MW, 0.0), 1.0)
                self._draw_reactor_vessel(rod_insertions, power_fraction, shim_fraction)

            # Wait for the next frame
            self.clock.tick(self.frame_rate)
            pygame.display.flip()

        # Clean up
        if restart_flag:
            self._end_game()
            return True

        pygame.quit()
        self._end_game()
        return False

    def run_pk(self):
        while self.running:
            t_start = time.monotonic()
            # backwards_euler, not implicit_heun: a SCRAM (or a lowered lever, or high
            # mass flow) can drop k_eff far enough below 1 that, combined with the very
            # short prompt neutron lifetime, the step is numerically stiff.
            # implicit_heun's corrector is an explicit update from an implicit
            # predictor, so - despite the name - it isn't actually unconditionally
            # stable: past a certain stiffness it diverges into a wildly growing,
            # sign-flipping oscillation each frame (verified numerically - a SCRAM from
            # a high-power state blows up within ~100 steps). Plain backwards_euler is
            # unconditionally stable for this linear system, so it stays well-behaved
            # (a smooth decay) at any k_eff, at the cost of being first- rather than
            # second-order accurate - immaterial for a real-time, frame-driven sim.
            self.pk.step(self.frame_time, self.k_eff, method="backwards_euler")
            t_end = time.monotonic()
            sleep_length = max(0.0, self.frame_time - (t_end - t_start))
            time.sleep(sleep_length)


if __name__ == "__main__":
    keep_playing = True
    while keep_playing:
        system = System(pk_n_animation=True)
        keep_playing = system.main()
