"""
test_graphics_applications.py — Phase 14: Downstream Graphics Applications Benchmark.
Implements Section 8, Section 12, and Stage 14 of the research proposal:
1. Non-Photorealistic Stippling with Blue Noise Relaxation
2. Monte Carlo 2D Quadrature Variance Reduction
3. Generates 4-panel publication figure: outputs/figures/downstream_graphics_applications.png
"""

import os
import sys
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from energy import NeuralPairwiseEnergy
from applications import (
    generate_synthetic_stipple_target,
    sample_importance_points,
    neural_stipple_relaxation,
    evaluate_monte_carlo_integration
)


def run_downstream_graphics_benchmark(
    ckpt_path: str = "outputs/checkpoints/checkpoint_continuous_gamma.pt",
    save_fig_path: str = "outputs/figures/downstream_graphics_applications.png"
):
    print("\n" + "="*85, flush=True)
    print("PHASE 14: DOWNSTREAM COMPUTER GRAPHICS APPLICATIONS BENCHMARK", flush=True)
    print("1. Density-Weighted Adaptive Stippling | 2. Monte Carlo Variance Reduction", flush=True)
    print("="*85, flush=True)
    
    device = "cpu"
    
    # Load model
    energy_model = NeuralPairwiseEnergy(
        hidden_dim=64,
        num_layers=3,
        use_divergence_prior=True,
        eps_divergence=0.005,
        r_repulsion=0.045
    ).to(device)
    
    if os.path.exists(ckpt_path):
        ckpt = torch.load(ckpt_path, map_location=device)
        energy_model.load_state_dict(ckpt['energy_model_state_dict'])
        print(f"[*] Loaded trained model from {ckpt_path}")
    else:
        print(f"[!] Warning: Trained checkpoint {ckpt_path} not found.")
        
    energy_model.eval()
    
    # -------------------------------------------------------------
    # Experiment 1: Non-Photorealistic Blue Noise Stippling
    # -------------------------------------------------------------
    print("\n[*] Running Experiment 1: Adaptive Blue Noise Stippling (N = 1,024 points) ...", flush=True)
    target_density = generate_synthetic_stipple_target(resolution=128)
    n_stipples = 1024
    
    # A. Raw Random Importance Sampling
    raw_stipples = sample_importance_points(target_density, n_particles=n_stipples, seed=42)
    
    # B. NeuroSpectrum Blue Noise Relaxation
    t0 = time.perf_counter()
    relaxed_stipples = neural_stipple_relaxation(
        raw_stipples,
        energy_model=energy_model,
        num_steps=30,
        dt=0.015,
        gamma_val=1.0,
        k=16
    )
    t_relax = time.perf_counter() - t0
    print(f"    -> Neural stippling relaxation completed in {t_relax*1000:.1f} ms ({n_stipples} points)", flush=True)

    # -------------------------------------------------------------
    # Experiment 2: Monte Carlo Quadrature Integration
    # -------------------------------------------------------------
    print("\n[*] Running Experiment 2: Monte Carlo Rendering Quadrature ...", flush=True)
    
    # Complex 2D smooth + oscillatory integrand
    def integrand(x, y):
        return (
            np.sin(2 * np.pi * x) * np.cos(2 * np.pi * y) + 
            2.5 * np.exp(-35.0 * ((x - 0.5)**2 + (y - 0.5)**2)) + 
            0.5 * (x**2 + y**2)
        )
    
    # Compute high-precision ground truth via high-density grid quadrature (M = 2,000 x 2,000)
    grid_res = 2000
    gx = np.linspace(0, 1, grid_res, endpoint=False) + 0.5/grid_res
    gy = np.linspace(0, 1, grid_res, endpoint=False) + 0.5/grid_res
    gxx, gyy = np.meshgrid(gx, gy)
    exact_I = float(np.mean(integrand(gxx, gyy)))
    print(f"    -> Ground-Truth Analytical Integral I* = {exact_I:.6f}", flush=True)
    
    sample_counts = [16, 32, 64, 128, 256, 512, 1024]
    mc_results = evaluate_monte_carlo_integration(
        f_integrand=integrand,
        exact_integral=exact_I,
        energy_model=energy_model,
        sample_counts=sample_counts,
        num_trials=40
    )
    
    # Print Monte Carlo Comparison Table
    print("\n" + "="*85)
    print("MONTE CARLO QUADRATURE ERROR (RMSE across 40 trials):")
    print("="*85)
    print(f"{'N':>6} | {'Random (White)':>18} | {'Jittered Grid':>16} | {'NeuroSpectrum (Blue)':>22} | {'Variance Reduction':>18}")
    print("-" * 85)
    for i, N in enumerate(sample_counts):
        r_err = mc_results['rmse_random'][i]
        j_err = mc_results['rmse_jittered'][i]
        n_err = mc_results['rmse_neurospectrum'][i]
        vr = (r_err / max(n_err, 1e-6))**2
        print(f"{N:6d} | {r_err:18.5f} | {j_err:16.5f} | {n_err:22.5f} | {vr:16.1f}x")
    print("="*85)

    # -------------------------------------------------------------
    # 4-Panel Publication Figure
    # -------------------------------------------------------------
    fig, axes = plt.subplots(1, 4, figsize=(22, 5.2), dpi=180)
    
    # Panel 1: Target Density Image
    im0 = axes[0].imshow(target_density, cmap='gray_r', origin='lower', extent=[0, 1, 0, 1])
    axes[0].set_title("Target Density Map $I(x, y)$\n(Ground Truth Image)", fontsize=11, fontweight='bold')
    axes[0].set_xlabel("x", fontsize=10); axes[0].set_ylabel("y", fontsize=10)
    plt.colorbar(im0, ax=axes[0], fraction=0.046, pad=0.04)
    
    # Panel 2: Blue Noise Stippling Comparison
    axes[1].scatter(raw_stipples[:, 0], raw_stipples[:, 1], s=4, color='#7f7f7f', alpha=0.35, label='Raw Random (Clumpy)')
    axes[1].scatter(relaxed_stipples[:, 0], relaxed_stipples[:, 1], s=7, color='#1f77b4', edgecolors='none', alpha=0.9, label='NeuroSpectrum ($\gamma=+1$)')
    axes[1].set_xlim(0, 1); axes[1].set_ylim(0, 1); axes[1].set_aspect('equal')
    axes[1].set_title(f"Adaptive Blue Noise Stippling\n(N={n_stipples} Particles)", fontsize=11, fontweight='bold')
    axes[1].legend(loc='lower left', fontsize=8.5, framealpha=0.9)
    axes[1].set_xlabel("x", fontsize=10); axes[1].set_ylabel("y", fontsize=10)
    
    # Panel 3: Monte Carlo Convergence Rate (Log-Log)
    axes[2].loglog(sample_counts, mc_results['rmse_random'], 's--', color='#d62728', lw=2, ms=6, label='Random (White noise) $O(N^{-0.5})$')
    axes[2].loglog(sample_counts, mc_results['rmse_jittered'], '^--', color='#ff7f0e', lw=2, ms=6, label='Jittered Grid')
    axes[2].loglog(sample_counts, mc_results['rmse_neurospectrum'], 'o-', color='#1f77b4', lw=2.5, ms=7, label='NeuroSpectrum (Blue $\gamma=+1$)')
    axes[2].set_xlabel("Sample Budget $N$", fontsize=11, fontweight='bold')
    axes[2].set_ylabel("Quadrature RMSE", fontsize=11, fontweight='bold')
    axes[2].set_title("Monte Carlo Convergence Rate\n(Log-Log RMSE vs Budget)", fontsize=11, fontweight='bold')
    axes[2].grid(True, which='both', linestyle=':', alpha=0.5)
    axes[2].legend(fontsize=8.5, loc='lower left')
    
    # Panel 4: Variance Reduction Ratio at Sample Budget
    vr_factors = [(r / max(n, 1e-6))**2 for r, n in zip(mc_results['rmse_random'], mc_results['rmse_neurospectrum'])]
    bars = axes[3].bar([str(n) for n in sample_counts], vr_factors, color='#2ca02c', width=0.6, edgecolor='black', lw=0.8)
    axes[3].set_xlabel("Sample Budget $N$", fontsize=11, fontweight='bold')
    axes[3].set_ylabel("Variance Reduction Factor $(\\sigma^2_{\\mathrm{rand}} / \\sigma^2_{\\mathrm{neuro}})$", fontsize=11, fontweight='bold')
    axes[3].set_title("Empirical Variance Reduction\n(Efficiency Gain vs Random)", fontsize=11, fontweight='bold')
    axes[3].grid(True, linestyle=':', alpha=0.4, axis='y')
    for b in bars:
        h = b.get_height()
        axes[3].text(b.get_x() + b.get_width()/2.0, h + 0.1, f"{h:.1f}x", ha='center', fontsize=8.5, fontweight='bold')
        
    plt.tight_layout()
    os.makedirs(os.path.dirname(save_fig_path), exist_ok=True)
    plt.savefig(save_fig_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"\n[*] Saved downstream graphics figure to {save_fig_path}", flush=True)
    
    return {
        'stipple_points': relaxed_stipples,
        'mc_results': mc_results,
        'exact_integral': exact_I
    }


if __name__ == "__main__":
    run_downstream_graphics_benchmark()
