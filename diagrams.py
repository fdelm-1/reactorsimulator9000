"""Pure rendering for Reactor Simulator 9000's on-screen diagrams: the power graph,
the reactor vessel (with its control/scram rods and chemical shim), the fuel-
temperature dial, the coolant-pump indicator, and the leaderboard.

Each class here owns its own cached surfaces/fonts and takes plain values (numbers,
dicts of bools, lists of entries) as arguments - none of them reach into game state
directly, so game_new.py's System decides *when* to draw and *what* to draw, while
this module only decides *how*.
"""

import math
import bisect

import config

from os import environ
environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame  # noqa: E402  (must import after PYGAME_HIDE_SUPPORT_PROMPT is set)


WIDTH, HEIGHT = 1920, 1080
FONT_PATH = "./fonts/retro.ttf"

WHITE = config.WHITE
BLACK = config.BLACK
GREEN = config.GREEN
AMBER = config.AMBER
RED = config.RED
GRID_COLOR = config.GRID


def _lerp_color(color_a, color_b, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(color_a, color_b))


# -- Layout: graph (left-centre), reactor vessel (far left), and the right column
# (pump panel + temperature dial pre-victory, leaderboard post-victory). -----------

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

# Pre-victory, the right column is split into a coolant-pump indicator panel
# stacked above the fuel-temperature dial (moved lower to make room for it).
# Post-victory, the leaderboard takes over the whole column (LEADERBOARD_* above).
PUMP_PANEL_ORIGIN_PX = LEADERBOARD_ORIGIN_PX
PUMP_PANEL_SIZE_PX = (LEADERBOARD_SIZE_PX[0], 170)
PUMP_PANEL_GAP_PX = 20

THERMO_ORIGIN_PX = (LEADERBOARD_ORIGIN_PX[0],
                     PUMP_PANEL_ORIGIN_PX[1] + PUMP_PANEL_SIZE_PX[1] + PUMP_PANEL_GAP_PX)
THERMO_SIZE_PX = (LEADERBOARD_SIZE_PX[0],
                   LEADERBOARD_SIZE_PX[1] - PUMP_PANEL_SIZE_PX[1] - PUMP_PANEL_GAP_PX)
DIAL_MIN_C = 500
DIAL_MAX_C = config.SCRAM_TEMPERATURE_C + 100   # a little headroom past the scram line
DIAL_WARN_C = config.SCRAM_TEMPERATURE_C - 200  # amber zone begins here
DIAL_RED_C = config.SCRAM_TEMPERATURE_C         # red (scram) zone begins here
DIAL_START_ANGLE_DEG = 135                       # min temp sits at lower-left...
DIAL_SWEEP_DEG = 270                             # ...sweeping clockwise to lower-right

REACTOR_SIZE_PX = (480, 880)
# Bottom-aligned with the graph block: the vessel's base sits level with the graph's.
REACTOR_ORIGIN_PX = (40, GRAPH_ORIGIN_PX[1] + GRAPH_SIZE_PX[1] - REACTOR_SIZE_PX[1])

# Vessel palette. Fuel is the same green used elsewhere in the UI.
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
SHIM_TINT_COLOR = (210, 30, 30)
PUMP_ON_COLOR = GREEN
PUMP_OFF_COLOR = (60, 64, 72)


