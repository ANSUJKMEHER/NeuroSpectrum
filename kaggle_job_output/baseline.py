"""
baseline.py — Classical per-target optimizer (Section 6.3 Baseline).
Directly optimizes raw particle positions X against the differentiable spectral loss for a fixed target gamma.
Used to benchmark:
1. Final spectral and spatial quality at matched gamma
2. Wall-clock optimization time per new target (Classical pays per-target, Neural model infers instantly)
"""

import time
import torch
import numpy as np
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from losses import CompositeSpectralLoss


class ClassicalPerTargetOptimizer:
    """
    Optimizes raw particle positions X directly against the differentiable spectral loss for a fixed target gamma.
    """
    def __init__(self, grid_size: int = 64, sigma: float = 0.02, 
                 lambda_spec: float = 1.0, lambda_gamma: float = 2.0, lambda_spacing: float = 0.5,
                 n_particles: int = 256):
        self.rasterizer = PeriodicGaussianSplat2D(grid_size=grid_size, sigma=sigma)
        self.analyzer = DifferentiableSpectralAnalyzer(
            grid_size=grid_size,
            sigma=sigma,
            f_min=2,
            f_max=24,
            deconvolve_gaussian=True
        )
        self.loss_fn = CompositeSpectralLoss(
            lambda_spec=lambda_spec,
            lambda_gamma=lambda_gamma,
            lambda_spacing=lambda_spacing,
            n_particles=n_particles
        )

    def optimize(self, points_init: torch.Tensor, target_gamma_val: float, 
                 num_iters: int = 150, lr: float = 0.01) -> dict:
        """
        Run per-target optimization on raw coordinates.
        Args:
            points_init: (1, N, 2) or (N, 2)
            target_gamma_val: float scalar
        Returns:
            dict containing optimized points, wall-clock time, final loss, and spectral properties
        """
        if points_init.dim() == 2:
            points = points_init.clone().unsqueeze(0)
        else:
            points = points_init.clone()
            
        points = points.detach().requires_grad_(True)
        target_gamma = torch.tensor([target_gamma_val], dtype=points.dtype, device=points.device)
        
        optimizer = torch.optim.Adam([points], lr=lr)
        
        start_time = time.perf_counter()
        loss_history = []
        
        for iteration in range(num_iters):
            optimizer.zero_grad()
            
            # Periodic wrapping
            points_wrapped = points % 1.0
            density = self.rasterizer(points_wrapped)
            spectral_out = self.analyzer(density)
            loss_dict = self.loss_fn(points_wrapped, target_gamma, spectral_out)
            
            loss = loss_dict['loss']
            loss.backward()
            optimizer.step()
            
            # Keep in [0, 1)
            with torch.no_grad():
                points.data = points.data % 1.0
                
            loss_history.append(loss.item())
            
        elapsed_time = time.perf_counter() - start_time
        
        # Final evaluation
        with torch.no_grad():
            points_final = points % 1.0
            density = self.rasterizer(points_final)
            spectral_out = self.analyzer(density)
            final_loss = self.loss_fn(points_final, target_gamma, spectral_out)
            
        return {
            'points_final': points_final.squeeze(0).cpu().numpy(),
            'wall_clock_time': elapsed_time,
            'measured_gamma': spectral_out['gamma'].item(),
            'target_gamma': target_gamma_val,
            'final_loss': final_loss['loss'].item(),
            'psd_2d': spectral_out['psd_2d'].squeeze(0).cpu().numpy(),
            'radial_psd': spectral_out['radial_psd'].squeeze(0).cpu().numpy(),
            'freqs': spectral_out['freqs'].cpu().numpy(),
            'loss_history': loss_history
        }
