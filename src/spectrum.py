"""
spectrum.py - Differentiable FFT, PSD Analyzer, Radial Averaging, and Spectral Slope Estimation.
Implements Stage 3 of the research proposal:
- Differentiable 2D FFT and shifted Power Spectral Density (PSD)
- Precomputed linear projection matrix for fast, batch-differentiable radial averaging
- Frequency band selection (excluding DC component and respecting Nyquist cutoff)
- Differentiable log-log linear regression for estimating spectral slope gamma
- Support for both raw PSD and Gaussian-filter compensated PSD
"""

import torch
import torch.nn as nn
import numpy as np


class DifferentiableSpectralAnalyzer(nn.Module):
    """
    Differentiable module that computes 2D PSD, radial average, and spectral slope gamma.
    """
    def __init__(self, grid_size: int = 64, sigma: float = 0.02, 
                 f_min: int = 2, f_max: int = None, deconvolve_gaussian: bool = True):
        super().__init__()
        self.grid_size = grid_size
        self.sigma = sigma
        self.f_min = f_min
        self.nyquist = grid_size // 2
        self.f_max = f_max if f_max is not None else int(self.nyquist * 0.75) # conservative cutoff
        self.deconvolve_gaussian = deconvolve_gaussian
        
        # Build radial binning linear operator matrix: (num_bins, M * M)
        center = grid_size // 2
        # Frequency coordinates in cycles per unit box [-M/2, M/2 - 1]
        freq_y, freq_x = torch.meshgrid(
            torch.arange(-center, center, dtype=torch.float32),
            torch.arange(-center, center, dtype=torch.float32),
            indexing='ij'
        )
        
        radial_dist = torch.sqrt(freq_x ** 2 + freq_y ** 2) # shape: (M, M)
        max_bin = int(radial_dist.max().item()) + 1
        
        # Radial projection matrix W of shape (max_bin, M * M)
        W = torch.zeros(max_bin, grid_size * grid_size, dtype=torch.float32)
        radial_dist_flat = torch.round(radial_dist).long().flatten()
        
        for r_bin in range(max_bin):
            mask = (radial_dist_flat == r_bin)
            count = mask.sum().item()
            if count > 0:
                W[r_bin, mask] = 1.0 / count
                
        self.register_buffer('W_radial', W)
        self.register_buffer('freq_grid', radial_dist)
        
        # Precompute Gaussian Fourier attenuation profile if deconvolution is enabled
        # G(f) = exp(-2 * pi^2 * sigma^2 * f^2) -> PSD attenuation = G(f)^2 = exp(-4 * pi^2 * sigma^2 * f^2)
        f_bins = torch.arange(max_bin, dtype=torch.float32)
        gaussian_psd_attenuation = torch.exp(-4.0 * (np.pi ** 2) * (sigma ** 2) * (f_bins ** 2))
        self.register_buffer('gaussian_filter_psd', gaussian_psd_attenuation)
        self.register_buffer('f_bins', f_bins)

    def compute_psd2d(self, density: torch.Tensor) -> torch.Tensor:
        """
        Compute 2D Power Spectral Density from rasterized density grid.
        Args:
            density: (B, M, M) or (M, M)
        Returns:
            psd_2d: (B, M, M) or (M, M)
        """
        # FFT over spatial dimensions
        fft_res = torch.fft.fft2(density, dim=(-2, -1))
        fft_shifted = torch.fft.fftshift(fft_res, dim=(-2, -1))
        psd_2d = torch.abs(fft_shifted) ** 2
        
        # Normalize by grid area squared
        psd_2d = psd_2d / (self.grid_size ** 4)
        return psd_2d

    def compute_radial_psd(self, psd_2d: torch.Tensor) -> torch.Tensor:
        """
        Compute radial average of 2D PSD using linear projection matrix.
        Args:
            psd_2d: (B, M, M) or (M, M)
        Returns:
            radial_psd: (B, num_bins) or (num_bins,)
        """
        is_unbatched = (psd_2d.dim() == 2)
        if is_unbatched:
            psd_2d = psd_2d.unsqueeze(0)
            
        B, M, _ = psd_2d.shape
        W = self.W_radial.to(dtype=psd_2d.dtype, device=psd_2d.device)
        
        # Flatten spatial dims: (B, M * M)
        psd_flat = psd_2d.reshape(B, M * M)
        
        # Matrix multiplication: (B, M*M) @ (M*M, num_bins) -> (B, num_bins)
        radial_psd = torch.matmul(psd_flat, W.t())
        
        if self.deconvolve_gaussian:
            # Divide by Gaussian blur power spectrum to recover true point-distribution PSD
            gauss_filter = self.gaussian_filter_psd.to(dtype=psd_2d.dtype, device=psd_2d.device)
            # Add numerical stabilizer & clamp gain to prevent division by near-zero at ultra-high frequencies (EC-I4)
            deconv_gain = torch.clamp(1.0 / (gauss_filter.unsqueeze(0) + 1e-12), max=100.0)
            radial_psd = radial_psd * deconv_gain
            
        if is_unbatched:
            radial_psd = radial_psd.squeeze(0)
            
        return radial_psd

    def estimate_gamma(self, radial_psd: torch.Tensor) -> torch.Tensor:
        """
        Fit spectral slope gamma over the valid frequency band [f_min, f_max] in log-log space.
        Args:
            radial_psd: (B, num_bins) or (num_bins,)
        Returns:
            gamma: (B,) or scalar tensor
        """
        is_unbatched = (radial_psd.dim() == 1)
        if is_unbatched:
            radial_psd = radial_psd.unsqueeze(0)
            
        device = radial_psd.device
        dtype = radial_psd.dtype
        
        # Select valid frequency range (excluding DC f=0)
        freqs = self.f_bins[self.f_min:self.f_max].to(device=device, dtype=dtype)
        psd_band = radial_psd[:, self.f_min:self.f_max]
        
        log_f = torch.log(freqs) # (K_band,)
        log_p = torch.log(psd_band + 1e-12) # (B, K_band)
        
        # Linear regression: log_p = gamma * log_f + C
        f_mean = log_f.mean()
        p_mean = log_p.mean(dim=-1, keepdim=True)
        
        f_diff = log_f - f_mean # (K_band,)
        p_diff = log_p - p_mean # (B, K_band)
        
        numerator = torch.sum(p_diff * f_diff.unsqueeze(0), dim=-1) # (B,)
        denominator = torch.sum(f_diff ** 2) # scalar
        
        gamma = numerator / (denominator + 1e-12)
        
        if is_unbatched:
            gamma = gamma.squeeze(0)
            
        return gamma

    def forward(self, density: torch.Tensor):
        """
        Full spectral analysis pipeline.
        Returns:
            dict containing psd_2d, radial_psd, gamma, and frequencies
        """
        psd_2d = self.compute_psd2d(density)
        radial_psd = self.compute_radial_psd(psd_2d)
        gamma = self.estimate_gamma(radial_psd)
        
        return {
            'psd_2d': psd_2d,
            'radial_psd': radial_psd,
            'gamma': gamma,
            'freqs': self.f_bins.to(device=density.device, dtype=density.dtype)
        }


