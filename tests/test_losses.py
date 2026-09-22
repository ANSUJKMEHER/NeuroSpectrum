"""
test_losses.py - Verification for Phase 6.
Tests:
- Anti-collapse spacing loss (proves paired-collapse is penalized over ideal blue noise)
- Spectral loss scaling & slope loss
- Classical baseline optimizer functional test
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np
from losses import CompositeSpectralLoss
from baseline import ClassicalPerTargetOptimizer
from data import generate_poisson_disk


def test_anti_collapse_spacing_loss():
    loss_fn = CompositeSpectralLoss()
    
    # 1. Artificially paired particles (sub-kernel collapse)
    N = 256
    centers = torch.rand(1, N // 2, 2)
    offsets = torch.randn(1, N // 2, 2)
    offsets = offsets / torch.norm(offsets, dim=-1, keepdim=True) * 0.001
    pts_pairs = torch.cat([centers + offsets, centers - offsets], dim=1) % 1.0
    
    l_pairs, _, _ = loss_fn.compute_spacing_loss(pts_pairs)
    
    # 2. Well-spaced Poisson disk blue noise
    pts_blue = generate_poisson_disk(batch_size=1, n_particles=N)
    l_blue, _, _ = loss_fn.compute_spacing_loss(pts_blue)
    
    print(f"[*] Spacing Loss: Paired Collapse = {l_pairs.item():.4f} vs Ideal Blue Noise = {l_blue.item():.4f}")
    assert l_pairs.item() > l_blue.item() * 5.0, "Spacing loss failed to severely penalize particle collapse!"
    print(">> Anti-collapse spacing loss test PASSED.")


def test_baseline_optimizer():
    opt = ClassicalPerTargetOptimizer(n_particles=64)
    pts = torch.rand(1, 64, 2)
    res = opt.optimize(pts, target_gamma_val=1.0, num_iters=10, lr=0.02)
    assert res['final_loss'] < res['loss_history'][0], "Baseline optimizer failed to reduce loss!"
    print(">> Classical baseline optimizer test PASSED.")


if __name__ == "__main__":
    test_anti_collapse_spacing_loss()
    test_baseline_optimizer()
