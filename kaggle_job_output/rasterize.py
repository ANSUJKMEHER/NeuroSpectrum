"""
rasterize.py - Differentiable Particle-to-Grid Rasterization.
Implements Stage 2 / Stage 3 soft Gaussian splatting with:
- Exact periodic boundary handling (Toroidal minimum-image convention)
- Proper pixel cell-center coordinates x_k = (k + 0.5) / M
- Full batch dimension support (B, N, 2) -> (B, M, M)
- Analytical differentiability w.r.t particle positions
"""

import torch
import torch.nn as nn
import numpy as np


class PeriodicGaussianSplat2D(nn.Module):
    """
    Differentiable Gaussian splatting module on a 2D periodic unit torus [0, 1)^2.
    """
    def __init__(self, grid_size: int = 64, sigma: float = 0.02):
        super().__init__()
        self.grid_size = grid_size
        self.sigma = sigma
        
        # Precompute cell centers in [0, 1): x_k = (k + 0.5) / M
        coords = (torch.arange(grid_size, dtype=torch.float32) + 0.5) / grid_size
        grid_y, grid_x = torch.meshgrid(coords, coords, indexing='ij')
        
        # Register as buffers (shape: 1, 1, M, M)
        self.register_buffer('grid_x', grid_x.unsqueeze(0).unsqueeze(0))
        self.register_buffer('grid_y', grid_y.unsqueeze(0).unsqueeze(0))

    def forward(self, points: torch.Tensor) -> torch.Tensor:
        """
        Args:
            points: Tensor of shape (B, N, 2) or (N, 2) in range [0, 1).
        Returns:
            density: Tensor of shape (B, M, M) or (M, M).
        """
        if isinstance(points, np.ndarray):
            points = torch.from_numpy(points).float()
            
        is_unbatched = (points.dim() == 2)
        if is_unbatched:
            points = points.unsqueeze(0) # (1, N, 2)
            
        B, N, _ = points.shape
        device = points.device
        dtype = points.dtype
        
        # Grid coordinates match device & dtype
        gx = self.grid_x.to(device=device, dtype=dtype)
        gy = self.grid_y.to(device=device, dtype=dtype)
        
        norm_factor = 1.0 / (2.0 * np.pi * (self.sigma ** 2))
        
        # For large N (e.g. 1024, 2048, 4096), chunk splatting to maintain tiny RAM footprint (<50MB)
        if N > 512:
            chunk_size = 512
            density = torch.zeros(B, self.grid_size, self.grid_size, device=device, dtype=dtype)
            for i in range(0, N, chunk_size):
                p_c = points[:, i:i+chunk_size, :]
                n_c = p_c.shape[1]
                px_c = p_c[:, :, 0].reshape(B, n_c, 1, 1)
                py_c = p_c[:, :, 1].reshape(B, n_c, 1, 1)
                dx_c = gx - px_c
                dy_c = gy - py_c
                dx_c = dx_c - torch.round(dx_c)
                dy_c = dy_c - torch.round(dy_c)
                dist_c = dx_c**2 + dy_c**2
                k_c = norm_factor * torch.exp(-dist_c / (2.0 * (self.sigma ** 2)))
                density = density + k_c.sum(dim=1)
        else:
            # Extract particle coordinates: (B, N, 1, 1)
            px = points[:, :, 0].reshape(B, N, 1, 1)
            py = points[:, :, 1].reshape(B, N, 1, 1)
            dx = gx - px
            dy = gy - py
            dx = dx - torch.round(dx)
            dy = dy - torch.round(dy)
            dist_sq = dx**2 + dy**2
            kernel = norm_factor * torch.exp(-dist_sq / (2.0 * (self.sigma ** 2)))
            density = kernel.sum(dim=1)
        
        if is_unbatched:
            density = density.squeeze(0)
            
        return density


def gaussian_splat(points: torch.Tensor, grid_size: int = 64, sigma: float = 0.02) -> torch.Tensor:
    """
    Functional interface for periodic Gaussian splatting.
    """
    splatter = PeriodicGaussianSplat2D(grid_size=grid_size, sigma=sigma)
    return splatter(points)
