==============================================================================
REACTOR SIMULATOR 9000
==============================================================================

A pygame-driven control-panel simulator for a small modular reactor, built to
run on a Raspberry Pi wired to a physical panel of control-rod levers,
coolant-pump switches, start/scram buttons and LED indicators. A keyboard-only
desktop mode is also provided for developing/testing without the hardware.

Under the hood it couples a six-group point-kinetics neutronics model
(point_kinetics.py) to a single-node fuel-temperature model (temperature_model.py)
and drives a full-screen pygame HUD (diagrams.py) showing live power, k_eff,
fuel temperature, the reactor vessel's control rods, and a leaderboard of best
times.

This file covers:
  1. Requirements & installation
  2. Running the game
  3. How to play (tutorial)
  4. Controls reference (hardware + keyboard)
  5. Hardware wiring & calibration
  6. Configuration / tuning guide (config.py)
  7. File-by-file code reference
  8. Known issues / quirks
  9. Project files


==============================================================================
1. REQUIREMENTS & INSTALLATION
==============================================================================

Python 3.10+ (the point-kinetics stepper uses "match" statements, which need
3.10 or later).

Required on every platform:
    pygame
    numpy
    matplotlib      (imported by point_kinetics.py, only actually used if you
                      run that file's own __main__ block directly)

Required ONLY on the Raspberry Pi (for the real control panel):
    RPi.GPIO
    gpiozero
    smbus

There is no requirements.txt in this repository - install the above with pip,
e.g.:
    pip install pygame numpy matplotlib
    pip install RPi.GPIO gpiozero smbus      (Pi only)

control_panel_states.py imports RPi.GPIO/gpiozero/smbus at module level, but
game_new.py only imports control_panel_states lazily, inside
System._create_panel_states(), so simply importing game_new.py (or running
game_keyboard.py) on a non-Pi machine will NOT fail as long as you use the
keyboard entry point described below.

A "fonts" folder containing retro.ttf must be present alongside the .py files
(it already is, in this repository) - every on-screen font is loaded from
"./fonts/retro.ttf", i.e. relative to whatever directory you launch python
from, so run the game from the repository root.


==============================================================================
2. RUNNING THE GAME
==============================================================================

On the Raspberry Pi, with the physical panel wired up:
    python3 game_new.py

This uses the real control panel (MyControlPanelStates in
control_panel_states.py) for every input - levers, switches, buttons and LEDs.

On a desktop/laptop with no physical panel:
    python3 game_keyboard.py

This runs the exact same game logic, but swaps in a keyboard-only stand-in
control panel (see section 4.3).

Both entry points loop forever, starting a brand new game (a fresh System /
WindowsSystem object) every time you restart, until you quit.


==============================================================================
3. HOW TO PLAY (TUTORIAL)
==============================================================================

3.1  Starting the reactor
--------------------------
Starting is a deliberate two-step sequence, mirroring a real plant startup
(spin the pumps up, then bring the reactor critical from a fully shut-down
position):

  Step 1 - Spin up the pumps:
    Turn ON all five coolant-pump switches (bottom, mid-bottom, middle,
    mid-top, top), then press and HOLD the LEFT button for 2 seconds
    (LEFT_BUTTON_HOLD_TO_START_S in config.py). If any switch is off, the
    hold doesn't count and the timer never starts. Once the 2 seconds are
    up, the pumps latch "on" - you can let go of the left button afterwards,
    it won't undo it. The "COOLANT PUMPS" panel above the graph will show all
    five boxes lit green once this has happened.

  Step 2 - Bring the reactor critical:
    Push all three control-rod levers all the way DOWN (safety, regulating,
    and chemical-shim), then press the RIGHT button. This only takes effect
    once ALL of the following are true at the moment you press it:
      - the pumps have been activated (step 1 above),
      - every pump switch is still currently on, and
      - every lever is pushed all the way down.
    Once it fires, the reactor starts and the graph/vessel/dial all come to
    life. Immediately begin withdrawing the levers (see 3.3) to bring the
    reactor up to power - it starts fully shut down (rods full in).

3.2  Objective - how to win
-----------------------------
Get the reactor's power into the green target band on the graph - by default
192-208 MW (TARGET_POWER_MW +/- TARGET_TOLERANCE_MW) - and KEEP it there
continuously for 5 seconds (TARGET_HOLD_TIME_S). The "Time at target" readout
on the graph's HUD counts up while you're in the band and resets to zero the
instant you leave it, so you need one unbroken 5-second hold, not 5 seconds
total.

Once you hold it, the reactor shuts down automatically, the final graph
freezes, and you're prompted to enter a name (up to 12 characters) for the
leaderboard. Press Enter to submit (leaving the name blank records you as
"Anonymous" and does NOT save a leaderboard entry), or Escape to clear what
you've typed so far. Afterwards, press the RIGHT button (or 'R' on a
keyboard) to play again.

