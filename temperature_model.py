"""
Fuel rod temperature model.

UO2 fuel rod cooled by subcooled water, scaled to a core of N_RODS rods.
Fuel-to-coolant heat transfer uses an overall coefficient U combining four
resistances in series: pellet conduction, pellet-clad gap, clad conduction,
and coolant convection.

    m * c_p_fuel * dT_f/dt = Q_dot_rod - U * A_clad * (T_f - T_c)

T_f is the volume-average fuel temperature. All values in SI units.
"""

import math

# --- Core configuration (200 MWt SMR) ---
N_ASSEMBLIES = 25
RODS_PER_ASSEMBLY = 264           # 17x17 lattice less 25 guide/instrument tubes
N_RODS = N_ASSEMBLIES * RODS_PER_ASSEMBLY               # 6600

# --- Fuel rod geometry ---
ROD_LENGTH = 2.0                  # m, active length
PELLET_DIAMETER = 0.0082          # m, UO2 pellet OD
CLAD_INNER_DIAMETER = 0.00838     # m
CLAD_OUTER_DIAMETER = 0.0095      # m
ROD_PITCH = 0.0126                # m, square lattice

# --- Fuel properties (UO2) ---
FUEL_DENSITY = 10420.0            # kg/m^3, 95% of theoretical density
FUEL_SPECIFIC_HEAT = 300.0        # J/(kg K), at mean fuel temp ~1000 K
FUEL_CONDUCTIVITY = 3.0           # W/(m K), at ~1000 K
FUEL_TEMP_INITIAL = 1114.0        # K, approx full-power steady state

# --- Gap and cladding ---
GAP_CONDUCTANCE = 6000.0          # W/(m^2 K), typical fresh-fuel value
CLAD_CONDUCTIVITY = 17.0          # W/(m K), Zircaloy-4 at ~600 K

# --- Coolant properties (water, 573 K, 160 bar) ---
COOLANT_TEMP = 573.0              # K
COOLANT_DENSITY = 735.0           # kg/m^3
COOLANT_CONDUCTIVITY = 0.564      # W/(m K)
COOLANT_SPECIFIC_HEAT = 5350.0    # J/(kg K)
COOLANT_VISCOSITY = 8.95e-5       # Pa s

# --- Dittus-Boelter correlation ---
DB_COEFFICIENT = 0.023
DB_REYNOLDS_EXPONENT = 0.8
DB_PRANDTL_EXPONENT = 0.4         # 0.4 for heating of the fluid


