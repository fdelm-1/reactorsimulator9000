from gpiozero import MCP3008
import RPi.GPIO as GPIO
import smbus
from time import sleep
from itertools import chain 

class ControlRodLever:
    def __init__(self, channel, min_V=0.1, max_V=1.0, clock_pin=21, mosi_pin=20, miso_pin=19, select_pin=16) -> None:
        self.channel = channel
        self.min_V = min_V
        self.max_V = max_V
        self.rel_pos = 0.0

        self.clock_pin = clock_pin
        self.mosi_pin = mosi_pin
        self.miso_pin = miso_pin
        self.select_pin = select_pin
        self.adc = MCP3008(channel=self.channel, clock_pin=self.clock_pin, mosi_pin=self.mosi_pin, miso_pin=self.miso_pin, select_pin=self.select_pin)
        self.update_rel_pos()
        return

    def update_rel_pos(self):
        self.rel_pos = (self.adc.value - self.min_V) / (self.max_V - self.min_V) 
        
        ## Ensure rel_pos is within 0 and 1
        if self.rel_pos < 0.0:
            self.rel_pos = 0.0
        elif self.rel_pos > 1.0:
            self.rel_pos = 1.0

        return self.rel_pos

class PCA9685Connection:
    def __init__(self, pca_address, MODE1, PRESCALE, LED0_ON_L, bus, frequency=1000):
        self.pca_address = pca_address
        self.MODE1 = MODE1
        self.PRESCALE = PRESCALE
        self.LED0_ON_L = LED0_ON_L
        self.bus = bus
        self.frequency = frequency

        self.bus.write_byte_data(self.pca_address, self.MODE1, 0x00)
        self.set_pwm_frequency(self.frequency)
        return  

    def set_pwm_frequency(self, frequency):    # Set PWM frequency
        prescale_value = int(25000000.0 / (4096.0 * frequency) - 1.0)
        old_mode = self.bus.read_byte_data(self.pca_address, self.MODE1)

        new_mode = (old_mode & 0x7F) | 0x10  # Set sleep bit to allow writing to prescale
        self.bus.write_byte_data(self.pca_address, self.MODE1, new_mode)
        self.bus.write_byte_data(self.pca_address, self.PRESCALE, prescale_value)
        self.bus.write_byte_data(self.pca_address, self.MODE1, old_mode)
        sleep(0.005)
        self.bus.write_byte_data(self.pca_address, self.MODE1, old_mode | 0x80)  # Restart

    def set_pwm(self, on, off, channel):
        self.bus.write_byte_data(self.pca_address, self.LED0_ON_L + 4 * channel, on & 0xFF)
        self.bus.write_byte_data(self.pca_address, self.LED0_ON_L + 4 * channel + 1, on >> 8)
        self.bus.write_byte_data(self.pca_address, self.LED0_ON_L + 4 * channel + 2, off & 0xFF)
        self.bus.write_byte_data(self.pca_address, self.LED0_ON_L + 4 * channel + 3, off >> 8)


class LED_Class:
    """Class to control the LEDs on the control panel"""
    def __init__(self, pca, channel):
        self.channel = channel
        ## The actual PCA9685Connection object is passed in
        self.pca = pca

        ## Start turned off
        self.led_on_state = False
        self.turn_off_led()
        self.pca_address = pca.pca_address
        return
    
    def set_pwm(self, on, off):
        self.pca.set_pwm(on, off, self.channel)
        return

    def turn_on_led(self):
        if self.led_on_state == False:
            self.led_on_state = True
            self.set_pwm(0, 4095)
        else:
            ## Do nothing
            return

    def turn_off_led(self):
        if self.led_on_state == True:
            self.led_on_state = False
            self.set_pwm(0, 0)
        else:
            ## Do nothing
            return

    def toggle_led(self):
        if self.led_on_state == False:
            self.turn_on_led()
        else:
            self.turn_off_led()
        return

class LED_Strip:
    def __init__(self, LEDs_list, name, colours = ['g', 'y', 'r'] ):
        self.name = name
        self.LEDs = LEDs_list
        self.colours = colours
        if len(self.LEDs) != len(colours):
            raise ValueError("Number of LEDs and colours must match")
    def set_colour(self, colour):
        for ii in range(len(self.LEDs)):
            if self.colours[ii] == colour:
                self.LEDs[ii].turn_on_led()
            else: 
                self.LEDs[ii].turn_off_led()
    def set_color(self, colour):
        for ii in range(len(self.LEDs)):
            if self.colours[ii] == colour:
                self.LEDs[ii].turn_on_led()
            else: 
                self.LEDs[ii].turn_off_led()
            
class ToggleSwitch:
    def __init__(self, GPIO_pin_no, name) -> None:
        self.pin = GPIO_pin_no
        self.name = name
        self.state = False ##False is off
        GPIO.setup(self.pin, GPIO.IN)
        self.state = GPIO.input(self.pin)

    def update_state(self):
        self.state = GPIO.input(self.pin)
        return self.state

