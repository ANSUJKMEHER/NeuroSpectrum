"""
losses.py - Multi-Objective Differentiable Loss Module (Phase 6).
Implements Stage 7 and Section 6 of the research proposal:
- Spectral Loss: L_spec = MSE(log P_theta(f), log P*(f))
- Slope Loss: L_gamma = (gamma_hat - gamma)^2
- Spacing Regularity Loss: L_spacing = CV_NND (prevents sub-kernel particle collapse)
- Anisotropy Loss: Angular variance penalty across radial frequency rings
- Unified Loss Engine returning total loss and breakdown metrics
"""

import torch
import torch.nn as nn
import numpy as np


class CompositeSpectralLoss(nn.Module):
    """
    Composite differentiable loss for training neural spectral point dynamics.
    L_total = lambda_s * L_spec + lambda_gamma * L_gamma + lambda_q * L_spacing + lambda_a * L_aniso
    """
    def __init__(self, lambda_spec: float = 1.0, lambda_gamma: float = 2.0, 
                 lambda_spacing: float = 0.5, lambda_anisotropy: float = 0.0,
                 f_min: int = 2, f_max: int = 24, box_size: float = 1.0,
                 n_particles: int = 256, ensemble_psd: bool = False):
        super().__init__()
        self.ensemble_psd = ensemble_psd
        self.lambda_spec = lambda_spec
        self.lambda_gamma = lambda_gamma
        self.lambda_spacing = lambda_spacing
        self.lambda_anisotropy = lambda_anisotropy
        self.f_min = f_min
        self.f_max = f_max
        self.L = box_size
        self.n_particles = n_particles
        # Theoretical expected nearest neighbor spacing for N particles in unit area
        self.target_spacing = 0.70 / np.sqrt(n_particles)

    def compute_spacing_loss(self, points: torch.Tensor) -> tuple:
        """
        Compute robust spacing regularity loss preventing both irregular spacing AND sub-kernel collapse.
        Combines:
        1. Relative regularity: CV_NND = sigma / mu (penalizes uneven spacing)
        2. Absolute scale penalty: ReLU(1 - mu / target_spacing)^2 (penalizes paired/clustered collapse)
        Args:
            points: (B, N, 2) in [0, L)
        Returns:
            l_spacing: (B,) composite spacing loss
            cv_nnd: (B,) coefficient of variation
            mu_nnd: (B,) mean nearest neighbor distance
        """
        B, N, _ = points.shape
        if N <= 1:
            z = torch.zeros(B, device=points.device, dtype=points.dtype)
            return z, z, z
        
        if N > 512:
            # Memory-efficient chunked computation for large particle counts (N=1024, 2048, 4096)
            chunk_size = 512
            nnd_list = []
            for i in range(0, N, chunk_size):
                p_c = points[:, i:i+chunk_size, :]
                n_c = p_c.shape[1]
                diff_c = p_c.unsqueeze(2) - points.unsqueeze(1) # (B, n_c, N, 2)
                diff_c = diff_c - self.L * torch.round(diff_c / self.L)
                dist_sq_c = (diff_c ** 2).sum(dim=-1)
                dist_c = torch.sqrt(dist_sq_c + 1e-12)
                
                # Mask self-distance in chunk
                idx_c = torch.arange(n_c, device=points.device)
                dist_c[:, idx_c, i + idx_c] = 1e6
                nnd_c, _ = torch.min(dist_c, dim=-1)
                nnd_list.append(nnd_c)
            nnd = torch.cat(nnd_list, dim=-1)
        else:
            # 1. Periodic minimum-image pairwise differences: (B, N, N, 2)
            diff = points.unsqueeze(2) - points.unsqueeze(1)
            diff = diff - self.L * torch.round(diff / self.L)
            
            # 2. Pairwise distances: (B, N, N)
            dist_sq = (diff ** 2).sum(dim=-1)
            dist = torch.sqrt(dist_sq + 1e-12)
            
            # 3. Mask diagonal (self-distance == 0) with infinity
            diag_mask = torch.eye(N, device=points.device, dtype=torch.bool).unsqueeze(0).expand(B, N, N)
            dist_masked = dist.masked_fill(diag_mask, 1e6)
            
            # 4. Nearest neighbor distance for each particle: (B, N)
            nnd, _ = torch.min(dist_masked, dim=-1)
        
        # 5. Compute mean and standard deviation across particles
        mu_nnd = torch.mean(nnd, dim=-1) # (B,)
        var_nnd = torch.var(nnd, dim=-1, unbiased=False) # (B,)
        sigma_nnd = torch.sqrt(var_nnd + 1e-12)
        
        # 6. CV = sigma / mu (relative regularity)
        cv_nnd = sigma_nnd / (mu_nnd + 1e-12)
        
        # 7. Absolute scale penalty: severely penalize if mean spacing drops below target
        # Dynamically adapts to particle count N (e.g. 256, 512, 1024, 4096)
        target_r = 0.70 * self.L / np.sqrt(N)
        collapse_penalty = torch.relu(1.0 - (mu_nnd / target_r)) ** 2
        
        l_spacing = cv_nnd + 2.0 * collapse_penalty
        return l_spacing, cv_nnd, mu_nnd

    def compute_spectral_loss(self, radial_psd: torch.Tensor, target_gamma: torch.Tensor, 
                              freqs: torch.Tensor) -> torch.Tensor:
        """
        Compute log-PSD MSE loss against ideal power-law target P*(f) = C * f^gamma.
        Args:
            radial_psd: (B, num_bins)
            target_gamma: (B,)
            freqs: (num_bins,)
        Returns:
            l_spec: (B,)
        """
        B = radial_psd.shape[0]
        # Valid frequency window [f_min, f_max]
        f_band = freqs[self.f_min:self.f_max].to(radial_psd.device)
        psd_band = radial_psd[:, self.f_min:self.f_max]
        
        log_f = torch.log(f_band).unsqueeze(0) # (1, K)
        log_p_measured = torch.log(psd_band + 1e-12) # (B, K)
        
        # Target log-PSD: log P*(f) = gamma * log(f) + offset
        # Match mean log-power to align scaling constant C
        target_slope = target_gamma.view(B, 1) # (B, 1)
        log_p_target_unnormalized = target_slope * log_f # (B, K)
        
        # Align target baseline to measured baseline for scale-invariant shape matching
        mean_measured = log_p_measured.mean(dim=-1, keepdim=True)
        mean_target = log_p_target_unnormalized.mean(dim=-1, keepdim=True)
        log_p_target = log_p_target_unnormalized - mean_target + mean_measured
        
        # MSE in log-power domain
        l_spec = torch.mean((log_p_measured - log_p_target) ** 2, dim=-1) # (B,)
        return l_spec

    def compute_anisotropy_loss(self, psd_2d: torch.Tensor) -> torch.Tensor:
        """
        Compute angular variance penalty for isotropic point distributions.
        Penalizes direction-dependent energy variations within frequency rings.
        """
        # We can penalize spatial asymmetry if lambda_anisotropy > 0
        B, M, _ = psd_2d.shape
        # Horizontal vs vertical symmetry difference
        diff_h_v = torch.mean(torch.abs(psd_2d - psd_2d.transpose(-2, -1)), dim=(-2, -1))
        return diff_h_v

    def forward(self, points: torch.Tensor, target_gamma: torch.Tensor, 
                spectral_out: dict) -> dict:
        """
        Evaluate full multi-objective loss.
        Args:
            points: (B, N, 2) final particle coordinates
            target_gamma: (B,) requested target slopes
            spectral_out: dict from DifferentiableSpectralAnalyzer (psd_2d, radial_psd, gamma, freqs)
        Returns:
            dict containing total_loss and individual loss terms
        """
        if points.dim() == 2:
            points = points.unsqueeze(0)
            
        if not isinstance(target_gamma, torch.Tensor):
            target_gamma = torch.tensor([target_gamma], device=points.device, dtype=points.dtype)
        elif target_gamma.dim() == 0:
            target_gamma = target_gamma.unsqueeze(0)
            
        B = points.shape[0]
        
        radial_psd = spectral_out['radial_psd']
        measured_gamma = spectral_out['gamma']
        freqs = spectral_out['freqs']
        psd_2d = spectral_out['psd_2d']
        
        if radial_psd.dim() == 1:
            radial_psd = radial_psd.unsqueeze(0)
            measured_gamma = measured_gamma.unsqueeze(0)
            psd_2d = psd_2d.unsqueeze(0)
            
        # 1. Spectral Shape Loss
        l_spec = self.compute_spectral_loss(radial_psd, target_gamma, freqs) # (B,)
        
        # 2. Spectral Slope Regression Loss
        l_gamma = (measured_gamma - target_gamma) ** 2 # (B,)
        
        # 3. Spacing Regularity & Anti-Collapse Loss
        l_spacing, cv_nnd, mu_nnd = self.compute_spacing_loss(points) # (B,)
        
        # 4. Optional Anisotropy Loss
        l_aniso = self.compute_anisotropy_loss(psd_2d) if self.lambda_anisotropy > 0 else torch.zeros_like(l_spec)
        
        # Total composite loss
        total_loss = (
            self.lambda_spec * l_spec +
            self.lambda_gamma * l_gamma +
            self.lambda_spacing * l_spacing +
            self.lambda_anisotropy * l_aniso
        ).mean()
        
        return {
            'loss': total_loss,
            'l_spec': l_spec.mean().item(),
            'l_gamma': l_gamma.mean().item(),
            'l_spacing': l_spacing.mean().item(),
            'l_aniso': l_aniso.mean().item(),
            'cv_nnd': cv_nnd.mean().item(),
            'mu_nnd': mu_nnd.mean().item(),
            'measured_gamma': measured_gamma.mean().item(),
            'target_gamma': target_gamma.mean().item()
        }
