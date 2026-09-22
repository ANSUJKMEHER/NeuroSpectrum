"""
visualize.py - Publication-Quality Multi-Panel Diagnostic Plotting.
Generates comprehensive 4-panel and 5-panel figures for:
- Point distribution in [0, 1)^2
- 2D Power Spectral Density (Log-PSD)
- Radial PSD curve vs. Target power-law profile P*(f) = C * f^gamma
- Pair-Correlation Function g(r) diagnostic for spatial regularity
- Training loss convergence histories
"""

import os
import numpy as np

# Ensure clean headless matplotlib execution with local writable config directory
mpl_cache = os.path.join(os.path.expanduser("~"), ".cache", "matplotlib")
try:
    os.makedirs(mpl_cache, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", mpl_cache)
except Exception:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp")

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from evaluate import compute_pair_correlation_2d


def plot_point_set_analysis(points: np.ndarray, psd_2d: np.ndarray, freqs: np.ndarray, 
                            radial_psd: np.ndarray, target_gamma: float, measured_gamma: float,
                            save_path: str = "point_set_analysis.png", title_prefix: str = ""):
    """
    Generate a 4-panel diagnostic figure for a synthesized point set.
    """
    fig, axes = plt.subplots(1, 4, figsize=(20, 5))
    
    # Panel 1: Point Cloud
    axes[0].scatter(points[:, 0], points[:, 1], s=14, c='#1f77b4', edgecolors='none', alpha=0.85)
    axes[0].set_xlim(0, 1)
    axes[0].set_ylim(0, 1)
    axes[0].set_aspect('equal')
    axes[0].set_title(f"{title_prefix}Point Distribution\n(N={len(points)})", fontsize=12, fontweight='bold')
    axes[0].set_xlabel("x")
    axes[0].set_ylabel("y")
    axes[0].grid(True, alpha=0.25, linestyle='--')
    
    # Panel 2: 2D Power Spectral Density
    nyq_val = psd_2d.shape[0] // 2
    im = axes[1].imshow(np.log10(psd_2d + 1e-12), cmap='magma', extent=[-nyq_val, nyq_val, -nyq_val, nyq_val], origin='lower')
    axes[1].set_title("2D Power Spectrum\n(log₁₀ PSD)", fontsize=12, fontweight='bold')
    axes[1].set_xlabel("u (cycles/box)")
    axes[1].set_ylabel("v (cycles/box)")
    plt.colorbar(im, ax=axes[1], fraction=0.046, pad=0.04)
    
    # Panel 3: Radial PSD vs Target Profile
    nyquist = len(freqs) // 2
    f_band = freqs[2:nyquist]
    p_measured = radial_psd[2:nyquist]
    
    # Fit target power law line for visualization
    log_f = np.log(f_band)
    log_p = np.log(p_measured + 1e-12)
    mean_p = np.mean(log_p)
    target_line = np.exp(target_gamma * (log_f - np.mean(log_f)) + mean_p)
    
    axes[2].plot(f_band, p_measured, 'o-', color='#1f77b4', lw=2, ms=4, label=f"Measured (γ̂ = {measured_gamma:+.2f})")
    axes[2].plot(f_band, target_line, '--', color='#d62728', lw=2.5, label=f"Target (γ* = {target_gamma:+.2f})")
    axes[2].set_xscale('log')
    axes[2].set_yscale('log')
    axes[2].set_xlabel("Radial Frequency f", fontsize=11)
    axes[2].set_ylabel("Power P(f)", fontsize=11)
    axes[2].set_title("Radial Power Spectrum\n(Log-Log Fit)", fontsize=12, fontweight='bold')
    axes[2].legend(loc='lower left', framealpha=0.9)
    axes[2].grid(True, which="both", alpha=0.3, linestyle=':')
    
    # Panel 4: Pair-Correlation Function g(r)
    r_centers, g_r = compute_pair_correlation_2d(points, r_max=0.20, num_bins=50)
    axes[3].plot(r_centers, g_r, color='#2ca02c', lw=2.5, label="Observed g(r)")
    axes[3].axhline(1.0, color='gray', linestyle='--', lw=1.5, label="Poisson (g=1)")
    axes[3].set_xlim(0, 0.20)
    axes[3].set_ylim(0, max(2.5, np.max(g_r) * 1.15))
    axes[3].set_xlabel("Pair Distance r", fontsize=11)
    axes[3].set_ylabel("Pair Correlation g(r)", fontsize=11)
    axes[3].set_title("Pair Correlation g(r)\n(Spatial Regularity)", fontsize=12, fontweight='bold')
    axes[3].legend(loc='upper right', framealpha=0.9)
    axes[3].grid(True, alpha=0.3, linestyle=':')
    
    plt.tight_layout()
    save_dir = os.path.dirname(os.path.abspath(save_path))
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
    plt.savefig(save_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"Saved diagnostic figure to {save_path}")


def plot_training_history(history: dict, save_path: str = "training_history.png"):
    """
    Plot training loss curves and parameter convergence.
    """
    fig, axes = plt.subplots(1, 3, figsize=(16, 4))
    
    epochs = history['epoch']
    
    # Panel 1: Losses
    axes[0].plot(epochs, history['loss'], 'k-', lw=2, label="Total Loss")
    axes[0].plot(epochs, history['l_spec'], 'b--', lw=1.5, label="L_spec")
    axes[0].plot(epochs, history['l_gamma'], 'r--', lw=1.5, label="L_gamma")
    axes[0].plot(epochs, history['l_spacing'], 'g--', lw=1.5, label="L_spacing")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].set_yscale('log')
    axes[0].set_title("Training Loss Breakdown", fontweight='bold')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)
    
    # Panel 2: Measured Gamma Convergence
    axes[1].plot(epochs, history['measured_gamma'], 'b-o', ms=3, lw=2, label="Measured γ̂")
    axes[1].axhline(history['target_gamma'][0], color='r', linestyle='--', lw=2, label=f"Target γ* = {history['target_gamma'][0]:+.2f}")
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("Spectral Slope γ")
    axes[1].set_title("Slope Convergence (γ̂ → γ*)", fontweight='bold')
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)
    
    # Panel 3: Spatial Regularity (CV_NND)
    axes[2].plot(epochs, history['cv_nnd'], 'g-s', ms=3, lw=2, label="CV_NND")
    axes[2].set_xlabel("Epoch")
    axes[2].set_ylabel("CV_NND (Lower = More Regular)")
    axes[2].set_title("Spatial Regularity (CV_NND)", fontweight='bold')
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    save_dir = os.path.dirname(os.path.abspath(save_path))
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
    plt.savefig(save_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"Saved training history to {save_path}")
