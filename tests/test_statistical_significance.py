"""
test_statistical_significance.py — Phase 13: Multi-Seed Statistical Significance Benchmark.
Implements Section 9 & Section 10 of the research proposal:
- Evaluates N = 10 independent random seeds per target condition
- Covers continuous spectral targets: gamma in {-1.5, -1.0, -0.5, 0.0, +0.5, +1.0, +1.5}
- Computes Mean, Standard Deviation, Standard Error, and 95% Confidence Intervals
- Measures:
  1. Slope Accuracy: |gamma_hat - gamma*|
  2. Spatial Regularity: CV_NND and mu_NND
  3. Anti-Collapse Integrity: max g(r <= 0.02)
  4. Spectral Log-MSE
- Produces publication figure: outputs/figures/statistical_significance_multiseed.png
"""

import os
import sys
import json
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from data import generate_uniform_random
from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from evaluate import compute_spatial_statistics


def run_statistical_significance_benchmark(
    ckpt_path: str = "outputs/checkpoints/checkpoint_continuous_gamma.pt",
    target_gammas = [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5],
    num_seeds: int = 10,
    n_particles: int = 256,
    num_steps: int = 20,
    dt: float = 0.02,
    save_fig_path: str = "outputs/figures/statistical_significance_multiseed.png",
    save_json_path: str = "outputs/statistical_significance_results.json"
):
    print("\n" + "="*85, flush=True)
    print(f"PHASE 13: MULTI-SEED STATISTICAL SIGNIFICANCE BENCHMARK (M = {num_seeds} SEEDS/TARGET)", flush=True)
    print(f"Targets: {target_gammas} | N={n_particles} | Steps={num_steps}", flush=True)
    print("="*85, flush=True)
    
    device = "cpu"
    
    # 1. Load trained continuous energy model
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
        print(f"[!] Warning: Trained checkpoint {ckpt_path} not found. Running with initialized model.")
        
    energy_model.eval()
    
    dynamics = DifferentiableSimulationEngine(
        energy_model=energy_model,
        num_steps=num_steps,
        dt=dt,
        L=1.0,
        use_checkpointing=False
    ).to(device)
    
    rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02).to(device)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24).to(device)
    
    results = {}
    
    for gamma_target in target_gammas:
        print(f"\n[*] Evaluating Target γ* = {gamma_target:+.2f} across {num_seeds} seeds ...", flush=True)
        
        measured_gammas = []
        errors = []
        cv_nnds = []
        mu_nnds = []
        collapse_scores = []
        
        # Batch evaluation across all seeds for high performance
        gamma_tensor = torch.full((num_seeds,), gamma_target, dtype=torch.float32, device=device)
        
        # Deterministically generate random initial states across seeds
        batch_pts_init = []
        for s in range(num_seeds):
            torch.manual_seed(1000 + s * 37)
            pts = generate_uniform_random(batch_size=1, n_particles=n_particles, device=device)
            batch_pts_init.append(pts[0])
        batch_pts_init = torch.stack(batch_pts_init, dim=0) # (num_seeds, N, 2)
        
        with torch.no_grad():
            t0 = time.perf_counter()
            pts_final = dynamics(batch_pts_init, gamma_tensor)
            density = rasterizer(pts_final)
            spec_out = analyzer(density)
            t_eval = time.perf_counter() - t0
            
            gammas = spec_out['gamma'].cpu().numpy()
            
        for s in range(num_seeds):
            g_meas = float(gammas[s])
            err = abs(g_meas - gamma_target)
            spatial_stats = compute_spatial_statistics(pts_final[s])
            
            measured_gammas.append(g_meas)
            errors.append(err)
            cv_nnds.append(float(spatial_stats['cv_nnd']))
            mu_nnds.append(float(spatial_stats['mean_nnd']))
            collapse_scores.append(float(spatial_stats['collapse_score']))
            
        # Statistical Aggregation
        meas_mean = float(np.mean(measured_gammas))
        meas_std = float(np.std(measured_gammas))
        meas_se = float(meas_std / np.sqrt(num_seeds))
        ci_95 = float(1.96 * meas_se)
        
        err_mean = float(np.mean(errors))
        err_std = float(np.std(errors))
        
        cv_mean = float(np.mean(cv_nnds))
        cv_std = float(np.std(cv_nnds))
        
        results[str(gamma_target)] = {
            'target_gamma': gamma_target,
            'measured_mean': meas_mean,
            'measured_std': meas_std,
            'measured_se': meas_se,
            'ci_95': ci_95,
            'error_mean': err_mean,
            'error_std': err_std,
            'cv_mean': cv_mean,
            'cv_std': cv_std,
            'collapse_max': float(np.max(collapse_scores)),
            'raw_measured': measured_gammas,
            'raw_errors': errors,
            'raw_cv': cv_nnds
        }
        
        print(f"    -> γ̂ = {meas_mean:+.3f} ± {meas_std:.3f} (95% CI: [{meas_mean - ci_95:+.3f}, {meas_mean + ci_95:+.3f}]) | "
              f"MAE = {err_mean:.3f} ± {err_std:.3f} | CV_NND = {cv_mean:.3f}", flush=True)

    # -------------------------------------------------------------
    # 4-Panel Publication Statistical Figure
    # -------------------------------------------------------------
    fig, axes = plt.subplots(1, 4, figsize=(22, 5.2), dpi=180)
    
    targets = [results[k]['target_gamma'] for k in results]
    means = [results[k]['measured_mean'] for k in results]
    stds = [results[k]['measured_std'] for k in results]
    ci95s = [results[k]['ci_95'] for k in results]
    err_means = [results[k]['error_mean'] for k in results]
    err_stds = [results[k]['error_std'] for k in results]
    cv_means = [results[k]['cv_mean'] for k in results]
    cv_stds = [results[k]['cv_std'] for k in results]
    
    # Panel 1: Target vs Measured Slope with 95% CI
    axes[0].errorbar(targets, means, yerr=ci95s, fmt='o', color='#1f77b4', ecolor='#1f77b4', 
                     elinewidth=2, capsize=5, capthick=1.5, ms=7, label=r'Measured $\hat{\gamma}$ (95% CI)')
    axes[0].plot([-2.0, 2.0], [-2.0, 2.0], 'k--', lw=1.8, label=r'Ideal Identity ($\hat{\gamma} = \gamma^*$)')
    axes[0].fill_between(targets, [m - s for m, s in zip(means, stds)], [m + s for m, s in zip(means, stds)], 
                         color='#1f77b4', alpha=0.15, label=r'$\pm 1\sigma$ Band')
    axes[0].set_xlim(-1.8, 1.8)
    axes[0].set_ylim(-1.8, 1.8)
    axes[0].set_xlabel(r'Target Spectral Slope $\gamma^*$', fontsize=11, fontweight='bold')
    axes[0].set_ylabel(r'Measured Spectral Slope $\hat{\gamma}$', fontsize=11, fontweight='bold')
    axes[0].set_title('Spectral Control Fidelity\n(10 Seeds per Target)', fontsize=12, fontweight='bold')
    axes[0].grid(True, linestyle=':', alpha=0.5)
    axes[0].legend(fontsize=9, loc='upper left')
    
    # Panel 2: Absolute Error Across Targets
    bar_pos = np.arange(len(targets))
    axes[1].bar(bar_pos, err_means, yerr=err_stds, capsize=4, color='#2ca02c', alpha=0.85, edgecolor='black', lw=1.0)
    axes[1].set_xticks(bar_pos)
    axes[1].set_xticklabels([f"{t:+.1f}" for t in targets])
    axes[1].set_xlabel('Target Spectral Slope $\gamma^*$', fontsize=11, fontweight='bold')
    axes[1].set_ylabel('Absolute Error $|\hat{\gamma} - \gamma^*|$', fontsize=11, fontweight='bold')
    axes[1].set_title('Mean Absolute Error & Std\n(Robustness across Regime)', fontsize=12, fontweight='bold')
    axes[1].grid(True, linestyle=':', alpha=0.4, axis='y')
    axes[1].set_ylim(0, max(err_means) * 1.6 + 0.05)
    for i, (m, s) in enumerate(zip(err_means, err_stds)):
        axes[1].text(i, m + s + 0.015, f"{m:.3f}", ha='center', fontsize=9, fontweight='bold')

    # Panel 3: Spatial Regularity CV_NND vs Gamma
    axes[2].plot(targets, cv_means, 's-', color='#d62728', lw=2, ms=6, label='Spatial CV_NND ($\mu \pm 1\sigma$)')
    axes[2].fill_between(targets, [c - s for c, s in zip(cv_means, cv_stds)], [c + s for c, s in zip(cv_means, cv_stds)], 
                         color='#d62728', alpha=0.15)
    axes[2].axhline(0.52, color='gray', linestyle=':', lw=1.5, label='Uniform Random ($\gamma=0$)')
    axes[2].set_xlabel('Target Spectral Slope $\gamma^*$', fontsize=11, fontweight='bold')
    axes[2].set_ylabel('Nearest Neighbor CV (Lower = Regular)', fontsize=11, fontweight='bold')
    axes[2].set_title('Spatial Regularity vs Spectrum\n(Clustered $\\to$ Hyperuniform)', fontsize=12, fontweight='bold')
    axes[2].grid(True, linestyle=':', alpha=0.5)
    axes[2].legend(fontsize=9, loc='upper right')
    
    # Panel 4: Distribution Boxplots for Key Regimes (Red, White, Blue)
    regimes = [-1.5, 0.0, 1.5]
    box_data = [results[str(r)]['raw_measured'] for r in regimes]
    bp = axes[3].boxplot(box_data, positions=[1, 2, 3], widths=0.45, patch_artist=True,
                         boxprops=dict(facecolor='#aec7e8', color='#1f77b4', lw=1.5),
                         medianprops=dict(color='#d62728', lw=2),
                         whiskerprops=dict(color='#1f77b4', lw=1.5),
                         capprops=dict(color='#1f77b4', lw=1.5))
    axes[3].plot([0.5, 3.5], [-1.5, -1.5], 'r--', alpha=0.4)
    axes[3].plot([0.5, 3.5], [0.0, 0.0], 'r--', alpha=0.4)
    axes[3].plot([0.5, 3.5], [1.5, 1.5], 'r--', alpha=0.4)
    axes[3].set_xticks([1, 2, 3])
    axes[3].set_xticklabels(['Red\n($\gamma^*=-1.5$)', 'White\n($\gamma^*=0.0$)', 'Blue\n($\gamma^*=+1.5$)'])
    axes[3].set_ylabel('Measured Slope $\hat{\gamma}$', fontsize=11, fontweight='bold')
    axes[3].set_title('Seed Variance in Key Regimes\n(Interquartile Dispersion)', fontsize=12, fontweight='bold')
    axes[3].grid(True, linestyle=':', alpha=0.5)
    
    plt.tight_layout()
    os.makedirs(os.path.dirname(save_fig_path), exist_ok=True)
    plt.savefig(save_fig_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"\n[*] Saved statistical significance figure to {save_fig_path}", flush=True)
    
    # Save json summary
    os.makedirs(os.path.dirname(save_json_path), exist_ok=True)
    with open(save_json_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"[*] Saved numerical statistics to {save_json_path}", flush=True)
    
    # -------------------------------------------------------------
    # Statistical Scorecard Printout
    # -------------------------------------------------------------
    print("\n" + "="*95)
    print("PHASE 13: MULTI-SEED STATISTICAL SIGNIFICANCE SUMMARY TABLE")
    print("="*95)
    print(f"{'Target γ*':>10} | {'Measured γ̂ (μ ± σ)':>22} | {'95% Conf. Interval':>20} | {'MAE (μ ± σ)':>15} | {'CV_NND (μ)':>12}")
    print("-" * 95)
    for k in results:
        r = results[k]
        print(f"{r['target_gamma']:+10.2f} | {r['measured_mean']:+8.3f} ± {r['measured_std']:<6.3f} | "
              f"[{r['measured_mean'] - r['ci_95']:+6.3f}, {r['measured_mean'] + r['ci_95']:+6.3f}] | "
              f"{r['error_mean']:6.3f} ± {r['error_std']:<5.3f} | {r['cv_mean']:12.3f}")
    print("="*95)
    
    return results


if __name__ == "__main__":
    run_statistical_significance_benchmark()