class TemperatureModel:
    """Transient temperature of fuel rods in a core of N rods."""

    def __init__(
        self,
        n_rods=N_RODS,
        rod_length=ROD_LENGTH,
        pellet_diameter=PELLET_DIAMETER,
        clad_inner_diameter=CLAD_INNER_DIAMETER,
        clad_outer_diameter=CLAD_OUTER_DIAMETER,
        rod_pitch=ROD_PITCH,
        fuel_density=FUEL_DENSITY,
        fuel_specific_heat=FUEL_SPECIFIC_HEAT,
        fuel_conductivity=FUEL_CONDUCTIVITY,
        gap_conductance=GAP_CONDUCTANCE,
        clad_conductivity=CLAD_CONDUCTIVITY,
        coolant_temp=COOLANT_TEMP,
        coolant_density=COOLANT_DENSITY,
        coolant_conductivity=COOLANT_CONDUCTIVITY,
        coolant_specific_heat=COOLANT_SPECIFIC_HEAT,
        coolant_viscosity=COOLANT_VISCOSITY,
    ):
        self.n_rods = n_rods
        self.rod_length = rod_length
        self.pellet_diameter = pellet_diameter
        self.clad_inner_diameter = clad_inner_diameter
        self.clad_outer_diameter = clad_outer_diameter
        self.rod_pitch = rod_pitch
        self.fuel_density = fuel_density
        self.fuel_specific_heat = fuel_specific_heat
        self.fuel_conductivity = fuel_conductivity
        self.gap_conductance = gap_conductance
        self.clad_conductivity = clad_conductivity
        self.coolant_temp = coolant_temp
        self.coolant_density = coolant_density
        self.coolant_conductivity = coolant_conductivity
        self.coolant_specific_heat = coolant_specific_heat
        self.coolant_viscosity = coolant_viscosity

    # --- Derived geometry (per rod) ---

    @property
    def pellet_volume(self):
        """UO2 volume of one rod, m^3."""
        return math.pi * (self.pellet_diameter / 2.0) ** 2 * self.rod_length

    @property
    def rod_mass(self):
        """UO2 mass of one rod, kg. Pellet only, excludes cladding."""
        return self.fuel_density * self.pellet_volume

    @property
    def heat_transfer_area(self):
        """Outer clad surface area of one rod, m^2. Reference area for U."""
        return math.pi * self.clad_outer_diameter * self.rod_length

    @property
    def flow_area(self):
        """Subchannel coolant flow area for one rod, m^2."""
        return self.rod_pitch ** 2 - math.pi * self.clad_outer_diameter ** 2 / 4.0

    @property
    def hydraulic_diameter(self):
        """Subchannel hydraulic diameter, m."""
        return 4.0 * self.flow_area / (math.pi * self.clad_outer_diameter)

    # --- Derived geometry (core totals) ---

    @property
    def core_mass(self):
        """Total UO2 mass in the core, kg."""
        return self.rod_mass * self.n_rods

    @property
    def core_heat_transfer_area(self):
        """Total clad outer surface area, m^2."""
        return self.heat_transfer_area * self.n_rods

    @property
    def core_flow_area(self):
        """Total coolant flow area, m^2."""
        return self.flow_area * self.n_rods

    # --- Per-rod splitting ---

    def mass_flow_per_rod(self, core_mass_flow_rate):
        """Coolant mass flow per rod, kg/s."""
        return core_mass_flow_rate / self.n_rods

    def power_per_rod(self, core_thermal_power):
        """Thermal power per rod, W."""
        return core_thermal_power / self.n_rods

    # --- Flow and convection ---

    def free_stream_velocity(self, core_mass_flow_rate):
        """Coolant velocity, m/s."""
        return self.mass_flow_per_rod(core_mass_flow_rate) / (
            self.coolant_density * self.flow_area
        )

    def reynolds_number(self, core_mass_flow_rate):
        """Reynolds number, dimensionless."""
        return (
            self.coolant_density
            * self.free_stream_velocity(core_mass_flow_rate)
            * self.hydraulic_diameter
            / self.coolant_viscosity
        )

    def prandtl_number(self):
        """Prandtl number, dimensionless."""
        return (
            self.coolant_viscosity
            * self.coolant_specific_heat
            / self.coolant_conductivity
        )

    def dittus_boelter(self, core_mass_flow_rate):
        """Nusselt number from the Dittus-Boelter correlation."""
        return (
            DB_COEFFICIENT
            * self.reynolds_number(core_mass_flow_rate) ** DB_REYNOLDS_EXPONENT
            * self.prandtl_number() ** DB_PRANDTL_EXPONENT
        )

    def heat_transfer_coefficient(self, core_mass_flow_rate):
        """Convective heat transfer coefficient, W/(m^2 K)."""
        return (
            self.dittus_boelter(core_mass_flow_rate)
            * self.coolant_conductivity
            / self.hydraulic_diameter
        )

    # --- Thermal resistances, per unit rod length, m K/W ---

    @property
    def fuel_resistance(self):
        """
        Pellet conduction resistance. Uses 1/(4 pi k) for a solid cylinder
        with uniform volumetric heat generation, which gives the volume-average
        pellet temperature rather than the centreline.
        """
        return 1.0 / (4.0 * math.pi * self.fuel_conductivity)

    @property
    def gap_resistance(self):
        """Pellet-clad gap resistance."""
        return 1.0 / (math.pi * self.pellet_diameter * self.gap_conductance)

    @property
    def clad_resistance(self):
        """Cladding conduction resistance."""
        return math.log(
            self.clad_outer_diameter / self.clad_inner_diameter
        ) / (2.0 * math.pi * self.clad_conductivity)

    def convection_resistance(self, core_mass_flow_rate):
        """Coolant convection resistance."""
        return 1.0 / (
            math.pi
            * self.clad_outer_diameter
            * self.heat_transfer_coefficient(core_mass_flow_rate)
        )

    def total_resistance(self, core_mass_flow_rate):
        """Sum of series resistances, m K/W."""
        return (
            self.fuel_resistance
            + self.gap_resistance
            + self.clad_resistance
            + self.convection_resistance(core_mass_flow_rate)
        )

    def overall_heat_transfer_coefficient(self, core_mass_flow_rate):
        """
        Overall coefficient U, W/(m^2 K), referenced to clad outer surface.
        """
        return 1.0 / (
            math.pi
            * self.clad_outer_diameter
            * self.total_resistance(core_mass_flow_rate)
        )

    # --- Transient ---

    def fuel_temperature_rate(
        self, core_mass_flow_rate, core_thermal_power, fuel_temp
    ):
        """
        Rate of change of volume-average fuel temperature, K/s.

        core_mass_flow_rate : total core coolant mass flow, kg/s
        core_thermal_power  : total core thermal power, W
        fuel_temp           : current fuel temperature, K
        """
        u_overall = self.overall_heat_transfer_coefficient(core_mass_flow_rate)
        heat_removed = (
            u_overall * self.heat_transfer_area * (fuel_temp - self.coolant_temp)
        )
        thermal_capacity = self.rod_mass * self.fuel_specific_heat
        return (
            self.power_per_rod(core_thermal_power) - heat_removed
        ) / thermal_capacity


