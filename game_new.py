"""Reactor Simulator 9000 - a pygame control panel for a point-kinetics reactor model."""

import time
import math
import bisect
import threading
from os import environ
import csv

import config
from point_kinetics import PointKinetics
from temperature_model import TemperatureModel

environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame  # noqa: E402  (must import after PYGAME_HIDE_SUPPORT_PROMPT is set)




WIDTH, HEIGHT = 1920, 1080
WHITE = config.WHITE
BLACK = config.BLACK
GREEN = config.GREEN
AMBER = config.AMBER
RED = config.RED
GRID_COLOR = config.GRID
FONT_PATH = "./fonts/retro.ttf"

GRAPH_ORIGIN_PX = (WIDTH * 0.3, HEIGHT * 0.2)
GRAPH_SIZE_PX = (900, 800)
GRAPH_MARGIN_LEFT = 70
GRAPH_MARGIN_RIGHT = 20
GRAPH_MARGIN_TOP = 70
GRAPH_MARGIN_BOTTOM = 50
GRAPH_BORDER_WIDTH = 2
GRAPH_LINE_WIDTH = 4
TARGET_ZONE_ALPHA = 130
WARNING_ZONE_ALPHA = 200
WARNING_ZONE_HATCH_SPACING_PX = 20
WARNING_ZONE_HATCH_WIDTH_PX = 4
FAILURE_ZONE_ALPHA = 130
Y_GRID_STEP_MW = 50

LEADERBOARD_ORIGIN_PX = (GRAPH_ORIGIN_PX[0] + GRAPH_SIZE_PX[0] + 20, GRAPH_ORIGIN_PX[1])
LEADERBOARD_SIZE_PX = (WIDTH - LEADERBOARD_ORIGIN_PX[0], GRAPH_SIZE_PX[1])
LEADERBOARD_MAX_ENTRIES = 20
RAW_SCORES_PATH = "raw_scores.csv"

# -- Fuel-temperature dial (right of the graph, where the leaderboard sits) --------
# The thermometer occupies the right column during play; the leaderboard only takes
# it over once the game is won.
THERMO_ORIGIN_PX = LEADERBOARD_ORIGIN_PX
THERMO_SIZE_PX = LEADERBOARD_SIZE_PX
DIAL_MIN_C = 500
DIAL_MAX_C = config.SCRAM_TEMPERATURE_C + 100   # a little headroom past the scram line
DIAL_WARN_C = config.SCRAM_TEMPERATURE_C - 200  # amber zone begins here
DIAL_RED_C = config.SCRAM_TEMPERATURE_C         # red (scram) zone begins here
DIAL_START_ANGLE_DEG = 135                       # min temp sits at lower-left...
DIAL_SWEEP_DEG = 270                             # ...sweeping clockwise to lower-right

# -- Reactor vessel diagram (drawn on the left, beside the graph) ------
# The whole diagram is rendered into its own REACTOR_SIZE_PX surface in local
# coordinates and blitted to the screen at REACTOR_ORIGIN_PX, so every layout
# number below is relative to that surface's top-left corner.
REACTOR_SIZE_PX = (480, 880)
# Bottom-aligned with the graph block: the vessel's base sits level with the graph's.
REACTOR_ORIGIN_PX = (40, GRAPH_ORIGIN_PX[1] + GRAPH_SIZE_PX[1] - REACTOR_SIZE_PX[1])

# Palette, kept local to the diagram rather than in config (these are purely
# presentational to this one feature). Fuel is the same green used elsewhere.
VESSEL_METAL = (140, 146, 158)
VESSEL_METAL_DARK = (74, 80, 92)
VESSEL_METAL_LIGHT = (198, 204, 214)
COOLANT_TOP_COLOR = (86, 152, 205)      # light blue water, lighter near the surface
COOLANT_BOTTOM_COLOR = (40, 92, 148)    # ...darkening with depth
CONTROL_ROD_COLOR = (96, 102, 114)      # metal
CONTROL_ROD_DARK = (54, 58, 70)
FUEL_ROD_COLOR = GREEN                  # glowing green fuel
FUEL_ROD_GLOW = (188, 255, 194)
CHERENKOV_COLOR = (120, 195, 255)       # characteristic blue glow from the core

# Vessel geometry within the REACTOR_SIZE_PX surface.
REACTOR_CX = REACTOR_SIZE_PX[0] // 2
# The vessel shell (its domed heads) is drawn as chunky horizontal bars this many
# pixels tall/wide instead of a smooth ellipse, for a blocky retro look.
VESSEL_PIXEL_STEP = 16
VESSEL_WALL_PX = 14
VESSEL_LEFT_PX = REACTOR_CX - 170
VESSEL_RIGHT_PX = REACTOR_CX + 170
VESSEL_BODY_TOP_PX = 150
VESSEL_BODY_BOTTOM_PX = 690
VESSEL_DOME_H_PX = 70
CRDM_HOUSING_TOP_PX = 8       # top of the control-rod drive housings above the head

# Core (fuel + control rods) region, well inside the vessel interior.
CORE_TOP_PX = 300
CORE_BOTTOM_PX = 590
CORE_LEFT_PX = REACTOR_CX - 140
CORE_RIGHT_PX = REACTOR_CX + 140