3.3  Controlling the reactor
------------------------------
Three levers, left to right:
  - SAFETY lever (left): the strongest effect on k_eff, and the SLOWEST to
    respond (about 2.5 seconds to reach full effect after you move it).
    Drives the two outer, thickest control rods in the vessel diagram.
  - REGULATING lever (middle): a medium effect, medium speed (about 1.2s).
    Drives the two inner control rods - your main day-to-day power trim.
  - CHEMICAL SHIM lever (right): the weakest effect on k_eff, but the
    FASTEST to respond (about 0.5s). Also visibly reddens the coolant in the
    vessel diagram the further down it's pushed (more dissolved boron).

All three levers only ever SUBTRACT reactivity: fully up means no effect at
all (this is also where k_eff is at its highest), fully down means maximum
negative effect. There's a small dead zone at each end of every lever's
travel that snaps to fully up/fully down, so you don't have to hit the exact
physical limit to get the full effect.

IMPORTANT: with every lever fully withdrawn (up), the reactor's baseline
k_eff is ABOVE prompt critical - i.e. left alone, power runs away extremely
fast (much faster than the delayed-neutron precursors can restrain it). Don't
expect to sit at "rods out" for long; you'll need to bring rods in well
before power gets away from you, and fine-tune from there rather than
starting from full withdrawal and hoping to catch it.

Coolant pumps: each of the five switches you turn on adds coolant mass flow,
which cools the fuel faster - but every unit of flow also costs a small
amount of k_eff (config.MASS_FLOW_K_EFF_COEFFICIENT). Running more pumps
buys you thermal safety margin at the cost of a little reactivity, so you may
need to withdraw a lever slightly to compensate for turning more pumps on
(or vice versa).

3.4  What to watch out for
-----------------------------
  - OVERPOWER SCRAM: if power exceeds FAILURE_POWER_MW (250 MW by default),
    the reactor automatically SCRAMs.
  - OVERTEMPERATURE SCRAM: if fuel temperature exceeds SCRAM_TEMPERATURE_C
    (1050 C by default), the reactor automatically SCRAMs. Watch the
    "TEMPERATURE WARNING" banner above the fuel-temperature dial - it starts
    flashing once you're within 200 C of the SCRAM temperature (the same
    point the dial itself turns amber), well before a SCRAM actually fires.
  - A SCRAM (automatic OR manual) immediately drops k_eff by
    SCRAM_K_EFF_DROP and the four amber-capped SCRAM rods slam fully into
    the core. This lock does NOT release on a timer alone: it needs BOTH a
    minimum hold time to elapse (2 seconds for a manual SCRAM, 4 seconds for
    an automatic one) AND every lever to be pushed all the way back down,
    confirming the reactor safe, before it will release and let the SCRAM
    rods start withdrawing again. If you don't push the levers down, the
    lock - and the dropped rods - will simply stay engaged indefinitely.
  - A SCRAM does not end your run - the game keeps going afterwards, so
    you can recover and still go for the target band. But every SCRAM costs
    you time (and the recovery procedure above takes active effort), so
    treat it as a costly mistake to avoid, not a safe reset button.
  - The "MAXIMUM!" reading in place of a k_eff number means k_eff is
    genuinely at its highest possible value right now: every lever fully
    withdrawn AND every pump switch off. Turning on even one pump, or
    nudging a single lever down at all, will replace it with the real
    k_eff number.