def compute_continuous_point_spectrum(points: np.ndarray, grid_res: int = 128):
    """
    Computes exact continuous 2D Power Spectral Density and 1D Radial Spectrum
    directly from point coordinates via continuous point Fourier transform:
    F(k) = (1 / sqrt(N)) * sum_j exp(-2*pi*i * k . x_j), P(k) = |F(k)|^2
    """
    N = len(points)
    half = grid_res // 2
    kx = np.arange(-half, half)
    ky = np.arange(-half, half)
    KX, KY = np.meshgrid(kx, ky)

    phases = 2.0 * np.pi * (
        KX[:, :, None] * points[None, None, :, 0] +
        KY[:, :, None] * points[None, None, :, 1]
    )
    cos_sum = np.sum(np.cos(phases), axis=-1)
    sin_sum = np.sum(np.sin(phases), axis=-1)
    psd_2d = (cos_sum ** 2 + sin_sum ** 2) / max(1, N)
    psd_2d[half, half] = 0.0

    rad_dist = np.sqrt(KX ** 2 + KY ** 2)
    max_r = half - 1
    bins = np.arange(1, max_r + 1)
    radial_p = []

    for r in bins:
        mask = (rad_dist >= r - 0.5) & (rad_dist < r + 0.5)
        if np.any(mask):
            radial_p.append(float(np.mean(psd_2d[mask])))
        else:
            radial_p.append(0.0)

    return psd_2d, [int(b) for b in bins], radial_p

import torch.nn.functional as F

class DifferentiableWPSD(nn.Module):
    def __init__(self, window_size=64, stride=32, fallback_global=True):
        super().__init__()
        self.window_size = window_size
        self.stride = stride
        self.fallback_global = fallback_global
        
        # 2D Hann window mitigates edge artifacts in local patches
        window_1d = torch.hann_window(window_size)
        self.register_buffer('window_2d', window_1d.unsqueeze(1) * window_1d.unsqueeze(0))
        
    def forward(self, point_density, gamma_field, global_psd_analyzer=None):
        """
        point_density: [B, 1, H, W] - Soft rasterized density grid
        gamma_field: [B, 1, H, W] - User-painted 2D gamma scalar field
        """
        B, C, H, W = point_density.shape
        
        # Backward compatibility: Fallback to global FFT if field is uniform
        if self.fallback_global and gamma_field.max() == gamma_field.min():
            if global_psd_analyzer is not None:
                # Bypass windowing, fallback to normal global FFT
                return None
            else:
                pass # Use windowing anyway

        # 1. Extract overlapping spatial patches using sliding windows
        patches = F.unfold(point_density, kernel_size=self.window_size, stride=self.stride)
        g_patches = F.unfold(gamma_field, kernel_size=self.window_size, stride=self.stride)
        
        # [B, C*W*W, NumPatches] -> [B, NumPatches, C, W, W]
        patches = patches.view(B, C, self.window_size, self.window_size, -1).permute(0, 4, 1, 2, 3)
        g_patches = g_patches.view(B, C, self.window_size, self.window_size, -1).permute(0, 4, 1, 2, 3)
        
        # 2. Apply Windowing Function
        patches = patches * self.window_2d.unsqueeze(0).unsqueeze(0).unsqueeze(0)
        
        # 3. Compute Local 2D FFT per patch
        fft_patches = torch.fft.fft2(patches, dim=(-2, -1))
        power_spectrum = torch.fft.fftshift(torch.abs(fft_patches)**2, dim=(-2, -1))
        
        # 4. Average the gamma target for each patch
        mean_gamma = g_patches.mean(dim=(-2, -1)).squeeze(-1) # [B, num_patches]
        
        return power_spectrum, mean_gamma