# Eight rods across the core: four lever-driven control rods (two outer safety rods
# on the left lever, two regulating rods on the middle lever) and four scram rods
# that sit fully withdrawn until a SCRAM drops them. Laid out symmetrically with the
# safety rods outermost/thickest. Each entry is (x-centre, group name).
ROD_HALF_WIDTHS = {"safety": 15, "regulating": 11, "scram": 10}
# Scram rods carry an amber cap so they read as the emergency rods at a glance.
SCRAM_ROD_CAP_COLOR = AMBER
CONTROL_RODS = [
    (REACTOR_CX - 118, "safety"),
    (REACTOR_CX - 84, "scram"),
    (REACTOR_CX - 50, "regulating"),
    (REACTOR_CX - 16, "scram"),
    (REACTOR_CX + 16, "scram"),
    (REACTOR_CX + 50, "regulating"),
    (REACTOR_CX + 84, "scram"),
    (REACTOR_CX + 118, "safety"),
]

# Chemical shim: the right lever dissolves neutron-absorbing boron into the coolant
# (instead of moving a rod), reddening the water more and more as it's increased.
SHIM_TINT_COLOR = (210, 30, 30)
SHIM_MAX_ALPHA = 175

# Cherenkov glow is pre-rendered at a handful of discrete intensities (a per-pixel-
# alpha surface can't be cheaply re-dimmed per frame), and the level nearest the
# current reactor power is blitted each frame.
CHERENKOV_LEVELS = 9
CHERENKOV_MIN_ALPHA = 26
CHERENKOV_MAX_ALPHA = 150


def _lerp_color(color_a, color_b, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(color_a, color_b))


# Popups (name entry, quit/restart instructions) live in the strip above the graph
# (which starts at GRAPH_ORIGIN_PX[1]) instead of screen-centre, so they never
# block the game view.
POPUP_WIDTH, POPUP_HEIGHT = 800, 170
POPUP_TOP_MARGIN = 20