3.5  Reading the screen
--------------------------
  - Top strip, above the graph: the coolant-pump indicator panel - one box
    per switch, green when on, grey when off (before the pumps are
    activated, every box reads off regardless of the physical switches).
  - Centre: the live power-vs-time graph. Green band = target zone, amber
    hazard-striped band = warning zone (getting close to overpower), red
    band = failure zone. The HUD in the top-left of the graph shows current
    power, k_eff, time at target, and total time played.
  - Far left: the reactor vessel cross-section - blue coolant, glowing green
    fuel rods, and eight sliding control/scram rods (see the legend printed
    below it). A blue Cherenkov-style glow brightens with power.
  - Right column (before you win): the temperature-warning banner (blank
    until it's needed - see 3.4) above the fuel-temperature dial, a
    green/amber/red zoned gauge with a digital read-out.
  - Right column (after you win): a LEADERBOARD of the fastest completed
    runs, fastest first.
  - Top-left corner: an FPS counter.


==============================================================================
4. CONTROLS REFERENCE
==============================================================================

4.1  Physical control panel (game_new.py, Raspberry Pi)
------------------------------------------------------------
  Three sliding levers   - safety (left), regulating (middle), chemical
                            shim (right). See section 3.3.
  Five toggle switches    - coolant pumps: bottom, mid-bottom, middle,
                            mid-top, top.
  LEFT button             - pre-game: hold for 2s (with all switches on) to
                            spin the pumps up. Once the reactor is running,
                            a fresh press instead triggers a MANUAL SCRAM.
  RIGHT button             - pre-game: press (with pumps activated, all
                            switches on, and all levers fully down) to start
                            the reactor. Once running, or right after a win,
                            a fresh press instead RESTARTS the game.
  LED indicators (all driven by _update_leds() in game_new.py):
      - Whenever the reactor is NOT running, every LED strip shows red.
      - Each lever's 3-colour LED strip: in practice you'll only ever see
        yellow (lever near the top / no real effect yet) or red (lever
        pushed down, subtracting reactivity) - green is defined for a
        "positive" lever contribution, but no lever can ever raise k_eff
        above baseline, so it's not something you'll see in play.
      - Pump-switch LED strips: green whenever the reactor is running
        (this does not currently reflect each switch's individual on/off
        state).
      - "Reactor" LED strip: red while SCRAMming, green while at the
        target power band, yellow otherwise.
      - Right-button LED strip: green whenever the reactor is running.
      - Left-button LED strip: not driven by _update_leds() at all, so it
        stays off once the game has started.

4.2  Keyboard bindings (game_new.py - apply on both the Pi and desktop)
----------------------------------------------------------------------------
  These are handled by pygame's own keyboard events, independently of the
  physical panel, so they work identically whether you're running
  game_new.py on the Pi (e.g. with a keyboard plugged in) or game_keyboard.py
  on a desktop.

  T           - Start the game (only while not already running). This is
                the keyboard equivalent of the right-button start sequence,
                but WITHOUT the pump/lever gating - pressing T starts the
                reactor immediately regardless of switch or lever state.
  SPACE or 0  - Manual SCRAM (only while running).
  W or Up     - Hold to raise k_eff (only has a visible effect when lever
                control is turned off - see WindowsSystem in section 4.3).
  S or Down   - Hold to lower k_eff (same caveat as above).
  R           - Restart the game immediately (works whether running or not).
  L           - Clear the leaderboard (raw_scores.csv).
  Q           - Quit without restarting.
  Window close (X) - Quits normally.

4.3  Desktop-only behaviour (game_keyboard.py)
--------------------------------------------------
  game_keyboard.py's WindowsSystem swaps in KeyboardControlPanelStates, a
  stand-in for the physical panel:
      - The three "levers" report a fixed reading (0.8) that never changes,
        used only if lever control is toggled on.
      - There is only one placeholder switch (not the five real pump
        switches), permanently off - so the physical left/right-button
        startup sequence (section 3.1) can never actually succeed on
        desktop; use T to start instead (see 4.2).
      - No physical buttons, so the left/right-button bindings (manual
        SCRAM once running, restart) never fire either - use SPACE/0 and 4.
      - WindowsSystem.USE_LEVERS_BY_DEFAULT is False, so k_eff is driven by
        the W/S keyboard nudges instead of lever position.


==============================================================================
5. HARDWARE WIRING & CALIBRATION
==============================================================================

All of this lives in control_panel_states.py. GPIO pin numbers are BCM
numbering.

5.1  Control-rod levers (slide potentiometers via an MCP3008 ADC)
----------------------------------------------------------------------
  ADC channel 2  -> left_lever   (safety)
  ADC channel 1  -> mid_lever    (regulating)
  ADC channel 0  -> right_lever  (chemical shim)

  MCP3008 SPI-ish pins (gpiozero, bit-banged): clock=GPIO21, MOSI=GPIO20,
  MISO=GPIO19, select=GPIO16.

  Each lever's raw ADC reading is min/max-normalised (min_V=0.1, max_V=1.0)
  then corrected against a quadratic calibration fit (_CALIBRATION_A/B/C in
  control_panel_states.py), because the potentiometers are logarithmic-taper
  and a raw reading isn't linear in physical position. Readings are then
  smoothed over the last 8 samples (LEVER_SMOOTHING_WINDOW) to cut down
  jitter.

5.2  Coolant-pump switches and start/scram buttons (plain GPIO inputs)
----------------------------------------------------------------------------
  Switches:
      GPIO4   -> bot_switch
      GPIO17  -> mid_bot_switch
      GPIO27  -> mid_switch
      GPIO22  -> mid_top_switch
      GPIO10  -> top_switch
  Buttons:
      GPIO9   -> left_button
      GPIO11  -> right_button

5.3  LED indicators (two PCA9685 16-channel PWM boards over I2C)
----------------------------------------------------------------------
  Board 1: I2C address 0x40, LED "ids" 1-16 (channel = id - 1).
  Board 2: I2C address 0x41, LED "ids" 17-32 (channel = id - 17).

  LED-id groups (each becomes one LED_Strip - see MyControlPanelStates in
  control_panel_states.py for the definitive list):
      top_reactor_leds_ids       = 21, 22, 23   (reactor status: g/y/r)
      left_button_leds_ids       = 4, 6, 5
      right_button_leds_ids      = 1, 2, 3
      top_switch_ids             = 7, 8, 9
      top_middle_switch_ids      = 10, 11, 12
      middle_switch_ids          = 13, 14, 15
      bottom_middle_switch_ids   = 16, 17, 18
      bottom_switch_ids          = 19, 20        (2 LEDs: red/green only)
      left_lever_ids             = 24, 25, 26
      middle_lever_ids           = 27, 28, 29
      right_lever_ids            = 30, 31, 32

  Every strip except bottom_switch_ids is a 3-LED red/yellow/green group;
  LED_Strip.set_colour()/set_color() light exactly one LED in the strip at a
  time by name ('r', 'y', or 'g').

5.4  Calibrating the levers
--------------------------------
  Run, from the repository root, on the Pi:
      python3 control_panel_states.py calibrate
  This streams each lever's live rel_pos reading to the terminal without
  needing the switches/buttons/LED boards wired up. Physically move a lever
  to a marked position (e.g. every 10% of its travel), note the printed
  value, move to the next mark, repeat for all three levers (any order),
  then Ctrl+C to stop. Use the resulting (physical position, rel_pos) pairs
  to refit _CALIBRATION_A/B/C if the levers ever need recalibrating (e.g.
  after replacing a potentiometer).

  Running the file with no arguments instead starts a continuous debug loop
  (MyControlPanelStates.state_output_loop()) that prints every input's live
  state and toggles every LED, until Ctrl+C.


==============================================================================
6. CONFIGURATION / TUNING GUIDE (config.py)
==============================================================================

All of the game's tunable balance constants live in config.py; nothing else
in the codebase hardcodes these values. Current defaults are noted alongside
each one.

Colours: WHITE, BLACK, GREEN, AMBER, RED, GRID - the HUD's whole colour
palette, used throughout diagrams.py.

Controller balance:
  LEVER_DEADZONE_FRACTION (0.075)        - fraction of travel at each end of
                                            every lever that snaps to 0/1.
  LEFT_BUTTON_HOLD_TO_START_S (2.0)      - how long the left button must be
                                            held (with all switches on) to
                                            activate the pumps.

Game balance:
  TARGET_POWER_MW (200), TARGET_TOLERANCE_MW (8) - the win-condition power
                                            band (200 +/- 8 MW by default).
  FAILURE_POWER_MW (250)                 - overpower auto-SCRAM threshold.
  TARGET_HOLD_TIME_S (5.0)               - continuous seconds in the target
                                            band needed to win.
  BASE_K_EFF (1.0089)                    - k_eff with every lever fully up.
  LEVER_MIN_EFFECT ([-0.01,-0.003,-0.001]) - each lever's (safety,
                                            regulating, shim) maximum
                                            subtraction from BASE_K_EFF.
  LEVER_EFFECT_DELAY_S ([2.5,1.2,0.5])   - seconds for each lever's full
                                            effect to be reached after a move
                                            (safety slowest, shim fastest).

