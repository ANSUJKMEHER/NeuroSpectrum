"""
test_ablations.py — Phase 11: 5 Systematic Ablation Studies (Section 10 of Proposal).
Ablations:
1. Loss Components: Full (L_spec + L_gamma + L_spacing) vs No Spacing (L_spec + L_gamma) vs Spectral Only (L_spec)
2. Simulation Steps: T in {5, 10, 20, 40}
3. Activation Functions: SiLU vs ReLU vs Tanh
4. Network Capacity: Shallow (2x32) vs Baseline (3x64) vs Deep (4x128)
5. Physics BPTT Mode: Full Checkpointing vs Truncated BPTT (K=5 steps)
Generates: outputs/figures/ablation_studies_summary.png
"""

import os
import sys
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import torch.nn as nn
import numpy as np
import matplotlib.pyplot as plt

from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from losses import CompositeSpectralLoss
from evaluate import compute_spatial_statistics


def run_quick_train(energy_model, num_steps=20, dt=0.02, 
                    lambda_spec=1.0, lambda_gamma=2.0, lambda_spacing=2.0,
                    num_epochs=30, lr=1e-3, target_gamma_val=1.0, 
                    use_truncated_bptt=False, trunc_k=5):
    """
    Standardized training harness for single ablation run.
    """
    device = "cpu"
    torch.manual_seed(42)
    np.random.seed(42)
    
    trunc_steps = trunc_k if use_truncated_bptt else None
    dynamics = DifferentiableSimulationEngine(
        energy_model, 
        num_steps=num_steps, 
        dt=dt, 
        L=1.0, 
        use_checkpointing=True,
        truncated_bptt_steps=trunc_steps
    )
    rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24)
    loss_fn = CompositeSpectralLoss(
        lambda_spec=lambda_spec, 
        lambda_gamma=lambda_gamma, 
        lambda_spacing=lambda_spacing, 
        n_particles=256
    )
    
    optimizer = torch.optim.Adam(energy_model.parameters(), lr=lr)
    pts_init = torch.rand(1, 256, 2)
    target_gamma_tensor = torch.tensor([target_gamma_val], dtype=torch.float32)
    
    loss_history = []
    
    t0 = time.perf_counter()
    for epoch in range(1, num_epochs + 1):
        optimizer.zero_grad()
        points_final = dynamics(pts_init, target_gamma_tensor)
        density = rasterizer(points_final)
        spectral_out = analyzer(density)
        loss_dict = loss_fn(points_final, target_gamma_tensor, spectral_out)
        
        loss = loss_dict['loss']
        loss.backward()
        torch.nn.utils.clip_grad_norm_(energy_model.parameters(), max_norm=1.0)
        optimizer.step()
        loss_history.append(loss.item())
        
    elapsed = time.perf_counter() - t0
    
    with torch.no_grad():
        pts_f = dynamics(pts_init, target_gamma_tensor)
        dens = rasterizer(pts_f)
        spec = analyzer(dens)
        meas_gamma = spec['gamma'][0].item()
        pts_np = pts_f[0].numpy()
        stats = compute_spatial_statistics(pts_np)
        
    return {
        'final_loss': loss_history[-1],
        'measured_gamma': meas_gamma,
        'gamma_error': abs(meas_gamma - target_gamma_val),
        'cv_nnd': stats['cv_nnd'],
        'collapse_score': stats['collapse_score'],
        'has_collapsed': stats['has_collapsed'],
        'time_sec': elapsed,
        'loss_history': loss_history
    }


