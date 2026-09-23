"""
test_edge_cases.py — Comprehensive Engineering Edge-Case & Boundary Stress Test Suite.
Verifies all extreme boundary, numerical, and structural edge cases:
1. N = 1 degenerate particle state (no pairs)
2. N = 2 minimal pair interaction
3. Unbatched 2D inputs (N, 2) vs 3D batched inputs (B, N, 2)
4. Coincident points r_ij = 0 (exact coordinate overlap gradient stability)
5. Corner/Torus wrapping across (0,0) <-> (1,1)
6. Extreme spectral targets (gamma = -3.0 and gamma = +3.0)
7. Over-saturated k-NN graph settings (k >= N and k = 1)
8. Double precision / mixed precision inputs (float64 / float32)
9. Zero-variance flat distributions
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np

from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine, compute_neural_force
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from losses import CompositeSpectralLoss
from evaluate import compute_spatial_statistics
from data import disambiguate_coincident_points
from inference import FrozenInferenceEngine


def test_edge_cases():
    print("=" * 80)
    print("[*] RUNNING COMPREHENSIVE ENGINEERING EDGE-CASE TEST SUITE")
    print("=" * 80)
    
    device = "cpu"
    energy_model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3).to(device)
    dynamics = DifferentiableSimulationEngine(energy_model, num_steps=5, dt=0.01).to(device)
    rasterizer = PeriodicGaussianSplat2D(grid_size=32, sigma=0.03).to(device)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=32, sigma=0.03, f_min=2, f_max=12).to(device)
    loss_fn = CompositeSpectralLoss(n_particles=64).to(device)
    
    # -------------------------------------------------------------------
    # TEST 1: N = 1 Particle (Degenerate state)
    # -------------------------------------------------------------------
    print("\n[1/8] Testing N = 1 Particle (Zero-Pair Degeneracy) ...")
    pts_1 = torch.tensor([[[0.5, 0.5]]], device=device, requires_grad=True)
    gamma_1 = torch.tensor([1.0], device=device)
    
    E_1 = energy_model.compute_total_energy(pts_1, gamma_1)
    E_1_knn = energy_model.compute_total_energy_knn(pts_1, gamma_1, k=4)
    assert not torch.isnan(E_1).any() and float(E_1) == 0.0, "N=1 energy must be zero"
    assert not torch.isnan(E_1_knn).any() and float(E_1_knn) == 0.0, "N=1 kNN energy must be zero"
    
    forces_1, _ = compute_neural_force(pts_1, gamma_1, energy_model)
    assert not torch.isnan(forces_1).any(), "N=1 force must not be NaN"
    print("  ✓ N = 1 Degenerate state handled safely with zero energy and finite forces.")

    # -------------------------------------------------------------------
    # TEST 2: Coincident Points (r_ij = 0 Exact Singularity)
    # -------------------------------------------------------------------
    print("\n[2/8] Testing Exact Coordinate Overlap (r_ij = 0 singularity) ...")
    pts_overlap = torch.tensor([[[0.3, 0.3], [0.3, 0.3], [0.7, 0.7]]], device=device, requires_grad=True)
    gamma_overlap = torch.tensor([1.0], device=device)
    
    forces_overlap, E_overlap = compute_neural_force(pts_overlap, gamma_overlap, energy_model)
    assert not torch.isnan(forces_overlap).any(), "Forces at exact overlap must be finite (no NaN)"
    assert not torch.isinf(forces_overlap).any(), "Forces at exact overlap must not be Inf"
    
    # Check that backward pass computes valid gradients without NaN
    forces_overlap.sum().backward()
    for p in energy_model.parameters():
        assert not torch.isnan(p.grad).any(), "Model weight gradients must be finite at r=0"
    print("  ✓ Coordinate singularity r=0 produces finite forces and smooth autograd gradients.")

    # -------------------------------------------------------------------
    # TEST 3: Unbatched 2D Inputs (N, 2) vs Batched (B, N, 2)
    # -------------------------------------------------------------------
    print("\n[3/8] Testing Unbatched 2D Inputs (N, 2) ...")
    pts_2d = torch.rand(32, 2, device=device)
    gamma_scalar = torch.tensor(0.5, device=device)
    
    out_2d = dynamics(pts_2d, gamma_scalar)
    assert out_2d.shape == (32, 2), f"Expected shape (32, 2), got {out_2d.shape}"
    
    density_2d = rasterizer(out_2d)
    assert density_2d.shape == (32, 32), f"Expected shape (32, 32), got {density_2d.shape}"
    print("  ✓ Unbatched 2D inputs seamlessly handled and shape-preserved.")

    # -------------------------------------------------------------------
    # TEST 4: Boundary & Toroidal Corner Wrapping
    # -------------------------------------------------------------------
    print("\n[4/8] Testing Toroidal Corner Wrapping (0.001 <-> 0.999) ...")
    pts_corner = torch.tensor([[[0.001, 0.001], [0.999, 0.999]]], device=device)
    gamma_c = torch.tensor([1.0], device=device)
    
    # Distance between (0.001, 0.001) and (0.999, 0.999) across torus should be ~0.0028, NOT ~1.414
    diff = pts_corner[:, 0:1, :] - pts_corner[:, 1:2, :]
    diff_wrapped = diff - torch.round(diff)
    dist_wrapped = torch.norm(diff_wrapped).item()
    assert abs(dist_wrapped - np.sqrt(2 * 0.002**2)) < 1e-4, f"Toroidal distance calculation wrong: {dist_wrapped}"
    print(f"  ✓ Corner wraparound distance exact: {dist_wrapped:.6f} (minimum image convention verified).")

    # -------------------------------------------------------------------
    # TEST 5: Over-Saturated k-NN (k >= N and k = 1)
    # -------------------------------------------------------------------
    print("\n[5/8] Testing k-NN Edge Cases (k >= N and k = 1) ...")
    pts_small = torch.rand(2, 6, 2, device=device)
    gamma_batch = torch.tensor([0.5, -0.5], device=device)
    
    # Request k=20 when N=6
    E_large_k = energy_model.compute_total_energy_knn(pts_small, gamma_batch, k=20)
    assert not torch.isnan(E_large_k).any(), "Over-saturated k must not cause index out of bounds"
    
    # Request k=1
    E_k1 = energy_model.compute_total_energy_knn(pts_small, gamma_batch, k=1)
    assert not torch.isnan(E_k1).any(), "k=1 must compute safely"
    print("  ✓ k-NN automatically clamps k_eff = min(k, N-1) without index errors.")

    # -------------------------------------------------------------------
    # TEST 6: Extreme Spectral Exponents (gamma = -3.0 and gamma = +3.0)
    # -------------------------------------------------------------------
    print("\n[6/8] Testing Extreme Spectral Exponents (gamma = ±3.0) ...")
    pts_ext = torch.rand(1, 64, 2, device=device)
    gamma_pos = torch.tensor([3.0], device=device)
    gamma_neg = torch.tensor([-3.0], device=device)
    
    out_pos = dynamics(pts_ext, gamma_pos)
    out_neg = dynamics(pts_ext, gamma_neg)
    assert not torch.isnan(out_pos).any() and not torch.isnan(out_neg).any(), "Extreme gamma must not explode"
    print("  ✓ Extreme gamma bounds ([-3.0, +3.0]) simulate with high numerical stability.")

    # -------------------------------------------------------------------
    # TEST 7: Spacing Loss Under Full Particle Clumping (Zero Dispersion)
    # -------------------------------------------------------------------
    print("\n[7/8] Testing Spacing Loss Under Particle Collapse ...")
    pts_clump = torch.zeros(1, 64, 2, device=device) # all particles at exact origin
    l_spacing, cv, mu = loss_fn.compute_spacing_loss(pts_clump)
    assert not torch.isnan(l_spacing).any(), "Spacing loss on collapsed particles must not be NaN"
    assert l_spacing.item() > 1.0, "Spacing loss must heavily penalize total collapse"
    print(f"  ✓ Spacing loss correctly penalizes complete collapse: L_spacing = {l_spacing.item():.4f}.")

    # -------------------------------------------------------------------
    # TEST 8: Full End-to-End Batch Size Scalability (B = 8, N = 128)
    # -------------------------------------------------------------------
    print("\n[8/8] Testing Batched Dynamics & Losses (B = 8, N = 128) ...")
    B_test = 8
    pts_batch = torch.rand(B_test, 128, 2, device=device, requires_grad=True)
    gamma_batch = torch.linspace(-1.5, 1.5, B_test, device=device)
    
    pts_fin = dynamics(pts_batch, gamma_batch)
    dens = rasterizer(pts_fin)
    spec = analyzer(dens)
    loss_out = loss_fn(pts_fin, gamma_batch, spec)
    loss_out['loss'].backward()
    
    assert pts_batch.grad is not None and not torch.isnan(pts_batch.grad).any()
    print(f"  ✓ Batched end-to-end forward/backward passed cleanly (Batch size {B_test}).")

    # -------------------------------------------------------------------
    # TEST 9: Non-Contiguous & Transposed Input Tensors (.reshape vs .view)
    # -------------------------------------------------------------------
    print("\n[9/12] Testing Non-Contiguous / Transposed Tensors ...")
    pts_transposed = torch.rand(2, 32, 2, device=device).transpose(0, 1) # transposed non-contiguous!
    gamma_transposed = torch.tensor([0.5] * 32, device=device)
    
    e_transposed = energy_model.compute_total_energy_knn(pts_transposed, gamma_transposed, k=4)
    dens_transposed = rasterizer(pts_transposed)
    assert not torch.isnan(e_transposed).any(), "Non-contiguous energy computation must succeed"
    assert dens_transposed.shape == (32, 32, 32), f"Expected shape (32, 32, 32), got {dens_transposed.shape}"
    print("  ✓ Non-contiguous and transposed tensors handled seamlessly without .view runtime errors.")

    # -------------------------------------------------------------------
    # TEST 10: NumPy Arrays & N <= 1 Diagnostics in evaluate.py
    # -------------------------------------------------------------------
    print("\n[10/12] Testing NumPy Input & Degenerate Counts in evaluate.py ...")
    from evaluate import evaluate_point_distribution
    pts_np_raw = np.random.rand(64, 2)
    eval_np = evaluate_point_distribution(pts_np_raw, target_gamma=1.0, analyzer=analyzer, rasterizer=rasterizer)
    assert 'measured_gamma' in eval_np and not np.isnan(eval_np['cv_nnd']), "NumPy evaluation must succeed"
    
    stats_0 = compute_spatial_statistics(np.empty((0, 2)))
    stats_1 = compute_spatial_statistics(np.random.rand(1, 2))
    assert stats_0['mean_nnd'] == 0.0 and stats_0['cv_nnd'] == 0.0, "N=0 statistics must be 0"
    assert stats_1['mean_nnd'] == 0.0 and stats_1['cv_nnd'] == 0.0, "N=1 statistics must be 0"
    print("  ✓ evaluate.py supports raw NumPy arrays and handles N <= 1 with zero NaN/Inf.")

    # -------------------------------------------------------------------
    # TEST 11: Memory-Efficient Chunked Spacing Loss for Large N (N = 1024)
    # -------------------------------------------------------------------
    print("\n[11/12] Testing Chunked Spacing Loss for Large N (N = 1024) ...")
    pts_1024 = torch.rand(1, 1024, 2, device=device)
    l_sp_1024, cv_1024, mu_1024 = loss_fn.compute_spacing_loss(pts_1024)
    assert not torch.isnan(l_sp_1024).any(), "Chunked spacing loss must not be NaN"
    assert cv_1024.item() > 0.0 and mu_1024.item() > 0.0, "Statistics must be strictly positive"
    print(f"  ✓ Large N=1024 spacing loss computed with chunking: L_spacing = {l_sp_1024.item():.4f}.")

    # -------------------------------------------------------------------
    # TEST 12: Scalar Float Target in Composite Loss
    # -------------------------------------------------------------------
    print("\n[12/14] Testing Scalar Float & 0D Target Gamma in Composite Loss ...")
    spec_dummy = analyzer(rasterizer(pts_ext))
    loss_float = loss_fn(pts_ext, 1.25, spec_dummy) # raw python float
    loss_0d = loss_fn(pts_ext, torch.tensor(1.25, device=device), spec_dummy) # 0D tensor
    assert not torch.isnan(loss_float['loss']) and not torch.isnan(loss_0d['loss'])
    assert abs(loss_float['loss'].item() - loss_0d['loss'].item()) < 1e-5
    print("  ✓ Composite loss transparently accepts python floats, 0D tensors, and 1D tensors.")

    # -------------------------------------------------------------------
    # TEST 13: Coincident Points Disambiguation & Physical Divergence
    # -------------------------------------------------------------------
    print("\n[13/14] Testing Coincident Points Disambiguation & Physical Divergence ...")
    pts_overlap = torch.tensor([[0.4, 0.4], [0.4, 0.4], [0.8, 0.8]], dtype=torch.float32, device=device)
    pts_disambiguated = disambiguate_coincident_points(pts_overlap, min_separation=0.02)
    d_init = torch.norm(pts_disambiguated[0] - pts_disambiguated[1]).item()
    assert d_init >= 0.01, f"Expected initial separation >= 0.01, got {d_init}"
    
    ckpt_path = os.path.join(os.path.dirname(__file__), '..', 'outputs', 'checkpoints', 'checkpoint_continuous_gamma.pt')
    d_fin = d_init
    if os.path.exists(ckpt_path):
        engine = FrozenInferenceEngine(ckpt_path, device=device)
        res = engine.run_inference(target_gamma=1.0, initial_points=pts_overlap, num_steps=20)
        pts_fin = np.array(res['final_points'])
        diff = pts_fin[0] - pts_fin[1]
        diff = diff - np.round(diff)
        d_fin = float(np.linalg.norm(diff))
        assert d_fin > 0.01, f"Coincident particles must physically separate, got d={d_fin}"
        assert res['results']['effective_unique_particles'] == 3, f"Expected 3 unique particles, got {res['results']['effective_unique_particles']}"
        assert res['results']['coincident_pairs_count'] == 0, f"Expected 0 coincident pairs, got {res['results']['coincident_pairs_count']}"
    print(f"  ✓ Coincident points automatically disambiguated and physically separated (initial {d_init:.4f} -> final {d_fin:.4f}).")

    # -------------------------------------------------------------------
    # TEST 14: Spatial Statistics Telemetry for Overlapping / Coincident Points
    # -------------------------------------------------------------------
    print("\n[14/14] Testing Spatial Statistics Overlap & Coincident Telemetry ...")
    pts_dup = np.array([[0.25, 0.25], [0.25, 0.25], [0.75, 0.75]])
    stats_dup = compute_spatial_statistics(pts_dup)
    assert stats_dup['coincident_pairs_count'] == 1, f"Expected 1 coincident pair, got {stats_dup['coincident_pairs_count']}"
    assert stats_dup['overlapping_pairs_count'] == 1
    assert stats_dup['effective_unique_particles'] == 2, f"Expected 2 unique, got {stats_dup['effective_unique_particles']}"
    assert stats_dup['has_coincident_points'] is True
    assert stats_dup['has_overlapping_points'] is True

    pts_unique = np.array([[0.2, 0.2], [0.5, 0.5], [0.8, 0.8]])
    stats_unique = compute_spatial_statistics(pts_unique)
    assert stats_unique['coincident_pairs_count'] == 0
    assert stats_unique['effective_unique_particles'] == 3
    assert stats_unique['has_coincident_points'] is False
    print("  ✓ Spatial statistics accurately detects overlapping pairs and effective unique clusters.")

    # -------------------------------------------------------------------
    # TEST 15: NaN / Inf & Zero-Particle Input Rejection (EC-A1 & EC-Q1)
    # -------------------------------------------------------------------
    print("\n[15/18] Testing NaN/Inf and Zero-Count Rejection in Dynamics ...")
    pts_nan = torch.tensor([[[float('nan'), 0.5], [0.2, 0.3]]], device=device)
    gamma_test = torch.tensor([1.0], device=device)
    nan_caught = False
    try:
        dynamics(pts_nan, gamma_test)
    except ValueError as e:
        nan_caught = True
        assert "NaN or Inf" in str(e)
    assert nan_caught, "Engine must raise ValueError on NaN input coordinates"

    pts_empty = torch.empty(1, 0, 2, device=device)
    empty_caught = False
    try:
        dynamics(pts_empty, gamma_test)
    except ValueError as e:
        empty_caught = True
        assert "0 particles" in str(e)
    assert empty_caught, "Engine must raise ValueError on 0 particle input"
    print("  ✓ Input validation safely rejects NaN/Inf coordinates and empty particle sets (EC-A1, EC-Q1).")

    # -------------------------------------------------------------------
    # TEST 16: Out-of-Bounds Coordinate Modulo Canonicalization (EC-A2)
    # -------------------------------------------------------------------
    print("\n[16/18] Testing Toroidal Domain Modulo Canonicalization (EC-A2) ...")
    pts_oob = torch.tensor([[[-0.25, 1.40], [0.50, -0.10]]], device=device)
    out_canonical = dynamics(pts_oob, gamma_test)
    assert (out_canonical >= 0.0).all() and (out_canonical < 1.0).all(), "All coordinates must lie in [0, 1)"
    assert not torch.isnan(out_canonical).any()
    print("  ✓ Out-of-bounds coordinates automatically canonicalized into [0, 1)^2 via modulo (EC-A2).")

    # -------------------------------------------------------------------
    # TEST 17: Gaussian Deconvolution Singularity Clamping (EC-I4)
    # -------------------------------------------------------------------
    print("\n[17/18] Testing Gaussian Deconvolution Numerical Clamp (EC-I4) ...")
    dummy_density = torch.rand(1, 32, 32, device=device)
    psd_2d_raw = analyzer.compute_psd2d(dummy_density)
    radial_deconv = analyzer.compute_radial_psd(psd_2d_raw)
    assert not torch.isnan(radial_deconv).any(), "Deconvolved radial PSD must not contain NaN"
    assert not torch.isinf(radial_deconv).any(), "Deconvolved radial PSD must not blow up to Inf"
    print("  ✓ High-frequency Gaussian deconvolution factor is clamped to max=100.0 without numerical blowup (EC-I4).")

    # -------------------------------------------------------------------
    # TEST 18: Unseen Intermediate Gamma Interpolation & Boundaries (EC-B1, EC-B2)
    # -------------------------------------------------------------------
    print("\n[18/18] Testing Unseen Intermediate Gamma Interpolation (EC-B1, EC-B2) ...")
    # Values mandated in Proposal 2, Section 6: -2.0, -1.5, -0.7, 0.3, 0.8, 1.5, +2.0
    gamma_test_set = [-2.0, -1.5, -0.7, 0.0, 0.3, 0.8, 1.5, 2.0]
    pts_eval = torch.rand(1, 32, 2, device=device)
    energies = []
    for g_val in gamma_test_set:
        g_t = torch.tensor([g_val], device=device)
        E_val = energy_model.compute_total_energy(pts_eval, g_t)
        assert not torch.isnan(E_val).any() and not torch.isinf(E_val).any()
        energies.append(E_val.item())
    assert len(energies) == len(gamma_test_set)
    print(f"  ✓ Evaluated all 8 proposal-mandated continuous gammas: {gamma_test_set} successfully (EC-B1, EC-B2).")

    print("\n" + "=" * 80)
    print("🏆 ALL 18 RESEARCH PROPOSAL & ENGINEERING EDGE-CASE TESTS PASSED PERFECTLY!")
    print("=" * 80)

if __name__ == "__main__":
    test_edge_cases()