SCRAM:
  SCRAM_K_EFF_DROP (0.1)                 - k_eff subtracted immediately by
                                            any SCRAM.
  SCRAM_LOCK_DURATION_S (2.0)            - minimum lock time for a manual
                                            SCRAM.
  SCRAM_AUTO_LOCK_MULTIPLIER (2)         - automatic SCRAMs lock for this
                                            many times longer (4s by
                                            default).
  SCRAM_ROD_TRAVEL_TIME_S (0.5)          - seconds for the SCRAM rods to
                                            fully lower/raise in the vessel
                                            diagram.
  MASS_FLOW_K_EFF_COEFFICIENT (5e-6)     - k_eff subtracted per kg/s of
                                            coolant mass flow.

Temperature control:
  BASE_MASS_FLOW_RATE (375), FLOW_RATE_PER_SWITCH (125) - coolant mass flow
                                            in kg/s: a base amount plus this
                                            much per pump switch that's on
                                            (375-1000 kg/s across 0-5
                                            switches).
  STARTING_TEMPERATURE_C (650)           - fuel temperature at the start of
                                            every run.
  SCRAM_TEMPERATURE_C (1050)             - overtemperature auto-SCRAM
                                            threshold; also drives the
                                            thermometer's dial range and the
                                            temperature-warning threshold
                                            (SCRAM_TEMPERATURE_C - 200) in
                                            diagrams.py.