def run_all_ablations(save_path: str = "outputs/figures/ablation_studies_summary.png"):
    print("\n" + "="*80)
    print("PHASE 11: 5 SYSTEMATIC ABLATION STUDIES (SECTION 10)")
    print("Target: γ* = +1.00 | N=256 Particles | Fast Epochs=30 per trial")
    print("="*80)
    
    results = {}
    
    # -------------------------------------------------------------
    # ABLATION 1: Loss Components
    # -------------------------------------------------------------
    print("\n--- [Ablation 1/5] Loss Component Ablation ---", flush=True)
    m1 = NeuralPairwiseEnergy(hidden_dim=64, num_layers=3, use_divergence_prior=True)
    m2 = NeuralPairwiseEnergy(hidden_dim=64, num_layers=3, use_divergence_prior=True)
    m3 = NeuralPairwiseEnergy(hidden_dim=64, num_layers=3, use_divergence_prior=True)
    
    res_full = run_quick_train(m1, lambda_spec=1.0, lambda_gamma=2.0, lambda_spacing=2.0)
    res_no_spacing = run_quick_train(m2, lambda_spec=1.0, lambda_gamma=2.0, lambda_spacing=0.0)
    res_spec_only = run_quick_train(m3, lambda_spec=1.0, lambda_gamma=0.0, lambda_spacing=0.0)
    
    results['loss_components'] = {
        'Full (L_spec + L_γ + L_spacing)': res_full,
        'No Spacing (L_spec + L_γ)': res_no_spacing,
        'Spectral Only (L_spec)': res_spec_only
    }
    
    # -------------------------------------------------------------
    # ABLATION 2: Simulation Depth (Steps T)
    # -------------------------------------------------------------
    print("\n--- [Ablation 2/5] Simulation Steps T ∈ {5, 10, 20, 40} ---", flush=True)
    results['sim_steps'] = {}
    for T in [5, 10, 20, 40]:
        m = NeuralPairwiseEnergy(hidden_dim=64, num_layers=3, use_divergence_prior=True)
        results['sim_steps'][f'T = {T}'] = run_quick_train(m, num_steps=T)
        
    # -------------------------------------------------------------
    # ABLATION 3: Activation Functions
    # -------------------------------------------------------------
    print("\n--- [Ablation 3/5] Activation Function (SiLU vs. ReLU vs. Tanh) ---", flush=True)
    class CustomEnergy(nn.Module):
        def __init__(self, act_fn):
            super().__init__()
            self.mlp = nn.Sequential(
                nn.Linear(2, 64), act_fn(),
                nn.Linear(64, 64), act_fn(),
                nn.Linear(64, 1, bias=False)
            )
            self.use_divergence_prior = True
            self.eps_divergence = 0.005
            self.r_repulsion = 0.045
            
        def forward_pairwise(self, r_matrix, gamma):
            B, N, _ = r_matrix.shape
            g_exp = gamma.view(B, 1, 1, 1).expand(B, N, N, 1)
            feat = torch.cat([r_matrix.unsqueeze(-1), g_exp], dim=-1)
            e = self.mlp(feat).squeeze(-1)
            return e + self.eps_divergence * torch.exp(-(r_matrix**2)/(2.0*self.r_repulsion**2))
            
        def compute_total_energy(self, points, gamma, L=1.0):
            diff = points.unsqueeze(2) - points.unsqueeze(1)
            diff = diff - L * torch.round(diff / L)
            r_mat = torch.sqrt((diff**2).sum(dim=-1) + 1e-12)
            e_mat = self.forward_pairwise(r_mat, gamma)
            return 0.5 * (e_mat.sum(dim=(-2, -1)) - torch.diagonal(e_mat, dim1=-2, dim2=-1).sum(dim=-1))

    results['activations'] = {
        'SiLU (C^∞ Smooth)': run_quick_train(CustomEnergy(nn.SiLU)),
        'ReLU (Non-smooth)': run_quick_train(CustomEnergy(nn.ReLU)),
        'Tanh (Smooth Bounded)': run_quick_train(CustomEnergy(nn.Tanh))
    }
    
    # -------------------------------------------------------------
    # ABLATION 4: Network Capacity (Depth & Width)
    # -------------------------------------------------------------
    print("\n--- [Ablation 4/5] Network Capacity ---", flush=True)
    results['capacity'] = {
        'Shallow (2L x 32)': run_quick_train(NeuralPairwiseEnergy(hidden_dim=32, num_layers=2)),
        'Baseline (3L x 64)': run_quick_train(NeuralPairwiseEnergy(hidden_dim=64, num_layers=3)),
        'Deep (4L x 128)': run_quick_train(NeuralPairwiseEnergy(hidden_dim=128, num_layers=4))
    }
    
    # -------------------------------------------------------------
    # ABLATION 5: BPTT Differentiation Mode
    # -------------------------------------------------------------
    print("\n--- [Ablation 5/5] Full Checkpointing vs. Truncated BPTT ---", flush=True)
    m_full = NeuralPairwiseEnergy(hidden_dim=64, num_layers=3)
    m_trunc = NeuralPairwiseEnergy(hidden_dim=64, num_layers=3)
    results['bptt_mode'] = {
        'Full BPTT (T=20)': run_quick_train(m_full, num_steps=20, use_truncated_bptt=False),
        'Truncated BPTT (K=5)': run_quick_train(m_trunc, num_steps=20, use_truncated_bptt=True, trunc_k=5)
    }
    
    # -------------------------------------------------------------
    # Visualization: 5-Panel Publication Figure
    # -------------------------------------------------------------
    fig, axes = plt.subplots(1, 5, figsize=(22, 4.5), dpi=150)
    
    # Panel 1: Loss Components Bar Chart
    ax = axes[0]
    names = list(results['loss_components'].keys())
    maes = [results['loss_components'][k]['gamma_error'] for k in names]
    bars = ax.bar(['Full Loss', 'No Spacing', 'Spec Only'], maes, color=['#2ca02c', '#ff7f0e', '#d62728'])
    ax.set_title("1. Loss Terms (|Δγ|)", fontweight='bold')
    ax.set_ylabel("Slope Error |γ̂ - γ*|")
    ax.grid(True, linestyle=':', alpha=0.4, axis='y')
    for b in bars:
        ax.text(b.get_x() + b.get_width()/2.0, b.get_height() + 0.02, f"{b.get_height():.2f}", ha='center', fontsize=9)
        
    # Panel 2: Simulation Steps Curve
    ax = axes[1]
    step_keys = list(results['sim_steps'].keys())
    step_errs = [results['sim_steps'][k]['gamma_error'] for k in step_keys]
    step_times = [results['sim_steps'][k]['time_sec'] for k in step_keys]
    ax.plot([5, 10, 20, 40], step_errs, 'o-', color='navy', lw=2, label='Slope Error')
    ax.set_title("2. Simulation Steps (T)", fontweight='bold')
    ax.set_xlabel("Steps T")
    ax.set_ylabel("Slope Error |Δγ|")
    ax.grid(True, linestyle=':', alpha=0.4)
    
    # Panel 3: Activations Bar Chart
    ax = axes[2]
    act_names = list(results['activations'].keys())
    act_maes = [results['activations'][k]['gamma_error'] for k in act_names]
    bars = ax.bar(['SiLU', 'ReLU', 'Tanh'], act_maes, color=['#1f77b4', '#e377c2', '#17becf'])
    ax.set_title("3. Activation Function", fontweight='bold')
    ax.set_ylabel("Slope Error |Δγ|")
    ax.grid(True, linestyle=':', alpha=0.4, axis='y')
    for b in bars:
        ax.text(b.get_x() + b.get_width()/2.0, b.get_height() + 0.02, f"{b.get_height():.2f}", ha='center', fontsize=9)
        
    # Panel 4: Capacity Bar Chart
    ax = axes[3]
    cap_names = list(results['capacity'].keys())
    cap_maes = [results['capacity'][k]['gamma_error'] for k in cap_names]
    bars = ax.bar(['Shallow', 'Baseline', 'Deep'], cap_maes, color=['#8c564b', '#2ca02c', '#9467bd'])
    ax.set_title("4. Network Capacity", fontweight='bold')
    ax.set_ylabel("Slope Error |Δγ|")
    ax.grid(True, linestyle=':', alpha=0.4, axis='y')
    for b in bars:
        ax.text(b.get_x() + b.get_width()/2.0, b.get_height() + 0.02, f"{b.get_height():.2f}", ha='center', fontsize=9)
        
    # Panel 5: BPTT Mode Comparison
    ax = axes[4]
    bptt_names = list(results['bptt_mode'].keys())
    bptt_times = [results['bptt_mode'][k]['time_sec'] for k in bptt_names]
    bars = ax.bar(['Full BPTT\n(T=20)', 'Truncated\n(K=5)'], bptt_times, color=['#2ca02c', '#ff7f0e'])
    ax.set_title("5. Physics Runtime (s)", fontweight='bold')
    ax.set_ylabel("Training Time (s)")
    ax.grid(True, linestyle=':', alpha=0.4, axis='y')
    for b in bars:
        ax.text(b.get_x() + b.get_width()/2.0, b.get_height() + 0.5, f"{b.get_height():.1f}s", ha='center', fontsize=9)
        
    plt.tight_layout()
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"\n[*] Saved 5-panel ablation summary figure to {save_path}", flush=True)
    
    # Summary report
    print("\n" + "="*85)
    print("PHASE 11 ABLATION SUMMARY SCORECARD:")
    print("="*85)
    print("1. Loss Component Ablation:")
    for k, v in results['loss_components'].items():
        print(f"   - {k:<35}: Error |Δγ| = {v['gamma_error']:.3f}, CV_NND = {v['cv_nnd']:.4f}, Collapsed = {v['has_collapsed']}")
    print("2. Simulation Depth (T):")
    for k, v in results['sim_steps'].items():
        print(f"   - {k:<35}: Error |Δγ| = {v['gamma_error']:.3f}, Time = {v['time_sec']:.2f}s")
    print("3. Activation Functions:")
    for k, v in results['activations'].items():
        print(f"   - {k:<35}: Error |Δγ| = {v['gamma_error']:.3f}, Loss = {v['final_loss']:.4f}")
    print("4. Network Capacity:")
    for k, v in results['capacity'].items():
        print(f"   - {k:<35}: Error |Δγ| = {v['gamma_error']:.3f}, Loss = {v['final_loss']:.4f}")
    print("5. Differentiable Physics Modes:")
    for k, v in results['bptt_mode'].items():
        print(f"   - {k:<35}: Error |Δγ| = {v['gamma_error']:.3f}, Time = {v['time_sec']:.2f}s")
    print("="*85)
    
    return results


if __name__ == "__main__":
    run_all_ablations()
