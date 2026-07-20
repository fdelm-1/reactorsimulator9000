import threading
from time import sleep as time_sleep, monotonic_ns
import numpy as np
from matplotlib.ticker import FormatStrFormatter
import matplotlib.backends.backend_agg as agg
import matplotlib.font_manager as fm
import matplotlib as plt
import pylab

from point_kinetics import PointKinetics
from control_panel_states import MyControlPanelStates
import time



from os import environ
environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame

POPUP_WIDTH = 800
POPUP_HEIGHT = 600
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
TRANSPARENT_BLACK = (0, 0, 0, 0)
    

class System:

    run_time            = 20 ##--seconds
    n_history_window    = 5 ##--seconds

    def __init__(self, framerate = 30, pk_n_animation = False, complexity_level = 1) -> None:
        self.frame_rate = framerate
        self.frame_time = 1 / framerate

        self.pk     = PointKinetics()
        self.k_eff  = 1.0

        self.pk_n_animation = pk_n_animation
        self.complexity_level = complexity_level

        self.running = False

        self.panel_states = MyControlPanelStates()
        self.lever_deadzone_states = {0:0, 1:0, 2:0}
        
    
    def main(self):           
        self.pk_thread      = threading.Thread(target=self.run_pk, args=(1,))
    
        restart_flag = self.run_pygame()
        return restart_flag
    
    def start_simulation(self):
        self.pk_thread.start()

    def update_pygame_keff_from_levers(self, lever_current_rel_pos, lever_origin_rel_pos=[0.75, 0.75, 0.75]):
        ##!! Update the k_eff value based on lever_rel_pos  
        fact = 0.75
        lever_deltas = [0.01*fact, 0.005*fact, 0.0025*fact]
        deadzone_range = [ [0.763, 0.87], [0.763, 0.87], [0.763, 0.87] ]
        temp_keff = 1.0

        for ii in range(len(lever_current_rel_pos)):
            
            if lever_current_rel_pos[ii] < deadzone_range[ii][1] and lever_current_rel_pos[ii] > deadzone_range[ii][0]:
                ##!! IN DEADZONE - Do not update k_eff
                self.lever_deadzone_states[ii] = 0
                temp_keff = temp_keff
                continue
            elif lever_current_rel_pos[ii] < deadzone_range[ii][0]:
                ##!! BELOW LOW DEADZONE - increase k_eff
                self.lever_deadzone_states[ii] = -1
                diff = - lever_current_rel_pos[ii] + deadzone_range[ii][0]
                temp_keff += lever_deltas[ii] * abs(diff) / ( deadzone_range[ii][0])

            else:
                ##!! ABOVE HIGH DEADZONE - Decrease k_eff
                self.lever_deadzone_states[ii] = 1
                diff = - deadzone_range[ii][1] + lever_current_rel_pos[ii] 
                print("Rel Pos:", lever_current_rel_pos[ii] )
                print("Diff   :", diff)
                temp_keff -= lever_deltas[ii] * abs(diff) / ( 1 - deadzone_range[ii][1] ) ##!! deadzone_range - 0.0
                continue
        self.pygame_k_eff = temp_keff

    
    def run_pygame(self):

        pygame.init()

        WIDTH, HEIGHT = 1920,1080
        green_colour = "#8bec92"
        green_colour = "#74e47c"

        screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("Reactor Simulator 9000")

        # Set up the clock
        clock = pygame.time.Clock()
        start_time = time.time()

        self.pygame_k_eff = 1.000

        inc             = 0.00005 * 30 / self.frame_rate
        lifting_rod     = False
        lowering_rod    = False
        scraming        = False
        scram_rate      = 10 * inc

        max_allowable_beta_fraction = 0.95
        max_allowable_k_eff = 1 + (self.pk.beta * max_allowable_beta_fraction)
        min_allowable_k_eff = 0.975

        pygame_running = True
        self.running = False

        target_power                    = 200 ##--MW
        target_power_tolerance          = 8 ##--MW
        target_power_lower_limit        = target_power - target_power_tolerance
        target_power_upper_limit        = target_power + target_power_tolerance
        target_time_at_target_condition = 5.0 ##--s
        time_at_target_condition        = 0.0 ##--s
        elapsed_time = 0.0

        failure_power                   = 250 ##--MW

        print("Welcome to Reactor Simulator 9000:")
        print()
        print("Your mission, should you choose to accept it, is to keep the reactor stable for 20 seconds at a power of 200 MW.")
        print("You are allowed 2 MW above or below this target.")
        print("The reactor will melt-down if it is taken above 250 MW!")
        print()
        print("You can control the reactor by pressing 'w' or 'up' to raise the control rods, and 's' or 'down' to lower them.")
        print("Press 'space' to SCRAM the reactor to slam the control rods down to stop an accidental melt-down!")
        print()
        print("Hold then release 'enter' to start the simulation.")

        set_ylim = lambda ax: ax.set_ylim(np.min(self.pk.n_history_solutions) / 1.1, np.max(self.pk.n_history_solutions) * 1.1)

        if self.pk_n_animation:
            self.pk.enable_n_history(self.n_history_window, self.frame_time)

            upper_time_bound = 0.5 * self.n_history_window
            dynamic_n_bound_factor = 1.1

            font_path = "./fonts/retro.ttf"
            fm.fontManager.addfont(font_path)
            custom_font = fm.FontProperties(fname=font_path)
            plt.rcParams['font.family'] = custom_font.get_name()
            
            #7.9,5.9
	    #w/243 * 1.7
	    #h/183 * 1.7
            fig = pylab.figure(figsize = (9, 8), dpi = 100,facecolor = 'black')
            #fig = pylab.figure(figsize = (12, 10), dpi = 80,facecolor = 'black')

            ax = fig.gca()
            ax.set_ylabel("Power (MW)")
            ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))

            # Set the retro-style colors
            ax.set_facecolor('black')
            ax.tick_params(axis='x', colors=green_colour)
            ax.tick_params(axis='y', colors=green_colour)
            ax.spines['bottom'].set_color(green_colour)
            ax.spines['left'].set_color(green_colour)
            ax.spines['top'].set_visible(True)
            ax.spines['right'].set_visible(True)
            ax.spines['top'].set_color(green_colour)
            ax.spines['right'].set_color(green_colour)
            ax.spines['bottom'].set_linewidth(2.0)
            ax.spines['top'].set_linewidth(2.0)
            ax.spines['right'].set_linewidth(2.0)
            ax.spines['left'].set_linewidth(2.0)

            ax.set_xlabel('Time (s)', color=green_colour, weight='bold',fontproperties=custom_font, labelpad=15)
            ax.set_ylabel('Power (MW)', color=green_colour,weight='bold',fontproperties=custom_font, labelpad=15)
            ax.set_title('ATOMIC ARCADE: REACTOR POWER', color=green_colour,weight='bold',fontproperties=custom_font, y=1.02)

            t_lims = -self.n_history_window, upper_time_bound
            #t_lims = 0,5
            print("TLIMS",t_lims)
            t_range = t_lims[1] - t_lims[0]
            ax.set_xlim(*t_lims)            
            
            pk_n_line = ax.plot(
                self.pk.n_history_time_window,
                self.pk.n_history_solutions,
                color = "#8bec92"
            )[0]

            ax.grid(True, color='grey', linewidth=0.3)

            _target_block = ax.fill(
                [
                    -self.n_history_window*1000,
                    upper_time_bound*1000,
                    upper_time_bound*1000,
                    -self.n_history_window*1000,
                ],
                [
                    target_power_lower_limit,
                    target_power_lower_limit,
                    target_power_upper_limit,
                    target_power_upper_limit,
                ],
                color=green_colour,
                alpha=0.5,
            )[0]

            _failure_block = ax.fill(
                    [
                        -self.n_history_window*1000,
                        upper_time_bound*1000,
                        upper_time_bound*1000,
                        -self.n_history_window*1000,
                    ],
                    [
                        failure_power,
                        failure_power,
                        failure_power * dynamic_n_bound_factor,
                        failure_power * dynamic_n_bound_factor,
                    ],
                    color="red",
                    alpha=0.5,
                )[0]

            get_power_str = lambda power: f"Power = {power:.3f} MW"
            get_k_eff_str = lambda k_eff: f"k_eff = {'MAXIMUM!' if k_eff == max_allowable_k_eff else f'{k_eff:.5f}'}"
            
            get_time_at_target_str  = lambda time_at_target_condition:  f"Time at target \n= {time_at_target_condition:.2f} s"
            get_time_elapsed_str  = lambda time_elapsed:  f"Time played = {time_elapsed:.2f} s"

            power_str =             ax.text(t_lims[0] + 0.70 * t_range, (1/dynamic_n_bound_factor) + 0.95 * (dynamic_n_bound_factor - 1 / dynamic_n_bound_factor), get_power_str(self.pk.n),color=green_colour,fontproperties=custom_font)
            k_eff_str =             ax.text(t_lims[0] + 0.70 * t_range, (1/dynamic_n_bound_factor) + 0.90 * (dynamic_n_bound_factor - 1 / dynamic_n_bound_factor), get_k_eff_str(self.k_eff),color=green_colour,fontproperties=custom_font)
            time_at_target_str  =   ax.text(t_lims[0] + 0.70 * t_range, (1/dynamic_n_bound_factor) + 0.85 * (dynamic_n_bound_factor - 1 / dynamic_n_bound_factor), get_k_eff_str(time_at_target_condition),color=green_colour,fontproperties=custom_font)
            time_elapsed_str =      ax.text(t_lims[0] + 0.70 * t_range, (1/dynamic_n_bound_factor) + 0.80 * (dynamic_n_bound_factor - 1 / dynamic_n_bound_factor), get_time_elapsed_str(0.0),color=green_colour,fontproperties=custom_font)

            # Start time for elapsed time calculation just for first plot
            start_time = time.time()
        
            def update_pk_n_graph_to_display():
                nonlocal self, pk_n_line, ax, set_ylim, screen, dynamic_n_bound_factor, t_lims, fig
                pk_n_line.set_ydata(self.pk.n_history_solutions)
                pk_n_line.set_xdata(self.pk.n_history_time_window)
                min_max = np.min(self.pk.n_history_solutions), np.max(self.pk.n_history_solutions)
                y_lims = min_max[0] / dynamic_n_bound_factor, min_max[1] * dynamic_n_bound_factor
                y_range = y_lims[1] - y_lims[0]
                ax.set_ylim(*y_lims)

                elapsed_time_harrison = time.time() - start_time
                x_vals = np.linspace(elapsed_time_harrison-6,elapsed_time_harrison-1,151)
                self.pk.n_history_time_window = x_vals
                upper_time_bound = x_vals[-1]*2
                ax.set_xlim(elapsed_time_harrison-5,elapsed_time_harrison)
                
                # _target_block = ax.fill(
                #     [
                #         -self.n_history_window,
                #         upper_time_bound,
                #         upper_time_bound,
                #         -self.n_history_window,
                #     ],
                #     [
                #         target_power_lower_limit,
                #         target_power_lower_limit,
                #         target_power_upper_limit,
                #         target_power_upper_limit,
                #     ],
                #     color=green_colour,
                #     alpha=0.5,
                # )[0]

                # _failure_block = ax.fill(
                #     [
                #         -self.n_history_window,
                #         upper_time_bound,
                #         upper_time_bound,
                #         -self.n_history_window,
                #     ],
                #     [
                #         failure_power,
                #         failure_power,
                #         failure_power * dynamic_n_bound_factor,
                #         failure_power * dynamic_n_bound_factor,
                #     ],
                #     color="red",
                #     alpha=0.5,
                # )[0]

                power_str.set_text(get_power_str(self.pk.n))
                power_str.set_y(y_lims[0] + 0.95 * y_range)
                power_str.set_x((t_lims[0] + 0.02 * t_range + elapsed_time_harrison))
                k_eff_str.set_text(get_k_eff_str(self.k_eff))
                k_eff_str.set_y(y_lims[0] + 0.90 * y_range)
                k_eff_str.set_x((t_lims[0] + 0.02 * t_range + elapsed_time_harrison))
                time_at_target_str.set_text(get_time_at_target_str(time_at_target_condition))
                time_at_target_str.set_y(y_lims[0] + 0.82 * y_range)
                time_at_target_str.set_x((t_lims[0] + 0.02 * t_range + elapsed_time_harrison))
                time_elapsed_str.set_text(get_time_elapsed_str(elapsed_time_harrison))
                time_elapsed_str.set_y(y_lims[0] + 0.78 * y_range)
                time_elapsed_str.set_x((t_lims[0] + 0.02 * t_range + elapsed_time_harrison))

                canvas = agg.FigureCanvasAgg(fig)
                canvas.draw()
                renderer = canvas.get_renderer()

                surf = pygame.image.frombuffer(renderer.buffer_rgba(), canvas.get_width_height(), "RGBA")
                #Here:
                screen.blit(surf, (WIDTH*0.3,HEIGHT*0.2))
            
            update_pk_n_graph_to_display()

        def draw_popup(message):
            # Draw a semi-transparent surface to cover the screen
            popup_surface = pygame.Surface((POPUP_WIDTH, POPUP_HEIGHT), pygame.SRCALPHA)
            popup_surface.fill(TRANSPARENT_BLACK)
            screen.blit(popup_surface, (0, 0))
            lines = message.split('\n')
            rendered_lines = []
            max_width = 0
            total_height = 0
            font = pygame.font.Font(font_path, 24)
            for line in lines:
                rendered_line = font.render(line, True, WHITE)
                rendered_lines.append(rendered_line)
                line_rect = rendered_line.get_rect()
                max_width = max(max_width, line_rect.width)
                total_height += line_rect.height

            y = 0
            for rendered_line in rendered_lines:
                popup_surface.blit(rendered_line, (0, y))
                y += rendered_line.get_rect().height
            screen.blit(popup_surface, (WIDTH // 2 - max_width // 2, HEIGHT // 2 - total_height // 2))
            pygame.display.flip()

        def update_leds(scraming_flag, at_target_flag):
            nonlocal self 
            led_strips_names = [key for key in self.panel_states.LED_strips.keys() ]
            if not self.running:
                for name in led_strips_names:
                    self.panel_states.LED_strips[name].set_colour('r')
            else:
                for name in led_strips_names:
                    if "lever" in name:
                        lever_deadzone_state = -1
                        if "left" in name:
                            lever_deadzone_state = self.lever_deadzone_states[0]
                        elif "middle" in name:
                            lever_deadzone_state = self.lever_deadzone_states[1]
                        elif "right" in name:
                            lever_deadzone_state = self.lever_deadzone_states[2]
                        self.panel_states.LED_strips[name].set_color( ['g', 'y', 'r'][lever_deadzone_state+1]  )

                    if "switch" in name:
                        self.panel_states.LED_strips[name].set_colour('g')

                    if "reactor" in name:
                        if scraming_flag:
                            self.panel_states.LED_strips[name].set_color( 'r' )
                        elif at_target_flag: 
                            self.panel_states.LED_strips[name].set_color( 'g' )
                        else:
                            self.panel_states.LED_strips[name].set_color( 'y' )

                    if "right_button" in name:
                        self.panel_states.LED_strips[name].set_colour('g')


            

                    
            # LED_strip_ids["top_reactor_leds_ids"] = [21, 22, 23]
            # LED_strip_ids["left_button_leds_ids"] = [4, 6, 5]
            # LED_strip_ids["right_button_leds_ids"] = [1, 2, 3]
            # LED_strip_ids["top_switch_ids"] = [7, 8, 9]
            # LED_strip_ids["top_middle_switch_ids"] = [10, 11, 12]
            # LED_strip_ids["middle_switch_ids"] = [13, 14, 15]
            # LED_strip_ids["bottom_middle_switch_ids"] = [16, 17, 18]
            # LED_strip_ids["bottom_switch_ids"] = [19, 20]
            # LED_strip_ids["left_lever_ids"] = [24,25,26]
            # LED_strip_ids["middle_lever_ids"] = [27,28,29]
            # LED_strip_ids["right_lever_ids"] = [30,31, 32]
 
        
        def end_game():
            nonlocal self
            self.running = False
            self.panel_states.turn_off_all_leds()
            if self.pk_thread.is_alive():
                self.pk_thread.join()
            self.pk.reset_sol()

        ##--Game loop
        lever_origin_rel_pos = []
        lever_origin_rel_pos = list(self.panel_states.control_rod_lever_rel_pos.values() )
        ##!! In isolation, left lever takes us between 0.98 and 1.02 
        ##!! In isolation, middle lever takes us between 0.99 and 1.01
        ##!! In isolation, right lever takes us between 0.995 and 1.005
        ##!! Overall possible k_eff range is 0.965 to 1.035 (too much?)

        use_levers_flag = True
        show_quit_popup = False
        show_victory_popup = False
        restart_flag = False
        quit_restart_message = "Press '3D' to quit\nor '1D' to restart.\nAny other key to continue"

        
        update_pk_n_graph_to_display()
        victory_flag = False
        at_target = False
        self.panel_states.turn_off_all_leds()
        while pygame_running:
            ##-- Handle events

            ##!! Figure out what the physical inputs from control panel are
            self.panel_states.update_state()
            update_leds(scraming, at_target)
            lever_rel_pos = list( self.panel_states.control_rod_lever_rel_pos.values() )


            ##!! To start the game:
            ##!! Check if the button is pressed and 
            if self.panel_states.button_states["left_button"]:
                ##!! Check if all switches are on
                if all(self.panel_states.switch_states.values()):
                    screen.fill((0, 0, 0))
                    if not self.running:
                        self.running = True
                        self.start_simulation()

            if (not self.running) and (not victory_flag): 
                start_time = time.time()
                update_pk_n_graph_to_display()

            for event in pygame.event.get():

                if event.type == pygame.QUIT:
                    pygame_running = False

                elif event.type == pygame.KEYDOWN:
                    if (event.key != pygame.K_4 and event.key != pygame.K_b ) and show_quit_popup:
                        ##!! Cancel the popup
                        show_quit_popup = False
                    if (event.key == pygame.K_q) or (event.key == pygame.K_b):
                        self.running = False
                        pygame_running = False
                        restart_flag = False
    
                    if (event.key == pygame.K_1 and not self.running):
                        screen.fill((252, 186, 3))

                    if (event.key == pygame.K_SPACE) or (event.key == pygame.K_0):
                        if self.running:
                            scraming = True

                    if (event.key == pygame.K_w) or (event.key == pygame.K_UP) or (event.key == pygame.K_2):
                        lifting_rod = True

                    if (event.key == pygame.K_s) or (event.key == pygame.K_DOWN) or (event.key == pygame.K_6):
                        lowering_rod = True

                    if (event.key == pygame.K_8) :
                        ##!! Toggle using the levers, instead just use keypad 
                        use_levers_flag = not use_levers_flag

                    if ((event.key == pygame.K_4 )):
                        if show_quit_popup:
                            ##!! RESTART
                            restart_flag = True
                            print("Restarting the game...")
                            self.running = False
                            pygame_running = False
                        else: 
                            show_quit_popup = True
                            draw_popup(quit_restart_message)
                    
                    



                elif event.type == pygame.KEYUP:
                    if (event.key == pygame.K_1 and not self.running):
                        ##!! Start the game
                        screen.fill((0, 50, 0))
                        self.running = True
                        self.start_simulation()

                    if (event.key == pygame.K_w) or (event.key == pygame.K_UP) or (event.key == pygame.K_2):
                        lifting_rod = False

                    if (event.key == pygame.K_s) or (event.key == pygame.K_DOWN) or (event.key == pygame.K_6):
                        lowering_rod = False

            ##--Apply Updates
            if self.running:
                if target_power_lower_limit < self.pk.n < target_power_upper_limit:
                    at_target = True
                    time_at_target_condition += self.frame_time
                else: 
                    at_target = False

                if self.pk.n > failure_power:
                    scraming = True

                elif time_at_target_condition >= target_time_at_target_condition:
                    print("Congratulations! You have successfully and safely kept the reactor stable for 20 seconds at 200 MW!")
                    print("You have helped to keep the country's lights on!")
                    print("Press 'q' or 'escape' to quit.")
                    end_game()
                    victory_flag = True
                    ax.set_title('!!!YOU WIN!!!', color=green_colour,weight='bold',fontproperties=custom_font, y=1.02, fontsize=30)
                    update_pk_n_graph_to_display()                    

                ##!! Update the k_eff value based on lever_rel_pos 
                if not scraming: 
                    if use_levers_flag:
                        self.update_pygame_keff_from_levers(lever_rel_pos, lever_origin_rel_pos)



            if self.running:
                screen.fill((0, 50, 0))
            if scraming:
                screen.fill((50, 0, 0))

            if self.pk_n_animation and self.running :
                update_pk_n_graph_to_display()

            if show_quit_popup:
                draw_popup(quit_restart_message)
            
            self.pygame_k_eff -= scram_rate if scraming else 0
            self.pygame_k_eff += inc if lifting_rod else 0
            self.pygame_k_eff -= inc if lowering_rod else 0
            self.pygame_k_eff = min(max(min_allowable_k_eff, self.pygame_k_eff), max_allowable_k_eff)

            self.k_eff = self.pygame_k_eff

            if scraming and self.pygame_k_eff == min_allowable_k_eff:
                scraming = False

            # Wait for the next frame
            clock.tick(self.frame_rate)
            pygame.display.flip()
            pygame.display.update()

        # Clean up
        if restart_flag:
            print("Restarting the game final if...")
            end_game()
            return True
        else:
            pygame.quit()
            end_game()
            return False




    def run_pk(self, thread_num):
        print(f"Thread {thread_num} is running the point kinetics.")

        self.pk_k_eff = self.k_eff

        while self.running:
            t_start = monotonic_ns() / 1e9
            self.pk.step(
                self.frame_time,
                self.k_eff,
                method = "implicit_heun"
                # method = "backwards_euler"
            )
            t_end = monotonic_ns() / 1e9
            sleep_length = self.frame_time - (t_end - t_start) 
            if sleep_length < 0.0:
                sleep_length = 0.0
            time_sleep(sleep_length)


if __name__ == "__main__":
    keep_playing = True 
    while keep_playing:
        system = System(pk_n_animation=True)
        keep_playing = system.main()
        if not keep_playing:
            print("Thanks for playing!")
        if keep_playing:
            print("Restarting the game...")
