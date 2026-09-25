"""
data.py - Reference point distributions and target condition sampling.
Implements Stage 0 and Stage 1 of the research proposal:
- Uniform Random (White noise, gamma ≈ 0)
- Poisson-Disk / Blue Noise (r_min constrained, gamma > 0)
- Jittered Grid (Regular spacing with noise)
- Clustered / Red Noise (Gaussian cluster point sets, gamma < 0)
"""

import torch
import numpy as np


def generate_uniform_random(batch_size: int, n_particles: int, device="cpu", dtype=torch.float32) -> torch.Tensor:
    """
    Uniform random distribution (White Noise).
    Expected spectrum: Flat across frequencies (gamma ≈ 0).
    Returns: Tensor of shape (batch_size, n_particles, 2) in [0, 1).
    """
    return torch.rand(batch_size, n_particles, 2, device=device, dtype=dtype)


def generate_jittered_grid(batch_size: int, n_particles: int, jitter_strength: float = 0.5, 
                           device="cpu", dtype=torch.float32) -> torch.Tensor:
    """
    Jittered grid distribution.
    Points are placed on a regular grid and perturbed by jitter.
    """
    grid_side = int(np.round(np.sqrt(n_particles)))
    actual_n = grid_side * grid_side
    
    # Base grid centers in [0, 1)
    coords = (torch.arange(grid_side, device=device, dtype=dtype) + 0.5) / grid_side
    grid_y, grid_x = torch.meshgrid(coords, coords, indexing='ij')
    base_grid = torch.stack([grid_x.flatten(), grid_y.flatten()], dim=-1) # (actual_n, 2)
    
    batch_points = []
    cell_size = 1.0 / grid_side
    for _ in range(batch_size):
        jitter = (torch.rand(actual_n, 2, device=device, dtype=dtype) - 0.5) * cell_size * jitter_strength
        pts = (base_grid + jitter) % 1.0
        
        # If n_particles != grid_side^2, adjust size
        if actual_n < n_particles:
            extra = torch.rand(n_particles - actual_n, 2, device=device, dtype=dtype)
            pts = torch.cat([pts, extra], dim=0)
        elif actual_n > n_particles:
            pts = pts[:n_particles]
            
        batch_points.append(pts)
        
    return torch.stack(batch_points, dim=0)


def generate_poisson_disk(batch_size: int, n_particles: int, r_min: float = None, 
                          k_candidates: int = 30, device="cpu", dtype=torch.float32) -> torch.Tensor:
    """
    Bridson's Poisson-Disk Sampling on a periodic torus [0, 1)^2 (Blue Noise).
    Expected spectrum: Low frequency suppression (gamma > 0).
    """
    if r_min is None:
        # Theoretical optimal packing estimate for N particles in [0, 1]^2
        r_min = 0.75 / np.sqrt(n_particles)
        
    batch_points = []
    for _ in range(batch_size):
        # We sample candidate points using rejection on a periodic domain
        points = []
        # Start with random seed point
        points.append(np.random.rand(2))
        active_list = [0]
        
        while len(active_list) > 0 and len(points) < n_particles:
            idx = np.random.choice(len(active_list))
            center = points[active_list[idx]]
            found = False
            
            for _ in range(k_candidates):
                # Sample in annulus [r_min, 2*r_min]
                angle = np.random.uniform(0, 2 * np.pi)
                radius = np.random.uniform(r_min, 2 * r_min)
                candidate = (center + np.array([radius * np.cos(angle), radius * np.sin(angle)])) % 1.0
                
                # Check periodic distance to all accepted points
                if len(points) > 0:
                    pts_arr = np.array(points)
                    delta = np.abs(pts_arr - candidate)
                    delta = np.where(delta > 0.5, 1.0 - delta, delta) # minimum image
                    dist_sq = np.sum(delta**2, axis=-1)
                    if np.all(dist_sq >= r_min**2):
                        points.append(candidate)
                        active_list.append(len(points) - 1)
                        found = True
                        if len(points) >= n_particles:
                            break
            if not found:
                active_list.pop(idx)
                
        # Fill remaining with darts if needed
        max_darts = 5000
        dart_count = 0
        while len(points) < n_particles and dart_count < max_darts:
            cand = np.random.rand(2)
            pts_arr = np.array(points)
            delta = np.abs(pts_arr - cand)
            delta = np.where(delta > 0.5, 1.0 - delta, delta)
            if np.all(np.sum(delta**2, axis=-1) >= (r_min * 0.5)**2):
                points.append(cand)
            dart_count += 1
                
        # Absolute fallback to guarantee exact array size
        while len(points) < n_particles:
            points.append(np.random.rand(2))
                
        batch_points.append(torch.tensor(np.array(points[:n_particles]), device=device, dtype=dtype))
        
    return torch.stack(batch_points, dim=0)


def generate_clustered_red_noise(batch_size: int, n_particles: int, n_clusters: int = 4, 
                                 cluster_std: float = 0.05, device="cpu", dtype=torch.float32) -> torch.Tensor:
    """
    Clustered particle distribution (Red/Pink Noise).
    Expected spectrum: Strong low frequencies decaying at high frequencies (gamma < 0).
    """
    batch_points = []
    for _ in range(batch_size):
        centers = torch.rand(n_clusters, 2, device=device, dtype=dtype)
        cluster_assignments = torch.randint(0, n_clusters, (n_particles,), device=device)
        assigned_centers = centers[cluster_assignments]
        offsets = torch.randn(n_particles, 2, device=device, dtype=dtype) * cluster_std
        pts = (assigned_centers + offsets) % 1.0
        batch_points.append(pts)
    return torch.stack(batch_points, dim=0)