class System:
    """Drives the reactor point-kinetics model and the pygame control-panel UI."""

    N_HISTORY_WINDOW_S = 5  # seconds of power history shown on the graph

    # How far across the visible window the live line's leading (current-time) point
    # sits, once there's enough history to place it there - e.g. 0.8 means it settles
    # at 80% of the way along (4s into a 5s-wide window) rather than running to the
    # very right edge, leaving a lookahead gap instead of the line hitting the wall.
    LIVE_POINT_FRACTION = 0.8

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

    # Fixed graph y-axis range in MW. Kept constant (no per-frame autoscaling from the
    # data's min/max) so the view never "zooms" and the y-gridlines only need drawing
    # once, into the cached static background (see _rebuild_graph_static_background).
    Y_AXIS_MIN_MW = 0
    Y_AXIS_MAX_MW = 300

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
    # multiply the reactor's power by SCRAM_POWER_FACTOR and lock k_eff at
    # MIN_ALLOWABLE_K_EFF; an automatic SCRAM (triggered by exceeding
    # FAILURE_POWER_MW) holds the lock SCRAM_AUTO_LOCK_MULTIPLIER times longer than
    # a manual one, since it represents a more severe, unplanned trip.
    SCRAM_POWER_FACTOR = config.SCRAM_POWER_FACTOR
    SCRAM_LOCK_DURATION_S = config.SCRAM_LOCK_DURATION_S
    SCRAM_AUTO_LOCK_MULTIPLIER = config.SCRAM_AUTO_LOCK_MULTIPLIER

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
        """SCRAM: immediately cut the reactor's power and lock k_eff at its minimum,
        ignoring lever/rod input, until the lock expires (see the per-frame handling
        in _game_loop). Guarded by self.scramming so re-triggering (e.g. holding
        SPACE, or staying above FAILURE_POWER_MW for multiple frames before the power
        cut takes effect) doesn't restack the lock or repeatedly halve the power.
        """
        if self.scramming:
            return
        self.scramming = True
        self.pk.scale_solution(self.SCRAM_POWER_FACTOR)
        self.pygame_k_eff = self.MIN_ALLOWABLE_K_EFF
        multiplier = self.SCRAM_AUTO_LOCK_MULTIPLIER if automatic else 1
        self.scram_lock_remaining_s = self.SCRAM_LOCK_DURATION_S * multiplier

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

    def _init_graph(self):
        self.graph_surface = pygame.Surface(GRAPH_SIZE_PX)

        self.graph_tick_font = pygame.font.Font(FONT_PATH, 14)
        self.graph_label_font = pygame.font.Font(FONT_PATH, 18)
        self.graph_title_font = pygame.font.Font(FONT_PATH, 26)
        self.graph_win_title_font = pygame.font.Font(FONT_PATH, 34)

        self.graph_plot_rect = pygame.Rect(
            GRAPH_MARGIN_LEFT, GRAPH_MARGIN_TOP,
            GRAPH_SIZE_PX[0] - GRAPH_MARGIN_LEFT - GRAPH_MARGIN_RIGHT,
            GRAPH_SIZE_PX[1] - GRAPH_MARGIN_TOP - GRAPH_MARGIN_BOTTOM,
        )

        # Accumulated once per frame in _record_history_sample(); starts empty so the
        # live plot has no pre-filled lead-in and genuinely begins at t=0. Kept as
        # plain lists - the live view only ever processes the slice within the
        # current window (see _render_graph), found by bisecting the monotonically
        # increasing timestamps, so this stays cheap regardless of session length.
        self.full_history_times = []
        self.full_history_powers = []

        self._rebuild_graph_static_background()
        self.graph_start_time = time.time()
        self._load_leaderboard()
        self._update_graph()

    def _mw_to_px(self, mw):
        frac = (mw - self.Y_AXIS_MIN_MW) / (self.Y_AXIS_MAX_MW - self.Y_AXIS_MIN_MW)
        return self.graph_plot_rect.bottom - frac * self.graph_plot_rect.height

    def _time_to_px(self, t, window_start, window_end):
        frac = (t - window_start) / (window_end - window_start)
        return self.graph_plot_rect.left + frac * self.graph_plot_rect.width

    def _draw_translucent_band(self, surface, mw_low, mw_high, color_rgb, alpha):
        plot_rect = self.graph_plot_rect
        y_top = max(self._mw_to_px(mw_high), plot_rect.top)
        y_bottom = min(self._mw_to_px(mw_low), plot_rect.bottom)
        if y_bottom <= y_top:
            return
        band = pygame.Surface((plot_rect.width, y_bottom - y_top), pygame.SRCALPHA)
        band.fill((*color_rgb, alpha))
        surface.blit(band, (plot_rect.left, y_top))

    def _draw_hatched_band(self, surface, mw_low, mw_high, color_rgb, alpha,
                            spacing=WARNING_ZONE_HATCH_SPACING_PX, line_width=WARNING_ZONE_HATCH_WIDTH_PX):
        """Same region as _draw_translucent_band, but filled with diagonal "\" stripes
        instead of a solid colour, so it reads as a hazard-stripe warning rather than
        a plain block.
        """
        plot_rect = self.graph_plot_rect
        y_top = max(self._mw_to_px(mw_high), plot_rect.top)
        y_bottom = min(self._mw_to_px(mw_low), plot_rect.bottom)
        if y_bottom <= y_top:
            return
        width, height = plot_rect.width, y_bottom - y_top
        band = pygame.Surface((width, height), pygame.SRCALPHA)
        color = (*color_rgb, alpha)
        # Lines run top-left to bottom-right ("\"); starting the sweep a full
        # height to the left of the surface (and continuing to its right edge)
        # ensures stripes still reach the surface's left/bottom corner instead of
        # leaving it blank.
        x = -height
        while x < width:
            pygame.draw.line(band, color, (x, 0), (x + height, height), line_width)
            x += spacing
        surface.blit(band, (plot_rect.left, y_top))

    def _rebuild_graph_static_background(self):
        """Everything that never changes while the graph is up: the y-axis never
        rescales, and the target/failure bands are fixed MW ranges, so all of this
        only needs to be drawn once instead of every frame.
        """
        bg = pygame.Surface(GRAPH_SIZE_PX)
        bg.fill(BLACK)
        plot_rect = self.graph_plot_rect

        self._draw_translucent_band(bg, self.TARGET_POWER_LOWER_MW, self.TARGET_POWER_UPPER_MW,
                                     GREEN, TARGET_ZONE_ALPHA)
        self._draw_hatched_band(bg, self.TARGET_POWER_UPPER_MW, self.FAILURE_POWER_MW,
                                 AMBER, WARNING_ZONE_ALPHA)
        self._draw_translucent_band(bg, self.FAILURE_POWER_MW,
                                     min(self.FAILURE_ZONE_TOP_MW, self.Y_AXIS_MAX_MW),
                                     RED, FAILURE_ZONE_ALPHA)

        mw = self.Y_AXIS_MIN_MW
        while mw <= self.Y_AXIS_MAX_MW:
            y = self._mw_to_px(mw)
            pygame.draw.line(bg, GRID_COLOR, (plot_rect.left, y), (plot_rect.right, y))
            label = self.graph_tick_font.render(f"{mw:.0f}", True, GREEN)
            bg.blit(label, (plot_rect.left - label.get_width() - 6, y - label.get_height() // 2))
            mw += Y_GRID_STEP_MW

        pygame.draw.rect(bg, GREEN, plot_rect, GRAPH_BORDER_WIDTH)

        power_label = self.graph_label_font.render("Power (MW)", True, GREEN)
        bg.blit(power_label, (plot_rect.left, plot_rect.top - power_label.get_height() - 8))

        time_label = self.graph_label_font.render("Time (s)", True, GREEN)
        bg.blit(time_label, (plot_rect.centerx - time_label.get_width() // 2, plot_rect.bottom + 28))

        self.graph_static_bg = bg

    # -- Reactor vessel diagram -------------------------------------------

    def _init_reactor_vessel(self):
        self.reactor_surface = pygame.Surface(REACTOR_SIZE_PX)
        self.reactor_title_font = pygame.font.Font(FONT_PATH, 20)
        self.reactor_legend_font = pygame.font.Font(FONT_PATH, 13)
        self._build_reactor_static_bg()
        self._build_cherenkov_levels()
        self._build_shim_overlay()

    @staticmethod
    def _fill_pixel_dome(surface, color, cx, cy, rx, ry, is_top, step):
        """Fill the top (or bottom) half of an ellipse as chunky horizontal bars, each
        `step` px tall and quantised to `step` px wide - so the vessel's domed heads
        read as a blocky, pixelated shell rather than a smooth circle.
        """
        if is_top:
            y0, y1 = int(cy - ry), int(cy)
        else:
            y0, y1 = int(cy), int(cy + ry)
        y = y0
        while y < y1:
            frac = 1.0 - ((y + step / 2 - cy) / ry) ** 2
            if frac > 0:
                w = min(rx, max(step, round(rx * math.sqrt(frac) / step) * step))
                surface.fill(color, (int(cx - w), y, int(2 * w), min(step, y1 - y)))
            y += step

    def _build_reactor_static_bg(self):
        """The parts of the vessel that never move: the metal pressure vessel and its
        drive housings, the coolant fill, and the green fuel rods. The control rods
        and the Cherenkov glow are drawn over this every frame in _draw_reactor_vessel.
        """
        bg = pygame.Surface(REACTOR_SIZE_PX)
        bg.fill(BLACK)

        vessel_w = VESSEL_RIGHT_PX - VESSEL_LEFT_PX
        rx = vessel_w / 2
        rx_in = rx - VESSEL_WALL_PX
        ry_in = VESSEL_DOME_H_PX - VESSEL_WALL_PX
        interior_left = VESSEL_LEFT_PX + VESSEL_WALL_PX
        interior_right = VESSEL_RIGHT_PX - VESSEL_WALL_PX
        step = VESSEL_PIXEL_STEP

        # Metal pressure vessel silhouette: a straight cylindrical body capped by a
        # blocky (pixelated) domed head top and bottom.
        body_rect = pygame.Rect(VESSEL_LEFT_PX, VESSEL_BODY_TOP_PX,
                                vessel_w, VESSEL_BODY_BOTTOM_PX - VESSEL_BODY_TOP_PX)
        self._fill_pixel_dome(bg, VESSEL_METAL, REACTOR_CX, VESSEL_BODY_TOP_PX, rx, VESSEL_DOME_H_PX, True, step)
        self._fill_pixel_dome(bg, VESSEL_METAL, REACTOR_CX, VESSEL_BODY_BOTTOM_PX, rx, VESSEL_DOME_H_PX, False, step)
        pygame.draw.rect(bg, VESSEL_METAL, body_rect)

        # Coolant: the same silhouette inset by the wall thickness. The straight body
        # section gets a top-to-bottom light-blue gradient (lighter near the surface,
        # darker with depth); the blocky domes take the nearest gradient endpoint.
        self._fill_pixel_dome(bg, COOLANT_TOP_COLOR, REACTOR_CX, VESSEL_BODY_TOP_PX, rx_in, ry_in, True, step)
        self._fill_pixel_dome(bg, COOLANT_BOTTOM_COLOR, REACTOR_CX, VESSEL_BODY_BOTTOM_PX, rx_in, ry_in, False, step)
        for y in range(VESSEL_BODY_TOP_PX, VESSEL_BODY_BOTTOM_PX):
            t = (y - VESSEL_BODY_TOP_PX) / (VESSEL_BODY_BOTTOM_PX - VESSEL_BODY_TOP_PX)
            pygame.draw.line(bg, _lerp_color(COOLANT_TOP_COLOR, COOLANT_BOTTOM_COLOR, t),
                             (interior_left, y), (interior_right, y))

        # Fuel rods: a lattice of thin glowing-green rods filling the core, each with a
        # brighter centre highlight so it reads as self-luminous. The movable control
        # rods slide down over these.
        spacing = 11
        rod_w = 6
        x = CORE_LEFT_PX
        while x <= CORE_RIGHT_PX - rod_w:
            pygame.draw.rect(bg, FUEL_ROD_COLOR, (x, CORE_TOP_PX, rod_w, CORE_BOTTOM_PX - CORE_TOP_PX))
            pygame.draw.line(bg, FUEL_ROD_GLOW, (x + rod_w // 2, CORE_TOP_PX),
                             (x + rod_w // 2, CORE_BOTTOM_PX))
            x += spacing

        # Straight wall bevel down the body sides (light left, dark right).
        pygame.draw.line(bg, VESSEL_METAL_LIGHT, (interior_left, VESSEL_BODY_TOP_PX),
                         (interior_left, VESSEL_BODY_BOTTOM_PX), 2)
        pygame.draw.line(bg, VESSEL_METAL_DARK, (interior_right, VESSEL_BODY_TOP_PX),
                         (interior_right, VESSEL_BODY_BOTTOM_PX), 2)

        # Control-rod drive housings: a metal tube above the head per rod, with a darker
        # hollow slot the rod retracts up into (the rod itself is drawn over this).
        for x_center, group in CONTROL_RODS:
            half_w = ROD_HALF_WIDTHS[group]
            housing = pygame.Rect(x_center - half_w - 4, CRDM_HOUSING_TOP_PX,
                                  2 * half_w + 8, VESSEL_BODY_TOP_PX - VESSEL_DOME_H_PX + 12 - CRDM_HOUSING_TOP_PX)
            pygame.draw.rect(bg, VESSEL_METAL, housing)
            pygame.draw.rect(bg, VESSEL_METAL_DARK, housing, 2)
            pygame.draw.rect(bg, CONTROL_ROD_DARK,
                             (x_center - half_w, CRDM_HOUSING_TOP_PX, 2 * half_w, housing.height))

        title = self.reactor_title_font.render("REACTOR CORE", True, GREEN)
        title_y = VESSEL_BODY_BOTTOM_PX + VESSEL_DOME_H_PX + 6
        bg.blit(title, (REACTOR_CX - title.get_width() // 2, title_y))

        legend = [
            ("SAFETY RODS: LEFT LEVER", WHITE),
            ("REGULATING RODS: MID LEVER", WHITE),
            ("SCRAM RODS: DROP ON SCRAM", SCRAM_ROD_CAP_COLOR),
            ("CHEM SHIM: RIGHT LEVER (REDDENS WATER)", WHITE),
        ]
        ly = title_y + title.get_height() + 6
        for line, colour in legend:
            text = self.reactor_legend_font.render(line, True, colour)
            bg.blit(text, (REACTOR_CX - text.get_width() // 2, ly))
            ly += text.get_height() + 2

        self.reactor_static_bg = bg

    def _make_glow(self, width, height, peak_alpha):
        """A soft radial blue glow, built once, by stacking filled ellipses from a
        large faint one down to a small bright one (each overwrites the centre, so the
        result is a radial alpha ramp peaking at peak_alpha in the middle).
        """
        glow = pygame.Surface((width, height), pygame.SRCALPHA)
        steps = 26
        for s in range(steps):
            t = s / (steps - 1)  # 0 = outer/faint, 1 = centre/brightest
            rw = (width / 2) * (1.0 - 0.92 * t)
            rh = (height / 2) * (1.0 - 0.92 * t)
            rect = pygame.Rect(width / 2 - rw, height / 2 - rh, 2 * rw, 2 * rh)
            pygame.draw.ellipse(glow, (*CHERENKOV_COLOR, int(peak_alpha * t)), rect)
        return glow

    def _build_cherenkov_levels(self):
        glow_w = (CORE_RIGHT_PX - CORE_LEFT_PX) + 150
        glow_h = (CORE_BOTTOM_PX - CORE_TOP_PX) + 150
        self.cherenkov_levels = []
        for i in range(CHERENKOV_LEVELS):
            peak = CHERENKOV_MIN_ALPHA + (CHERENKOV_MAX_ALPHA - CHERENKOV_MIN_ALPHA) * i / (CHERENKOV_LEVELS - 1)
            self.cherenkov_levels.append(self._make_glow(glow_w, glow_h, peak))

    def _build_shim_overlay(self):
        """A red wash shaped like the coolant, blitted over the water each frame with a
        per-surface alpha set from the chemical-shim level. Built once as a plain
        (non-per-pixel) surface with a black colour-key so set_alpha can re-dim it
        cheaply every frame (which a per-pixel-alpha surface can't do).
        """
        overlay = pygame.Surface(REACTOR_SIZE_PX)
        overlay.fill(BLACK)
        vessel_w = VESSEL_RIGHT_PX - VESSEL_LEFT_PX
        rx_in = vessel_w / 2 - VESSEL_WALL_PX
        ry_in = VESSEL_DOME_H_PX - VESSEL_WALL_PX
        step = VESSEL_PIXEL_STEP
        # Match the (pixelated) coolant silhouette so the red wash lines up with the water.
        self._fill_pixel_dome(overlay, SHIM_TINT_COLOR, REACTOR_CX, VESSEL_BODY_TOP_PX, rx_in, ry_in, True, step)
        self._fill_pixel_dome(overlay, SHIM_TINT_COLOR, REACTOR_CX, VESSEL_BODY_BOTTOM_PX, rx_in, ry_in, False, step)
        body = pygame.Rect(VESSEL_LEFT_PX + VESSEL_WALL_PX, VESSEL_BODY_TOP_PX,
                           vessel_w - 2 * VESSEL_WALL_PX, VESSEL_BODY_BOTTOM_PX - VESSEL_BODY_TOP_PX)
        pygame.draw.rect(overlay, SHIM_TINT_COLOR, body)
        overlay.set_colorkey(BLACK)
        self.shim_overlay = overlay

    def _draw_reactor_vessel(self, rod_insertions, power_fraction, shim_fraction):
        """rod_insertions: {"safety", "regulating", "scram"} -> insertion in [0, 1],
        where 1 is a fully inserted (fully down) rod. power_fraction sets the Cherenkov
        glow brightness; shim_fraction reddens the coolant. All in [0, 1].
        """
        surface = self.reactor_surface
        surface.blit(self.reactor_static_bg, (0, 0))

        interior_rect = pygame.Rect(
            VESSEL_LEFT_PX + VESSEL_WALL_PX, VESSEL_BODY_TOP_PX,
            (VESSEL_RIGHT_PX - VESSEL_WALL_PX) - (VESSEL_LEFT_PX + VESSEL_WALL_PX),
            VESSEL_BODY_BOTTOM_PX - VESSEL_BODY_TOP_PX,
        )

        # Cherenkov glow over the core, brighter with power. Clipped to the coolant
        # column so the soft glow doesn't spill out over the metal walls / black panel.
        idx = min(max(int(round(power_fraction * (CHERENKOV_LEVELS - 1))), 0), CHERENKOV_LEVELS - 1)
        glow = self.cherenkov_levels[idx]
        core_cx = (CORE_LEFT_PX + CORE_RIGHT_PX) // 2
        core_cy = (CORE_TOP_PX + CORE_BOTTOM_PX) // 2
        surface.set_clip(interior_rect)
        surface.blit(glow, (core_cx - glow.get_width() // 2, core_cy - glow.get_height() // 2))
        surface.set_clip(None)

        # Chemical shim: wash the coolant redder the more boron is dissolved in it.
        self.shim_overlay.set_alpha(int(min(max(shim_fraction, 0.0), 1.0) * SHIM_MAX_ALPHA))
        surface.blit(self.shim_overlay, (0, 0))

        # Rods at their current insertion depth. Each rod is one core-height long, so
        # insertion 1.0 exactly fills the core and 0.0 lifts it clear, up into its drive
        # housing. Scram rods carry an amber cap and normally sit withdrawn.
        core_h = CORE_BOTTOM_PX - CORE_TOP_PX
        for x_center, group in CONTROL_RODS:
            f = rod_insertions[group]
            half_w = ROD_HALF_WIDTHS[group]
            rod_top = int(CORE_TOP_PX - (1.0 - f) * core_h)

            # Thin drive shaft from the rod top up into the housing.
            shaft_w = max(3, half_w // 2)
            pygame.draw.rect(surface, CONTROL_ROD_DARK,
                             (x_center - shaft_w // 2, CRDM_HOUSING_TOP_PX, shaft_w, rod_top - CRDM_HOUSING_TOP_PX))

            # Flat, unshaded rod body (retro look). Scram rods keep their amber cap.
            pygame.draw.rect(surface, CONTROL_ROD_COLOR, (x_center - half_w, rod_top, 2 * half_w, core_h))
            if group == "scram":
                pygame.draw.rect(surface, SCRAM_ROD_CAP_COLOR, (x_center - half_w, rod_top, 2 * half_w, 7))

        self.screen.blit(surface, REACTOR_ORIGIN_PX)

    @staticmethod
    def _power_str(power):
        return f"Power = {power:.3f} MW"

    def _display_power(self):
        """Reactor power for the graph/HUD only - never the raw game-logic value."""
        return max(self.pk.n, self.MIN_DISPLAY_POWER_MW)

    def _keff_str(self, k_eff):
        value = "MAXIMUM!" if k_eff == self.max_allowable_k_eff else f"{k_eff:.5f}"
        return f"k_eff = {value}"

    @staticmethod
    def _time_at_target_str(seconds_at_target):
        return f"Time at target \n= {seconds_at_target:.2f} s"

    @staticmethod
    def _time_elapsed_str(seconds_elapsed):
        return f"Time played = {seconds_elapsed:.2f} s"


    # -- Per-frame rendering ----------------------------------------------

    def _record_history_sample(self):
        elapsed = time.time() - self.graph_start_time
        self.full_history_times.append(elapsed)
        self.full_history_powers.append(self._display_power())

    def _blit_graph(self):
        self.screen.blit(self.graph_surface, GRAPH_ORIGIN_PX)

    def _render_graph(self, window_start, window_end, title, title_font, hud_lines):
        surface = self.graph_surface
        plot_rect = self.graph_plot_rect
        surface.blit(self.graph_static_bg, (0, 0))

        # X gridlines + labels are the only dynamic part of the axes, since the
        # window slides/widens over time. Step adapts to the window's width so a
        # wide (post-win, full-session) window doesn't draw hundreds of lines.
        step = max(1, round((window_end - window_start) / 8))
        t = math.ceil(window_start / step) * step
        while t <= window_end:
            x = self._time_to_px(t, window_start, window_end)
            pygame.draw.line(surface, GRID_COLOR, (x, plot_rect.top), (x, plot_rect.bottom))
            label = self.graph_tick_font.render(f"{t:.0f}", True, GREEN)
            surface.blit(label, (x - label.get_width() // 2, plot_rect.bottom + 6))
            t += step

        # Power line: only the points within the visible window (found by bisecting
        # the monotonically increasing timestamps), plus one point on either side so
        # the line doesn't visibly start/end mid-air at the window's edge.
        left = bisect.bisect_left(self.full_history_times, window_start)
        right = bisect.bisect_right(self.full_history_times, window_end)
        left = max(0, left - 1)
        right = min(len(self.full_history_times), right + 1)
        points = [
            (self._time_to_px(t, window_start, window_end), self._mw_to_px(mw))
            for t, mw in zip(self.full_history_times[left:right], self.full_history_powers[left:right])
        ]
        if len(points) >= 2:
            surface.set_clip(plot_rect)
            pygame.draw.lines(surface, GREEN, False, points, GRAPH_LINE_WIDTH)
            surface.set_clip(None)

        title_surface = title_font.render(title, True, GREEN)
        surface.blit(title_surface, (GRAPH_SIZE_PX[0] // 2 - title_surface.get_width() // 2, 10))

        hud_x, hud_y = plot_rect.left + 10, plot_rect.top + 10
        for line in hud_lines:
            for sub_line in line.split("\n"):
                text_surface = self.graph_label_font.render(sub_line, True, GREEN)
                surface.blit(text_surface, (hud_x, hud_y))
                hud_y += text_surface.get_height() + 2
            hud_y += 6

    def _hud_lines(self, time_elapsed_value):
        return [
            self._power_str(self._display_power()),
            self._keff_str(self.k_eff),
            self._time_at_target_str(self.time_at_target_condition),
            self._time_elapsed_str(time_elapsed_value),
        ]

    def _update_graph(self):
        elapsed = time.time() - self.graph_start_time
        # Window is always N_HISTORY_WINDOW_S wide. Starts pinned at 0 (never shows
        # negative/pre-game time), so the live point crawls from the left edge; once
        # elapsed passes LIVE_POINT_FRACTION * N_HISTORY_WINDOW_S the window starts
        # rolling forward to hold the live point at that fraction across, rather than
        # letting it reach the right edge.
        window_start = max(0.0, elapsed - self.LIVE_POINT_FRACTION * self.N_HISTORY_WINDOW_S)
        window_end = window_start + self.N_HISTORY_WINDOW_S

        self._render_graph(window_start, window_end, "REACTOR SIMULATOR 9000",
                            self.graph_title_font, self._hud_lines(elapsed))
        self._blit_graph()

    def _draw_final_graph(self):
        """Freeze the graph on the full 0-n second power trace for the win screen."""
        self._render_graph(0.0, self.final_elapsed_time, "!!!YOU WIN!!!",
                            self.graph_win_title_font, self._hud_lines(self.final_elapsed_time))
        self._blit_graph()

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
        self.leaderboard_entries = entries[:LEADERBOARD_MAX_ENTRIES]
        self._rebuild_leaderboard_surface()

    def _clear_leaderboard(self):
        open(RAW_SCORES_PATH, "w").close()
        if self.pk_n_animation:
            self._load_leaderboard()

    def _rebuild_leaderboard_surface(self):
        # Rendered once here (whenever the leaderboard changes) rather than every
        # frame in _draw_leaderboard(), which just blits this cached surface.
        surface = pygame.Surface((int(LEADERBOARD_SIZE_PX[0]), int(LEADERBOARD_SIZE_PX[1])))
        surface.fill(BLACK)

        header = self.fps_font.render("LEADERBOARD", True, GREEN)
        surface.blit(header, (0, 0))
        y = header.get_height() + 10

        for rank, (elapsed, name) in enumerate(self.leaderboard_entries, start=1):
            row = self.fps_font.render(f"{rank}. {name} - {elapsed:.2f}s", True, WHITE)
            surface.blit(row, (0, y))
            y += row.get_height() + 4

        self._leaderboard_surface = surface

    def _draw_leaderboard(self):
        self.screen.blit(self._leaderboard_surface, LEADERBOARD_ORIGIN_PX)

    # -- Fuel-temperature dial --------------------------------------------

    def _init_thermometer(self):
        self.thermo_surface = pygame.Surface((int(THERMO_SIZE_PX[0]), int(THERMO_SIZE_PX[1])))
        self.thermo_title_font = pygame.font.Font(FONT_PATH, 22)
        self.thermo_tick_font = pygame.font.Font(FONT_PATH, 14)
        self.thermo_value_font = pygame.font.Font(FONT_PATH, 42)
        self.thermo_note_font = pygame.font.Font(FONT_PATH, 15)
        self.thermo_center = (int(THERMO_SIZE_PX[0]) // 2, 320)
        self.thermo_radius = 165
        self._build_thermometer_static()

    @staticmethod
    def _temp_to_angle(temp):
        frac = min(max((temp - DIAL_MIN_C) / (DIAL_MAX_C - DIAL_MIN_C), 0.0), 1.0)
        return math.radians(DIAL_START_ANGLE_DEG + frac * DIAL_SWEEP_DEG)

    def _dial_point(self, angle, radius):
        cx, cy = self.thermo_center
        return (cx + radius * math.cos(angle), cy + radius * math.sin(angle))

    @staticmethod
    def _temp_zone_color(temp):
        if temp >= DIAL_RED_C:
            return RED
        if temp >= DIAL_WARN_C:
            return AMBER
        return GREEN

    def _build_thermometer_static(self):
        """The dial face - title, coloured zone arc, tick marks + labels - drawn once.
        Only the needle and the digital read-out are redrawn each frame.
        """
        surf = pygame.Surface((int(THERMO_SIZE_PX[0]), int(THERMO_SIZE_PX[1])))
        surf.fill(BLACK)

        title = self.thermo_title_font.render("FUEL TEMPERATURE", True, GREEN)
        surf.blit(title, (surf.get_width() // 2 - title.get_width() // 2, 12))

        # Coloured zone arc: green up to the warning temp, amber to the scram temp, red past it.
        for t0, t1, colour in ((DIAL_MIN_C, DIAL_WARN_C, GREEN),
                               (DIAL_WARN_C, DIAL_RED_C, AMBER),
                               (DIAL_RED_C, DIAL_MAX_C, RED)):
            a0, a1 = self._temp_to_angle(t0), self._temp_to_angle(t1)
            n = max(2, int((a1 - a0) / math.radians(3)))
            pts = [self._dial_point(a0 + (a1 - a0) * i / n, self.thermo_radius) for i in range(n + 1)]
            pygame.draw.lines(surf, colour, False, pts, 10)

        # Tick marks + labels every 200 C.
        t = DIAL_MIN_C
        while t <= DIAL_MAX_C:
            a = self._temp_to_angle(t)
            pygame.draw.line(surf, WHITE, self._dial_point(a, self.thermo_radius - 16),
                             self._dial_point(a, self.thermo_radius), 2)
            label = self.thermo_tick_font.render(str(t), True, WHITE)
            lx, ly = self._dial_point(a, self.thermo_radius - 36)
            surf.blit(label, (lx - label.get_width() // 2, ly - label.get_height() // 2))
            t += 200

        note = self.thermo_note_font.render(f"SCRAM AT {config.SCRAM_TEMPERATURE_C} C", True, RED)
        surf.blit(note, (surf.get_width() // 2 - note.get_width() // 2,
                         self.thermo_center[1] + self.thermo_radius + 74))

        self.thermo_static = surf

    def _draw_thermometer(self, temp):
        surf = self.thermo_surface
        surf.blit(self.thermo_static, (0, 0))
        zone = self._temp_zone_color(temp)

        # Needle from the hub to the current temperature.
        tip = self._dial_point(self._temp_to_angle(temp), self.thermo_radius - 22)
        pygame.draw.line(surf, zone, self.thermo_center, tip, 4)
        pygame.draw.circle(surf, WHITE, self.thermo_center, 9)
        pygame.draw.circle(surf, zone, self.thermo_center, 5)

        # Digital read-out below the dial.
        value = self.thermo_value_font.render(f"{temp:.0f} C", True, zone)
        surf.blit(value, (surf.get_width() // 2 - value.get_width() // 2,
                          self.thermo_center[1] + self.thermo_radius + 22))

        self.screen.blit(surf, THERMO_ORIGIN_PX)

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
            self._advance_effective_levers(lever_rel_pos, self.clock.get_time() / 1000.0)

            ##!! To start the game: check if both buttons are pressed and all switches are on
            if self.panel_states.button_states["left_button"] and self.panel_states.button_states["right_button"]:
                if all(self.panel_states.switch_states.values()):
                    self.screen.fill(BLACK)
                    if not self.running:
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
                # fuel faster. Power is MW in the game but the model works in SI, so it's
                # converted to watts. Overheating past the scram temp trips an auto-SCRAM.
                switches_on = sum(1 for on in self.panel_states.switch_states.values() if on)
                mass_flow = config.BASE_MASS_FLOW_RATE + config.FLOW_RATE_PER_SWITCH * switches_on
                temp_rate = self.temperature_model.rate_of_fuel_temperature_change(
                    self.pk.n * 1e6, mass_flow, self.temperature)
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
                self.pygame_k_eff = self.MIN_ALLOWABLE_K_EFF
                if self.scram_lock_remaining_s <= 0.0:
                    self.scramming = False
            else:
                self.pygame_k_eff += self.inc if self.lifting_rod else 0
                self.pygame_k_eff -= self.inc if self.lowering_rod else 0
                self.pygame_k_eff = min(max(self.MIN_ALLOWABLE_K_EFF, self.pygame_k_eff), self.max_allowable_k_eff)

            self.k_eff = self.pygame_k_eff

            self._draw_fps()
            if self.pk_n_animation:
                # The right column shows the live fuel-temperature dial during play;
                # the leaderboard only takes it over once the game has been won.
                if victory_flag:
                    self._draw_leaderboard()
                else:
                    self._draw_thermometer(self.temperature)
                # Safety (left lever) and regulating (mid lever) rods track their
                # levers' effective (drawn-out) positions and are unaffected by a SCRAM.
                # The scram rods sit withdrawn and only drop - fully, immediately -
                # while a SCRAM lock is active (self.scramming). The right lever is now
                # chemical shim: its effective position reddens the coolant.
                eff = self.effective_lever_pos
                rod_insertions = {
                    "safety": min(max(eff[0], 0.0), 1.0),
                    "regulating": min(max(eff[1], 0.0), 1.0),
                    "scram": 1.0 if self.scramming else 0.0,
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
            self.pk.step(self.frame_time, self.k_eff, method="implicit_heun")
            t_end = time.monotonic()
            sleep_length = max(0.0, self.frame_time - (t_end - t_start))
            time.sleep(sleep_length)


if __name__ == "__main__":
    keep_playing = True
    while keep_playing:
        system = System(pk_n_animation=True)
        keep_playing = system.main()