A few more tunables live in game_new.py itself (as System class attributes,
mostly just copied from config.py) and in diagrams.py's module-level layout
constants (screen positions/sizes for every panel) - see section 7 below.


==============================================================================
7. FILE-BY-FILE CODE REFERENCE
==============================================================================

7.1  game_new.py - main game loop and physical-panel entry point
----------------------------------------------------------------------
Module-level: WIDTH/HEIGHT (1920x1080), colour aliases, FONT_PATH,
RAW_SCORES_PATH, popup sizing constants.

class System - drives the point-kinetics/temperature models and the pygame
UI. Class attributes mirror most of config.py's constants (plus a couple of
derived ones, e.g. MIN_ALLOWABLE_K_EFF). Key methods:
  __init__                     - builds the PointKinetics and TemperatureModel
                                  instances, and the panel-state object.
  _create_panel_states()       - returns MyControlPanelStates(); overridden
                                  by WindowsSystem in game_keyboard.py.
  main()                       - spawns the physics thread, then runs the UI.
  start_simulation()           - starts the physics thread.
  update_pygame_keff_from_levers() - computes k_eff from lever positions and
                                  sets each lever's LED colour state.
  _apply_lever_deadzone()      - snaps near-extreme lever readings to 0/1.
  _advance_effective_levers()  - eases each lever's drawn/logic-driving
                                  position toward its real reading over time.
  _trigger_scram()             - begins a SCRAM: drops and locks k_eff.
  _advance_scram_rods()        - animates the SCRAM-rod insertion diagram to
                                  track self.scramming.
  _current_mass_flow_rate()    - coolant mass flow from switch states.
  _init_display()              - opens the pygame window and resets all
                                  per-game state; called once per game.
  _init_graph/_init_reactor_vessel/_init_pump_panel/_init_thermometer/
  _init_temp_warning           - build the diagrams.py renderer objects.
  _display_power()             - reactor power clamped to a display floor.
  _record_history_sample()     - feeds a power sample into the live graph.
  _is_max_k_eff()               - whether k_eff is genuinely at its ceiling.
  _update_graph/_draw_final_graph - render the live/frozen power graph.
  _draw_reactor_vessel/_draw_pump_panel/_draw_thermometer/
  _update_temp_warning/_draw_temp_warning - thin wrappers around the
                                  matching diagrams.py renderers.
  _load_leaderboard/_clear_leaderboard/_draw_leaderboard - manage the
                                  CSV-backed high-score table.
  _draw_popup()                 - renders a centred, semi-transparent text box.
  _draw_fps()                   - top-left FPS counter.
  _prompt_for_name()             - blocking modal loop for post-win name entry.
  _update_leds()                 - drives the physical panel's LEDs.
  _end_game()                    - stops the reactor, LEDs off, joins the
                                  physics thread.
  _record_score()                - appends a finished run to raw_scores.csv.
  run_pygame()                   - sets up the display + diagrams, enters the
                                  main loop.
  _game_loop()                   - the per-frame loop: reads panel/keyboard
                                  input, handles the startup/SCRAM/restart
                                  button logic, advances physics-adjacent
                                  state, finalises k_eff, and draws
                                  everything.
  run_pk()                       - background-thread loop stepping the
                                  point-kinetics model at a fixed rate
                                  (backwards_euler - see section 8).