def sample_target_gamma(batch_size: int, gamma_min: float = -2.0, gamma_max: float = 2.0, 
                        device="cpu", dtype=torch.float32) -> torch.Tensor:
    """
    Sample continuous target spectral exponents gamma ~ U[gamma_min, gamma_max].
    Returns: Tensor of shape (batch_size,)
    """
    return torch.empty(batch_size, device=device, dtype=dtype).uniform_(gamma_min, gamma_max)


def generate_regular_grid(batch_size: int, n_particles: int, device="cpu", dtype=torch.float32) -> torch.Tensor:
    """
    Exact regular grid distribution without jitter.
    Highly structured input distribution to test whether the learned energy field can de-crystallize structure.
    """
    grid_side = int(np.round(np.sqrt(n_particles)))
    actual_n = grid_side * grid_side
    coords = (torch.arange(grid_side, device=device, dtype=dtype) + 0.5) / grid_side
    grid_y, grid_x = torch.meshgrid(coords, coords, indexing='ij')
    base_grid = torch.stack([grid_x.flatten(), grid_y.flatten()], dim=-1) # (actual_n, 2)
    
    batch_points = []
    for _ in range(batch_size):
        pts = base_grid.clone()
        if actual_n < n_particles:
            extra = torch.rand(n_particles - actual_n, 2, device=device, dtype=dtype)
            pts = torch.cat([pts, extra], dim=0)
        elif actual_n > n_particles:
            pts = pts[:n_particles]
        batch_points.append(pts)
    return torch.stack(batch_points, dim=0)


def generate_archimedean_spiral(batch_size: int, n_particles: int, turns: float = 4.5,
                                device="cpu", dtype=torch.float32) -> torch.Tensor:
    """
    Archimedean spiral distribution (r = a * theta).
    A challenging non-uniform, highly ordered initial pattern (from EGSR 2026 paper Figure 2).
    """
    batch_points = []
    for _ in range(batch_size):
        theta = torch.linspace(0.5, turns * 2 * np.pi, n_particles, device=device, dtype=dtype)
        # Normalized radius to fit within [0.05, 0.45] centered at (0.5, 0.5)
        r = 0.42 * (theta / (turns * 2 * np.pi))
        x = 0.5 + r * torch.cos(theta)
        y = 0.5 + r * torch.sin(theta)
        pts = torch.stack([x % 1.0, y % 1.0], dim=-1)
        batch_points.append(pts)
    return torch.stack(batch_points, dim=0)


def disambiguate_coincident_points(points, min_separation: float = None, L: float = 1.0, max_iters: int = 5):
    """
    Physical Symmetry-Breaking Disambiguation for Coincident & Overlapping Points.
    
    When two or more points share identical coordinates (r_ij = 0), autograd force evaluates to zero
    by spherical symmetry, and identical external forces keep them locked permanently in lockstep.
    
    This function detects any pair of points within min_separation on the periodic torus [0, L)^2.
    For coincident points (r_ij < 1e-4), it injects an isotropic microscopic displacement along a 
    random angle theta in [0, 2*pi). For near-coincident points, it nudges them apart along their 
    unit separation vector so that learned neural forces can immediately take over.
    
    Args:
        points: (B, N, 2) or (N, 2) torch.Tensor or numpy.ndarray
        min_separation: minimum spatial exclusion distance (defaults to 0.35 / sqrt(N))
        L: periodic box domain size (default 1.0)
        max_iters: maximum relaxation passes for dense multi-point clusters
    Returns:
        Disambiguated points of matching type and shape.
    """
    is_numpy = isinstance(points, np.ndarray)
    if is_numpy:
        pts = torch.from_numpy(points).float()
    else:
        pts = points.clone()
        
    is_unbatched = (pts.dim() == 2)
    if is_unbatched:
        pts = pts.unsqueeze(0)
        
    B, N, _ = pts.shape
    if N <= 1:
        return points
        
    if min_separation is None:
        min_separation = max(0.005, min(0.025, 0.35 / float(np.sqrt(N))))
        
    device = pts.device
    for b in range(B):
        for _ in range(max_iters):
            diff = pts[b].unsqueeze(0) - pts[b].unsqueeze(1) # (N, N, 2)
            diff = diff - L * torch.round(diff / L)
            dist = torch.norm(diff, dim=-1)
            dist.fill_diagonal_(float('inf'))
            
            close_mask = (dist < min_separation)
            triu_close = torch.triu(close_mask, diagonal=1)
            close_indices = triu_close.nonzero(as_tuple=False)
            
            if len(close_indices) == 0:
                break
                
            for idx in range(len(close_indices)):
                i = close_indices[idx][0].item()
                j = close_indices[idx][1].item()
                d_ij = dist[i, j].item()
                if d_ij < 1e-4:
                    theta = float(torch.rand(1).item() * 2.0 * np.pi)
                    u = torch.tensor([np.cos(theta), np.sin(theta)], device=device, dtype=pts.dtype)
                    disp = u * (min_separation * 0.5)
                else:
                    u = diff[i, j] / max(d_ij, 1e-6)
                    push = (min_separation - d_ij) * 0.55
                    disp = u * push
                    
                pts[b, i] = torch.remainder(pts[b, i] + disp, L)
                pts[b, j] = torch.remainder(pts[b, j] - disp, L)

    res = pts.squeeze(0) if is_unbatched else pts
    return res.cpu().numpy() if is_numpy else res

