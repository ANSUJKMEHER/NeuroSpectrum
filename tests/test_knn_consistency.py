"""
test_knn_consistency.py - Local (kNN) vs Global Interaction Consistency.

The scalable O(Nk) local-interaction approximation must not change the
learned physics:
  * kNN energy converges to the global energy as k -> N-1
  * kNN forces pass a finite-difference check on the kNN energy
  * quality (gamma_hat) stays close between global and kNN dynamics
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np

from energy import NeuralPairwiseEnergy
from dynamics import compute_neural_force, DifferentiableSimulationEngine
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer


class TestKnnConsistency(unittest.TestCase):

    def setUp(self):
        torch.manual_seed(11)
        self.model = NeuralPairwiseEnergy(hidden_dim=24, num_layers=3)

    def test_knn_energy_converges_to_global(self):
        pts = torch.rand(1, 48, 2)
        gt = torch.tensor([0.6])
        with torch.no_grad():
            E_global = self.model.compute_total_energy(pts, gt)
            E_knn = self.model.compute_total_energy_knn(pts, gt, k=47)
        self.assertAlmostEqual(E_global.item(), E_knn.item(), places=4)

    def test_knn_forces_finite_difference(self):
        pts = torch.rand(1, 12, 2) * 0.8 + 0.1
        gt = torch.tensor([0.2])
        F_knn, _ = compute_neural_force(pts, gt, self.model,
                                        use_knn=True, k=6, is_inference=True)
        # finite differences of the kNN energy
        eps = 1e-5
        F_fd = torch.zeros_like(pts)
        for i in range(12):
            for d in range(2):
                p_plus = pts.clone(); p_minus = pts.clone()
                p_plus[0, i, d] += eps
                p_minus[0, i, d] -= eps
                with torch.no_grad():
                    Ep = self.model.compute_total_energy_knn(p_plus, gt, k=6)
                    Em = self.model.compute_total_energy_knn(p_minus, gt, k=6)
                F_fd[0, i, d] = -((Ep[0] - Em[0]) / (2 * eps))
        self.assertLess((F_knn - F_fd).abs().max().item(), 2e-3,
                        "kNN autograd force != finite-difference of kNN energy")

    def test_knn_matches_global_quality(self):
        m1 = NeuralPairwiseEnergy(hidden_dim=24, num_layers=3)
        m2 = NeuralPairwiseEnergy(hidden_dim=24, num_layers=3)
        m2.load_state_dict(m1.state_dict())

        rast = PeriodicGaussianSplat2D(grid_size=32, sigma=0.04)
        ana = DifferentiableSpectralAnalyzer(grid_size=32, sigma=0.04,
                                             f_min=2, f_max=12)
        pts = torch.rand(1, 64, 2)
        gt = torch.tensor([0.5])

        sim_global = DifferentiableSimulationEngine(
            m1, num_steps=6, dt=0.5, use_checkpointing=False)
        sim_knn = DifferentiableSimulationEngine(
            m2, num_steps=6, dt=0.5, use_checkpointing=False,
            use_knn=True, k=32)

        with torch.no_grad():
            xg = sim_global(pts, gt)
            xk = sim_knn(pts, gt)
            g_g = ana(rast(xg))['gamma'].item()
            g_k = ana(rast(xk))['gamma'].item()
        self.assertAlmostEqual(g_g, g_k, delta=0.25,
                               msg="kNN (k=32) final spectrum must stay close "
                                   "to global for the same field")


if __name__ == "__main__":
    unittest.main()
