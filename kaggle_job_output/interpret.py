"""
interpret.py — Interaction-Law Interpretation Engine for NeuroSpectrum.

Answers the central scientific question:
"What physical interaction law did the neural network actually learn?"

Computes:
1. E(r, γ) vs distance r for any gamma
2. Conservative inter-particle force F(r, γ) = -∂E/∂r
3. Continuous 2D Phase Diagram heatmaps: E(r, γ) and F(r, γ)
4. Equilibrium separations r_0(γ) and attractive/repulsive zones
"""

import torch
import numpy as np
from typing import Dict, Any, List, Optional

from energy import NeuralPairwiseEnergy
from checkpoints import get_optimal_device


def compute_energy_and_force(
    energy_model: NeuralPairwiseEnergy,
    gamma_val: float,
    r_min: float = 0.005,
    r_max: float = 0.50,
    num_points: int = 150,
    device: Optional[torch.device] = None
) -> Dict[str, Any]:
    """
    Computes 1D potential energy E(r) and force F(r) = -dE/dr for a specific spectral slope gamma.
    """
    if device is None:
        device = get_optimal_device()
        
    energy_model = energy_model.to(device)
    energy_model.eval()
    
    r_vals = torch.linspace(r_min, r_max, num_points, device=device, dtype=torch.float32, requires_grad=True)
    gamma_tensor = torch.full((1,), gamma_val, device=device, dtype=torch.float32)
    
    # Forward pairwise through model: input shape (1, num_points, 1)
    r_matrix = r_vals.reshape(1, num_points, 1)
    
    # Calculate energy
    with torch.enable_grad():
        e_matrix = energy_model.forward_pairwise(r_matrix, gamma_tensor) # (1, num_points, 1)
        e_vals = e_matrix.squeeze()
        
        # Force is the negative gradient of pairwise potential w.r.t distance r: F(r) = -dE/dr
        grad_E = torch.autograd.grad(
            outputs=e_vals.sum(),
            inputs=r_vals,
            create_graph=False,
            retain_graph=False
        )[0]
        f_vals = -grad_E
        
    r_np = r_vals.detach().cpu().numpy()
    e_np = e_vals.detach().cpu().numpy()
    f_np = f_vals.detach().cpu().numpy()
    
    # Analyze force character
    # F > 0 => pushes apart (repulsive)
    # F < 0 => pulls together (attractive)
    repulsive_mask = f_np > 0
    attractive_mask = f_np < 0
    
    # Find zero crossing (equilibrium radius r_0 where F(r_0) = 0)
    zero_crossings = []
    for i in range(len(f_np) - 1):
        if (f_np[i] > 0 and f_np[i+1] < 0) or (f_np[i] < 0 and f_np[i+1] > 0):
            # Linear interpolation for zero crossing
            r_zero = r_np[i] - f_np[i] * (r_np[i+1] - r_np[i]) / (f_np[i+1] - f_np[i])
            zero_crossings.append(float(r_zero))
            
    return {
        "gamma": float(gamma_val),
        "r": r_np.tolist(),
        "energy": e_np.tolist(),
        "force": f_np.tolist(),
        "min_energy": float(np.min(e_np)),
        "max_energy": float(np.max(e_np)),
        "max_repulsion": float(np.max(f_np)) if np.any(repulsive_mask) else 0.0,
        "max_attraction": float(np.min(f_np)) if np.any(attractive_mask) else 0.0,
        "equilibrium_radii": zero_crossings,
        "primary_nature": "Dominant Repulsion (Hyperuniform)" if np.mean(f_np[:20]) > 0 else "Dominant Attraction (Clustering)"
    }


def compute_phase_diagram(
    energy_model: NeuralPairwiseEnergy,
    gamma_min: float = -2.0,
    gamma_max: float = 2.0,
    num_gamma: int = 41,
    r_min: float = 0.01,
    r_max: float = 0.45,
    num_r: int = 60,
    device: Optional[torch.device] = None
) -> Dict[str, Any]:
    """
    Computes full 2D Physical Phase Diagram: E(r, γ) and F(r, γ) grid.
    Visualizes how physical interaction morphs continuously from Red (-2) to Violet (+2).
    """
    if device is None:
        device = get_optimal_device()
        
    energy_model = energy_model.to(device)
    energy_model.eval()
    
    gammas = np.linspace(gamma_min, gamma_max, num_gamma)
    r_vals_base = np.linspace(r_min, r_max, num_r)
    
    energy_grid = []
    force_grid = []
    equilibrium_trajectory = []
    
    for g in gammas:
        curve_data = compute_energy_and_force(
            energy_model=energy_model,
            gamma_val=float(g),
            r_min=r_min,
            r_max=r_max,
            num_points=num_r,
            device=device
        )
        energy_grid.append(curve_data["energy"])
        force_grid.append(curve_data["force"])
        
        # Record first stable equilibrium radius if it exists
        eq_r = curve_data["equilibrium_radii"][0] if curve_data["equilibrium_radii"] else None
        equilibrium_trajectory.append({"gamma": float(g), "r_eq": eq_r})
        
    return {
        "gammas": gammas.tolist(),
        "r_values": r_vals_base.tolist(),
        "energy_grid": energy_grid, # Shape: (num_gamma, num_r)
        "force_grid": force_grid,   # Shape: (num_gamma, num_r)
        "equilibrium_trajectory": equilibrium_trajectory
    }


def compute_multi_gamma_curves(
    energy_model: NeuralPairwiseEnergy,
    gammas: List[float] = [-1.0, 0.0, 1.0],
    r_min: float = 0.01,
    r_max: float = 0.50,
    num_points: int = 100,
    device: Optional[torch.device] = None
) -> Dict[str, Any]:
    """
    Computes pairwise energy and force curves for multiple gamma targets simultaneously
    (e.g., gamma = -1.0 [red], 0.0 [white], +1.0 [blue]) for Panel 3 of dashboard.
    """
    if device is None:
        device = get_optimal_device()
        
    series = {}
    r_list = None
    
    for g in gammas:
        data = compute_energy_and_force(
            energy_model=energy_model,
            gamma_val=g,
            r_min=r_min,
            r_max=r_max,
            num_points=num_points,
            device=device
        )
        if r_list is None:
            r_list = data["r"]
        series[str(g)] = {
            "gamma": g,
            "energy": data["energy"],
            "force": data["force"],
            "primary_nature": data["primary_nature"],
            "equilibrium_radii": data["equilibrium_radii"]
        }
        
    return {
        "r": r_list,
        "curves": series,
        "gammas": gammas
    }
