"""
test_interpolation.py - Phase 9 (Milestone 3): Unseen Target Gamma Interpolation.
Evaluates the continuous neural energy model on 5 unseen spectral exponents:
gamma* in {-1.50, -0.70, +0.30, +0.80, +1.50}
Generates publication-quality 5-column multi-spectral visualization.
"""

import os
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


import torch
import numpy as np
import matplotlib.pyplot as plt

from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from evaluate import compute_spatial_statistics


def run_unseen_gamma_interpolation(ckpt_path: str = "outputs/checkpoints/checkpoint_continuous_gamma.pt",
                                   test_gammas = [-1.50, -0.70, +0.30, +0.80, +1.50],
                                   n_particles: int = 256,
                                   num_steps: int = 25,
                                   dt: float = 0.02,
                                   save_path: str = "outputs/figures/unseen_gamma_interpolation.png"):
    
    print("\n" + "="*70)
    print("PHASE 9 (MILESTONE 3): UNSEEN SPECTRAL GAMMA INTERPOLATION TEST")
    print(f"Test Targets: {test_gammas}")
    print("="*70)
    
    device = "cpu"
    torch.manual_seed(123)
    np.random.seed(123)
    
    # 1. Load trained continuous model
    model = NeuralPairwiseEnergy(
        hidden_dim=64, 
        num_layers=3, 
        use_divergence_prior=True, 
        eps_divergence=0.005, 
        r_repulsion=0.045
    ).to(device)
    
    if os.path.exists(ckpt_path):
        ckpt = torch.load(ckpt_path, map_location=device)
        model.load_state_dict(ckpt['energy_model_state_dict'])
        print(f"[*] Loaded trained model from {ckpt_path}")
    else:
        print(f"[!] Checkpoint not found at {ckpt_path}, using initialized model")
        
    model.eval()
    dynamics = DifferentiableSimulationEngine(model, num_steps=num_steps, dt=dt, L=1.0)
    rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24)
    
    # Create fixed initial point set for uniform comparison
    pts_init = torch.rand(1, n_particles, 2)
    
    results = []
    fig, axes = plt.subplots(3, len(test_gammas), figsize=(3.8 * len(test_gammas), 11), dpi=150)
    
    noise_names = {
        -1.50: "Red Noise",
        -0.70: "Pink Noise",
        0.30: "Near-White",
        0.80: "Blue Noise",
        1.50: "Violet Noise"
    }
    
    for idx, target_g in enumerate(test_gammas):
        gamma_tensor = torch.tensor([target_g], dtype=torch.float32)
        
        with torch.no_grad():
            pts_final = dynamics(pts_init, gamma_tensor)
            density = rasterizer(pts_final)
            spectral_out = analyzer(density)
            
            pts_np = pts_final[0].numpy()
            psd_2d_np = spectral_out['psd_2d'][0].numpy()
            radial_psd_np = spectral_out['radial_psd'][0].numpy()
            freqs_np = spectral_out['freqs'].numpy()
            measured_g = spectral_out['gamma'][0].item()
            
        stats = compute_spatial_statistics(pts_np)
        results.append({
            'target_gamma': target_g,
            'measured_gamma': measured_g,
            'error': abs(measured_g - target_g),
            'cv_nnd': stats['cv_nnd'],
            'collapse_score': stats['collapse_score']
        })
        
        # --- Row 1: Point Cloud ---
        ax1 = axes[0, idx]
        ax1.scatter(pts_np[:, 0], pts_np[:, 1], s=14, color='#1f77b4', edgecolors='none', alpha=0.85)
        ax1.set_xlim(0, 1)
        ax1.set_ylim(0, 1)
        ax1.set_aspect('equal')
        name = noise_names.get(target_g, f"γ*={target_g:+.2f}")
        ax1.set_title(f"{name}\nTarget: γ* = {target_g:+.2f}", fontsize=11, fontweight='bold')
        ax1.grid(True, linestyle=':', alpha=0.4)
        if idx == 0:
            ax1.set_ylabel("Point Space [0,1)²", fontsize=11, fontweight='bold')
            
        # --- Row 2: 2D PSD ---
        ax2 = axes[1, idx]
        log_psd = np.log10(np.clip(psd_2d_np, 1e-6, None))
        im = ax2.imshow(log_psd, origin='lower', cmap='magma', extent=[-32, 32, -32, 32])
        ax2.set_title(f"2D PSD (Log₁₀)", fontsize=10)
        if idx == 0:
            ax2.set_ylabel("Frequency (u, v)", fontsize=11, fontweight='bold')
            
        # --- Row 3: Radial PSD vs Target ---
        ax3 = axes[2, idx]
        mask = (freqs_np >= 2) & (freqs_np <= 24)
        f_band = freqs_np[mask]
        p_band = np.clip(radial_psd_np[mask], 1e-6, None)
        
        # Plot measured
        ax3.loglog(f_band, p_band, 'o-', color='#1f77b4', lw=1.8, ms=4, label=f'Synthesized (γ̂={measured_g:+.2f})')
        
        # Fit reference target line
        f_mid = np.exp(np.mean(np.log(f_band)))
        p_mid = np.exp(np.mean(np.log(p_band)))
        p_target = p_mid * (f_band / f_mid) ** target_g
        ax3.loglog(f_band, p_target, '--', color='#d62728', lw=2.0, label=f'Target (γ*={target_g:+.2f})')
        
        ax3.set_xlabel("Frequency f", fontsize=10)
        ax3.grid(True, which='both', linestyle=':', alpha=0.5)
        ax3.legend(fontsize=8, loc='best')
        if idx == 0:
            ax3.set_ylabel("Radial PSD P(f)", fontsize=11, fontweight='bold')
            
    plt.tight_layout()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"[*] Saved interpolation figure to {save_path}")
    
    # Print summary table
    print("\n" + "="*70)
    print("UNSEEN SPECTRAL INTERPOLATION RESULTS (Contribution C4):")
    print(f"{'Target γ*':>10} | {'Noise Class':<12} | {'Measured γ̂':>10} | {'Error |Δγ|':>10} | {'CV_NND':>8}")
    print("-" * 70)
    for r in results:
        tg = r['target_gamma']
        name = noise_names.get(tg, "Custom")
        print(f"{tg:+10.2f} | {name:<12} | {r['measured_gamma']:+10.3f} | {r['error']:10.3f} | {r['cv_nnd']:8.4f}")
    print("="*70)
    
    return results


if __name__ == "__main__":
    run_unseen_gamma_interpolation()
