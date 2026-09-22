"""
test_periodic_boundary.py - Toroidal Boundary Condition Verification.

Tests every boundary direction and corner:
  * right / left / top / bottom edge crossings reappear on the opposite side
  * the four corner crossings map to the diagonally opposite corners
  * wrapping is part of the STATE (torch.remainder), not just a view
  * minimum-image distances pair points across the seam correctly
  * no artificial walls or edge accumulation (wrapping is lossless)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np

from dynamics import step_euler, compute_neural_force
from energy import NeuralPairwiseEnergy
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer


class TestPeriodicBoundary(unittest.TestCase):

    def setUp(self):
        torch.manual_seed(0)
        self.L = 1.0

    # ------------------------------------------------------------------ #
    #  Direct wrap behaviour of the integrator                            #
    # ------------------------------------------------------------------ #

    def test_right_boundary_wraps_to_left(self):
        pts = torch.tensor([[0.99, 0.5]])
        F = torch.tensor([[5.0, 0.0]])          # large positive x-force
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertLess(new_pts[0, 0].item(), 0.1)
        self.assertGreaterEqual(new_pts[0, 0].item(), 0.0)
        self.assertAlmostEqual(new_pts[0, 1].item(), 0.5, places=5)

    def test_left_boundary_wraps_to_right(self):
        pts = torch.tensor([[0.01, 0.5]])
        F = torch.tensor([[-5.0, 0.0]])
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertGreater(new_pts[0, 0].item(), 0.9)
        self.assertLess(new_pts[0, 0].item(), 1.0)

    def test_top_boundary_wraps_to_bottom(self):
        pts = torch.tensor([[0.5, 0.99]])
        F = torch.tensor([[0.0, 5.0]])
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertLess(new_pts[0, 1].item(), 0.1)
        self.assertAlmostEqual(new_pts[0, 0].item(), 0.5, places=5)

    def test_bottom_boundary_wraps_to_top(self):
        pts = torch.tensor([[0.5, 0.01]])
        F = torch.tensor([[0.0, -5.0]])
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertGreater(new_pts[0, 1].item(), 0.9)

    def test_corner_top_right_wraps_to_bottom_left(self):
        pts = torch.tensor([[0.99, 0.99]])
        F = torch.tensor([[5.0, 5.0]])
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertLess(new_pts[0, 0].item(), 0.1)
        self.assertLess(new_pts[0, 1].item(), 0.1)

    def test_corner_top_left_wraps_to_bottom_right(self):
        pts = torch.tensor([[0.01, 0.99]])
        F = torch.tensor([[-5.0, 5.0]])
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertGreater(new_pts[0, 0].item(), 0.9)
        self.assertLess(new_pts[0, 1].item(), 0.1)

    def test_corner_bottom_right_wraps_to_top_left(self):
        pts = torch.tensor([[0.99, 0.01]])
        F = torch.tensor([[5.0, -5.0]])
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertLess(new_pts[0, 0].item(), 0.1)
        self.assertGreater(new_pts[0, 1].item(), 0.9)

    def test_corner_bottom_left_wraps_to_top_right(self):
        pts = torch.tensor([[0.01, 0.01]])
        F = torch.tensor([[-5.0, -5.0]])
        new_pts = step_euler(pts, F, dt=0.02, L=self.L, max_displacement=10.0)
        self.assertGreater(new_pts[0, 0].item(), 0.9)
        self.assertGreater(new_pts[0, 1].item(), 0.9)

    def test_no_particle_loss_no_clamping(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.rand(1, 64, 2)
        gt = torch.tensor([0.5])
        for _ in range(40):
            F, E = compute_neural_force(pts, gt, model, is_inference=True)
            pts = step_euler(pts, F, dt=0.5, max_displacement=0.05)
        self.assertEqual(pts.shape[1], 64)
        self.assertTrue(((pts >= 0) & (pts < 1)).all(),
                        "particles must stay in [0,1)^2 (wrap, not clamp)")
        self.assertAlmostEqual(pts[:, :, 0].mean().item(), 0.5, delta=0.1)
        self.assertAlmostEqual(pts[:, :, 1].mean().item(), 0.5, delta=0.1)

    # ------------------------------------------------------------------ #
    #  Minimum-image geometry                                              #
    # ------------------------------------------------------------------ #

    def test_minimum_image_distance_across_seam(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.tensor([[[0.995, 0.5], [0.005, 0.5]]])
        gt = torch.tensor([0.0])
        with torch.no_grad():
            F, E = compute_neural_force(pts, gt, model, is_inference=True)
        pts2 = torch.tensor([[[0.5, 0.5], [0.51, 0.5]]])
        with torch.no_grad():
            F2, _ = compute_neural_force(pts2, gt, model, is_inference=True)
        self.assertAlmostEqual(
            F[0, 0, 0].item(), -F[0, 1, 0].item(), places=5,
            msg="seam-crossing pair must interact like a close pair (Newton 3)")
        self.assertAlmostEqual(abs(F[0, 0, 0].item()),
                               abs(F2[0, 0, 0].item()), places=4)

    def test_minimum_image_energy_equivalence(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        gt = torch.tensor([0.25])
        pts_close = torch.tensor([[[0.5, 0.5], [0.5, 0.55]]])
        pts_seam = torch.tensor([[[0.5, 0.5], [0.5, 0.55 + 1.0]]])
        with torch.no_grad():
            E_close = model.compute_total_energy(pts_close, gt)
            E_seam = model.compute_total_energy(pts_seam, gt)
        self.assertAlmostEqual(E_close.item(), E_seam.item(), places=5)

    def test_diagonal_periodic_distance(self):
        model = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
        pts = torch.tensor([[[0.995, 0.995], [0.005, 0.005]]])
        gt = torch.tensor([0.0])
        with torch.no_grad():
            F, _ = compute_neural_force(pts, gt, model, is_inference=True)
        self.assertGreater(torch.abs(F).max().item(), 0.0)

    # ------------------------------------------------------------------ #
    #  Rasterization respects the torus                                   #
    # ------------------------------------------------------------------ #

    def test_rasterizer_wraps_density(self):
        splat = PeriodicGaussianSplat2D(grid_size=16, sigma=0.08)
        pts_near_right_edge = torch.tensor([[0.99, 0.5]])
        d1 = splat(pts_near_right_edge)
        pts_near_left_edge = torch.tensor([[0.01, 0.5]])
        d2 = splat(pts_near_left_edge)
        # a particle at x=0.99 straddles the seam: the direct splat lands on
        # the RIGHT columns and its periodic image on the LEFT columns
        left_weight = d1[:, :2].sum().item()
        right_weight = d1[:, -2:].sum().item()
        self.assertGreater(left_weight, 0.5 * right_weight,
                           "splat at x=0.99 must show a periodic image at "
                           "the left seam")
        self.assertAlmostEqual(d1.sum().item(), d2.sum().item(), places=3)


if __name__ == "__main__":
    unittest.main()
