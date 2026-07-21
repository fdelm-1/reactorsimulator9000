"""Windows/desktop entry point for Reactor Simulator 9000.

game_new.py talks to the physical Raspberry Pi control panel (GPIO-driven levers,
switches and LEDs) via MyControlPanelStates, which ties the game to a Pi. The
game already supports full keyboard control independently of that panel
('1' to start, 'w'/'s' to raaise/lower the control rods, 'space' to SCRAM, 'q'
to quit), so on a desktop we just swap the panel for a stub that satisfies
the same ineterface without touching any hardware.
"""

from game_new import System


class KeyboardControlPanelStates:
    """Stand-in for MyControlPanelStaetes when no physical control panel is attached."""

    def __init__(self):
        # Held inside the lever deadzone (0.763-0.87) so the "levers" read as
        # neutral and only the keyboard rod controls affect k_eff. Key names
        # match MyControlPanelStates.control_rod_lever_rel_pos.
        self.control_rod_lever_rel_pos = {"left_lever": 0.8, "mid_lever": 0.8, "right_lever": 0.8}
        self.button_states = {"left_button": False, "right_button": False}
        self.switch_states = {"switch": False}
        self.LED_strips = {}

    def update_state(self):
        pass

    def turn_off_all_leds(self):
        pass


class WindowsSystem(System):
    # No physical lever to read, so drive k_eff from the w/s keyboard increments
    # instead of update_pygame_keff_from_levers() (which would just read the stub's
    # fixed neutral position and reset k_eff to 1.0 every frame). Still togglable
    # in-game with '8' if you want to see the (static) lever behavior.
    USE_LEVERS_BY_DEFAULT = False

    def _create_panel_states(self):
        return KeyboardControlPanelStates()


if __name__ == "__main__":
    keep_playing = True
    while keep_playing:
        system = WindowsSystem(pk_n_animation=True)
        keep_playing = system.main()
        if keep_playing:
            print("Restarting the game...")
        else:
            print("Thanks for playing!")
