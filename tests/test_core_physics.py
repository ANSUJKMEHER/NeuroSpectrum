"""
test_core_physics.py - Conservative Force, Gradient Flow, Symmetry Tests.

Validates the energy-based formulation itself:
  * forces equal the negative autograd gradient of the energy (finite diff)
  * Newton's third law / zero net force (momentum conservation)
  * permutation invariance of the energy
  * translation invariance on the torus
  * gamma-conditioning actually changes the force field
  * gradients flow: positions -> energy -> force -> params (loss)
  * end-to-end gradients (positions -> raster -> FFT -> radial PSD -> loss)
  * reproducibility under fixed seeds
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
from losses import CompositeSpectralLoss


def _finite_diff_force(model, pts, gamma, eps=1e-5):
    """Reference forces by central finite differences of the total energy."""
    pts = pts.detach().clone().requires_grad_(False)
    F = torch.zeros_like(pts)
    for b in range(pts.shape[0]):
        for i in range(pts.shape[1]):
            for d in range(2):
                p_plus = pts.clone()
                p_minus = pts.clone()
                p_plus[b, i, d] += eps
                p_minus[b, i, d] -= eps
                with torch.no_grad():
                    E_plus = model.compute_total_energy(p_plus, gamma)
                    E_minus = model.compute_total_energy(p_minus, gamma)
                F[b, i, d] = -((E_plus[b] - E_minus[b]) / (2 * eps))
    return F


class TestCorePhysics(unittest.TestCase):

    def setUp(self):
        torch.manual_seed(7)

    def test_force_matches_negative_energy_gradient(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3,
                                     use_divergence_prior=True)
        pts = torch.rand(1, 8, 2) * 0.8 + 0.1
        gt = torch.tensor([0.7])
        F_autograd, E = compute_neural_force(pts, gt, model, is_inference=True)
        F_fd = _finite_diff_force(model, pts, gt)
        self.assertLess((F_autograd - F_fd).abs().max().item(), 1e-3,
                        "autograd force != -grad E (finite difference)")

    def test_newton_third_law(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.rand(2, 32, 2)
        gt = torch.tensor([1.0, -1.0])
        F, _ = compute_neural_force(pts, gt, model, is_inference=True)
        self.assertLess(F.sum(dim=1).abs().max().item(), 1e-5,
                        "net force must vanish (momentum conservation)")

    def test_permutation_invariance(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.rand(1, 40, 2)
        gt = torch.tensor([0.3])
        perm = torch.randperm(40)
        with torch.no_grad():
            E1 = model.compute_total_energy(pts, gt)
            E2 = model.compute_total_energy(pts[:, perm], gt)
        self.assertAlmostEqual(E1.item(), E2.item(), places=6)

    def test_translation_invariance_on_torus(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.rand(1, 40, 2)
        shift = torch.tensor([0.37, 0.61])
        pts_shifted = torch.remainder(pts + shift, 1.0)
        gt = torch.tensor([-0.4])
        with torch.no_grad():
            E1 = model.compute_total_energy(pts, gt)
            E2 = model.compute_total_energy(pts_shifted, gt)
        self.assertAlmostEqual(E1.item(), E2.item(), places=5)

    def test_gamma_conditioning_changes_forces(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3, use_divergence_prior=False)
        pts = torch.rand(1, 32, 2)
        F_a, _ = compute_neural_force(pts, torch.tensor([-1.5]), model,
                                      is_inference=True)
        F_b, _ = compute_neural_force(pts, torch.tensor([+1.5]), model,
                                      is_inference=True)
        rel = (F_a - F_b).norm() / (F_a.norm() + 1e-9)
        self.assertGreater(rel.item(), 1e-4,
                           "gamma conditioning must change the force field")

    def test_gradient_flow_positions_to_params(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.rand(1, 16, 2, requires_grad=True)
        gt = torch.tensor([1.0])
        E = model.compute_total_energy(pts, gt)
        E.backward()
        self.assertIsNotNone(pts.grad)
        self.assertGreater(pts.grad.abs().sum().item(), 0.0)
        self.assertIsNotNone(next(model.parameters()).grad)
        self.assertGreater(next(model.parameters()).grad.abs().sum().item(),
                           0.0)

    def test_end_to_end_gradient_positions_to_loss(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        sim = DifferentiableSimulationEngine(model, num_steps=4, dt=0.5,
                                             use_checkpointing=False)
        rasterizer = PeriodicGaussianSplat2D(grid_size=16, sigma=0.06)
        analyzer = DifferentiableSpectralAnalyzer(grid_size=16, sigma=0.06,
                                                  f_min=2, f_max=6)
        loss_fn = CompositeSpectralLoss(lambda_spec=1.0, lambda_gamma=3.0,
                                        lambda_spacing=0.5, n_particles=16,
                                        ensemble_psd=True)
        pts = torch.rand(1, 16, 2, requires_grad=True)
        gt = torch.tensor([1.0])
        xf = sim(pts, gt)
        out = analyzer(rasterizer(xf))
        ld = loss_fn(xf, gt, out)
        ld['loss'].backward()
        self.assertIsNotNone(pts.grad)
        self.assertGreater(pts.grad.abs().sum().item(), 0.0)
        self.assertTrue(np.isfinite(pts.grad.abs().sum().item()))
        for p in model.parameters():
            self.assertIsNotNone(p.grad)
            self.assertTrue(torch.isfinite(p.grad).all())

    def test_reproducibility_same_seed(self):
        def run(seed):
            torch.manual_seed(seed)
            model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
            pts = torch.rand(1, 16, 2)
            gt = torch.tensor([0.5])
            with torch.no_grad():
                for _ in range(3):
                    F, _ = compute_neural_force(pts, gt, model,
                                                is_inference=True)
                    pts = torch.remainder(pts + 0.02 * F / (F.norm(dim=-1,
                                        keepdim=True) + 1e-8), 1.0)
            return pts.clone()

        a = run(123)
        b = run(123)
        c = run(999)
        self.assertTrue(torch.equal(a, b), "same seed must reproduce exactly")
        self.assertFalse(torch.equal(a, c),
                         "different seeds should differ (sanity)")

    def test_energy_symmetry_under_coordinate_inversion(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.rand(1, 30, 2)
        gt = torch.tensor([0.2])
        pts_mirror = torch.remainder(-pts, 1.0)
        with torch.no_grad():
            E1 = model.compute_total_energy(pts, gt)
            E2 = model.compute_total_energy(pts_mirror, gt)
        self.assertAlmostEqual(E1.item(), E2.item(), places=5)


if __name__ == "__main__":
    unittest.main()