Module __main__ block: loops forever, creating a fresh System(pk_n_
animation=True) and calling .main() each time the previous game requests a
restart.

7.2  diagrams.py - pure rendering, no game logic
----------------------------------------------------------------------
Owns every on-screen visual. None of its classes read game state directly -
game_new.py's System decides *when*/*what* to draw and hands each renderer
plain values (numbers, bools, dicts) every frame.

Module-level: WIDTH/HEIGHT, colour aliases, and every panel's screen
position/size (GRAPH_ORIGIN_PX, PUMP_PANEL_ORIGIN_PX, TEMP_WARNING_ORIGIN_PX,
THERMO_ORIGIN_PX, REACTOR_ORIGIN_PX, LEADERBOARD_ORIGIN_PX, and matching
_SIZE_PX constants), plus the vessel's colour palette and the thermometer's
dial range constants (DIAL_MIN_C/MAX_C/WARN_C/RED_C, derived from
config.SCRAM_TEMPERATURE_C). Helper: _lerp_color() (linear colour blend).

  GraphRenderer      - the live/final power-vs-time graph: target/warning/
                        failure coloured bands (the warning band's hazard
                        stripes scroll with the time axis), gridlines, the
                        power trace itself, and the HUD text block.
  ReactorVesselRenderer - the vessel cross-section: a static background
                        (metal shell, coolant gradient, fuel-rod lattice,
                        drive housings, legend) built once, plus per-frame
                        Cherenkov glow, chemical-shim tint, and the 8 sliding
                        control/scram rods. _fill_pixel_dome() is the shared
                        routine that draws the shell/coolant/shim-overlay's
                        blocky, pixel-art domed heads.
  ThermometerRenderer - the fuel-temperature dial: a static face (zoned arc,
                        tick marks) built once, plus a per-frame needle and
                        digital read-out.
  TempWarningRenderer - the flashing temperature-SCRAM warning banner (see
                        section 3.4).
  PumpPanelRenderer   - the row of coolant-pump indicator boxes.
  LeaderboardRenderer - the post-victory high-score list, rebuilt only when
                        its entries actually change.

7.3  config.py - tunable constants only, no logic
----------------------------------------------------------------------
See section 6 above for the full walkthrough.

7.4  control_panel_states.py - Raspberry Pi hardware interface
----------------------------------------------------------------------
See section 5 above for the wiring/calibration details. Classes/functions:
  ControlRodLever, _cm_from_raw_rel_pos() - one lever's ADC reading,
                        calibrated into a 0-1 position.
  calibrate_levers()  - standalone CLI utility for building/checking the
                        calibration fit.
  PCA9685Connection   - low-level I2C driver for one 16-channel LED board.
  LED_Class           - one physical LED's on/off state.
  LED_Strip           - a named group of LEDs with set_colour()/set_color()
                        to light exactly one at a time.
  ToggleSwitch        - one GPIO-input switch or button.
  MyControlPanelStates - aggregates every lever/switch/button/LED strip into
                        the single object game_new.System talks to.
                        update_state() re-reads every input once per frame;
                        state_output_loop()/toggle_leds()/turn_off_all_leds()
                        are debug/utility helpers (state_output_loop() is
                        also what running this file directly, without
                        "calibrate", drops you into).

7.5  game_keyboard.py - desktop entry point
----------------------------------------------------------------------
  KeyboardControlPanelStates - a stand-in for MyControlPanelStates: fixed
                        lever readings, one placeholder switch, no real
                        buttons/LEDs (see section 4.3).
  WindowsSystem(System) - swaps in that stand-in and drives k_eff from the
                        W/S keyboard nudges instead of lever position
                        (USE_LEVERS_BY_DEFAULT = False).

