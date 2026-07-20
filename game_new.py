"""Reactor Simulator 9000 - a pygame control panel for a point-kinetics reactor model."""

import time
import threading
from os import environ

import numpy as np
import matplotlib
import matplotlib.backends.backend_agg as agg
import matplotlib.font_manager as fm
from matplotlib.figure import Figure
from matplotlib.ticker import FormatStrFormatter

from point_kinetics import PointKinetics

environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame  # noqa: E402  (must import after PYGAME_HIDE_SUPPORT_PROMPT is set)




WIDTH, HEIGHT = 1920, 1080
POPUP_WIDTH, POPUP_HEIGHT = 800, 600
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
TRANSPARENT_BLACK = (0, 0, 0, 0)
GREEN = "#74e47c"
FONT_PATH = "./fonts/retro.ttf"


class System:
    """Drives the reactor point-kinetics model and the pygame control-panel UI."""

    N_HISTORY_WINDOW_S = 5  # seconds of power history shown on the graph

    TARGET_POWER_MW = 200
    TARGET_POWER_TOLERANCE_MW = 8
    TARGET_POWER_LOWER_MW = TARGET_POWER_MW - TARGET_POWER_TOLERANCE_MW
    TARGET_POWER_UPPER_MW = TARGET_POWER_MW + TARGET_POWER_TOLERANCE_MW
    TARGET_HOLD_TIME_S = 5.0
    FAILURE_POWER_MW = 250

    MIN_ALLOWABLE_K_EFF = 0.975
    MAX_ALLOWABLE_BETA_FRACTION = 0.95

    # Lever positions in isolation: left ~0.98-1.02, middle ~0.99-1.01, right ~0.995-1.005
    # giving an overall possible k_eff range of roughly 0.965 to 1.035.
    LEVER_DEADZONE_RANGE = [(0.763, 0.87)] * 3
    LEVER_DELTA_FACTORS = [0.01, 0.005, 0.0025]
    LEVER_FACTOR = 0.75

    # Whether the control-rod levers drive k_eff by default ('8' toggles this in-game).
    # update_pygame_keff_from_levers() sets k_eff purely from the current lever position,
    # so it must be off wherever there's no real lever - otherwise it overwrites the
    # keyboard w/s increments back to neutral every frame.
    USE_LEVERS_BY_DEFAULT = True

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
        deltas = [self.LEVER_FACTOR * factor for factor in self.LEVER_DELTA_FACTORS]
        temp_k_eff = 1.0

        for i, rel_pos in enumerate(lever_current_rel_pos):
            low, high = self.LEVER_DEADZONE_RANGE[i]

            if low < rel_pos < high:
                ##!! IN DEADZONE - do not update k_eff
                self.lever_deadzone_states[i] = 0
            elif rel_pos < low:
                ##!! BELOW LOW DEADZONE - increase k_eff
                self.lever_deadzone_states[i] = -1
                diff = low - rel_pos
                temp_k_eff += deltas[i] * diff / low
            else:
                ##!! ABOVE HIGH DEADZONE - decrease k_eff
                self.lever_deadzone_states[i] = 1
                diff = rel_pos - high
                temp_k_eff -= deltas[i] * diff / (1 - high)

        self.pygame_k_eff = temp_k_eff

    # -- Setup -----------------------------------------------------------

    def _init_display(self):
        pygame.init()
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
        self.pk.enable_n_history(self.N_HISTORY_WINDOW_S, self.frame_time)

        upper_time_bound = 0.5 * self.N_HISTORY_WINDOW_S
        self._dynamic_bound_factor = 1.1

        fm.fontManager.addfont(FONT_PATH)
        self.custom_font = fm.FontProperties(fname=FONT_PATH)
        matplotlib.rcParams["font.family"] = self.custom_font.get_name()

        self.fig = Figure(figsize=(9, 8), dpi=100, facecolor="black")
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
        ax.set_title("ATOMIC ARCADE: REACTOR POWER", color=GREEN, weight="bold",
                      fontproperties=self.custom_font, y=1.02)

        t_lims = -self.N_HISTORY_WINDOW_S, upper_time_bound
        t_range = t_lims[1] - t_lims[0]
        ax.set_xlim(*t_lims)
        self._t_lims = t_lims

        self.pk_n_line = ax.plot(
            self.pk.n_history_time_window,
            self.pk.n_history_solutions,
            color=GREEN,
        )[0]

        ax.grid(True, color="grey", linewidth=0.3)

        span = [-self.N_HISTORY_WINDOW_S * 1000, upper_time_bound * 1000,
                upper_time_bound * 1000, -self.N_HISTORY_WINDOW_S * 1000]
        ax.fill(span, [self.TARGET_POWER_LOWER_MW, self.TARGET_POWER_LOWER_MW,
                       self.TARGET_POWER_UPPER_MW, self.TARGET_POWER_UPPER_MW],
                color=GREEN, alpha=0.5)
        ax.fill(span, [self.FAILURE_POWER_MW, self.FAILURE_POWER_MW,
                       self.FAILURE_POWER_MW * self._dynamic_bound_factor,
                       self.FAILURE_POWER_MW * self._dynamic_bound_factor],
                color="red", alpha=0.5)

        bf = self._dynamic_bound_factor
        text_x = t_lims[0] + 0.70 * t_range
        self.power_text = ax.text(text_x, (1 / bf) + 0.95 * (bf - 1 / bf),
                                   self._power_str(self.pk.n), color=GREEN, fontproperties=self.custom_font)
        self.keff_text = ax.text(text_x, (1 / bf) + 0.90 * (bf - 1 / bf),
                                  self._keff_str(self.k_eff), color=GREEN, fontproperties=self.custom_font)
        self.target_time_text = ax.text(text_x, (1 / bf) + 0.85 * (bf - 1 / bf),
                                         self._time_at_target_str(0.0), color=GREEN, fontproperties=self.custom_font)
        self.elapsed_time_text = ax.text(text_x, (1 / bf) + 0.80 * (bf - 1 / bf),
                                          self._time_elapsed_str(0.0), color=GREEN, fontproperties=self.custom_font)

        self.ax = ax
        self.graph_start_time = time.time()
        self._update_graph()

    @staticmethod
    def _power_str(power):
        return f"Power = {power:.3f} MW"

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

    def _update_graph(self):
        self.pk_n_line.set_ydata(self.pk.n_history_solutions)
        self.pk_n_line.set_xdata(self.pk.n_history_time_window)

        n_min, n_max = np.min(self.pk.n_history_solutions), np.max(self.pk.n_history_solutions)
        bf = self._dynamic_bound_factor
        y_lims = n_min / bf, n_max * bf
        y_range = y_lims[1] - y_lims[0]
        self.ax.set_ylim(*y_lims)

        elapsed = time.time() - self.graph_start_time
        self.pk.n_history_time_window = np.linspace(elapsed - 6, elapsed - 1, 151)
        self.ax.set_xlim(elapsed - 5, elapsed)

        t_lims = self._t_lims
        t_range = t_lims[1] - t_lims[0]
        text_x = t_lims[0] + 0.02 * t_range + elapsed

        self.power_text.set_text(self._power_str(self.pk.n))
        self.power_text.set_position((text_x, y_lims[0] + 0.95 * y_range))

        self.keff_text.set_text(self._keff_str(self.k_eff))
        self.keff_text.set_position((text_x, y_lims[0] + 0.90 * y_range))

        self.target_time_text.set_text(self._time_at_target_str(self.time_at_target_condition))
        self.target_time_text.set_position((text_x, y_lims[0] + 0.82 * y_range))

        self.elapsed_time_text.set_text(self._time_elapsed_str(elapsed))
        self.elapsed_time_text.set_position((text_x, y_lims[0] + 0.78 * y_range))

        self.canvas.draw()
        renderer = self.canvas.get_renderer()
        surf = pygame.image.frombuffer(renderer.buffer_rgba(), self.canvas.get_width_height(), "RGBA")
        self.screen.blit(surf, (WIDTH * 0.3, HEIGHT * 0.2))

    def _draw_popup(self, message):
        popup_surface = pygame.Surface((POPUP_WIDTH, POPUP_HEIGHT), pygame.SRCALPHA)
        popup_surface.fill(TRANSPARENT_BLACK)
        self.screen.blit(popup_surface, (0, 0))

        font = pygame.font.Font(FONT_PATH, 24)
        rendered_lines = [font.render(line, True, WHITE) for line in message.split("\n")]
        max_width = max(line.get_width() for line in rendered_lines)
        total_height = sum(line.get_height() for line in rendered_lines)

        y = 0
        for rendered_line in rendered_lines:
            popup_surface.blit(rendered_line, (0, y))
            y += rendered_line.get_height()

        self.screen.blit(popup_surface, (WIDTH // 2 - max_width // 2, HEIGHT // 2 - total_height // 2))
        pygame.display.flip()

    def _draw_fps(self):
        fps_surface = self.fps_font.render(f"FPS: {self.clock.get_fps():.1f}", True, WHITE)
        self.screen.blit(fps_surface, (10, 10))

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

        # LED_strip_ids["top_reactor_leds_ids"] = [21, 22, 23]
        # LED_strip_ids["left_button_leds_ids"] = [4, 6, 5]
        # LED_strip_ids["right_button_leds_ids"] = [1, 2, 3]
        # LED_strip_ids["top_switch_ids"] = [7, 8, 9]
        # LED_strip_ids["top_middle_switch_ids"] = [10, 11, 12]
        # LED_strip_ids["middle_switch_ids"] = [13, 14, 15]
        # LED_strip_ids["bottom_middle_switch_ids"] = [16, 17, 18]
        # LED_strip_ids["bottom_switch_ids"] = [19, 20]
        # LED_strip_ids["left_lever_ids"] = [24, 25, 26]
        # LED_strip_ids["middle_lever_ids"] = [27, 28, 29]
        # LED_strip_ids["right_lever_ids"] = [30, 31, 32]

    def _end_game(self):
        self.running = False
        self.panel_states.turn_off_all_leds()
        if self.pk_thread.is_alive():
            self.pk_thread.join()
        self.pk.reset_sol()

        elapsed = time.time() - getattr(self, "graph_start_time", time.time())
        with open("raw_scores.txt", "a") as raw_scores:
            raw_scores.write("{:.3f},{}\n".format(elapsed, "Placeholdername"))

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
        quit_restart_message = "Press '3D' to quit\nor '1D' to restart.\nAny other key to continue"

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
                    self.time_at_target_condition += self.frame_time
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
                        self.ax.set_title("!!!YOU WIN!!!", color=GREEN, weight="bold",
                                           fontproperties=self.custom_font, y=1.02, fontsize=30)
                        self._update_graph()

                ##!! Update the k_eff value based on lever_rel_pos
                if not self.scramming and use_levers_flag:
                    self.update_pygame_keff_from_levers(lever_rel_pos, lever_origin_rel_pos)

            if self.running:
                self.screen.fill((0, 50, 0))
            if self.scramming:
                self.screen.fill((50, 0, 0))

            if self.pk_n_animation and self.running:
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
