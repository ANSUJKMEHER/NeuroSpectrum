"""
applications.py — Downstream Computer Graphics Applications (Phase 14).
Implements Section 1.2, Section 8, and Section 12 of the research proposal:
1. Adaptive Point Stippling / Non-Photorealistic Rendering (NPR) with Blue Noise Spacing
2. Monte Carlo Rendering Numerical Integration & Empirical Variance Reduction
"""

import torch
import numpy as np
from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine


def generate_synthetic_stipple_target(resolution: int = 128) -> np.ndarray:
    """
    Generate a high-contrast geometric target density map for stippling.
    Combines smooth gradients, sharp discs, and fine rings.
    """
    x = np.linspace(0, 1, resolution)
    y = np.linspace(0, 1, resolution)
    xx, yy = np.meshgrid(x, y)
    
    # 1. Base gradient
    density = 0.3 * (1.0 - yy)
    
    # 2. Central smooth Gaussian spot
    r1 = np.sqrt((xx - 0.5)**2 + (yy - 0.5)**2)
    density += 0.6 * np.exp(-(r1**2) / (2 * 0.18**2))
    
    # 3. Off-center ring
    r2 = np.sqrt((xx - 0.3)**2 + (yy - 0.7)**2)
    density += 0.5 * np.exp(-((r2 - 0.15)**2) / (2 * 0.03**2))
    
    # Clip to [0, 1]
    density = np.clip(density, 0.05, 0.95)
    return density


def sample_importance_points(density_map: np.ndarray, n_particles: int = 1024, seed: int = 42) -> np.ndarray:
    """
    Rejection sampling to initialize particle coordinates proportional to target density map.
    """
    np.random.seed(seed)
    res_y, res_x = density_map.shape
    sampled_pts = []
    
    max_d = np.max(density_map)
    while len(sampled_pts) < n_particles:
        cands = np.random.rand(n_particles * 2, 2)
        # Sample image intensity at candidate coordinates
        ix = np.clip(np.floor(cands[:, 0] * res_x).astype(int), 0, res_x - 1)
        iy = np.clip(np.floor(cands[:, 1] * res_y).astype(int), 0, res_y - 1)
        
        prob = density_map[iy, ix] / max_d
        accept = np.random.rand(len(cands)) < prob
        
        accepted_pts = cands[accept]
        sampled_pts.extend(accepted_pts)
        
    return np.array(sampled_pts[:n_particles], dtype=np.float32)


def neural_stipple_relaxation(
    points_init: np.ndarray,
    energy_model: NeuralPairwiseEnergy,
    num_steps: int = 25,
    dt: float = 0.015,
    gamma_val: float = 1.0,
    k: int = 16
) -> np.ndarray:
    """
    Apply learned neural energy relaxation (Blue Noise gamma = +1.0) to regularize point stippling.
    """
    device = next(energy_model.parameters()).device
    pts_t = torch.tensor(points_init, dtype=torch.float32, device=device).unsqueeze(0)
    gamma_t = torch.tensor([gamma_val], dtype=torch.float32, device=device)
    
    sim = DifferentiableSimulationEngine(
        energy_model=energy_model,
        num_steps=num_steps,
        dt=dt,
        L=1.0,
        use_checkpointing=False,
        use_knn=True,
        k=k
    ).to(device)
    
    with torch.no_grad():
        pts_relaxed = sim(pts_t, gamma_t)
        
    return pts_relaxed[0].cpu().numpy()


def evaluate_monte_carlo_integration(
    f_integrand,
    exact_integral: float,
    energy_model: NeuralPairwiseEnergy,
    sample_counts = [16, 32, 64, 128, 256, 512, 1024],
    num_trials: int = 40
) -> dict:
    """
    Benchmark Monte Carlo rendering integration error across point sampling strategies:
    1. Uniform Random (White noise)
    2. Jittered Grid Sampling
    3. NeuroSpectrum Synthesized Point Sets (Blue Noise gamma = +1.0)
    """
    results = {
        'N': sample_counts,
        'rmse_random': [],
        'rmse_jittered': [],
        'rmse_neurospectrum': [],
        'std_random': [],
        'std_jittered': [],
        'std_neurospectrum': []
    }
    
    device = next(energy_model.parameters()).device
    gamma_blue = torch.tensor([1.0], dtype=torch.float32, device=device)
    sim = DifferentiableSimulationEngine(
        energy_model=energy_model,
        num_steps=20,
        dt=0.02,
        L=1.0,
        use_checkpointing=False,
        use_knn=True,
        k=16
    ).to(device)
    
    for N in sample_counts:
        errs_random = []
        errs_jittered = []
        errs_neuro = []
        
        # Jittered grid side
        side = int(np.round(np.sqrt(N)))
        n_jit = side * side
        cell_size = 1.0 / side
        coords = (np.arange(side) + 0.5) / side
        gx, gy = np.meshgrid(coords, coords)
        base_grid = np.stack([gx.flatten(), gy.flatten()], axis=-1)
        
        for t in range(num_trials):
            np.random.seed(2000 + t * 41 + N)
            torch.manual_seed(2000 + t * 41 + N)
            
            # 1. Uniform Random
            pts_rand = np.random.rand(N, 2)
            est_rand = np.mean(f_integrand(pts_rand[:, 0], pts_rand[:, 1]))
            errs_random.append(est_rand - exact_integral)
            
            # 2. Jittered Grid
            jit = (np.random.rand(n_jit, 2) - 0.5) * cell_size * 0.8
            pts_jit = (base_grid + jit) % 1.0
            if n_jit < N:
                pts_jit = np.concatenate([pts_jit, np.random.rand(N - n_jit, 2)], axis=0)
            elif n_jit > N:
                pts_jit = pts_jit[:N]
            est_jit = np.mean(f_integrand(pts_jit[:, 0], pts_jit[:, 1]))
            errs_jittered.append(est_jit - exact_integral)
            
            # 3. NeuroSpectrum Point Set
            pts_init_t = torch.rand(1, N, 2, device=device)
            with torch.no_grad():
                pts_neuro_t = sim(pts_init_t, gamma_blue)
            pts_neuro = pts_neuro_t[0].cpu().numpy()
            est_neuro = np.mean(f_integrand(pts_neuro[:, 0], pts_neuro[:, 1]))
            errs_neuro.append(est_neuro - exact_integral)
            
        results['rmse_random'].append(float(np.sqrt(np.mean(np.array(errs_random)**2))))
        results['rmse_jittered'].append(float(np.sqrt(np.mean(np.array(errs_jittered)**2))))
        results['rmse_neurospectrum'].append(float(np.sqrt(np.mean(np.array(errs_neuro)**2))))
        
        results['std_random'].append(float(np.std(errs_random)))
        results['std_jittered'].append(float(np.std(errs_jittered)))
        results['std_neurospectrum'].append(float(np.std(errs_neuro)))
        
    return results
