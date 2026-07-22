"""Tunable gameplay/reactor-balance constants for Reactor Simulator 9000.

Centralised here, instead of scattered through game_new.py's control and
rendering logic, so game balance can be retuned without hunting through code.
"""

TARGET_POWER_MW = 200
TARGET_TOLERANCE_MW = 8
FAILURE_POWER_MW = 250

# k_eff with all control-rod levers fully up (neutral - no lever contributes
# anything).
BASE_K_EFF = 1.0089

# How much each lever (left, middle, right) subtracts from BASE_K_EFF when
# pushed all the way down, scaling linearly to no effect at all the way up.
LEVER_MIN_EFFECT = [-0.01, -0.003, -0.001]

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
