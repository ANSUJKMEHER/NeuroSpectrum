"""
evaluate.py - Quantitative Evaluation and Spatial Diagnostic Harness.
Implements Section 9 (Evaluation Protocol) of the research proposal:
- Pair-Correlation Function g(r): Critical evaluation-time diagnostic for sub-kernel particle collapse
- Coefficient of Variation of Nearest-Neighbor Distances (CV_NND)
- Absolute Mean Nearest Neighbor Spacing (mu_NND)
- Spectral Slope Error MAE: |gamma_hat - gamma_target|
- PSD Log-MSE against target power-law profile
"""

import torch
import numpy as np


def compute_pair_correlation_2d(points: np.ndarray, r_max: float = 0.25, 
                                num_bins: int = 100, L: float = 1.0) -> tuple:
    """
    Compute 2D Pair-Correlation Function g(r) with periodic boundary conditions.
    g(r) measures the probability of finding a particle at distance r from another particle,
    normalized by the expectation for a completely uniform Poisson point process of density rho.
    
    Diagnostic interpretation:
    - g(r) -> 0 for r < r_min: Strong spatial exclusion / blue noise spacing (Ideal).
    - g(r) -> 1 for all r: Uniform random / Poisson process (White noise).
    - Spike in g(r) near r=0: Signature of particle collapse / clustering (Failure mode).
    
    Args:
        points: (N, 2) numpy array in [0, L)^2
        r_max: maximum radial distance to analyze
        num_bins: number of radial bins
        L: box size
    Returns:
        r_centers: (num_bins,) bin center radii
        g_r: (num_bins,) pair-correlation values
    """
    N = len(points)
    bin_edges = np.linspace(0, r_max, num_bins + 1)
    r_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    
    if N <= 1:
        return r_centers, np.zeros(num_bins)
        
    rho = N / (L * L) # particle density
    
    # 1. Pairwise differences with minimum-image convention
    diff = points[:, np.newaxis, :] - points[np.newaxis, :, :] # (N, N, 2)
    diff = diff - L * np.round(diff / L)
    
    # 2. Pairwise distances
    dist = np.sqrt(np.sum(diff ** 2, axis=-1))
    
    # Extract upper triangle distances (i < j)
    triu_indices = np.triu_indices(N, k=1)
    pair_distances = dist[triu_indices]
    
    # 3. Bin pair distances
    dr = bin_edges[1] - bin_edges[0]
    
    counts, _ = np.histogram(pair_distances, bins=bin_edges)
    
    # 4. Expected count for uniform Poisson process in circular annulus:
    # E[dN(r)] = 0.5 * N * rho * Area(annulus) = 0.5 * N * rho * pi * (r_out^2 - r_in^2)
    annulus_areas = np.pi * (bin_edges[1:]**2 - bin_edges[:-1]**2)
    expected_counts = 0.5 * N * rho * annulus_areas
    
    # g(r) = observed / expected
    g_r = np.zeros(num_bins)
    valid = expected_counts > 0
    g_r[valid] = counts[valid] / expected_counts[valid]
    
    return r_centers, g_r