class GraphRenderer:
    """The live/final power-vs-time graph, with target/warning/failure zones and a
    small HUD (power, k_eff, time-at-target, time-elapsed) overlaid on it.
    """

    N_HISTORY_WINDOW_S = 5  # seconds of power history shown on the graph
    # How far across the visible window the live line's leading (current-time) point
    # sits, once there's enough history to place it there - e.g. 0.8 means it settles
    # at 80% of the way along (4s into a 5s-wide window) rather than running to the
    # very right edge, leaving a lookahead gap instead of the line hitting the wall.
    LIVE_POINT_FRACTION = 0.8
    Y_AXIS_MIN_MW = 0
    Y_AXIS_MAX_MW = 300

    def __init__(self, target_lower_mw, target_upper_mw, failure_mw, failure_zone_top_mw):
        self.target_lower_mw = target_lower_mw
        self.target_upper_mw = target_upper_mw
        self.failure_mw = failure_mw
        self.failure_zone_top_mw = failure_zone_top_mw

        self.surface = pygame.Surface(GRAPH_SIZE_PX)
        self.tick_font = pygame.font.Font(FONT_PATH, 14)
        self.label_font = pygame.font.Font(FONT_PATH, 18)
        self.title_font = pygame.font.Font(FONT_PATH, 26)
        self.win_title_font = pygame.font.Font(FONT_PATH, 34)

        self.plot_rect = pygame.Rect(
            GRAPH_MARGIN_LEFT, GRAPH_MARGIN_TOP,
            GRAPH_SIZE_PX[0] - GRAPH_MARGIN_LEFT - GRAPH_MARGIN_RIGHT,
            GRAPH_SIZE_PX[1] - GRAPH_MARGIN_TOP - GRAPH_MARGIN_BOTTOM,
        )

        # Accumulated once per frame via record_sample(); starts empty so the live
        # plot has no pre-filled lead-in and genuinely begins at t=0. Kept as plain
        # lists - the live view only ever processes the slice within the current
        # window (see _render), found by bisecting the monotonically increasing
        # timestamps, so this stays cheap regardless of session length.
        self.full_history_times = []
        self.full_history_powers = []

        self._build_static_background()

    def reset(self):
        self.full_history_times = []
        self.full_history_powers = []

    def record_sample(self, elapsed, power):
        self.full_history_times.append(elapsed)
        self.full_history_powers.append(power)

    def _mw_to_px(self, mw):
        frac = (mw - self.Y_AXIS_MIN_MW) / (self.Y_AXIS_MAX_MW - self.Y_AXIS_MIN_MW)
        return self.plot_rect.bottom - frac * self.plot_rect.height

    def _time_to_px(self, t, window_start, window_end):
        frac = (t - window_start) / (window_end - window_start)
        return self.plot_rect.left + frac * self.plot_rect.width

    def _draw_translucent_band(self, surface, mw_low, mw_high, color_rgb, alpha):
        plot_rect = self.plot_rect
        y_top = max(self._mw_to_px(mw_high), plot_rect.top)
        y_bottom = min(self._mw_to_px(mw_low), plot_rect.bottom)
        if y_bottom <= y_top:
            return
        band = pygame.Surface((plot_rect.width, y_bottom - y_top), pygame.SRCALPHA)
        band.fill((*color_rgb, alpha))
        surface.blit(band, (plot_rect.left, y_top))

    def _draw_hatched_band(self, surface, mw_low, mw_high, color_rgb, alpha, x_phase=0,
                            spacing=WARNING_ZONE_HATCH_SPACING_PX, line_width=WARNING_ZONE_HATCH_WIDTH_PX):
        """Same region as _draw_translucent_band, but filled with diagonal "\" stripes
        instead of a solid colour, so it reads as a hazard-stripe warning rather than
        a plain block. x_phase shifts the whole stripe pattern left by that many
        pixels (mod spacing, so the shift stays cheap over a long session) - used to
        make the stripes scroll in lock-step with the graph's time axis.
        """
        plot_rect = self.plot_rect
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
        x = -height - (x_phase % spacing)
        while x < width:
            pygame.draw.line(band, color, (x, 0), (x + height, height), line_width)
            x += spacing
        surface.blit(band, (plot_rect.left, y_top))

    def _build_static_background(self):
        """Everything that never changes while the graph is up: the y-axis never
        rescales, and the target/failure bands are fixed MW ranges, so all of this
        only needs to be drawn once instead of every frame.
        """
        bg = pygame.Surface(GRAPH_SIZE_PX)
        bg.fill(BLACK)
        plot_rect = self.plot_rect

        self._draw_translucent_band(bg, self.target_lower_mw, self.target_upper_mw,
                                     GREEN, TARGET_ZONE_ALPHA)
        # The warning zone's hazard stripes are drawn dynamically each frame instead
        # (see _render) so they can scroll with the time axis, rather than being
        # baked into this static background.
        self._draw_translucent_band(bg, self.failure_mw,
                                     min(self.failure_zone_top_mw, self.Y_AXIS_MAX_MW),
                                     RED, FAILURE_ZONE_ALPHA)

        mw = self.Y_AXIS_MIN_MW
        while mw <= self.Y_AXIS_MAX_MW:
            y = self._mw_to_px(mw)
            pygame.draw.line(bg, GRID_COLOR, (plot_rect.left, y), (plot_rect.right, y))
            label = self.tick_font.render(f"{mw:.0f}", True, GREEN)
            bg.blit(label, (plot_rect.left - label.get_width() - 6, y - label.get_height() // 2))
            mw += Y_GRID_STEP_MW

        pygame.draw.rect(bg, GREEN, plot_rect, GRAPH_BORDER_WIDTH)

        power_label = self.label_font.render("Power (MW)", True, GREEN)
        bg.blit(power_label, (plot_rect.left, plot_rect.top - power_label.get_height() - 8))

        time_label = self.label_font.render("Time (s)", True, GREEN)
        bg.blit(time_label, (plot_rect.centerx - time_label.get_width() // 2, plot_rect.bottom + 28))

        self.static_bg = bg

    @staticmethod
    def _power_str(power):
        return f"Power = {power:.3f} MW"

    @staticmethod
    def _keff_str(k_eff, is_max):
        value = "MAXIMUM!" if is_max else f"{k_eff:.5f}"
        return f"k_eff = {value}"

    @staticmethod
    def _time_at_target_str(seconds_at_target):
        return f"Time at target \n= {seconds_at_target:.2f} s"

    @staticmethod
    def _time_elapsed_str(seconds_elapsed):
        return f"Time played = {seconds_elapsed:.2f} s"

    def _hud_lines(self, power, k_eff, is_max_k_eff, time_at_target, time_elapsed):
        return [
            self._power_str(power),
            self._keff_str(k_eff, is_max_k_eff),
            self._time_at_target_str(time_at_target),
            self._time_elapsed_str(time_elapsed),
        ]

    def _render(self, window_start, window_end, title, title_font, hud_lines):
        surface = self.surface
        plot_rect = self.plot_rect
        surface.blit(self.static_bg, (0, 0))

        # Slide the warning zone's hazard stripes left in lock-step with the
        # scrolling time axis: 1 second of window time is always
        # plot_rect.width / window_span pixels, so that many pixels of phase
        # shift per second of window_start makes the stripes track the same
        # motion as the gridlines/power line below.
        window_span = window_end - window_start
        phase = (window_start * plot_rect.width / window_span) if window_span > 0 else 0
        self._draw_hatched_band(surface, self.target_upper_mw, self.failure_mw,
                                 AMBER, WARNING_ZONE_ALPHA, x_phase=phase)

        # X gridlines + labels are the only dynamic part of the axes, since the
        # window slides/widens over time. Step adapts to the window's width so a
        # wide (post-win, full-session) window doesn't draw hundreds of lines.
        step = max(1, round((window_end - window_start) / 8))
        t = math.ceil(window_start / step) * step
        while t <= window_end:
            x = self._time_to_px(t, window_start, window_end)
            pygame.draw.line(surface, GRID_COLOR, (x, plot_rect.top), (x, plot_rect.bottom))
            label = self.tick_font.render(f"{t:.0f}", True, GREEN)
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
                text_surface = self.label_font.render(sub_line, True, GREEN)
                surface.blit(text_surface, (hud_x, hud_y))
                hud_y += text_surface.get_height() + 2
            hud_y += 6

    def render_live(self, elapsed, power, k_eff, is_max_k_eff, time_at_target):
        # Window is always N_HISTORY_WINDOW_S wide. Starts pinned at 0 (never shows
        # negative/pre-game time), so the live point crawls from the left edge; once
        # elapsed passes LIVE_POINT_FRACTION * N_HISTORY_WINDOW_S the window starts
        # rolling forward to hold the live point at that fraction across, rather than
        # letting it reach the right edge.
        window_start = max(0.0, elapsed - self.LIVE_POINT_FRACTION * self.N_HISTORY_WINDOW_S)
        window_end = window_start + self.N_HISTORY_WINDOW_S
        hud = self._hud_lines(power, k_eff, is_max_k_eff, time_at_target, elapsed)
        self._render(window_start, window_end, "REACTOR SIMULATOR 9000", self.title_font, hud)

    def render_final(self, total_time, power, k_eff, is_max_k_eff, time_at_target):
        """Freeze the graph on the full 0-n second power trace for the win screen."""
        hud = self._hud_lines(power, k_eff, is_max_k_eff, time_at_target, total_time)
        self._render(0.0, total_time, "!!!YOU WIN!!!", self.win_title_font, hud)

    def blit_to(self, screen):
        screen.blit(self.surface, GRAPH_ORIGIN_PX)


class ReactorVesselRenderer:
    """A cross-section diagram of the reactor pressure vessel: a metallic shell with
    domed heads, light-blue coolant (reddened by chemical shim), glowing-green fuel
    rods, and eight control/scram rods that slide down as they're inserted.
    """

    CX = REACTOR_SIZE_PX[0] // 2
    # The vessel shell (its domed heads) is drawn as chunky horizontal bars this many
    # pixels tall/wide instead of a smooth ellipse, for a blocky retro look.
    PIXEL_STEP = 16
    WALL_PX = 14
    LEFT_PX = CX - 170
    RIGHT_PX = CX + 170
    BODY_TOP_PX = 150
    BODY_BOTTOM_PX = 690
    DOME_H_PX = 70
    CRDM_HOUSING_TOP_PX = 8   # top of the control-rod drive housings above the head

    # Core (fuel + control rods) region, well inside the vessel interior.
    CORE_TOP_PX = 300
    CORE_BOTTOM_PX = 590
    CORE_LEFT_PX = CX - 140
    CORE_RIGHT_PX = CX + 140

    # Eight rods across the core: four lever-driven control rods (two outer safety
    # rods on the left lever, two regulating rods on the middle lever) and four
    # scram rods that sit fully withdrawn until a SCRAM drops them. Laid out
    # symmetrically with the safety rods outermost/thickest. Each entry is
    # (x-centre, group name).
    ROD_HALF_WIDTHS = {"safety": 15, "regulating": 11, "scram": 10}
    SCRAM_ROD_CAP_COLOR = AMBER  # scram rods carry an amber cap to read as the emergency rods at a glance
    CONTROL_RODS = [
        (CX - 118, "safety"),
        (CX - 84, "scram"),
        (CX - 50, "regulating"),
        (CX - 16, "scram"),
        (CX + 16, "scram"),
        (CX + 50, "regulating"),
        (CX + 84, "scram"),
        (CX + 118, "safety"),
    ]

    SHIM_MAX_ALPHA = 175

    # Cherenkov glow is pre-rendered at a handful of discrete intensities (a per-
    # pixel-alpha surface can't be cheaply re-dimmed per frame), and the level
    # nearest the current reactor power is blitted each frame.
    CHERENKOV_LEVELS = 9
    CHERENKOV_MIN_ALPHA = 26
    CHERENKOV_MAX_ALPHA = 150

    def __init__(self):
        self.surface = pygame.Surface(REACTOR_SIZE_PX)
        self.title_font = pygame.font.Font(FONT_PATH, 20)
        self.legend_font = pygame.font.Font(FONT_PATH, 13)
        self._build_static_bg()
        self._build_cherenkov_levels()
        self._build_shim_overlay()

    @staticmethod
    def _fill_pixel_dome(surface, color, cx, cy, rx, ry, is_top, step):
        """Fill the top (or bottom) half of an ellipse as chunky horizontal bars, each
        `step` px tall and quantised to `step` px wide, for a blocky pixelated shell
        instead of a smooth circle.

        Each row's rectangle is extended all the way to y1 (the loop's far edge -
        the body seam for the top dome, the tip for the bottom dome) rather than
        just its own `step`-tall band. Rows are drawn in order from the wide/body
        end to the narrow/tip end, so each later (narrower) row simply paints over
        the centre of the earlier (wider) one, leaving the wider row's "shoulders"
        visible as a staircase all the way down - with no gaps of unpainted
        background between rows of different widths (previously visible as two
        holes flanking the narrow bit at the bottom of the vessel).
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
                surface.fill(color, (int(cx - w), y, int(2 * w), y1 - y))
            y += step

    def _build_static_bg(self):
        """The parts of the vessel that never move: the metal pressure vessel and its
        drive housings, the coolant fill, and the green fuel rods. The control rods
        and the Cherenkov glow are drawn over this every frame in draw().
        """
        bg = pygame.Surface(REACTOR_SIZE_PX)
        bg.fill(BLACK)

        vessel_w = self.RIGHT_PX - self.LEFT_PX
        rx = vessel_w / 2
        rx_in = rx - self.WALL_PX
        ry_in = self.DOME_H_PX - self.WALL_PX
        interior_left = self.LEFT_PX + self.WALL_PX
        interior_right = self.RIGHT_PX - self.WALL_PX
        step = self.PIXEL_STEP

        # Metal pressure vessel silhouette: a straight cylindrical body capped by a
        # blocky (pixelated) domed head top and bottom.
        body_rect = pygame.Rect(self.LEFT_PX, self.BODY_TOP_PX,
                                vessel_w, self.BODY_BOTTOM_PX - self.BODY_TOP_PX)
        self._fill_pixel_dome(bg, VESSEL_METAL, self.CX, self.BODY_TOP_PX, rx, self.DOME_H_PX, True, step)
        self._fill_pixel_dome(bg, VESSEL_METAL, self.CX, self.BODY_BOTTOM_PX, rx, self.DOME_H_PX, False, step)
        pygame.draw.rect(bg, VESSEL_METAL, body_rect)

        # Coolant: the same silhouette inset by the wall thickness. The straight body
        # section gets a top-to-bottom light-blue gradient (lighter near the surface,
        # darker with depth); the blocky domes take the nearest gradient endpoint.
        self._fill_pixel_dome(bg, COOLANT_TOP_COLOR, self.CX, self.BODY_TOP_PX, rx_in, ry_in, True, step)
        self._fill_pixel_dome(bg, COOLANT_BOTTOM_COLOR, self.CX, self.BODY_BOTTOM_PX, rx_in, ry_in, False, step)
        for y in range(self.BODY_TOP_PX, self.BODY_BOTTOM_PX):
            t = (y - self.BODY_TOP_PX) / (self.BODY_BOTTOM_PX - self.BODY_TOP_PX)
            pygame.draw.line(bg, _lerp_color(COOLANT_TOP_COLOR, COOLANT_BOTTOM_COLOR, t),
                             (interior_left, y), (interior_right, y))

        # Fuel rods: a lattice of thin glowing-green rods filling the core, each with a
        # brighter centre highlight so it reads as self-luminous. The movable control
        # rods slide down over these.
        spacing = 11
        rod_w = 6
        x = self.CORE_LEFT_PX
        while x <= self.CORE_RIGHT_PX - rod_w:
            pygame.draw.rect(bg, FUEL_ROD_COLOR, (x, self.CORE_TOP_PX, rod_w, self.CORE_BOTTOM_PX - self.CORE_TOP_PX))
            pygame.draw.line(bg, FUEL_ROD_GLOW, (x + rod_w // 2, self.CORE_TOP_PX),
                             (x + rod_w // 2, self.CORE_BOTTOM_PX))
            x += spacing

        # Straight wall bevel down the body sides (light left, dark right).
        pygame.draw.line(bg, VESSEL_METAL_LIGHT, (interior_left, self.BODY_TOP_PX),
                         (interior_left, self.BODY_BOTTOM_PX), 2)
        pygame.draw.line(bg, VESSEL_METAL_DARK, (interior_right, self.BODY_TOP_PX),
                         (interior_right, self.BODY_BOTTOM_PX), 2)

        # Control-rod drive housings: a metal tube above the head per rod, with a darker
        # hollow slot the rod retracts up into (the rod itself is drawn over this).
        for x_center, group in self.CONTROL_RODS:
            half_w = self.ROD_HALF_WIDTHS[group]
            housing = pygame.Rect(x_center - half_w - 4, self.CRDM_HOUSING_TOP_PX,
                                  2 * half_w + 8, self.BODY_TOP_PX - self.DOME_H_PX + 12 - self.CRDM_HOUSING_TOP_PX)
            pygame.draw.rect(bg, VESSEL_METAL, housing)
            pygame.draw.rect(bg, VESSEL_METAL_DARK, housing, 2)
            pygame.draw.rect(bg, CONTROL_ROD_DARK,
                             (x_center - half_w, self.CRDM_HOUSING_TOP_PX, 2 * half_w, housing.height))

        title = self.title_font.render("REACTOR CORE", True, GREEN)
        title_y = self.BODY_BOTTOM_PX + self.DOME_H_PX + 6
        bg.blit(title, (self.CX - title.get_width() // 2, title_y))

        legend = [
            ("SAFETY RODS: LEFT LEVER", WHITE),
            ("REGULATING RODS: MID LEVER", WHITE),
            ("SCRAM RODS: DROP ON SCRAM", self.SCRAM_ROD_CAP_COLOR),
            ("CHEM SHIM: RIGHT LEVER (REDDENS WATER)", WHITE),
        ]
        ly = title_y + title.get_height() + 6
        for line, colour in legend:
            text = self.legend_font.render(line, True, colour)
            bg.blit(text, (self.CX - text.get_width() // 2, ly))
            ly += text.get_height() + 2

        self.static_bg = bg

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
        glow_w = (self.CORE_RIGHT_PX - self.CORE_LEFT_PX) + 150
        glow_h = (self.CORE_BOTTOM_PX - self.CORE_TOP_PX) + 150
        self.cherenkov_levels = []
        for i in range(self.CHERENKOV_LEVELS):
            peak = self.CHERENKOV_MIN_ALPHA + (self.CHERENKOV_MAX_ALPHA - self.CHERENKOV_MIN_ALPHA) * i / (self.CHERENKOV_LEVELS - 1)
            self.cherenkov_levels.append(self._make_glow(glow_w, glow_h, peak))

    def _build_shim_overlay(self):
        """A red wash shaped like the coolant, blitted over the water each frame with a
        per-surface alpha set from the chemical-shim level. Built once as a plain
        (non-per-pixel) surface with a black colour-key so set_alpha can re-dim it
        cheaply every frame (which a per-pixel-alpha surface can't do).
        """
        overlay = pygame.Surface(REACTOR_SIZE_PX)
        overlay.fill(BLACK)
        vessel_w = self.RIGHT_PX - self.LEFT_PX
        rx_in = vessel_w / 2 - self.WALL_PX
        ry_in = self.DOME_H_PX - self.WALL_PX
        step = self.PIXEL_STEP
        # Match the (pixelated) coolant silhouette so the red wash lines up with the water.
        self._fill_pixel_dome(overlay, SHIM_TINT_COLOR, self.CX, self.BODY_TOP_PX, rx_in, ry_in, True, step)
        self._fill_pixel_dome(overlay, SHIM_TINT_COLOR, self.CX, self.BODY_BOTTOM_PX, rx_in, ry_in, False, step)
        body = pygame.Rect(self.LEFT_PX + self.WALL_PX, self.BODY_TOP_PX,
                           vessel_w - 2 * self.WALL_PX, self.BODY_BOTTOM_PX - self.BODY_TOP_PX)
        pygame.draw.rect(overlay, SHIM_TINT_COLOR, body)
        overlay.set_colorkey(BLACK)
        self.shim_overlay = overlay

    def draw(self, screen, rod_insertions, power_fraction, shim_fraction):
        """rod_insertions: {"safety", "regulating", "scram"} -> insertion in [0, 1],
        where 1 is a fully inserted (fully down) rod. power_fraction sets the Cherenkov
        glow brightness; shim_fraction reddens the coolant. All in [0, 1].
        """
        surface = self.surface
        surface.blit(self.static_bg, (0, 0))

        interior_rect = pygame.Rect(
            self.LEFT_PX + self.WALL_PX, self.BODY_TOP_PX,
            (self.RIGHT_PX - self.WALL_PX) - (self.LEFT_PX + self.WALL_PX),
            self.BODY_BOTTOM_PX - self.BODY_TOP_PX,
        )

        # Cherenkov glow over the core, brighter with power. Clipped to the coolant
        # column so the soft glow doesn't spill out over the metal walls / black panel.
        idx = min(max(int(round(power_fraction * (self.CHERENKOV_LEVELS - 1))), 0), self.CHERENKOV_LEVELS - 1)
        glow = self.cherenkov_levels[idx]
        core_cx = (self.CORE_LEFT_PX + self.CORE_RIGHT_PX) // 2
        core_cy = (self.CORE_TOP_PX + self.CORE_BOTTOM_PX) // 2
        surface.set_clip(interior_rect)
        surface.blit(glow, (core_cx - glow.get_width() // 2, core_cy - glow.get_height() // 2))
        surface.set_clip(None)

        # Chemical shim: wash the coolant redder the more boron is dissolved in it.
        self.shim_overlay.set_alpha(int(min(max(shim_fraction, 0.0), 1.0) * self.SHIM_MAX_ALPHA))
        surface.blit(self.shim_overlay, (0, 0))

        # Rods at their current insertion depth. Each rod is one core-height long, so
        # insertion 1.0 exactly fills the core and 0.0 lifts it clear, up into its drive
        # housing. Scram rods carry an amber cap and normally sit withdrawn.
        core_h = self.CORE_BOTTOM_PX - self.CORE_TOP_PX
        for x_center, group in self.CONTROL_RODS:
            f = rod_insertions[group]
            half_w = self.ROD_HALF_WIDTHS[group]
            rod_top = int(self.CORE_TOP_PX - (1.0 - f) * core_h)

            # Thin drive shaft from the rod top up into the housing.
            shaft_w = max(3, half_w // 2)
            pygame.draw.rect(surface, CONTROL_ROD_DARK,
                             (x_center - shaft_w // 2, self.CRDM_HOUSING_TOP_PX, shaft_w, rod_top - self.CRDM_HOUSING_TOP_PX))

            # Flat, unshaded rod body (retro look). Scram rods keep their amber cap.
            pygame.draw.rect(surface, CONTROL_ROD_COLOR, (x_center - half_w, rod_top, 2 * half_w, core_h))
            if group == "scram":
                pygame.draw.rect(surface, self.SCRAM_ROD_CAP_COLOR, (x_center - half_w, rod_top, 2 * half_w, 7))

        screen.blit(surface, REACTOR_ORIGIN_PX)


class ThermometerRenderer:
    """A dial-style thermometer for fuel temperature: a needle + digital read-out
    over a green/amber/red zoned arc, with a note for the SCRAM temperature.
    """

    MIN_C = DIAL_MIN_C
    MAX_C = DIAL_MAX_C
    WARN_C = DIAL_WARN_C
    RED_C = DIAL_RED_C
    START_ANGLE_DEG = DIAL_START_ANGLE_DEG
    SWEEP_DEG = DIAL_SWEEP_DEG

    def __init__(self):
        self.surface = pygame.Surface((int(THERMO_SIZE_PX[0]), int(THERMO_SIZE_PX[1])))
        self.title_font = pygame.font.Font(FONT_PATH, 22)
        self.tick_font = pygame.font.Font(FONT_PATH, 14)
        self.value_font = pygame.font.Font(FONT_PATH, 42)
        self.note_font = pygame.font.Font(FONT_PATH, 15)
        self.center = (int(THERMO_SIZE_PX[0]) // 2, 320)
        self.radius = 165
        self._build_static()

    @classmethod
    def _temp_to_angle(cls, temp):
        frac = min(max((temp - cls.MIN_C) / (cls.MAX_C - cls.MIN_C), 0.0), 1.0)
        return math.radians(cls.START_ANGLE_DEG + frac * cls.SWEEP_DEG)

    def _dial_point(self, angle, radius):
        cx, cy = self.center
        return (cx + radius * math.cos(angle), cy + radius * math.sin(angle))

    @classmethod
    def _temp_zone_color(cls, temp):
        if temp >= cls.RED_C:
            return RED
        if temp >= cls.WARN_C:
            return AMBER
        return GREEN

    def _build_static(self):
        """The dial face - title, coloured zone arc, tick marks + labels - drawn once.
        Only the needle and the digital read-out are redrawn each frame.
        """
        surf = pygame.Surface((int(THERMO_SIZE_PX[0]), int(THERMO_SIZE_PX[1])))
        surf.fill(BLACK)

        title = self.title_font.render("FUEL TEMPERATURE", True, GREEN)
        surf.blit(title, (surf.get_width() // 2 - title.get_width() // 2, 12))

        # Coloured zone arc: green up to the warning temp, amber to the scram temp, red past it.
        for t0, t1, colour in ((self.MIN_C, self.WARN_C, GREEN),
                               (self.WARN_C, self.RED_C, AMBER),
                               (self.RED_C, self.MAX_C, RED)):
            a0, a1 = self._temp_to_angle(t0), self._temp_to_angle(t1)
            n = max(2, int((a1 - a0) / math.radians(3)))
            pts = [self._dial_point(a0 + (a1 - a0) * i / n, self.radius) for i in range(n + 1)]
            pygame.draw.lines(surf, colour, False, pts, 10)

        # Tick marks + labels every 200 C.
        t = self.MIN_C
        while t <= self.MAX_C:
            a = self._temp_to_angle(t)
            pygame.draw.line(surf, WHITE, self._dial_point(a, self.radius - 16),
                             self._dial_point(a, self.radius), 2)
            label = self.tick_font.render(str(t), True, WHITE)
            lx, ly = self._dial_point(a, self.radius - 36)
            surf.blit(label, (lx - label.get_width() // 2, ly - label.get_height() // 2))
            t += 200

        note = self.note_font.render(f"SCRAM AT {config.SCRAM_TEMPERATURE_C} C", True, RED)
        surf.blit(note, (surf.get_width() // 2 - note.get_width() // 2,
                         self.center[1] + self.radius + 74))

        self.static = surf

    def draw(self, screen, temp):
        surf = self.surface
        surf.blit(self.static, (0, 0))
        zone = self._temp_zone_color(temp)

        # Needle from the hub to the current temperature.
        tip = self._dial_point(self._temp_to_angle(temp), self.radius - 22)
        pygame.draw.line(surf, zone, self.center, tip, 4)
        pygame.draw.circle(surf, WHITE, self.center, 9)
        pygame.draw.circle(surf, zone, self.center, 5)

        # Digital read-out below the dial.
        value = self.value_font.render(f"{temp:.0f} C", True, zone)
        surf.blit(value, (surf.get_width() // 2 - value.get_width() // 2,
                          self.center[1] + self.radius + 22))

        screen.blit(surf, THERMO_ORIGIN_PX)


class PumpPanelRenderer:
    """A row of indicator boxes, one per coolant-pump switch: lit green when on,
    dark when off. Sits above the temperature dial in the right column.
    """

    BOX_SIZE_PX = 46
    BOX_MIN_GAP_PX = 10

    def __init__(self):
        self.surface = pygame.Surface((int(PUMP_PANEL_SIZE_PX[0]), int(PUMP_PANEL_SIZE_PX[1])))
        self.title_font = pygame.font.Font(FONT_PATH, 20)
        self.label_font = pygame.font.Font(FONT_PATH, 13)

    def draw(self, screen, switch_states):
        surf = self.surface
        surf.fill(BLACK)

        title = self.title_font.render("COOLANT PUMPS", True, GREEN)
        surf.blit(title, (surf.get_width() // 2 - title.get_width() // 2, 4))

        names = list(switch_states.keys())
        count = max(1, len(names))
        box = self.BOX_SIZE_PX
        gap = max(self.BOX_MIN_GAP_PX, (surf.get_width() - count * box) // (count + 1))
        total_w = count * box + (count - 1) * gap
        x = surf.get_width() // 2 - total_w // 2
        y = title.get_height() + 30

        for name in names:
            on = switch_states[name]
            color = PUMP_ON_COLOR if on else PUMP_OFF_COLOR
            pygame.draw.rect(surf, color, (x, y, box, box))
            pygame.draw.rect(surf, WHITE, (x, y, box, box), 2)

            label_text = name.replace("_switch", "").replace("_", " ").upper() or "PUMP"
            label = self.label_font.render(label_text, True, WHITE)
            surf.blit(label, (x + box // 2 - label.get_width() // 2, y + box + 4))
            x += box + gap

        screen.blit(surf, PUMP_PANEL_ORIGIN_PX)


class LeaderboardRenderer:
    """The post-victory high-score list, cached as a surface and only rebuilt when
    the entries actually change (a new score, or the leaderboard being cleared).
    """

    def __init__(self):
        self.surface = pygame.Surface((int(LEADERBOARD_SIZE_PX[0]), int(LEADERBOARD_SIZE_PX[1])))
        self.header_font = pygame.font.Font(FONT_PATH, 20)
        self.row_font = pygame.font.Font(FONT_PATH, 20)
        self.entries = []
        self._rebuild()

    def set_entries(self, entries):
        self.entries = entries
        self._rebuild()

    def _rebuild(self):
        surface = self.surface
        surface.fill(BLACK)

        header = self.header_font.render("LEADERBOARD", True, GREEN)
        surface.blit(header, (0, 0))
        y = header.get_height() + 10

        for rank, (elapsed, name) in enumerate(self.entries, start=1):
            row = self.row_font.render(f"{rank}. {name} - {elapsed:.2f}s", True, WHITE)
            surface.blit(row, (0, y))
            y += row.get_height() + 4

    def draw(self, screen):
        screen.blit(self.surface, LEADERBOARD_ORIGIN_PX)
