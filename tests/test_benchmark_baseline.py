"""
test_benchmark_baseline.py — Phase 10: Classical Baseline vs. NeuroSpectrum Benchmark.
Implements Section 6.3 of the research proposal:
- Compares Classical per-target optimization (Adam on raw coordinates) vs. NeuroSpectrum (instant neural simulation)
- Measures: Wall-clock runtime, Speedup, Spectral MSE, Slope Error, CV_NND
- Generates publication-quality comparative figure: outputs/figures/baseline_comparison.png
"""

import os
import sys
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np
import matplotlib.pyplot as plt

from baseline import ClassicalPerTargetOptimizer
from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from losses import CompositeSpectralLoss
from evaluate import compute_spatial_statistics


def run_baseline_benchmark(ckpt_path: str = "outputs/checkpoints/checkpoint_continuous_gamma.pt",
                           test_gammas = [-1.50, 0.00, +1.00, +1.50],
                           n_particles: int = 256,
                           classical_iters: int = 100,
                           save_path: str = "outputs/figures/baseline_comparison.png"):
    
    print("\n" + "="*80)
    print("PHASE 10 (SECTION 6.3): CLASSICAL DIRECT OPTIMIZER VS. NEUROSPECTRUM BENCHMARK")
    print(f"Targets: {test_gammas} | Particles N={n_particles} | Classical Iters={classical_iters}")
    print("="*80)
    
    device = "cpu"
    torch.manual_seed(42)
    np.random.seed(42)
    
    # 1. Classical Optimizer Setup
    classical_opt = ClassicalPerTargetOptimizer(
        grid_size=64, 
        sigma=0.02, 
        lambda_spec=1.0, 
        lambda_gamma=2.0, 
        lambda_spacing=2.0, 
        n_particles=n_particles
    )
    
    # 2. NeuroSpectrum Setup
    neural_model = NeuralPairwiseEnergy(
        hidden_dim=64, 
        num_layers=3, 
        use_divergence_prior=True, 
        eps_divergence=0.005, 
        r_repulsion=0.045
    ).to(device)
    
    if os.path.exists(ckpt_path):
        ckpt = torch.load(ckpt_path, map_location=device)
        neural_model.load_state_dict(ckpt['energy_model_state_dict'])
        print(f"[*] Loaded trained neural model from {ckpt_path}")
    else:
        print(f"[!] Checkpoint not found at {ckpt_path}, using initialized weights")
        
    neural_model.eval()
    neural_dynamics = DifferentiableSimulationEngine(neural_model, num_steps=20, dt=0.02, L=1.0)
    rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24)
    loss_fn = CompositeSpectralLoss(lambda_spec=1.0, lambda_gamma=2.0, lambda_spacing=2.0, n_particles=n_particles)
    
    # Common random initial points
    pts_init = torch.rand(1, n_particles, 2)
    
    benchmark_results = []
    
    fig, axes = plt.subplots(4, len(test_gammas), figsize=(4.0 * len(test_gammas), 14), dpi=150)
    
    for col_idx, gamma_val in enumerate(test_gammas):
        gamma_tensor = torch.tensor([gamma_val], dtype=torch.float32)
        
        # --- METHOD A: Classical Direct-Optimizer ---
        print(f"\n[*] Running Classical Optimizer on γ* = {gamma_val:+.2f} ...", flush=True)
        res_class = classical_opt.optimize(pts_init, target_gamma_val=gamma_val, num_iters=classical_iters, lr=0.01)
        stats_class = compute_spatial_statistics(res_class['points_final'])
        
        # --- METHOD B: NeuroSpectrum (Instant Neural Simulation) ---
        print(f"[*] Running NeuroSpectrum Neural Simulation on γ* = {gamma_val:+.2f} ...", flush=True)
        t0 = time.perf_counter()
        with torch.no_grad():
            pts_neural = neural_dynamics(pts_init, gamma_tensor)
            dens_neural = rasterizer(pts_neural)
            spec_neural = analyzer(dens_neural)
            loss_neural_dict = loss_fn(pts_neural, gamma_tensor, spec_neural)
        t_neural = time.perf_counter() - t0
        
        pts_neural_np = pts_neural[0].numpy()
        gamma_neural = spec_neural['gamma'][0].item()
        stats_neural = compute_spatial_statistics(pts_neural_np)
        
        speedup = res_class['wall_clock_time'] / max(t_neural, 1e-6)
        
        benchmark_results.append({
            'gamma': gamma_val,
            'time_classical': res_class['wall_clock_time'],
            'time_neural': t_neural,
            'speedup': speedup,
            'gamma_classical': res_class['measured_gamma'],
            'gamma_neural': gamma_neural,
            'err_classical': abs(res_class['measured_gamma'] - gamma_val),
            'err_neural': abs(gamma_neural - gamma_val),
            'cv_classical': stats_class['cv_nnd'],
            'cv_neural': stats_neural['cv_nnd']
        })
        
        # --- Plotting ---
        # Row 1: Classical Point Cloud
        ax1 = axes[0, col_idx]
        ax1.scatter(res_class['points_final'][:, 0], res_class['points_final'][:, 1], s=12, color='#d62728', alpha=0.8)
        ax1.set_xlim(0, 1); ax1.set_ylim(0, 1); ax1.set_aspect('equal')
        ax1.set_title(f"Target γ* = {gamma_val:+.2f}\nClassical (Adam 100 iters)\nTime: {res_class['wall_clock_time']:.2f}s", fontsize=10)
        ax1.grid(True, linestyle=':', alpha=0.3)
        if col_idx == 0: ax1.set_ylabel("Classical Points", fontsize=11, fontweight='bold')
        
        # Row 2: NeuroSpectrum Point Cloud
        ax2 = axes[1, col_idx]
        ax2.scatter(pts_neural_np[:, 0], pts_neural_np[:, 1], s=12, color='#1f77b4', alpha=0.8)
        ax2.set_xlim(0, 1); ax2.set_ylim(0, 1); ax2.set_aspect('equal')
        ax2.set_title(f"NeuroSpectrum (Ours)\nTime: {t_neural*1000:.1f}ms ({speedup:.1f}x Faster)", fontsize=10, color='navy', fontweight='bold')
        ax2.grid(True, linestyle=':', alpha=0.3)
        if col_idx == 0: ax2.set_ylabel("Neural Points", fontsize=11, fontweight='bold')
        
        # Row 3: Radial PSD Comparison
        ax3 = axes[2, col_idx]
        freqs = spec_neural['freqs'].numpy()
        mask = (freqs >= 2) & (freqs <= 24)
        f_b = freqs[mask]
        
        p_class = np.clip(res_class['radial_psd'][mask], 1e-6, None)
        p_neural = np.clip(spec_neural['radial_psd'][0].numpy()[mask], 1e-6, None)
        
        ax3.loglog(f_b, p_class, 's--', color='#d62728', lw=1.5, ms=4, label=f"Classical (γ̂={res_class['measured_gamma']:+.2f})")
        ax3.loglog(f_b, p_neural, 'o-', color='#1f77b4', lw=1.8, ms=4, label=f"Neural (γ̂={gamma_neural:+.2f})")
        ax3.set_title("Radial Power Spectrum", fontsize=10)
        ax3.grid(True, which='both', linestyle=':', alpha=0.4)
        ax3.legend(fontsize=8, loc='best')
        if col_idx == 0: ax3.set_ylabel("PSD P(f)", fontsize=11, fontweight='bold')
        
        # Row 4: Speedup Bar
        ax4 = axes[3, col_idx]
        bars = ax4.bar(['Classical\n(Per-target)', 'NeuroSpectrum\n(1-Pass Forward)'], 
                       [res_class['wall_clock_time'], t_neural], 
                       color=['#d62728', '#2ca02c'], width=0.55)
        ax4.set_ylabel("Runtime (Seconds)", fontsize=9)
        ax4.set_title(f"Speedup: {speedup:.1f}x", fontsize=10, fontweight='bold')
        ax4.grid(True, linestyle=':', alpha=0.4, axis='y')
        for bar in bars:
            yval = bar.get_height()
            ax4.text(bar.get_x() + bar.get_width()/2.0, yval, f'{yval:.3f}s', ha='center', va='bottom', fontsize=8, fontweight='bold')
            
    plt.tight_layout()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"[*] Saved benchmark comparative plot to {save_path}")
    
    # Print formatted comparative table
    print("\n" + "="*95)
    print("SECTION 6.3 BENCHMARK TABLE: CLASSICAL PER-TARGET OPTIMIZER VS. NEUROSPECTRUM")
    print("="*95)
    print(f"{'Target γ*':>9} | {'Classical Time':>15} | {'Neural Time':>12} | {'Speedup':>10} | {'Class |Δγ|':>11} | {'Neural |Δγ|':>11} | {'Neural CV':>10}")
    print("-" * 95)
    for r in benchmark_results:
        print(f"{r['gamma']:+9.2f} | {r['time_classical']:13.3f} s | {r['time_neural']*1000:9.2f} ms | "
              f"{r['speedup']:9.1f}x | {r['err_classical']:11.3f} | {r['err_neural']:11.3f} | {r['cv_neural']:10.4f}")
    print("="*95)
    
    return benchmark_results


if __name__ == "__main__":
    run_baseline_benchmark()
