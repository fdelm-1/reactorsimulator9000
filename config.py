# -- GAME DESIGN ---------------------------------------------------------

import math


WHITE = (255, 255, 255)  # #FFFFFF
BLACK = (0, 0, 0)        # #000000
GREEN = (116, 228, 124)  # #74E47C
AMBER = (239, 183, 0)    # #EFB700
RED = (225, 0, 0)        # #E10000
GRID = (90, 90, 90)      # #5A5A5A

# -- CONTROLLER BALANCE --------------------------------------------------

LEVER_DEADZONE_FRACTION = 0.075

# How long (seconds) the left button (pumps) must be held continuously - with
# every pump switch already on - before the pumps count as spun up: both for the
# pump-panel display and as the first step of the two-step startup sequence (hold
# left, then press right - with every lever fully down - to start the reactor).
# Once reached, the pumps stay "on" even if left is released - it isn't reset by
# letting go.
LEFT_BUTTON_HOLD_TO_START_S = 2.0

# -- GAME BALANCE --------------------------------------------------------

TARGET_POWER_MW = 200
TARGET_TOLERANCE_MW = 8
FAILURE_POWER_MW = 250
TARGET_HOLD_TIME_S = 5.0

# k_eff with all control-rod levers fully up (neutral - no lever contributes
# anything).
BASE_K_EFF = 1.0089

# How much each lever (left, middle, right) subtracts from BASE_K_EFF when
# pushed all the way down, scaling linearly to no effect at all the way up.
LEVER_MIN_EFFECT = [-0.01, -0.003, -0.001]

# Time (seconds) over which each lever's full-travel movement is drawn out before
# its full effect is reached, simulating the gradual physical response (control-rod
# drive speed / chemical-shim mixing). Order is [safety (left), regulating (mid),
# shim (right)] - the safety rods are slowest, the chemical shim the fastest.
LEVER_EFFECT_DELAY_S = [2.5, 1.2, 0.5]

# -- SCRAM --------------------------------------------------------------

# A SCRAM (manual or automatic) immediately subtracts this much from k_eff -
# standing in for the scram rods dropping fully into the core - rather than
# directly cutting the reactor's power; the resulting power drop plays out
# through the point-kinetics model itself instead of being forced.
SCRAM_K_EFF_DROP = 0.1

# How long (seconds) a manually triggered SCRAM locks k_eff at its
# (SCRAM_K_EFF_DROP-reduced) value before returning control to the player.
SCRAM_LOCK_DURATION_S = 2.0

# An automatic SCRAM (triggered by exceeding FAILURE_POWER_MW) locks k_eff
# for this many times longer than a manually triggered one.
SCRAM_AUTO_LOCK_MULTIPLIER = 2

# How long (seconds) the scram rods take to fully lower on a SCRAM, or to
# fully raise once the reset conditions are met (see _advance_scram_rods) -
# drawn out rather than snapping instantly, like the control-rod levers.
SCRAM_ROD_TRAVEL_TIME_S = 0.5

# How much each kg/s of coolant mass flow subtracts from k_eff - a game-balance
# trade-off for running the pumps harder to cool faster: k_eff drop =
# MASS_FLOW_K_EFF_COEFFICIENT * mass_flow_rate.
MASS_FLOW_K_EFF_COEFFICIENT = 5e-6

# -- TEMP CONTROL ------------------------------------------------------

# Coolant mass flow rate (kg/s): a base amount, plus an increment for each panel
# switch that is turned on. More flow removes more heat, so it cools the fuel faster
# - but also costs some reactivity, via MASS_FLOW_K_EFF_COEFFICIENT above.
BASE_MASS_FLOW_RATE = 375
FLOW_RATE_PER_SWITCH = 125

# Fuel temperature (deg C): the reactor starts here, and an automatic SCRAM fires if
# the temperature ever climbs above the scram temperature.
STARTING_TEMPERATURE_C = 650
SCRAM_TEMPERATURE_C = 1050
