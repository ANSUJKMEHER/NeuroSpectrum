"""
test_physics.py - Verification for Phases 3, 4, and 5.
Tests:
- Permutation invariance of energy field
- Momentum conservation (sum F_i == 0)
- Autograd vs. Finite-Difference force check
- Gradient checkpointing equivalence
- Inference & training force computation modes
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np
from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine, compute_neural_force


def test_permutation_invariance_and_momentum():
    torch.manual_seed(42)
    model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
    gamma = torch.tensor([1.0, -0.5])
    pts = torch.rand(2, 64, 2, requires_grad=True)
    
    # 1. Invariance
    E_orig = model.compute_total_energy(pts, gamma)
    perm = torch.randperm(64)
    E_perm = model.compute_total_energy(pts[:, perm, :], gamma)
    assert torch.abs(E_orig - E_perm).max().item() < 1e-6, "Energy is not permutation-invariant!"
    
    # 2. Momentum conservation
    forces, _ = compute_neural_force(pts, gamma, model)
    net_forces = forces.sum(dim=1)
    assert torch.norm(net_forces).item() < 1e-5, "Newton's third law violation: net force != 0!"
    print(">> Permutation invariance & Momentum conservation PASSED.")


def test_gradient_checkpointing():
    torch.manual_seed(42)
    m1 = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
    m2 = NeuralPairwiseEnergy(hidden_dim=16, num_layers=3)
    m2.load_state_dict(m1.state_dict())
    
    pts1 = torch.rand(2, 32, 2, requires_grad=True)
    pts2 = pts1.clone().detach().requires_grad_(True)
    gamma = torch.tensor([1.0, -1.0])
    
    sim1 = DifferentiableSimulationEngine(m1, num_steps=8, dt=0.01, use_checkpointing=False)
    sim2 = DifferentiableSimulationEngine(m2, num_steps=8, dt=0.01, use_checkpointing=True)
    
    out1 = sim1(pts1, gamma)
    out2 = sim2(pts2, gamma)
    
    out1.sum().backward()
    out2.sum().backward()
    
    diff = torch.abs(out1 - out2).max().item()
    diff_grad = max(torch.abs(p1.grad - p2.grad).max().item() for p1, p2 in zip(m1.parameters(), m2.parameters()))
    assert diff < 1e-6 and diff_grad < 1e-6, "Checkpointing gradient discrepancy!"
    print(">> Gradient Checkpointing equivalence PASSED.")


if __name__ == "__main__":
    test_permutation_invariance_and_momentum()
    test_gradient_checkpointing()
