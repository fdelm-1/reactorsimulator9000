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

# A SCRAM (manual or automatic) immediately multiplies the reactor's power
# by this factor.
SCRAM_POWER_FACTOR = 0.5

# How long (seconds) a manually triggered SCRAM locks k_eff at its minimum
# before returning control to the player.
SCRAM_LOCK_DURATION_S = 2.0

# An automatic SCRAM (triggered by exceeding FAILURE_POWER_MW) locks k_eff
# for this many times longer than a manually triggered one.
SCRAM_AUTO_LOCK_MULTIPLIER = 2

# -- TEMP CONTROL ------------------------------------------------------

ROD_DIAMETER = 0.0108
ROD_LENGTH = 6
NUMBER_OF_RODS = 52*17*17

FUEL_ROD_MASS = 10400 * math.pi * ((ROD_DIAMETER/2)**2) * ROD_LENGTH
FUEL_SPECIFIC_HEAT_CAPACITY = 300
# Lateral (curved) surface area of a cylindrical rod: pi * diameter * length. Was
# previously "6 * pi * (diameter/2)**2", which didn't use ROD_LENGTH at all and gave
# an area ~370x too small - with too little surface to shed heat through, fuel
# temperature could barely cool and got stuck pinned above the SCRAM temperature.
FUEL_ROD_HEAT_TRANSFER_AREA = math.pi * ROD_DIAMETER * ROD_LENGTH

# Coolant mass flow rate (kg/s): a base amount, plus an increment for each panel
# switch that is turned on. More flow removes more heat, so it cools the fuel faster.
BASE_MASS_FLOW_RATE = 10000
FLOW_RATE_PER_SWITCH = 5000

# Fuel temperature (deg C): the reactor starts here, and an automatic SCRAM fires if
# the temperature ever climbs above the scram temperature.
STARTING_TEMPERATURE_C = 800
SCRAM_TEMPERATURE_C = 1400
