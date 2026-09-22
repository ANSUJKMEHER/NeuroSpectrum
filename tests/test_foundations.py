"""
test_foundations.py - Verification for Phases 0, 1, and 2.
Tests:
- Reference distributions (Red, White, Blue ordering)
- Periodic Gaussian Splatting
- Differentiable 2D FFT & PSD
- Finite-difference gradient check on interior, boundary edges, and corners
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np
from data import generate_uniform_random, generate_poisson_disk, generate_clustered_red_noise
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer


def test_reference_distributions():
    rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24)
    
    # 1. White noise
    pts_white = generate_uniform_random(batch_size=4, n_particles=256)
    gamma_white = analyzer(rasterizer(pts_white))['gamma'].mean().item()
    
    # 2. Blue noise
    pts_blue = generate_poisson_disk(batch_size=2, n_particles=256)
    gamma_blue = analyzer(rasterizer(pts_blue))['gamma'].mean().item()
    
    # 3. Red noise
    pts_red = generate_clustered_red_noise(batch_size=4, n_particles=256)
    gamma_red = analyzer(rasterizer(pts_red))['gamma'].mean().item()
    
    print(f"[*] Reference Gammas: Red={gamma_red:+.3f} | White={gamma_white:+.3f} | Blue={gamma_blue:+.3f}")
    assert gamma_red < gamma_white < gamma_blue, "Reference spectra ordering violation!"
    print(">> Reference distributions test PASSED.")


def test_boundary_gradcheck():
    rasterizer = PeriodicGaussianSplat2D(grid_size=16, sigma=0.1)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=16, sigma=0.1, f_min=2, f_max=6)
    
    def loss_fn(pts):
        return analyzer(rasterizer(pts))['gamma'].sum()

    pts_test = torch.tensor([[
        [0.999, 0.500],
        [0.001, 0.500],
        [0.500, 0.999],
        [0.500, 0.001],
        [0.999, 0.999],
        [0.500, 0.500]
    ]], dtype=torch.float64, requires_grad=True)
    
    passed = torch.autograd.gradcheck(loss_fn, (pts_test,), eps=1e-6, atol=1e-4)
    assert passed, "Boundary finite-difference gradcheck failed!"
    print(">> Boundary gradcheck test PASSED.")


if __name__ == "__main__":
    test_reference_distributions()
    test_boundary_gradcheck()