7.6  point_kinetics.py - reactor neutronics
----------------------------------------------------------------------
  PrecursorGroup      - one delayed-neutron precursor group's decay constant
                        and beta (delayed-neutron fraction share).
  PointKinetics       - holds the current neutron population and precursor
                        concentrations (self.sol) and steps them forward
                        given a k_eff:
                          backwards_euler_step()/step(method="backwards_euler")
                              - the integrator game_new.py's run_pk() actually
                                uses; unconditionally stable at any k_eff.
                          implicit_heun_step()/step(method="implicit_heun")
                              - also available, but NOT used by the game -
                                see the "Known issues" note in section 8.
                        n is a property for the current power (arbitrary
                        units, displayed/read as MW by the rest of the
                        game). enable_n_history/push_to_n_history/
                        multi_step are analysis/debugging helpers not used
                        by the game itself.

7.7  temperature_model.py - fuel temperature model
----------------------------------------------------------------------
  TemperatureModel    - a single-node fuel-temperature model for a
                        6600-rod, ~200 MWt-scale core (UO2 pellet -> gap ->
                        Zircaloy clad -> Dittus-Boelter convection to the
                        coolant). Geometry/material constants are
                        constructor defaults (module-level constants of the
                        same name). A chain of properties/methods
                        (pellet_volume, rod_mass, heat_transfer_area,
                        reynolds_number, prandtl_number, dittus_boelter,
                        heat_transfer_coefficient, the *_resistance
                        properties, overall_heat_transfer_coefficient) derive
                        an overall heat-transfer coefficient from the
                        current coolant mass flow rate.
                        fuel_temperature_rate() - returns dT/dt given the
                        core's total mass flow, thermal power, and current
                        fuel temperature; this is the one method game_new.py
                        actually calls, once per frame.
                        Running this file directly (python3
                        temperature_model.py) drops you into an interactive
                        prompt that reports every intermediate quantity for
                        a given flow/power/temperature.


==============================================================================
8. KNOWN ISSUES / QUIRKS
==============================================================================

  - The on-screen quit/restart popup ("Press Q to quit or R to restart...")
    is only ever shown once, immediately after a win (replacing the
    name-entry prompt) - the show_quit_popup flag that otherwise gates it is
    initialised False and never set True anywhere in the current code, so
    that path is effectively dead.

  - Lever LEDs are wired up to show green for a "positive" contribution to
    k_eff, but no lever can ever raise k_eff above baseline (every
    LEVER_MIN_EFFECT entry is negative), so in practice you will only ever
    see yellow or red on a lever's indicator - green is unreachable as the
    game is currently balanced.

  - Pump-switch LEDs turn green whenever the reactor is running, regardless
    of whether that particular switch is actually on - they don't currently
    reflect individual switch state.

  - System.__init__ accepts a complexity_level argument and stores it as
    self.complexity_level, but nothing else in the codebase reads it - it's
    currently a no-op, presumably reserved for a difficulty feature that
    hasn't been built yet.

  - PointKinetics.step()'s "implicit_heun" method is not used by the game
    (run_pk() always passes method="backwards_euler") - despite its name,
    implicit_heun's corrector step is explicit, so it isn't unconditionally
    stable: at the reactivity swings a SCRAM produces (combined with the
    reactor's very short prompt neutron lifetime), it diverges into a wildly
    growing, sign-flipping oscillation within about 100 steps. backwards_euler
    is unconditionally stable at any k_eff, at the cost of being first-order
    rather than second-order accurate - immaterial for a real-time,
    frame-driven simulation. See the comment on run_pk() in game_new.py.

  - raw_scores.csv ships with one sample row ("felix, 128903") that does not
    match the format _record_score() actually writes (elapsed-time-in-
    seconds first, then name, e.g. "128.903,felix"). _load_leaderboard()
    silently skips any row whose first column doesn't parse as a float, so
    this sample row is simply ignored rather than shown on the leaderboard.


==============================================================================
9. PROJECT FILES
==============================================================================

  game_new.py            - main game loop / Raspberry Pi entry point.
  game_keyboard.py       - desktop entry point (keyboard stand-in panel).
  diagrams.py            - all on-screen rendering.
  config.py              - tunable game-balance constants.
  control_panel_states.py - Raspberry Pi GPIO/I2C hardware interface.
  point_kinetics.py      - reactor neutronics (point-kinetics) model.
  temperature_model.py   - fuel temperature model.
  fonts/retro.ttf        - the retro-style font used throughout the HUD.
  raw_scores.csv         - leaderboard data (elapsed_seconds,name per line).
  equationstemp.jpeg, IMG_0467.jpeg - reference/design sketches used while
                            building the physics/vessel diagram; not read by
                            any code.
