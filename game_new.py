"""Reactor Simulator 9000 - a pygame control panel for a point-kinetics reactor model."""

import time
import math
import bisect
import threading
from os import environ
import csv

from point_kinetics import PointKinetics

environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame  # noqa: E402  (must import after PYGAME_HIDE_SUPPORT_PROMPT is set)




WIDTH, HEIGHT = 1920, 1080
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREEN = "#74e47c"
GREEN_RGB = (0x74, 0xE4, 0x7C)
RED_RGB = (255, 0, 0)
GRID_COLOR = (90, 90, 90)
FONT_PATH = "./fonts/retro.ttf"

GRAPH_ORIGIN_PX = (WIDTH * 0.3, HEIGHT * 0.2)
GRAPH_SIZE_PX = (900, 800)
GRAPH_MARGIN_LEFT = 70
GRAPH_MARGIN_RIGHT = 20
GRAPH_MARGIN_TOP = 70
GRAPH_MARGIN_BOTTOM = 50
GRAPH_BORDER_WIDTH = 2
TARGET_ZONE_ALPHA = 130
FAILURE_ZONE_ALPHA = 130
Y_GRID_STEP_MW = 50

LEADERBOARD_ORIGIN_PX = (GRAPH_ORIGIN_PX[0] + GRAPH_SIZE_PX[0] + 20, GRAPH_ORIGIN_PX[1])
LEADERBOARD_SIZE_PX = (WIDTH - LEADERBOARD_ORIGIN_PX[0], GRAPH_SIZE_PX[1])
LEADERBOARD_MAX_ENTRIES = 10
RAW_SCORES_PATH = "raw_scores.csv"
LOG_PATH = "function_calls_log.csv"

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

    TARGET_POWER_MW = 200
    TARGET_POWER_TOLERANCE_MW = 8
    TARGET_POWER_LOWER_MW = TARGET_POWER_MW - TARGET_POWER_TOLERANCE_MW
    TARGET_POWER_UPPER_MW = TARGET_POWER_MW + TARGET_POWER_TOLERANCE_MW
    TARGET_HOLD_TIME_S = 5.0
    FAILURE_POWER_MW = 250
    FAILURE_ZONE_TOP_MW = 500  # how far up the graph's red danger band is drawn
    MIN_DISPLAY_POWER_MW = 1  # displayed/plotted power never reads below this

    # Fixed graph y-axis range in MW. Kept constant (no per-frame autoscaling from the
    # data's min/max) so the view never "zooms" and the y-gridlines only need drawing
    # once, into the cached static background (see _rebuild_graph_static_background).
    Y_AXIS_MIN_MW = 0
    Y_AXIS_MAX_MW = 300

    MIN_ALLOWABLE_K_EFF = 0.975

    # k_eff contributed by a given lever when it's pushed all the way up / all the way
    # down (left, middle, right). Each is linear in between and passes through 1.0 a
    # third of the way up, so k_eff is exactly LEVER_COMBINED_MAX_K_EFF (1.007) when all
    # three are simultaneously all the way up, 0.9965 when all three are all the way
    # down, and 1.0 when all three are a third of the way up.
    LEVER_MAX_K_EFF = [1.004, 1.002, 1.001]
    LEVER_MIN_K_EFF = [0.998, 0.999, 0.9995]
    LEVER_COMBINED_MAX_K_EFF = 1.0 + sum(m - 1.0 for m in LEVER_MAX_K_EFF)

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
        self.k_eff = 1.0

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
        self.pk_thread = threading.Thread(target=self.run_pk, args=(1,))
        return self.run_pygame()

    def start_simulation(self):
        self.pk_thread.start()

    def update_pygame_keff_from_levers(self, lever_current_rel_pos, lever_origin_rel_pos=(0.75, 0.75, 0.75)):
        """Each lever contributes linearly across its full travel (LEVER_MIN_K_EFF all
        the way down to LEVER_MAX_K_EFF all the way up), with no flat/dead band - the
        physical lever is a plain slider potentiometer, so its software response
        should track it continuously rather than pinning to 1.0 near the median.
        Since every lever passes through 1.0 exactly a third of the way up, k_eff is
        1.0 when all three sit there, and LEVER_COMBINED_MAX_K_EFF only when all three
        are simultaneously pushed all the way up.
        """
        temp_k_eff = 1.0

        for i, rel_pos in enumerate(lever_current_rel_pos):
            # This hardware reports a higher rel_pos the further DOWN the lever is
            # pushed, so convert to "how far up" before applying the linear response.
            up_fraction = 1.0 - rel_pos
            min_k_eff, max_k_eff = self.LEVER_MIN_K_EFF[i], self.LEVER_MAX_K_EFF[i]
            lever_value = min_k_eff + (max_k_eff - min_k_eff) * up_fraction - 1.0
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

        self.pygame_k_eff = 1.000
        self.inc = 0.00005 * 30 / self.frame_rate
        self.scram_rate = 10 * self.inc
        self.lifting_rod = False
        self.lowering_rod = False
        self.scramming = False

        # Tied to the levers' real combined ceiling so pygame_k_eff is never clamped
        # short of what the levers can actually produce, and "MAXIMUM!" can display.
        self.max_allowable_k_eff = self.LEVER_COMBINED_MAX_K_EFF

        self.running = False
        self.time_at_target_condition = 0.0

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

    def _rebuild_graph_static_background(self):
        """Everything that never changes while the graph is up: the y-axis never
        rescales, and the target/failure bands are fixed MW ranges, so all of this
        only needs to be drawn once instead of every frame.
        """
        bg = pygame.Surface(GRAPH_SIZE_PX)
        bg.fill(BLACK)
        plot_rect = self.graph_plot_rect

        self._draw_translucent_band(bg, self.TARGET_POWER_LOWER_MW, self.TARGET_POWER_UPPER_MW,
                                     GREEN_RGB, TARGET_ZONE_ALPHA)
        self._draw_translucent_band(bg, self.FAILURE_POWER_MW,
                                     min(self.FAILURE_ZONE_TOP_MW, self.Y_AXIS_MAX_MW),
                                     RED_RGB, FAILURE_ZONE_ALPHA)

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

    @staticmethod
    def _print_welcome_message():
        print(
            "Welcome to Reactor Simulator 9000:\n\n"
            "Your mission, should you choose to accept it, is to keep the reactor stable "
            "for 5 seconds at a power of 200 MW.\n"
            "You are allowed 8 MW above or below this target.\n"
            "The reactor will melt-down if it is taken above 250 MW!\n\n"
            "You can control the reactor by pressing 'w' or 'up' to raise the control rods, "
            "and 's' or 'down' to lower them.\n"
            "Press 'space' to SCRAM the reactor to slam the control rods down to stop an "
            "accidental melt-down!\n\n"
            "Hold then release 'enter' to start the simulation."
        )

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
            pygame.draw.aalines(surface, GREEN, False, points)
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
        _log_function_call("_update_graph")
        self.pk_n_line.set_xdata(self.full_history_times)
        self.pk_n_line.set_ydata(self.full_history_powers)

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
        total_time = self.full_history_times[-1]
        self._render_graph(0.0, total_time, "!!!YOU WIN!!!",
                            self.graph_win_title_font, self._hud_lines(total_time))
        self._blit_graph()

    def _load_leaderboard(self):
        entries = []
        '''try:
            with open(RAW_SCORES_PATH) as raw_scores:
                for line in raw_scores:
                    time_str, _, name = line.strip().partition(",")
                    if not time_str:
                        continue
                    try:
                        entries.append((float(time_str), name))
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass'''
        #

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

    def _draw_leaderboard(self):
        x, y = LEADERBOARD_ORIGIN_PX
        # Clear this column first: once self.running is False (idle screen, post-win
        # screen) nothing else repaints the background here, so without this a new
        # score/entry would just be drawn over the top of the previous render instead
        # of replacing it.
        self.screen.fill(BLACK, (x, y, *LEADERBOARD_SIZE_PX))

        header = self.fps_font.render("LEADERBOARD", True, GREEN)
        self.screen.blit(header, (x, y))
        y += header.get_height() + 10

        for rank, (elapsed, name) in enumerate(self.leaderboard_entries, start=1):
            row = self.fps_font.render(f"{rank}. {name} - {elapsed:.2f}s", True, WHITE)
            self.screen.blit(row, (x, y))
            y += row.get_height() + 4

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
        total_elapsed = time.time() - self.graph_start_time
        with open(RAW_SCORES_PATH, "a") as raw_scores:
            score_writer = csv.writer(raw_scores)
            score_writer.writerow([f"{total_elapsed:.3f}", name])
        #  
        if self.pk_n_animation:
            self._load_leaderboard()

    def _log_function_call(self, function_name):
        with open(LOG_PATH, "a", newline="") as log_file:
            log_writer = csv.writer(log_file)
            log_writer.writerow([function_name, time.time()])

    # -- Main loop ----------------------------------------------------------

    def run_pygame(self):
        self._init_display()
        if self.pk_n_animation:
            self._init_graph()
        self._print_welcome_message()
        return self._game_loop()

    def _game_loop(self):
        lever_origin_rel_pos = list(self.panel_states.control_rod_lever_rel_pos.values())
        diff = 0
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
            print(lever_rel_pos[0]-diff)
            diff = lever_rel_pos[0]


            ##!! To start the game: check if both buttons are pressed and all switches are on
            if self.panel_states.button_states["left_button"] and self.panel_states.button_states["right_button"]:
                if all(self.panel_states.switch_states.values()):
                    self.screen.fill(BLACK)
                    if not self.running:
                        self.running = True
                        self.start_simulation()

            if not self.running and not victory_flag:
                self.graph_start_time = time.time()
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
                            self.scramming = True

                    if event.key in (pygame.K_w, pygame.K_UP, pygame.K_2):
                        self.lifting_rod = True

                    if event.key in (pygame.K_s, pygame.K_DOWN, pygame.K_6):
                        self.lowering_rod = True

                    if event.key == pygame.K_8:
                        ##!! Toggle using the levers, instead just use keypad
                        use_levers_flag = not use_levers_flag

                    if event.key == pygame.K_4:
                        if show_quit_popup:
                            ##!! RESTART
                            restart_flag = True
                            print("Restarting the game...")
                            self.running = False
                            pygame_running = False
                        else:
                            show_quit_popup = True
                            self._draw_popup(quit_restart_message)

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

                if self.pk.n > self.FAILURE_POWER_MW:
                    self.scramming = True

                elif self.time_at_target_condition >= self.TARGET_HOLD_TIME_S:
                    print("Congratulations! You have successfully and safely kept the reactor "
                          "stable for 20 seconds at 200 MW!")
                    print("You have helped to keep the country's lights on!")
                    print("Press 'q' or 'escape' to quit.")
                    
                    self._end_game()
                    victory_flag = True
                    if self.pk_n_animation:
                        self._draw_final_graph()
                    name = self._prompt_for_name()
                    if name != "Anonymous":
                        self._record_score(name)
                    # Replace the (now stale) name-entry popup with the existing
                    # quit/restart instructions rather than leaving it on screen.
                    self._draw_popup(quit_restart_message)

                ##!! Update the k_eff value based on lever_rel_pos
                if not self.scramming and use_levers_flag:
                    self.update_pygame_keff_from_levers(lever_rel_pos, lever_origin_rel_pos)

            if self.running:
                self.screen.fill((0, 50, 0))
            if self.scramming:
                self.screen.fill((50, 0, 0))

            if self.pk_n_animation and self.running:
                self._record_history_sample()
                self._update_graph()

            if show_quit_popup:
                self._draw_popup(quit_restart_message)

            self.pygame_k_eff -= self.scram_rate if self.scramming else 0
            self.pygame_k_eff += self.inc if self.lifting_rod else 0
            self.pygame_k_eff -= self.inc if self.lowering_rod else 0
            self.pygame_k_eff = min(max(self.MIN_ALLOWABLE_K_EFF, self.pygame_k_eff), self.max_allowable_k_eff)

            self.k_eff = self.pygame_k_eff

            if self.scramming and self.pygame_k_eff == self.MIN_ALLOWABLE_K_EFF:
                self.scramming = False

            self._draw_fps()
            if self.pk_n_animation:
                self._draw_leaderboard()

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

    def run_pk(self, thread_num):
        print(f"Thread {thread_num} is running the point kinetics.")

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
        if keep_playing:
            print("Restarting the game...")
        else:
            print("Thanks for playing!")
