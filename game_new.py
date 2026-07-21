"""Reactor Simulator 9000 - a pygame control panel for a point-kinetics reactor model."""

import time
import threading
from os import environ

import matplotlib
import matplotlib.backends.backend_agg as agg
import matplotlib.font_manager as fm
from matplotlib.figure import Figure
from matplotlib.ticker import FormatStrFormatter

from point_kinetics import PointKinetics

environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame  # noqa: E402  (must import after PYGAME_HIDE_SUPPORT_PROMPT is set)




WIDTH, HEIGHT = 1920, 1080
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREEN = "#74e47c"
FONT_PATH = "./fonts/retro.ttf"

GRAPH_FIGSIZE_IN = (9, 8)
GRAPH_DPI = 100
GRAPH_ORIGIN_PX = (WIDTH * 0.3, HEIGHT * 0.2)
GRAPH_SIZE_PX = (GRAPH_FIGSIZE_IN[0] * GRAPH_DPI, GRAPH_FIGSIZE_IN[1] * GRAPH_DPI)
LEADERBOARD_ORIGIN_PX = (GRAPH_ORIGIN_PX[0] + GRAPH_SIZE_PX[0] + 20, GRAPH_ORIGIN_PX[1])
LEADERBOARD_SIZE_PX = (WIDTH - LEADERBOARD_ORIGIN_PX[0], GRAPH_SIZE_PX[1])
LEADERBOARD_MAX_ENTRIES = 10
RAW_SCORES_PATH = "raw_scores.txt"

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
    # data's min/max) so the view never "zooms" and set_ylim() is only called once -
    # this was a meaningful chunk of the matplotlib redraw cost per frame on the Pi.
    Y_AXIS_MIN_MW = 0
    Y_AXIS_MAX_MW = 300

    MIN_ALLOWABLE_K_EFF = 0.975
    MAX_ALLOWABLE_BETA_FRACTION = 0.95

    # Deadzone (neutral) band is centred a third of the way up each lever's travel,
    # same total width as before, just recentred.
    LEVER_MEDIAN_REL_POS = 1 / 3
    LEVER_DEADZONE_HALF_WIDTH = 0.0535
    LEVER_DEADZONE_RANGE = [(LEVER_MEDIAN_REL_POS - LEVER_DEADZONE_HALF_WIDTH,
                             LEVER_MEDIAN_REL_POS + LEVER_DEADZONE_HALF_WIDTH)] * 3

    # k_eff reached when a given lever is pushed to the bottom/top of its travel while
    # the other two levers sit at their median (deadzone centre). Left, middle, right.
    # Overall combined range (all three levers pushed the same way at once) is
    # roughly 0.965 to 1.07.
    LEVER_MAX_K_EFF = [1.04, 1.02, 1.01]
    LEVER_MIN_K_EFF = [0.98, 0.99, 0.995]

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
        self.lever_deadzone_states = [0, 0, 0]

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
        """Nudge k_eff based on how far each lever sits outside its central deadzone."""
        temp_k_eff = 1.0

        for i, rel_pos in enumerate(lever_current_rel_pos):
            low, high = self.LEVER_DEADZONE_RANGE[i]

            if low < rel_pos < high:
                ##!! IN DEADZONE - do not update k_eff
                self.lever_deadzone_states[i] = 0
            elif rel_pos < low:
                ##!! BELOW LOW DEADZONE - increase k_eff towards LEVER_MAX_K_EFF[i]
                self.lever_deadzone_states[i] = -1
                diff = low - rel_pos
                temp_k_eff += (self.LEVER_MAX_K_EFF[i] - 1.0) * diff / low
            else:
                ##!! ABOVE HIGH DEADZONE - decrease k_eff towards LEVER_MIN_K_EFF[i]
                self.lever_deadzone_states[i] = 1
                diff = rel_pos - high
                temp_k_eff -= (1.0 - self.LEVER_MIN_K_EFF[i]) * diff / (1 - high)

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

        self.max_allowable_k_eff = 1 + (self.pk.beta * self.MAX_ALLOWABLE_BETA_FRACTION)

        self.running = False
        self.time_at_target_condition = 0.0

    def _init_graph(self):
        upper_time_bound = 0.5 * self.N_HISTORY_WINDOW_S

        fm.fontManager.addfont(FONT_PATH)
        self.custom_font = fm.FontProperties(fname=FONT_PATH)
        matplotlib.rcParams["font.family"] = self.custom_font.get_name()

        self.fig = Figure(figsize=GRAPH_FIGSIZE_IN, dpi=GRAPH_DPI, facecolor="black")
        self.canvas = agg.FigureCanvasAgg(self.fig)
        ax = self.fig.gca()
        ax.set_facecolor("black")
        ax.tick_params(axis="x", colors=GREEN)
        ax.tick_params(axis="y", colors=GREEN)
        for spine in ("bottom", "left", "top", "right"):
            ax.spines[spine].set_visible(True)
            ax.spines[spine].set_color(GREEN)
            ax.spines[spine].set_linewidth(2.0)

        ax.set_xlabel("Time (s)", color=GREEN, weight="bold", fontproperties=self.custom_font, labelpad=15)
        ax.set_ylabel("Power (MW)", color=GREEN, weight="bold", fontproperties=self.custom_font, labelpad=15)
        ax.yaxis.set_major_formatter(FormatStrFormatter("%.1f"))
        ax.set_title("REACTOR SIMULATOR 9000", color=GREEN, weight="bold",
                      fontproperties=self.custom_font, y=1.02)

        # The live view starts at t=0 and grows/rolls forward from there (see
        # _update_graph) - no fixed initial range to hold onto here.
        ax.set_xlim(0, self.N_HISTORY_WINDOW_S)

        # Fixed range, set once - never rescaled per frame.
        ax.set_ylim(self.Y_AXIS_MIN_MW, self.Y_AXIS_MAX_MW)
        self._y_range = self.Y_AXIS_MAX_MW - self.Y_AXIS_MIN_MW

        # Accumulated once per frame in _record_history_sample(); starts empty so the
        # live plot has no pre-filled lead-in and genuinely begins at t=0.
        self.full_history_times = []
        self.full_history_powers = []

        self.pk_n_line = ax.plot(
            self.full_history_times,
            self.full_history_powers,
            color=GREEN,
        )[0]

        ax.grid(True, color="grey", linewidth=0.3)

        span = [-self.N_HISTORY_WINDOW_S * 1000, upper_time_bound * 1000,
                upper_time_bound * 1000, -self.N_HISTORY_WINDOW_S * 1000]
        ax.fill(span, [self.TARGET_POWER_LOWER_MW, self.TARGET_POWER_LOWER_MW,
                       self.TARGET_POWER_UPPER_MW, self.TARGET_POWER_UPPER_MW],
                color=GREEN, alpha=0.5)
        ax.fill(span, [self.FAILURE_POWER_MW, self.FAILURE_POWER_MW,
                       self.FAILURE_ZONE_TOP_MW, self.FAILURE_ZONE_TOP_MW],
                color="red", alpha=0.5)

        text_x = 0.70 * self.N_HISTORY_WINDOW_S
        self.power_text = ax.text(text_x, self.Y_AXIS_MIN_MW + 0.95 * self._y_range,
                                   self._power_str(self._display_power()), color=GREEN, fontproperties=self.custom_font)
        self.keff_text = ax.text(text_x, self.Y_AXIS_MIN_MW + 0.90 * self._y_range,
                                  self._keff_str(self.k_eff), color=GREEN, fontproperties=self.custom_font)
        self.target_time_text = ax.text(text_x, self.Y_AXIS_MIN_MW + 0.82 * self._y_range,
                                         self._time_at_target_str(0.0), color=GREEN, fontproperties=self.custom_font)
        self.elapsed_time_text = ax.text(text_x, self.Y_AXIS_MIN_MW + 0.78 * self._y_range,
                                          self._time_elapsed_str(0.0), color=GREEN, fontproperties=self.custom_font)

        self.ax = ax
        self.graph_start_time = time.time()
        self._load_leaderboard()
        self._update_graph()

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
        self.canvas.draw()
        renderer = self.canvas.get_renderer()
        surf = pygame.image.frombuffer(renderer.buffer_rgba(), self.canvas.get_width_height(), "RGBA")
        self.screen.blit(surf, GRAPH_ORIGIN_PX)

    def _update_graph(self):
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
        self.ax.set_xlim(window_start, window_end)

        text_x = window_start + 0.02 * (window_end - window_start)

        # Y-axis is fixed (see _init_graph), so only the x position needs updating each frame.
        self.power_text.set_text(self._power_str(self._display_power()))
        self.power_text.set_x(text_x)

        self.keff_text.set_text(self._keff_str(self.k_eff))
        self.keff_text.set_x(text_x)

        self.target_time_text.set_text(self._time_at_target_str(self.time_at_target_condition))
        self.target_time_text.set_x(text_x)

        self.elapsed_time_text.set_text(self._time_elapsed_str(elapsed))
        self.elapsed_time_text.set_x(text_x)

        self._blit_graph()

    def _draw_final_graph(self):
        """Freeze the graph on the full 0-n second power trace for the win screen."""
        self.pk_n_line.set_xdata(self.full_history_times)
        self.pk_n_line.set_ydata(self.full_history_powers)

        total_time = self.full_history_times[-1]
        self.ax.set_xlim(0, total_time)
        self.ax.set_title("!!!YOU WIN!!!", color=GREEN, weight="bold",
                           fontproperties=self.custom_font, y=1.02, fontsize=30)

        text_x = total_time * 0.7
        self.power_text.set_text(self._power_str(self._display_power()))
        self.power_text.set_x(text_x)

        self.keff_text.set_text(self._keff_str(self.k_eff))
        self.keff_text.set_x(text_x)

        self.target_time_text.set_text(self._time_at_target_str(self.time_at_target_condition))
        self.target_time_text.set_x(text_x)

        self.elapsed_time_text.set_text(self._time_elapsed_str(total_time))
        self.elapsed_time_text.set_x(text_x)

        self._blit_graph()

    def _load_leaderboard(self):
        entries = []
        try:
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
            pass

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
                    state = self.lever_deadzone_states[0]
                elif "middle" in name:
                    state = self.lever_deadzone_states[1]
                else:
                    state = self.lever_deadzone_states[2]
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
            raw_scores.write("{:.3f},{}\n".format(total_elapsed, name))
        if self.pk_n_animation:
            self._load_leaderboard()

    # -- Main loop ----------------------------------------------------------

    def run_pygame(self):
        self._init_display()
        if self.pk_n_animation:
            self._init_graph()
        self._print_welcome_message()
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
