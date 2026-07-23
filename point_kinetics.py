import numpy as np
import matplotlib.pyplot as plt

from time import monotonic_ns


class PrecursorGroup:
    def __init__(self, decay_constant, beta_i):
        self.decay_constant = decay_constant
        self.beta_i = beta_i

wikipedia_precursors = [
        PrecursorGroup(0.0124, 0.000215),
        PrecursorGroup(0.0305, 0.001424),
        PrecursorGroup(0.111,  0.001274),
        PrecursorGroup(0.301,  0.002568),
        PrecursorGroup(1.14,   0.000748),
        PrecursorGroup(3.01,   0.000273),
    ]

# The array below is the precursor/n steady-state ratio for a critical (k_eff = 1)
# reactor with n = 1. Scaled up so the game starts producing STARTING_POWER_MW
# rather than 1 MW - scaling every component keeps them in the same equilibrium
# ratio, just at a higher power level.
STARTING_POWER_MW = 10.0

wikipedia_precursors_stable_solution = np.array([17.33870791164053, 46.68852515479314, 11.477477500705534, 8.531561467616813, 0.6561403509912391, 0.09069767442452846, 1.0]) * STARTING_POWER_MW


class PointKinetics:

    def __init__(
            self,
            precursors = wikipedia_precursors,
            initial_solution = wikipedia_precursors_stable_solution,
            l = 1e-3,
        ):

        self.precursors = precursors
        self.l = l

        self._precursor_betas            = np.array([precursor.beta_i for precursor in precursors])
        self._precursor_decay_constants  = np.array([precursor.decay_constant for precursor in precursors])

        self.beta = np.sum(self._precursor_betas)
        self.prompt_critical_point = 1 + self.beta

        ##--sol: C_1, C_2, C_3, ..., C_{len(precursors)}, n
        self.mat = np.zeros(
                shape = (len(precursors) + 1, len(precursors) + 1),
                dtype = np.float64
            )
        self.sol = np.zeros(
                shape = (len(precursors) + 1,),
                dtype = np.float64
            )
        # self.sol[-1] = 1.0
        self.initial_solution = initial_solution
        self.sol[:] = initial_solution
        self.src = np.zeros_like(self.sol)

        self.n_history = False
    
    def reset_sol(self):
        self.sol = self.initial_solution

    def enable_n_history(self, back_duration, dt):
        self.n_history = True
        self.n_history_time_window  = np.arange(0, back_duration + dt, dt) * -1
        self.n_history_time_window  = np.flip(self.n_history_time_window)
        self.n_history_solutions    = np.full_like(self.n_history_time_window, self.n)
    
    def push_to_n_history(self):
        self.n_history_solutions = np.roll(self.n_history_solutions, -1)
        self.n_history_solutions[-1] = self.n
    
    @property
    def n(self):
        return self.sol[-1]
    
    @n.setter
    def n(self, value):
        self.sol[-1] = value
    
    def _n_eqn_n_term(self, k_eff):
        return (k_eff * (1 - self.beta) - 1) / self.l
    
    def _c_i_eqn_n_term(self, k_eff):
        return k_eff * self._precursor_betas / self.l
    
    def backwards_euler_step(
            self,
            dt,
            k_eff
        ):
        """
        dn/dt = n * ((k_eff *(1-beta) - 1) / l) + sum([decay_constant_i * C_i for all i])
        dC_i/dt = n * (k * beta_i / l) - decay_constant_i * C_i
        """

        self.src[:] = self.sol[:] / dt

        self.mat[-1,-1] = (1/dt) - self._n_eqn_n_term(k_eff)
        self.mat[-1,:-1] = - self._precursor_decay_constants

        c_i_n_terms         = self._c_i_eqn_n_term(k_eff)
        self.mat[:-1, -1]   = - c_i_n_terms
        for i, precursor in enumerate(self.precursors):
            self.mat[i, i]  = (precursor.decay_constant + (1/dt))

        sol_bw_euler = np.linalg.solve(self.mat, self.src)

        return sol_bw_euler
    
    def implicit_heun_step(self, dt, k_eff):
        sol_bw_euler = self.backwards_euler_step(dt, k_eff)
        sol_avg = (self.sol + sol_bw_euler) / 2

        sol_heun = np.empty_like(self.sol)

        sol_heun[:-1]   = self.sol[:-1] + dt * ( (self._c_i_eqn_n_term(k_eff) * sol_avg[-1]) - (self._precursor_decay_constants * sol_avg[:-1]) )
        sol_heun[-1]    = self.sol[-1] + dt * ( (self._n_eqn_n_term(k_eff) * sol_avg[-1]) + np.sum(self._precursor_decay_constants * sol_avg[:-1]) )

        return sol_heun
    
    def step(self, dt, k_eff, method = "backwards_euler"):
        match method:
            case "backwards_euler":
                self.sol = self.backwards_euler_step(dt, k_eff)
            case "implicit_heun":
                self.sol = self.implicit_heun_step(dt, k_eff)
            case _:
                raise ValueError(f"Method {method} not implemented.")
        
        if self.n_history:
            self.push_to_n_history()
        
        return self.sol

    def multi_step(self, duration, dt, k_eff, method = "backwards_euler"):
        time_steps = np.arange(0, duration + dt, dt)
        n_for_time = np.empty_like(time_steps)

        n_for_time[0] = self.sol[-1]
        for i in range(1, len(time_steps)):
            n_for_time[i] = self.step(dt, k_eff, method)[-1]

        return time_steps, n_for_time


if __name__ == "__main__":

    pk = PointKinetics(l = 1e-3)

    # t1 = monotonic_ns()
    # pk.multi_step(1, 0.0005, 1.006, method = "backwards_euler")
    # t2 = monotonic_ns()

    # print(f"Time taken BW Euler: {(t2 - t1) / 1e9} s")

    # t1 = monotonic_ns()
    # pk.multi_step(1, 0.2, 1.006, method = "heun")
    # t2 = monotonic_ns()

    # print(f"Time taken heun 0.2: {(t2 - t1) / 1e9} s")

    # t1 = monotonic_ns()
    # pk.multi_step(1, 0.1, 1.006, method = "heun")
    # t2 = monotonic_ns()

    # print(f"Time taken heun 0.1: {(t2 - t1) / 1e9} s")

    pk.multi_step(1000, 0.1, 1.0)

    # print(np.array([0.17283352, 0.56690474, 0.13752102, 0.10190134, 0.00782917, 0.001082, 0.01192822]) / 0.01192822)

    # for dt in [0.5, 0.1, 0.05, 0.001, 0.0005]:
    #     pk.sol[:] = 0.0
    #     pk.sol[-1] = 1.0
    #     time_steps, n_for_time = pk.multi_step(1, dt, 1.006)
    #     plt.plot(time_steps, n_for_time, label = f"dt = {dt}")
    
    # for dt in [0.1]:
    #     pk.sol[:] = 0.0
    #     pk.sol[-1] = 1.0
    #     time_steps, n_for_time = pk.multi_step(1, dt, 1.006, method = "heun")
    #     plt.plot(time_steps, n_for_time, label = f"heun dt = {dt}", marker = "o")
    
    # plt.legend()
    # plt.show()

"""
Wow
Heun predictor-corrector with dt = 0.1 is as accurate as backwards euler with dt = 0.0005
dt = 0.2 is also amazingly accurate
"""