def compute_spatial_statistics(points, L: float = 1.0) -> dict:
    """
    Compute comprehensive quantitative spatial statistics for point set.
    Args:
        points: (N, 2) tensor or numpy array in [0, L)^2
    Returns:
        dict containing CV_NND, mean_NND, min_distance, and g(r) diagnostic
    """
    if isinstance(points, torch.Tensor):
        pts_np = points.detach().cpu().numpy()
    else:
        pts_np = np.asarray(points)
        
    N = len(pts_np)
    if N <= 1:
        r_centers, g_r = compute_pair_correlation_2d(pts_np, r_max=0.20, num_bins=60, L=L)
        return {
            'mean_nnd': 0.0,
            'std_nnd': 0.0,
            'cv_nnd': 0.0,
            'min_distance': 0.0,
            'coincident_pairs_count': 0,
            'overlapping_pairs_count': 0,
            'effective_unique_particles': N,
            'total_particles': N,
            'has_coincident_points': False,
            'has_overlapping_points': False,
            'g_r_radii': r_centers,
            'g_r_values': g_r,
            'collapse_score': 0.0,
            'has_collapsed': False
        }
    
    # Pairwise periodic distances
    diff = pts_np[:, np.newaxis, :] - pts_np[np.newaxis, :, :]
    diff = diff - L * np.round(diff / L)
    dist = np.sqrt(np.sum(diff ** 2, axis=-1))
    
    # Check for coincident (exact overlap r < 1e-4) and overlapping (r < 0.005) points
    triu_indices = np.triu_indices(N, k=1)
    pair_distances = dist[triu_indices]
    coincident_pairs_count = int(np.sum(pair_distances < 1e-4))
    overlapping_pairs_count = int(np.sum(pair_distances < 0.005))
    
    # Effective unique spatial clusters within 1e-4 tolerance
    visited = np.zeros(N, dtype=bool)
    num_unique_clusters = 0
    for i in range(N):
        if not visited[i]:
            num_unique_clusters += 1
            visited[dist[i] < 1e-4] = True
            visited[i] = True
            
    # Mask self distance
    np.fill_diagonal(dist, np.inf)
    
    # Nearest neighbor distances
    nnd = np.min(dist, axis=-1)
    mean_nnd = float(np.mean(nnd))
    std_nnd = float(np.std(nnd))
    cv_nnd = float(std_nnd / (mean_nnd + 1e-12))
    min_dist = float(np.min(nnd))
    
    # Pair correlation function
    r_centers, g_r = compute_pair_correlation_2d(pts_np, r_max=0.20, num_bins=60, L=L)
    
    # Check for sub-kernel collapse: spike in g(r) near origin r < 0.02
    collapse_band = r_centers < 0.02
    collapse_score = float(np.max(g_r[collapse_band])) if np.any(collapse_band) else 0.0
    has_collapsed = collapse_score > 3.0
    
    # Nearest neighbor distance histogram vs Poisson reference
    # For Poisson process in 2D: P(r) = 2 * pi * rho * r * exp(-pi * rho * r^2)
    rho = N / (L * L)
    nnd_max = max(0.30, float(np.max(nnd)) + 0.05) if len(nnd) > 0 else 0.30
    nnd_bins = np.linspace(0.0, nnd_max, 30)
    nnd_bin_centers = 0.5 * (nnd_bins[:-1] + nnd_bins[1:])
    raw_counts, _ = np.histogram(nnd, bins=nnd_bins)
    total_in_bins = np.sum(raw_counts)
    if total_in_bins > 0:
        db = nnd_bins[1] - nnd_bins[0]
        hist_counts = raw_counts / (total_in_bins * db)
    else:
        hist_counts = np.zeros_like(nnd_bin_centers)
    
    # Poisson theoretical Rayleigh distribution
    poisson_ref = 2.0 * np.pi * rho * nnd_bin_centers * np.exp(-np.pi * rho * (nnd_bin_centers ** 2))
    
    return {
        'mean_nnd': mean_nnd,
        'std_nnd': std_nnd,
        'cv_nnd': cv_nnd,
        'min_distance': min_dist,
        'coincident_pairs_count': coincident_pairs_count,
        'overlapping_pairs_count': overlapping_pairs_count,
        'effective_unique_particles': int(num_unique_clusters),
        'total_particles': N,
        'has_coincident_points': bool(coincident_pairs_count > 0),
        'has_overlapping_points': bool(overlapping_pairs_count > 0),
        'g_r_radii': r_centers.tolist() if isinstance(r_centers, np.ndarray) else r_centers,
        'g_r_values': g_r.tolist() if isinstance(g_r, np.ndarray) else g_r,
        'collapse_score': collapse_score,
        'has_collapsed': has_collapsed,
        'nnd_bins': nnd_bin_centers.tolist(),
        'nnd_histogram': hist_counts.tolist(),
        'poisson_reference': poisson_ref.tolist()
    }


def evaluate_point_distribution(points, target_gamma: float = 0.0, 
                                analyzer = None, rasterizer = None, L: float = 1.0) -> dict:
    """
    Run complete quantitative evaluation combining spatial and spectral metrics.
    Supports both PyTorch tensors and NumPy arrays.
    """
    if isinstance(points, np.ndarray):
        points_t = torch.from_numpy(points).float()
    else:
        points_t = points
        
    if points_t.dim() == 2:
        points_t = points_t.unsqueeze(0)
        
    # 1. Spatial statistics
    metrics = compute_spatial_statistics(points_t[0], L=L)
    
    # 2. Spectral statistics (if modules provided)
    if rasterizer is not None and analyzer is not None:
        with torch.no_grad():
            density = rasterizer(points_t)
            spec_out = analyzer(density)
            measured_gamma = spec_out['gamma'][0].item() if spec_out['gamma'].dim() > 0 else spec_out['gamma'].item()
            metrics['measured_gamma'] = measured_gamma
            metrics['target_gamma'] = target_gamma
            metrics['gamma_error'] = abs(measured_gamma - target_gamma)
            metrics['psd_2d'] = spec_out['psd_2d'][0].cpu().numpy()
            metrics['radial_psd'] = spec_out['radial_psd'][0].cpu().numpy()
            metrics['freqs'] = spec_out['freqs'].cpu().numpy()
            
    return metrics
