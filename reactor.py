from matplotlib import pyplot as plt
import numpy as np

from time import monotonic_ns

class Unit:

    def __init__(
            self,
            x : float,
            y : float,
            z : float,
        ) -> None:
        self.x = x
        self.y = y
        self.z = z

        self.sol = np.array([1.0, 0.0])

        self.index = 0 ##--nullifying index

    def __repr__(self) -> str:
        return f"xU(    {self.x:.3f}, {self.y:.3f}, {self.z:.3f})"


class SolvedUnit(Unit):

    def __init__(
            self,
            x : float,
            y : float,
            z : float,
            index : int,
        ) -> None:
        super().__init__(x, y, z)
        self.index = index
    
    def set_neighbours(self, xp, xm, yp, ym, zp, zm):
        self.xp = xp
        self.xm = xm
        self.yp = yp
        self.ym = ym
        self.zp = zp
        self.zm = zm

        def make_weights(selfx, mx, px):
            nonlocal self
            m = selfx - mx
            p = px - selfx
            denom = m * p * (m + p) / 2
            self_weight, m_weight, p_weight = - (m+p) / denom, p / denom, m / denom
            return self_weight, m_weight, p_weight
        
        self.x_weight, self.xm_weight, self.xp_weight = make_weights(self.x, self.xm.x, self.xp.x)
        self.y_weight, self.ym_weight, self.yp_weight = make_weights(self.y, self.ym.y, self.yp.y)
        self.z_weight, self.zm_weight, self.zp_weight = make_weights(self.z, self.zm.z, self.zp.z)

    @property
    def SIGMA_r(self):
        return self.SIGMA_a + np.sum(self.SIGMA_s, axis = 1)
    
    @property
    def diffusion_coefficient(self):
        return 1 / (3 * self.SIGMA_r)
    
    def make_flux_dependent_sources(self):
        self._fission_source = np.matmul(self.chi_nu_SIGMA_f, self.sol)
        self._scatter_source = np.matmul(self.SIGMA_s, self.sol)
    
    def get_equation_eigenvalue_mat(self):
        return (
            (self.index   , self.SIGMA_r - self.diffusion_coefficient * (self.x_weight + self.y_weight + self.z_weight)),
            (self.xm.index, - self.diffusion_coefficient * self.xm_weight),
            (self.xp.index, - self.diffusion_coefficient * self.xp_weight),
            (self.ym.index, - self.diffusion_coefficient * self.ym_weight),
            (self.yp.index, - self.diffusion_coefficient * self.yp_weight),
            (self.zm.index, - self.diffusion_coefficient * self.zm_weight),
            (self.zp.index, - self.diffusion_coefficient * self.zp_weight),
        )
    
    def get_equation_eigenvalue_src(self, k_eff):
        return self.static_source + self._scatter_source + (self._fission_source/k_eff)
    
    def __repr__(self) -> str:
        return f"SU({self.index:2}: {self.x:.3f}, {self.y:.3f}, {self.z:.3f})"






if __name__ == "__main__":
    from cProfile import Profile
    from pstats import SortKey, Stats

    r = Reactor(9, 9, 10, 0.1, 0.1, 0.1)

    r.add_cell_props(
        SIGMA_a = np.array([1.0, 2.0]),
        SIGMA_s = np.array([[0.0, 0.0], [1.0, 0.0]]),
        chi_nu_SIGMA_f = np.array([[0,2.75 * 10], [0,0]]),
        static_source = np.array([0.0, 0.0]),
    )

    r.update_sources()

    with Profile() as profile:
        k_eff = r.build_and_solve_eigenvalue()["k_eff"]
        print(k_eff)
        (
            Stats(profile)
            .strip_dirs()
            .sort_stats(SortKey.CALLS)
            .print_stats()
        )