def main():
    model = TemperatureModel()

    core_mass_flow_rate = float(input("Total core coolant mass flow rate (kg/s): "))
    core_thermal_power = float(input("Total core thermal power output (W): "))
    fuel_temp_input = input(
        f"Fuel temperature (K) [default {FUEL_TEMP_INITIAL}]: "
    ).strip()
    fuel_temp = float(fuel_temp_input) if fuel_temp_input else FUEL_TEMP_INITIAL

    r_f = model.fuel_resistance
    r_g = model.gap_resistance
    r_c = model.clad_resistance
    r_v = model.convection_resistance(core_mass_flow_rate)
    r_tot = model.total_resistance(core_mass_flow_rate)

    print(f"\nNumber of rods:          {model.n_rods}")
    print(f"UO2 mass per rod:        {model.rod_mass:.4f} kg")
    print(f"Core fuel mass:          {model.core_mass:.4g} kg")
    print(f"Clad area per rod:       {model.heat_transfer_area:.5f} m^2")
    print(f"Flow area per rod:       {model.flow_area:.4e} m^2")
    print(f"Hydraulic diameter:      {model.hydraulic_diameter:.5f} m")
    print(f"Mass flow per rod:       {model.mass_flow_per_rod(core_mass_flow_rate):.4f} kg/s")
    print(f"Power per rod:           {model.power_per_rod(core_thermal_power):.4g} W")
    print(f"Free-stream velocity:    {model.free_stream_velocity(core_mass_flow_rate):.3f} m/s")
    print(f"Reynolds number:         {model.reynolds_number(core_mass_flow_rate):.4g}")
    print(f"Prandtl number:          {model.prandtl_number():.4f}")
    print(f"Nusselt number:          {model.dittus_boelter(core_mass_flow_rate):.4g}")
    print(f"Convective h:            {model.heat_transfer_coefficient(core_mass_flow_rate):.4g} W/(m^2 K)")

    print("\nThermal resistances (m K/W, and share of total):")
    print(f"  Pellet conduction:     {r_f:.5f}  ({100*r_f/r_tot:.1f}%)")
    print(f"  Gap:                   {r_g:.5f}  ({100*r_g/r_tot:.1f}%)")
    print(f"  Cladding:              {r_c:.5f}  ({100*r_c/r_tot:.1f}%)")
    print(f"  Convection:            {r_v:.5f}  ({100*r_v/r_tot:.1f}%)")
    print(f"  Total:                 {r_tot:.5f}")

    print(f"\nOverall U:               {model.overall_heat_transfer_coefficient(core_mass_flow_rate):.4g} W/(m^2 K)")
    print(f"Steady-state fuel temp:  {model.steady_state_fuel_temp(core_mass_flow_rate, core_thermal_power):.1f} K")
    print(f"\ndT_f/dt = {model.fuel_temperature_rate(core_mass_flow_rate, core_thermal_power, fuel_temp):.4f} K/s")


if __name__ == "__main__":
    main()
