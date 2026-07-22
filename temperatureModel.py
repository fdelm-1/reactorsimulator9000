import math
import time
import config

class TemperatureModel:

    rod_diameter = config.ROD_DIAMETER
    rod_length = config.ROD_LENGTH
    hydraulic_area = (1.26**2 - math.pi * ((rod_diameter/2)**2)) #in m^2
    wetted_perimeter = math.pi * rod_diameter #in m
    number_of_rods = config.NUMBER_OF_RODS

    #constants

    FUEL_ROD_MASS = config.FUEL_ROD_MASS
    FUEL_SPECIFIC_HEAT_CAPACITY = config.FUEL_SPECIFIC_HEAT_CAPACITY
    FUEL_ROD_HEAT_TRANSFER_AREA = config.FUEL_ROD_HEAT_TRANSFER_AREA
    
    FUEL_ROD_HEAT_TRANSFER_AREA = 6 * math.pi * ((rod_diameter/2) ** 2)
    THERMAL_CONDUCTIVITY = 0.598 #of coolant
    FLOW_VISCOSITY = 8.833 * 10**-5 #of coolant
    FLOW_DENSITY = 727.9 #of coolant 
    FLOW_TEMPERATURE = 300 #of coolant 

    PRANDTL_NUMBER = (FLOW_VISCOSITY * FUEL_SPECIFIC_HEAT_CAPACITY) / THERMAL_CONDUCTIVITY

    def __init__(self):
        pass

    def _hydraulic_diameter(self, hydraulic_area, wetted_perimeter):
        return 4 * hydraulic_area / wetted_perimeter
    
    def _flow_free_stream_velocity(self, mass_flow_rate, flow_density, hydraulic_area):
        return mass_flow_rate / (flow_density * hydraulic_area)
    
    def _flow_reynolds_number(self, flow_density, free_stream_velocity, hydraulic_diameter, flow_viscosity):
        return (flow_density * free_stream_velocity * hydraulic_diameter) / flow_viscosity
    
    def _prandtl_number(self, flow_viscosity, specific_heat_capacity, thermal_conductivity):
        return (flow_viscosity * specific_heat_capacity) / thermal_conductivity
    
    def _heat_transfer_coefficient(self, flow_reynolds_number, prandtl_number, thermal_conductivity, hydraulic_diameter):
        return (0.023 * (flow_reynolds_number ** 0.8) * (prandtl_number ** 0.4) * thermal_conductivity) / hydraulic_diameter
    
    def _rod_heat_flux(self, heat_transfer_coefficient, fuel_temperature, flow_temperature):
        return heat_transfer_coefficient * (fuel_temperature - flow_temperature)
    
    def rate_of_fuel_temperature_change(self, game_power, mass_flow_rate, fuel_temperature):

        hydraulic_diameter = self._hydraulic_diameter(self.hydraulic_area, self.wetted_perimeter)
        free_stream_velocity = self._flow_free_stream_velocity(mass_flow_rate, self.FLOW_DENSITY, self.hydraulic_area)
        flow_reynolds_number = self._flow_reynolds_number(self.FLOW_DENSITY, free_stream_velocity, hydraulic_diameter, self.FLOW_VISCOSITY)
        heat_transfer_coefficient = self._heat_transfer_coefficient(flow_reynolds_number, self.PRANDTL_NUMBER, self.THERMAL_CONDUCTIVITY, hydraulic_diameter)
        rod_heat_flux = self._rod_heat_flux(heat_transfer_coefficient, fuel_temperature, self.FLOW_TEMPERATURE)

        return((game_power/self.number_of_rods - (self.FUEL_ROD_HEAT_TRANSFER_AREA * rod_heat_flux)) / (self.FUEL_ROD_MASS * self.FUEL_SPECIFIC_HEAT_CAPACITY))
        

if __name__ == "__main__":
    model = TemperatureModel()
    time1 = time.time()
    print(model.rate_of_fuel_temperature_change(1000000, 0.1, 300))

    print("Time taken: ", time.time() - time1)