class MyControlPanelStates: 
    """Class to store all the physical states of the control panel"""
    def __init__(self) -> None:

        ## ========= Control Rod Levers =========
        ## TODO: Better names for control rod levers
        self.control_rod_levers = {"left_lever": ControlRodLever(2), "mid_lever": ControlRodLever(1), "right_lever": ControlRodLever(0)}
        self.control_rod_lever_rel_pos = {"left_lever": 0.0, "mid_lever": 0.0, "right_lever": 0.0}
        
        ## ======== Switches and Buttons =========
        ## Setup GPIO mode
        GPIO.setmode(GPIO.BCM)
        ## Actual switches are 4, 17, 27, 22, 10
        self.switches = {
                        "bot_switch": ToggleSwitch(4, "bot_switch"),
                         "mid_bot_switch": ToggleSwitch(17, "mid_bot_switch"),
                         "mid_switch": ToggleSwitch(27, "mid_switch"),
                         "mid_top_switch": ToggleSwitch(22, "mid_top_switch"),
                        "top_switch": ToggleSwitch(10, "top_switch") 
                        ## top_switch is me OwO -- Luis
                         }
        ## Switch states addressed by name
        self.switch_states = {}
        for switch in self.switches:
            self.switch_states[switch] = False

        ## Buttons are 9 and 11
        self.buttons = { "left_button": ToggleSwitch(9, "left_button"), "right_button": ToggleSwitch(11, "right_button") }
        self.button_states = {}
        for button in self.buttons:
            self.button_states[button] = False

        ## ========= LEDs =========
        self.bus = smbus.SMBus(1)
        ## Two PCA9685 boards are used, as we have so many cunting LEDs
        self.pca_1 = PCA9685Connection(0x40, 0x00, 0xFE, 0x06, self.bus)
        self.pca_2 = PCA9685Connection(0x41, 0x00, 0xFE, 0x06, self.bus)
        
        ## LED numbers, channel is 1 less
        ## Cant be arsed to name them all 
        ## These variables are just for reference
        ##!! Organised left to right 
        ## Looking at panel with keyboard on left, with red buttons above it
        LED_strip_ids = {}
        LED_strip_ids["top_reactor_leds_ids"] = [21, 22, 23]
        LED_strip_ids["left_button_leds_ids"] = [4, 6, 5]
        LED_strip_ids["right_button_leds_ids"] = [1, 2, 3]
        LED_strip_ids["top_switch_ids"] = [7, 8, 9]
        LED_strip_ids["top_middle_switch_ids"] = [10, 11, 12]
        LED_strip_ids["middle_switch_ids"] = [13, 14, 15]
        LED_strip_ids["bottom_middle_switch_ids"] = [16, 17, 18]
        LED_strip_ids["bottom_switch_ids"] = [19, 20]
        LED_strip_ids["left_lever_ids"] = [24,25,26]
        LED_strip_ids["middle_lever_ids"] = [27,28,29]
        LED_strip_ids["right_lever_ids"] = [30,31, 32]
        self.all_led_ids = [x for xs in list(chain(LED_strip_ids.values())) for x in xs]
        print("All LED IDs:")
        print(self.all_led_ids)
        ## Big dict of all LEDs by channel number
        self.LEDs_by_id = {}
        for led_id in self.all_led_ids:
            channel = led_id - 1
            if channel < 16:
                pca = self.pca_1
                self.LEDs_by_id[led_id] = LED_Class(pca, channel)
            elif channel >= 16:
                pca = self.pca_2
                channel = channel - 16 ## Need to adjust channel number tp start from 0 on new board
                self.LEDs_by_id[led_id] = LED_Class(pca, channel)
            else:
                raise ValueError("Invalid LED channel number")
        self.LED_strips = {}
        LED_obj_list = []
        for strip_name in LED_strip_ids.keys():
            LED_obj_list = []
            LED_obj_list = [self.LEDs_by_id[led_id] for led_id in LED_strip_ids[strip_name]]
            if strip_name == "bottom_switch_ids":
                self.LED_strips[strip_name] = LED_Strip( LED_obj_list , strip_name, colours = ['r', 'g'] )
                continue
            self.LED_strips[strip_name] = LED_Strip( LED_obj_list , strip_name)
        ## Now update states to ensure all is correct
        self.update_state()
        
    def update_state(self):
        """Update everything in the control panel, 
        including the control rod levers, switches and buttons.
        States stored in corresponding dictionaries"""
        for lever in self.control_rod_levers:
            self.control_rod_lever_rel_pos[lever] = self.control_rod_levers[lever].update_rel_pos()
        for switch in self.switches:
            self.switch_states[switch] = self.switches[switch].update_state()
        for button in self.buttons:
            self.button_states[button] = self.buttons[button].update_state()

    def state_output_loop(self):
        """ Code to output all state variables in a loop until user interrupts with CTRL+C
        Also turns LEDS on or off each loop
        """
        while True:
            try: 
                self.update_state()
                for led in self.LEDs_by_id:
                    self.LEDs_by_id[led].toggle_led()
                    print(led)
                print("Lever rel pos")
                print(self.control_rod_lever_rel_pos)
                print("\n")
                print("Level abs pos")
                print([lever.adc.value for lever in self.control_rod_levers.values()])
                print(self.switch_states)
                print(self.button_states)
                sleep(0.5)
            except KeyboardInterrupt:
                print("Exiting")
                for led in self.LEDs_by_id:
                    self.LEDs_by_id[led].turn_off_led()
                GPIO.cleanup()
                break
            except:
                print("Error")
                for led in self.LEDs_by_id:
                    self.LEDs_by_id[led].turn_off_led()
                GPIO.cleanup()
                break

    def toggle_leds(self, all=False, leds=[]):
        """Toggle either all LEDs or a list of LEDs"""
        if all:
            for led in self.LEDs_by_id:
                self.LEDs_by_id[led].toggle_led()
        else:
            for led in leds:
                ## Check if led is valid
                try:    
                    self.LEDs_by_id[led].toggle_led()
                except KeyError:
                    print(f"Invalid LED number: {led}")
                    continue

    def turn_off_all_leds(self):
        """Turn off all leds"""
        for led in self.LEDs_by_id:
            self.LEDs_by_id[led].turn_off_led()



if __name__ == "__main__":
    control_panel = MyControlPanelStates()
    control_panel.state_output_loop()
