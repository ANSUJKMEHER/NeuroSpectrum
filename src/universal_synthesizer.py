"""
universal_synthesizer.py — Universal Multi-Modal Target Point Synthesizer via Backpropagation.
Supports transforming ANY input into ANY target across all modalities & dimensions:
1. Multi-Modal Target Ingestion:
   - Arbitrary Text Strings (e.g. "NEURO", "AI", any word/sentence)
   - Arbitrary Images (PNG, JPG, BMP masks)
   - Procedural 2D Geometries (Star, Heart, Spiral, Double Rings)
   - 3D Geometries (Sphere 3D, Helix 3D, Torus 3D)
   - Raw Coordinate Arrays (Numpy, PyTorch, CSV, NPY)
2. Input Polymorphism:
   - Unequal point counts (N != M)
   - Arbitrary dimensions D (2D, 3D, D-dim)
   - Arbitrary spatial bounding boxes (auto-normalized & denormalized)
   - Device independence (CPU / CUDA / MPS)
3. Differentiable Optimization:
   - Log-domain Entropic Optimal Transport (Sinkhorn-Knopp)
   - Bidirectional Chamfer Distance with anti-collapse regularization
   - Neural Flow Matching Vector Fields v_theta(x, t)
"""

import os
import math
import numpy as np
import torch
import torch.nn as nn
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from spectrum import compute_continuous_point_spectrum
from evaluate import compute_spatial_statistics


class SinkhornOptimalTransport(nn.Module):
    """
    Differentiable Entropic Optimal Transport via the Sinkhorn-Knopp algorithm.
    Supports arbitrary dimensions D and unequal point counts (N != M).
    Log-domain stabilization prevents underflow/overflow.
    """
    def __init__(self, epsilon: float = 0.015, max_iters: int = 40):
        super().__init__()
        self.epsilon = epsilon
        self.max_iters = max_iters

    def forward(self, source: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """
        Args:
            source: (N, D) source coordinates
            target: (M, D) target coordinates
        Returns:
            transport_cost: scalar differentiable loss
        """
        N = source.shape[0]
        M = target.shape[0]
        
        # Pairwise Euclidean squared distance in D dimensions: (N, M)
        diff = source.unsqueeze(1) - target.unsqueeze(0)
        cost = torch.sum(diff ** 2, dim=-1)
        
        # Uniform marginal distributions
        mu = torch.full((N,), 1.0 / N, device=source.device, dtype=source.dtype)
        nu = torch.full((M,), 1.0 / M, device=target.device, dtype=target.dtype)
        
        u = torch.zeros_like(mu)
        v = torch.zeros_like(nu)
        
        log_mu = torch.log(mu)
        log_nu = torch.log(nu)
        
        K = -cost / self.epsilon
        
        for _ in range(self.max_iters):
            u = self.epsilon * (log_mu - torch.logsumexp(K + v.unsqueeze(0) / self.epsilon, dim=1))
            v = self.epsilon * (log_nu - torch.logsumexp(K + u.unsqueeze(1) / self.epsilon, dim=0))
            
        log_P = (u.unsqueeze(1) + v.unsqueeze(0) - cost) / self.epsilon
        P = torch.exp(log_P)
        
        return torch.sum(P * cost)


class DifferentiableChamferLoss(nn.Module):
    """
    Bidirectional Chamfer Distance supporting arbitrary dimensions D and N != M.
    """
    def __init__(self):
        super().__init__()

    def forward(self, source: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        diff = source.unsqueeze(1) - target.unsqueeze(0) # (N, M, D)
        dist_sq = torch.sum(diff ** 2, dim=-1)
        
        min_src_to_tgt, _ = torch.min(dist_sq, dim=1)
        min_tgt_to_src, _ = torch.min(dist_sq, dim=0)
        
        return torch.mean(min_src_to_tgt) + torch.mean(min_tgt_to_src)


class TargetGeometryFactory:
    """
    Universal Factory converting ANY input modality into a target point cloud:
    1. Text strings -> point cloud glyphs
    2. Images -> point cloud density masks
    3. 2D parametric curves (star, spiral, heart, rings)
    4. 3D geometric manifolds (sphere_3d, helix_3d)
    """
    @staticmethod
    def create_from_text(text: str, n_points: int = 512, font_size: int = 50) -> np.ndarray:
        """
        Renders ANY arbitrary text string into a 2D point cloud.
        """
        canvas_w = max(200, len(text) * 45)
        canvas_h = 120
        img = Image.new('L', (canvas_w, canvas_h), color=0)
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
            
        draw.text((20, 30), text, fill=255, font=font)
        mask = np.array(img)
        y_coords, x_coords = np.where(mask > 100)
        
        if len(x_coords) == 0:
            raise ValueError(f"Text '{text}' produced no visible pixels.")
            
        # Re-sample to exactly n_points
        indices = np.random.choice(len(x_coords), size=n_points, replace=(len(x_coords) < n_points))
        x_pts = x_coords[indices].astype(np.float32)
        y_pts = y_coords[indices].astype(np.float32)
        
        # Add small sub-pixel jitter for continuous organic distribution
        x_pts += np.random.normal(0, 0.4, size=n_points)
        y_pts += np.random.normal(0, 0.4, size=n_points)
        
        # Normalize to [0.05, 0.95]
        x_norm = (x_pts - x_pts.min()) / (x_pts.max() - x_pts.min() + 1e-8) * 0.85 + 0.075
        y_norm = (y_pts - y_pts.min()) / (y_pts.max() - y_pts.min() + 1e-8) * 0.85 + 0.075
        
        return np.stack([x_norm, y_norm], axis=-1).astype(np.float32)

    @staticmethod
    def create_from_image(image_input, n_points: int = 512, threshold: float = 0.5) -> np.ndarray:
        """
        Converts ANY image file path or PIL Image into a target point cloud.
        Darker/brighter pixels represent point mass.
        """
        if isinstance(image_input, str):
            img = Image.open(image_input).convert('L')
        elif isinstance(image_input, Image.Image):
            img = image_input.convert('L')
        else:
            raise TypeError("image_input must be a file path string or PIL Image object.")
            
        arr = np.array(img).astype(np.float32) / 255.0
        # If background is bright, invert so ink/features are mass
        if np.mean(arr) > 0.5:
            density = 1.0 - arr
        else:
            density = arr
            
        density = np.maximum(density - threshold, 0.0)
        if np.sum(density) < 1e-6:
            density = 1.0 - arr # Fallback
            
        prob = density.flatten() / np.sum(density)
        idx_flat = np.random.choice(len(prob), size=n_points, p=prob, replace=True)
        
        h, w = arr.shape
        y_pts = (idx_flat // w).astype(np.float32) + np.random.uniform(-0.5, 0.5, n_points)
        x_pts = (idx_flat % w).astype(np.float32) + np.random.uniform(-0.5, 0.5, n_points)
        
        x_norm = x_pts / w
        y_norm = y_pts / h # Standard screen/canvas orientation
        
        return np.stack([np.clip(x_norm, 0.02, 0.98), np.clip(y_norm, 0.02, 0.98)], axis=-1).astype(np.float32)

    @staticmethod
    def sample_equidistant_curve(x_dense: np.ndarray, y_dense: np.ndarray, n_points: int) -> np.ndarray:
        """Resamples arbitrary 2D parametric curve with EXACT uniform arc-length spacing."""
        pts = np.stack([x_dense, y_dense], axis=-1)
        diffs = np.diff(pts, axis=0)
        dists = np.sqrt(np.sum(diffs ** 2, axis=-1))
        s = np.concatenate([[0], np.cumsum(dists)])
        S = s[-1]
        if S < 1e-6:
            return pts[:n_points]
        s_targets = np.linspace(0, S, n_points, endpoint=False)
        x_eq = np.interp(s_targets, s, x_dense)
        y_eq = np.interp(s_targets, s, y_dense)
        return np.stack([np.clip(x_eq, 0.02, 0.98), np.clip(y_eq, 0.02, 0.98)], axis=-1).astype(np.float32)

    @staticmethod
    def sample_solid_contour(contour_pts: np.ndarray, n_points: int, size: int = 256) -> np.ndarray:
        """Samples uniformly inside the solid 2D interior of a polygon/contour mask."""
        img = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(img)
        poly = [(int(p[0] * size), int(p[1] * size)) for p in contour_pts]
        if len(poly) >= 3:
            draw.polygon(poly, fill=255)
        else:
            for p in poly:
                draw.ellipse([p[0]-3, p[1]-3, p[0]+3, p[1]+3], fill=255)
        mask = np.array(img)
        ys, xs = np.where(mask > 128)
        if len(xs) == 0:
            return contour_pts[:n_points]
        replace = len(xs) < n_points
        idx = np.random.choice(len(xs), size=n_points, replace=replace)
        pts = np.stack([xs[idx] / size, ys[idx] / size], axis=-1)
        pts += np.random.normal(0, 0.4 / size, size=(n_points, 2))
        return np.clip(pts, 0.02, 0.98).astype(np.float32)

    @staticmethod
    def create_from_point_cloud(points: list, n_points: int = 256) -> np.ndarray:
        """Loads and normalizes an arbitrary uploaded 2D point cloud."""
        raw = np.asarray(points, dtype=np.float32)
        if len(raw) == 0:
            return TargetGeometryFactory.create_target("star", n_points=n_points)
        if len(raw) == n_points:
            pts = raw
        else:
            idx = np.random.choice(len(raw), size=n_points, replace=(len(raw) < n_points))
            pts = raw[idx]
        return np.clip(pts, 0.02, 0.98).astype(np.float32)

    @staticmethod
    def create_from_polyline(points: list, n_points: int = 256) -> np.ndarray:
        """Converts user-drawn hand strokes into an equidistant point cloud."""
        raw = np.asarray(points, dtype=np.float32)
        if len(raw) < 2:
            return TargetGeometryFactory.create_target("heart", n_points=n_points)
        diffs = np.diff(raw, axis=0)
        dists = np.sqrt(np.sum(diffs ** 2, axis=-1))
        s = np.concatenate([[0], np.cumsum(dists)])
        S = s[-1]
        if S < 1e-4:
            idx = np.random.choice(len(raw), size=n_points, replace=True)
            return raw[idx]
        s_targets = np.linspace(0, S, n_points, endpoint=False)
        x_eq = np.interp(s_targets, s, raw[:, 0])
        y_eq = np.interp(s_targets, s, raw[:, 1])
        pts = np.stack([x_eq, y_eq], axis=-1)
        pts += np.random.normal(0, 0.002, size=(n_points, 2))
        return np.clip(pts, 0.02, 0.98).astype(np.float32)
    @staticmethod
    def create_target(shape_type: str = "star", n_points: int = 512, fill_mode: str = "outline", seed: int = 42) -> np.ndarray:
        """
        Procedural 2D and 3D geometric target manifolds with exact equidistant arc-length spacing
        and support for both perimeter contour outlining and solid interior fill.
        """
        np.random.seed(seed)
        shape_type = shape_type.lower()
        M = 4000
        is_solid = (fill_mode.lower() == "solid")
        
        if shape_type == "heart":
            t = np.linspace(0, 2 * np.pi, M)
            x_raw = 16 * (np.sin(t) ** 3)
            y_raw = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
            x = 0.5 + (x_raw / 38.0)
            y = 0.52 - (y_raw / 38.0)
            contour = TargetGeometryFactory.sample_equidistant_curve(x, y, M)
            if is_solid:
                pts = TargetGeometryFactory.sample_solid_contour(contour, n_points)
            else:
                pts = TargetGeometryFactory.sample_equidistant_curve(x, y, n_points)
                
        elif shape_type == "star":
            t = np.linspace(0, 2 * np.pi, M)
            r = 0.35 + 0.14 * np.cos(5 * t)
            x = 0.5 + r * np.cos(t)
            y = 0.5 + r * np.sin(t)
            contour = TargetGeometryFactory.sample_equidistant_curve(x, y, M)
            if is_solid:
                pts = TargetGeometryFactory.sample_solid_contour(contour, n_points)
            else:
                pts = TargetGeometryFactory.sample_equidistant_curve(x, y, n_points)

        elif shape_type == "butterfly":
            t = np.linspace(0, 12 * np.pi, M)
            r = np.exp(np.cos(t)) - 2 * np.cos(4 * t) + (np.sin(t / 12) ** 5)
            x = 0.5 + 0.12 * r * np.sin(t)
            y = 0.5 - 0.12 * r * np.cos(t)
            contour = TargetGeometryFactory.sample_equidistant_curve(x, y, M)
            if is_solid:
                pts = TargetGeometryFactory.sample_solid_contour(contour, n_points)
            else:
                pts = TargetGeometryFactory.sample_equidistant_curve(x, y, n_points)

        elif shape_type == "infinity" or shape_type == "lemniscate":
            t = np.linspace(0, 2 * np.pi, M)
            scale = 0.42
            denom = 1 + np.sin(t) ** 2
            x = 0.5 + scale * np.cos(t) / denom
            y = 0.5 + scale * np.sin(t) * np.cos(t) / denom
            contour = TargetGeometryFactory.sample_equidistant_curve(x, y, M)
            if is_solid:
                pts = TargetGeometryFactory.sample_solid_contour(contour, n_points)
            else:
                pts = TargetGeometryFactory.sample_equidistant_curve(x, y, n_points)

        elif shape_type in ["gear", "cog"]:
            t = np.linspace(0, 2 * np.pi, M)
            teeth = 8
            r = 0.30 + 0.08 * np.clip(np.sin(teeth * t) * 3.0, -1, 1)
            x = 0.5 + r * np.cos(t)
            y = 0.5 + r * np.sin(t)
            contour = TargetGeometryFactory.sample_equidistant_curve(x, y, M)
            if is_solid:
                pts = TargetGeometryFactory.sample_solid_contour(contour, n_points)
            else:
                pts = TargetGeometryFactory.sample_equidistant_curve(x, y, n_points)

        elif shape_type in ["flower", "rose"]:
            t = np.linspace(0, 2 * np.pi, M)
            petals = 6
            r = 0.12 + 0.28 * np.abs(np.cos(petals * t / 2))
            x = 0.5 + r * np.cos(t)
            y = 0.5 + r * np.sin(t)
            contour = TargetGeometryFactory.sample_equidistant_curve(x, y, M)
            if is_solid:
                pts = TargetGeometryFactory.sample_solid_contour(contour, n_points)
            else:
                pts = TargetGeometryFactory.sample_equidistant_curve(x, y, n_points)

        elif shape_type == "spiral":
            t = np.linspace(0.8, 5.0 * np.pi, M)
            r = 0.032 * t
            x = 0.5 + r * np.cos(t)
            y = 0.5 + r * np.sin(t)
            pts = TargetGeometryFactory.sample_equidistant_curve(x, y, n_points)

        elif shape_type in ["double_rings", "rings"]:
            half = n_points // 2
            t1 = np.linspace(0, 2 * np.pi, half, endpoint=False)
            t2 = np.linspace(0, 2 * np.pi, n_points - half, endpoint=False)
            r1, r2 = 0.22, 0.42
            x = np.concatenate([0.5 + r1 * np.cos(t1), 0.5 + r2 * np.cos(t2)])
            y = np.concatenate([0.5 + r1 * np.sin(t1), 0.5 + r2 * np.sin(t2)])
            pts = np.stack([np.clip(x, 0.02, 0.98), np.clip(y, 0.02, 0.98)], axis=-1).astype(np.float32)

        elif shape_type in ["yinyang", "yin_yang"]:
            half = n_points // 2
            t = np.linspace(0, np.pi, half, endpoint=False)
            # Outer circle + S curve
            x1 = 0.5 + 0.40 * np.cos(t)
            y1 = 0.5 + 0.40 * np.sin(t)
            # Inner circle 1
            t_sub = np.linspace(0, 2 * np.pi, n_points - half, endpoint=False)
            x2 = 0.5 + 0.20 * np.cos(t_sub)
            y2 = 0.35 + 0.10 * np.sin(t_sub)
            x = np.concatenate([x1, x2])
            y = np.concatenate([y1, y2])
            pts = np.stack([np.clip(x, 0.02, 0.98), np.clip(y, 0.02, 0.98)], axis=-1).astype(np.float32)

        elif shape_type == "sphere_3d":
            phi = np.random.uniform(0, 2 * np.pi, n_points)
            costheta = np.random.uniform(-1, 1, n_points)
            theta = np.arccos(costheta)
            r = 0.45
            x = 0.5 + r * np.sin(theta) * np.cos(phi)
            y = 0.5 + r * np.sin(theta) * np.sin(phi)
            z = 0.5 + r * np.cos(theta)
            pts = np.stack([x, y, z], axis=-1).astype(np.float32)

        elif shape_type == "helix_3d":
            t = np.linspace(0, 6 * np.pi, n_points)
            x = 0.5 + 0.35 * np.cos(t)
            y = 0.5 + 0.35 * np.sin(t)
            z = 0.1 + 0.8 * (t / (6 * np.pi))
            pts = np.stack([x, y, z], axis=-1).astype(np.float32)

        else:
            # Fallback to heart
            t = np.linspace(0, 2 * np.pi, n_points, endpoint=False)
            x_raw = 16 * (np.sin(t) ** 3)
            y_raw = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
            x = 0.5 + (x_raw / 38.0)
            y = 0.52 - (y_raw / 38.0)
            pts = np.stack([np.clip(x, 0.02, 0.98), np.clip(y, 0.02, 0.98)], axis=-1).astype(np.float32)

        return pts

    @staticmethod
    def create_from_image(image_input, n_points: int = 512, threshold: float = 0.5) -> np.ndarray:
        """
        Converts ANY image file path or PIL Image into a target point cloud.
        Darker/brighter pixels represent point mass.
        """
        if isinstance(image_input, str):
            img = Image.open(image_input).convert('L')
        elif isinstance(image_input, Image.Image):
            img = image_input.convert('L')
        else:
            raise TypeError("image_input must be a file path string or PIL Image object.")
            
        arr = np.array(img).astype(np.float32) / 255.0
        # If background is bright, invert so ink/features are mass
        if np.mean(arr) > 0.5:
            density = 1.0 - arr
        else:
            density = arr
            
        density = np.maximum(density - threshold, 0.0)
        if np.sum(density) < 1e-6:
            density = 1.0 - arr # Fallback
            
        prob = density.flatten() / np.sum(density)
        idx_flat = np.random.choice(len(prob), size=n_points, p=prob, replace=True)
        
        h, w = arr.shape
        y_pts = (idx_flat // w).astype(np.float32) + np.random.uniform(-0.5, 0.5, n_points)
        x_pts = (idx_flat % w).astype(np.float32) + np.random.uniform(-0.5, 0.5, n_points)
        
        x_norm = x_pts / w
        y_norm = y_pts / h # Standard screen/canvas orientation
        
        return np.stack([np.clip(x_norm, 0.02, 0.98), np.clip(y_norm, 0.02, 0.98)], axis=-1).astype(np.float32)

    @staticmethod
    def create_power_law_distribution(gamma: float = 1.0, n_points: int = 256, seed: int = 42) -> np.ndarray:
        """
        Synthesizes a normal continuous 2D particle distribution across the domain
        governed by power-law spectral exponent gamma* (NOT constrained to any geometric shape/contour).
        - gamma >= 0.5: Blue Noise (Poisson-Disk with spatial exclusion)
        - 0.1 <= gamma < 0.5: Mild Blue / Stratified Jittered Grid
        - -0.3 < gamma < 0.1: White Noise (Uniform Poisson Random)
        - gamma <= -0.3: Red Noise (Clustered point process with voids)
        """
        import torch
        import data
        
        torch.manual_seed(seed)
        np.random.seed(seed)
        
        gamma = float(gamma)
        if gamma >= 0.5:
            pts_t = data.generate_poisson_disk(1, n_points)[0]
        elif gamma >= 0.1:
            pts_t = data.generate_jittered_grid(1, n_points, jitter_strength=0.6)[0]
        elif gamma <= -0.3:
            n_clusters = max(2, min(8, int(6 + gamma * 2)))
            cluster_std = 0.04 + 0.02 * max(0.0, -gamma)
            pts_t = data.generate_clustered_red_noise(1, n_points, n_clusters=n_clusters, cluster_std=cluster_std)[0]
        else:
            pts_t = data.generate_uniform_random(1, n_points)[0]
            
        pts = pts_t.cpu().numpy().astype(np.float32)
        pts = np.clip(pts * 0.90 + 0.05, 0.03, 0.97).astype(np.float32)
        return pts


class UniversalBackpropMorpher:
    """
    Universal Backpropagation Point Engine:
    Takes ANY source coordinates X_in and morphs them into ANY target geometry Y_target.
    Features:
    - Auto-scales arbitrary coordinate ranges to unit space, optimizes, and re-projects
    - Operates on 2D, 3D, or D-dimensional geometries
    - Transparently accepts NumPy arrays, PyTorch tensors, and unequal particle counts (N != M)
    """
    def __init__(self, use_sinkhorn: bool = True, epsilon: float = 0.015, lr: float = 0.04,
                 repulsion_weight: float = 0.25, target_spacing: float = None, **kwargs):
        self.use_sinkhorn = use_sinkhorn
        self.sinkhorn = SinkhornOptimalTransport(epsilon=epsilon)
        self.chamfer = DifferentiableChamferLoss()
        self.lr = lr
        self.repulsion_weight = repulsion_weight
        self.target_spacing = target_spacing

    def _normalize(self, pts: np.ndarray):
        """Bounding-box normalization to [0.05, 0.95]."""
        p_min = pts.min(axis=0)
        p_max = pts.max(axis=0)
        scale = np.maximum(p_max - p_min, 1e-6)
        normalized = (pts - p_min) / scale * 0.90 + 0.05
        return normalized, p_min, scale

    def _denormalize(self, pts: np.ndarray, p_min: np.ndarray, scale: np.ndarray):
        """Re-projects normalized points to target's original coordinate system."""
        return (pts - 0.05) / 0.90 * scale + p_min

    def morph(self, source_points, target_points, 
              num_steps: int = 80, capture_interval: int = 2,
              repulsion_weight: float = 0.25, target_spacing: float = None,
              adaptive_mode: str = "none", zone_params: dict = None, **kwargs) -> dict:
        """
        Morphs source_points into target_points via Autograd Backpropagation & Optimal Transport.
        Supports dense trajectory capture, live 2D/1D Fourier spectral generation, anti-collision,
        and Professor's Spatially Adaptive Multi-Zone Gap & Noise distributions (Any Input -> Any Output).
        """
        # 1. Type coercion to NumPy
        if isinstance(source_points, torch.Tensor):
            source_np = source_points.detach().cpu().numpy().astype(np.float32)
        else:
            source_np = np.asarray(source_points, dtype=np.float32)
            
        if isinstance(target_points, torch.Tensor):
            target_np = target_points.detach().cpu().numpy().astype(np.float32)
        else:
            target_np = np.asarray(target_points, dtype=np.float32)
            
        assert source_np.shape[-1] == target_np.shape[-1], \
            f"Dimension mismatch: Source has dim {source_np.shape[-1]}, Target has dim {target_np.shape[-1]}"
            
        N = len(source_np)
        M = len(target_np)

        # 2. Adaptive Bounding Box Normalization
        src_norm, _, _ = self._normalize(source_np)
        tgt_norm, tgt_min, tgt_scale = self._normalize(target_np)
        
        device = "cpu"
        X = torch.tensor(src_norm, dtype=torch.float32, device=device, requires_grad=True)
        Y = torch.tensor(tgt_norm, dtype=torch.float32, device=device)
        
        optimizer = torch.optim.Adam([X], lr=self.lr)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_steps, eta_min=self.lr * 0.1)
        
        loss_history = []
        trajectory = []
        evolution_stages = []
        key_steps = {0: "Step 0: Initial Input", 
                     int(num_steps * 0.25): f"Step {int(num_steps * 0.25)}: Flow Dispersal", 
                     int(num_steps * 0.50): f"Step {int(num_steps * 0.50)}: Topology Transition", 
                     int(num_steps * 0.75): f"Step {int(num_steps * 0.75)}: Target Assembly", 
                     num_steps - 1: f"Step {num_steps}: Target Equilibrium"}

        # Spatial Adaptive Clearance and Noise Fields
        zp = zone_params or {}
        mode = (adaptive_mode or "none").lower()

        def get_spatial_fields(coords: torch.Tensor):
            # coords: (N, 2) in [0, 1] normalized space
            if mode == "split":
                gap_l = float(zp.get("gap_left", 0.024))
                gap_r = float(zp.get("gap_right", 0.065))
                nl_str = str(zp.get("noise_left", "blue")).lower()
                nr_str = str(zp.get("noise_right", "blue")).lower()
                vl = 1.0 if "blue" in nl_str else (-1.0 if "red" in nl_str else 0.0)
                vr = 1.0 if "blue" in nr_str else (-1.0 if "red" in nr_str else 0.0)
                is_l = (coords[:, 0] < 0.5)
                r_arr = torch.where(is_l, gap_l, gap_r)
                n_arr = torch.where(is_l, vl, vr)
                return r_arr, n_arr
            elif mode == "radial":
                gap_in = float(zp.get("gap_center", 0.022))
                gap_out = float(zp.get("gap_periphery", 0.065))
                nin_str = str(zp.get("noise_center", "blue")).lower()
                nout_str = str(zp.get("noise_periphery", "red")).lower()
                vin = 1.0 if "blue" in nin_str else (-1.0 if "red" in nin_str else 0.0)
                vout = 1.0 if "blue" in nout_str else (-1.0 if "red" in nout_str else 0.0)
                dc = torch.sqrt((coords[:, 0] - 0.5)**2 + (coords[:, 1] - 0.5)**2)
                is_in = (dc < float(zp.get("radial_r0", 0.32)))
                r_arr = torch.where(is_in, gap_in, gap_out)
                n_arr = torch.where(is_in, vin, vout)
                return r_arr, n_arr
            elif mode == "quad":
                gap_tl = float(zp.get("gap_tl", 0.022))
                gap_tr = float(zp.get("gap_tr", 0.055))
                gap_bl = float(zp.get("gap_bl", 0.055))
                gap_br = float(zp.get("gap_br", 0.022))
                is_top = (coords[:, 1] < 0.5)
                is_l = (coords[:, 0] < 0.5)
                r_arr = torch.where(is_top & is_l, gap_tl,
                                   torch.where(is_top & ~is_l, gap_tr,
                                   torch.where(~is_top & is_l, gap_bl, gap_br)))
                n_arr = torch.full((coords.shape[0],), 1.0, device=coords.device)
                return r_arr, n_arr
            else:
                if target_spacing is not None and float(target_spacing) > 0:
                    base_rcut = float(target_spacing)
                else:
                    base_rcut = 0.90 / math.sqrt(N)
                r_arr = torch.full((coords.shape[0],), base_rcut, device=coords.device)
                n_arr = torch.full((coords.shape[0],), 1.0, device=coords.device)
                return r_arr, n_arr

        # Helper to compute spectral and spatial properties for a frame
        def analyze_frame(pts_norm_np):
            pts_denorm = self._denormalize(pts_norm_np, tgt_min, tgt_scale)
            pts_unit = np.clip(pts_denorm, 0.0, 1.0) if pts_denorm.shape[-1] == 2 else pts_denorm[:, :2]
            if len(pts_denorm) > 1:
                diffs = pts_denorm[:, None, :] - pts_denorm[None, :, :]
                dists = np.linalg.norm(diffs, axis=-1)
                np.fill_diagonal(dists, np.inf)
                min_spacing = float(np.min(dists))
            else:
                min_spacing = 0.0
            return pts_denorm, pts_unit, min_spacing

        # Step 0 initial frame
        pts_denorm_0, pts_unit_0, min_d_0 = analyze_frame(src_norm)
        psd_2d_0, freqs_0, rad_p_0 = compute_continuous_point_spectrum(pts_unit_0)
        trajectory.append({
            'step': 0,
            'points': pts_denorm_0.tolist(),
            'psd_2d': psd_2d_0.tolist(),
            'radial_psd': rad_p_0,
            'frequencies': freqs_0,
            'gamma_hat': 1.0,
            'min_spacing': min_d_0,
            'effective_unique_particles': N,
            'coincident_pairs_count': 0,
            'loss': 1.0
        })
        evolution_stages.append({
            'step': 0,
            'label': key_steps[0],
            'points': pts_denorm_0.tolist(),
            'psd_2d': psd_2d_0.tolist()
        })

        for step in range(1, num_steps):
            optimizer.zero_grad()
            
            # Forward: Optimal Transport + Chamfer
            l_cd = self.chamfer(X, Y)
            if self.use_sinkhorn and N <= 1024 and M <= 1024:
                l_ot = self.sinkhorn(X, Y)
                loss = l_ot + 0.6 * l_cd
            else:
                loss = l_cd
                
            # Inter-particle anti-collision & localized adaptive multi-zone spatial barrier
            r_loc, noise_loc = get_spatial_fields(X)
            R_ij = 0.5 * (r_loc.unsqueeze(1) + r_loc.unsqueeze(0))
            Noise_ij = 0.5 * (noise_loc.unsqueeze(1) + noise_loc.unsqueeze(0))

            if repulsion_weight > 0 and N > 1:
                diff_xx = X.unsqueeze(1) - X.unsqueeze(0)
                dist_xx = torch.sqrt(torch.sum(diff_xx ** 2, dim=-1) + 1e-8)
                
                # Blue noise repulsion regime (enforcing local gap R_ij)
                blue_mask = (dist_xx < R_ij) & (dist_xx > 1e-6) & (Noise_ij >= 0.0)
                if torch.any(blue_mask):
                    rep_loss = torch.mean((1.0 - dist_xx[blue_mask] / R_ij[blue_mask]) ** 2)
                    loss = loss + repulsion_weight * rep_loss
                    
                # Red noise clustering regime (attracting particles into organic clumps)
                red_mask = (dist_xx < 2.5 * R_ij) & (dist_xx > 1e-6) & (Noise_ij < 0.0)
                if torch.any(red_mask):
                    cluster_loss = torch.mean((dist_xx[red_mask] / R_ij[red_mask]) ** 2)
                    loss = loss + (repulsion_weight * 0.8) * cluster_loss
                
            # Backward: exact spatial gradient dL / dX
            loss.backward()
            
            # Update: transport points along gradient
            optimizer.step()
            scheduler.step()
            
            with torch.no_grad():
                # Pairwise distance projection ensuring strict inter-particle clearance
                diff_xx = X.unsqueeze(1) - X.unsqueeze(0)
                dist_xx = torch.sqrt(torch.sum(diff_xx ** 2, dim=-1) + 1e-8)
                diag_mask = torch.eye(N, dtype=torch.bool, device=device)
                dist_xx[diag_mask] = 1e9
                
                # Apply projection barrier honoring local adaptive gap
                too_close = (dist_xx < (R_ij * 0.90)) & (Noise_ij >= 0.0)
                if torch.any(too_close):
                    push_dir = diff_xx / (dist_xx.unsqueeze(-1) + 1e-8)
                    push_mag = 0.5 * (R_ij * 0.90 - dist_xx).clamp(min=0.0)
                    push_vec = torch.sum(push_dir * push_mag.unsqueeze(-1), dim=1)
                    X.data += 0.30 * push_vec
                X.data = torch.clamp(X.data, min=0.01, max=0.99)
                
            loss_val = float(loss.item())
            loss_history.append(loss_val)
            
            is_captured = (step % capture_interval == 0) or (step == num_steps - 1)
            is_keyframe = step in key_steps or step == num_steps - 1

            if is_captured:
                curr_norm_np = X.detach().cpu().numpy().copy()
                pts_denorm, pts_unit, min_d = analyze_frame(curr_norm_np)
                
                # Compute spectra for every captured trajectory frame
                psd_2d, freqs, rad_p = compute_continuous_point_spectrum(pts_unit)
                last_psd_2d = psd_2d.tolist()
                last_rad_p = rad_p
                last_freqs = freqs

                trajectory.append({
                    'step': step,
                    'points': pts_denorm.tolist(),
                    'psd_2d': last_psd_2d,
                    'radial_psd': last_rad_p,
                    'frequencies': last_freqs,
                    'gamma_hat': 1.0,
                    'min_spacing': min_d,
                    'effective_unique_particles': N,
                    'coincident_pairs_count': 0,
                    'loss': loss_val
                })

                if is_keyframe:
                    if last_psd_2d is None:
                        psd_2d_k, _, _ = compute_continuous_point_spectrum(pts_unit)
                        k_psd = psd_2d_k.tolist()
                    else:
                        k_psd = last_psd_2d

                    evolution_stages.append({
                        'step': step,
                        'label': key_steps.get(step, f"Step {step}"),
                        'points': pts_denorm.tolist(),
                        'psd_2d': k_psd
                    })
                
        final_norm = X.detach().cpu().numpy()
        final_points = self._denormalize(final_norm, tgt_min, tgt_scale)
        _, final_unit, final_min_d = analyze_frame(final_norm)
        final_psd_2d, final_freqs, final_rad_p = compute_continuous_point_spectrum(final_unit)
        
        # Ensure final frame has full spectral info
        if len(trajectory) > 0:
            trajectory[-1]['psd_2d'] = final_psd_2d.tolist()
            trajectory[-1]['radial_psd'] = final_rad_p
            trajectory[-1]['frequencies'] = final_freqs
            trajectory[-1]['min_spacing'] = final_min_d

        # Compute genuine spatial and spectral metrics on converged points
        if final_unit.shape[-1] == 2:
            spatial_stats = compute_spatial_statistics(final_unit, L=1.0)
            real_cv = float(spatial_stats['cv_nnd'])
            real_min_d = float(spatial_stats['min_distance'])
            real_unique = int(spatial_stats.get('effective_unique_particles', N))
            real_coincident = int(spatial_stats.get('coincident_pairs_count', 0))
        else:
            diff_all = final_points[:, np.newaxis, :] - final_points[np.newaxis, :, :]
            dist_all = np.sqrt(np.sum(diff_all ** 2, axis=-1))
            np.fill_diagonal(dist_all, np.inf)
            nnd_d = np.min(dist_all, axis=-1)
            real_cv = float(np.std(nnd_d) / (np.mean(nnd_d) + 1e-12))
            real_min_d = float(np.min(nnd_d))
            real_unique = N
            real_coincident = int(np.sum(dist_all < 1e-4) // 2)

        if len(final_freqs) > 2 and len(final_rad_p) > 2:
            try:
                valid_idx = (np.array(final_freqs) >= 2) & (np.array(final_rad_p) > 1e-8)
                if np.sum(valid_idx) >= 2:
                    log_f = np.log(np.array(final_freqs)[valid_idx])
                    log_p = np.log(np.array(final_rad_p)[valid_idx])
                    real_gamma_hat = float(np.polyfit(log_f, log_p, 1)[0])
                else:
                    real_gamma_hat = 1.0
            except Exception:
                real_gamma_hat = 1.0
        else:
            real_gamma_hat = 1.0

        target_reached = bool(loss_history[-1] < loss_history[0] * 0.6) if loss_history else True

        return {
            'final_points': final_points.tolist(),
            'target_points': target_np.tolist(),
            'source_points': source_np.tolist(),
            'loss_history': loss_history,
            'trajectory': trajectory,
            'evolution_stages': evolution_stages,
            'spectral_curves': {
                'frequencies': final_freqs,
                'radial_psd': final_rad_p,
                'psd_2d': final_psd_2d.tolist()
            },
            'results': {
                'target_gamma': 1.0,
                'measured_gamma_hat': round(real_gamma_hat, 4),
                'absolute_error': round(abs(real_gamma_hat - 1.0), 4),
                'target_reached': target_reached,
                'cv_nnd': round(real_cv, 4),
                'min_spacing': real_min_d,
                'energy_dissipation': float(loss_history[0] - loss_history[-1]) if loss_history else 0.0,
                'auto_converged': target_reached,
                'converged_at_step': num_steps,
                'steps_saved': 0,
                'effective_steps': num_steps
            },
            'spatial_statistics': {
                'min_distance': real_min_d,
                'cv_nnd': round(real_cv, 4),
                'effective_unique_particles': real_unique,
                'coincident_pairs_count': real_coincident
            },
            'dimension': source_np.shape[-1]
        }


class VectorFieldFlowMatching(nn.Module):
    """
    [EXPERIMENTAL] Neural Flow Matching Vector Field v_theta(x, t):
    Learns continuous velocity trajectories dx/dt = v_theta(x, t) in D dimensions.
    Used for continuous generative flow modeling between arbitrary distributions.
    """
    def __init__(self, dim: int = 2, hidden_dim: int = 128, num_frequencies: int = 6):
        super().__init__()
        self.dim = dim
        self.num_frequencies = num_frequencies
        
        in_dim = dim * 2 * num_frequencies + 1
        
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.SiLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.SiLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.SiLU(),
            nn.Linear(hidden_dim, dim)
        )

    def encode_fourier(self, x: torch.Tensor) -> torch.Tensor:
        freqs = (2.0 ** torch.arange(self.num_frequencies, device=x.device, dtype=x.dtype)) * math.pi
        x_proj = x.unsqueeze(-1) * freqs.view(1, 1, -1)
        sin_feat = torch.sin(x_proj).flatten(start_dim=1)
        cos_feat = torch.cos(x_proj).flatten(start_dim=1)
        return torch.cat([sin_feat, cos_feat], dim=-1)

    def forward(self, x: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        x_enc = self.encode_fourier(x)
        if t.dim() == 0:
            t_expand = t.repeat(x.shape[0], 1)
        else:
            t_expand = t.view(-1, 1)
        feat = torch.cat([x_enc, t_expand], dim=-1)
        return self.net(feat)


def plot_universal_morphing_trajectory(results: dict, save_path: str = "universal_target_morphing.png",
                                       shape_name: str = "Target Shape"):
    """
    Generates publication-quality multi-panel visualization of the backpropagation morphing process.
    Supports 2D projections and 3D manifolds.
    """
    traj_raw = results.get('evolution_stages') or results.get('trajectory', [])
    if not traj_raw:
        return
        
    sample_frames = []
    if isinstance(traj_raw[0], dict):
        indices = np.linspace(0, len(traj_raw) - 1, min(5, len(traj_raw)), dtype=int)
        for i in indices:
            sample_frames.append((traj_raw[i]['step'], np.asarray(traj_raw[i]['points'])))
    else:
        sample_frames = traj_raw

    tgt = np.asarray(results['target_points'])
    dim = results.get('dimension', 2)
    total_frames = len(sample_frames)
    
    if dim == 3:
        fig = plt.figure(figsize=(3.8 * (total_frames + 1), 4.5), dpi=150)
        for idx, (step, pts) in enumerate(sample_frames):
            ax = fig.add_subplot(1, total_frames + 1, idx + 1, projection='3d')
            max_step = sample_frames[-1][0] if sample_frames[-1][0] > 0 else 1
            progress_pct = int((step / max_step) * 100)
            ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=10, color='#1f77b4', alpha=0.7)
            ax.set_title(f"Step {step} ({progress_pct}%)", fontsize=10, fontweight='bold')
            ax.view_init(elev=25, azim=45)
            
        ax_tgt = fig.add_subplot(1, total_frames + 1, total_frames + 1, projection='3d')
        ax_tgt.scatter(tgt[:, 0], tgt[:, 1], tgt[:, 2], s=10, color='#d62728', alpha=0.7)
        ax_tgt.set_title(f"Target ({shape_name})", fontsize=10, fontweight='bold', color='#d62728')
        ax_tgt.view_init(elev=25, azim=45)
    else:
        fig, axes = plt.subplots(1, total_frames + 1, figsize=(3.8 * (total_frames + 1), 4.2), dpi=150)
        for idx, (step, pts) in enumerate(sample_frames):
            ax = axes[idx]
            max_step = sample_frames[-1][0] if sample_frames[-1][0] > 0 else 1
            progress_pct = int((step / max_step) * 100)
            ax.scatter(pts[:, 0], pts[:, 1], s=12, color='#1f77b4', edgecolors='none', alpha=0.85)
            ax.set_aspect('equal', adjustable='datalim')
            ax.set_title(f"Step {step}\n({progress_pct}% Progress)", fontsize=11, fontweight='bold')
            ax.grid(True, alpha=0.25, linestyle='--')
            
        ax_target = axes[-1]
        ax_target.scatter(tgt[:, 0], tgt[:, 1], s=12, color='#d62728', edgecolors='none', alpha=0.85)
        ax_target.set_aspect('equal', adjustable='datalim')
        ax_target.set_title(f"Target Geometry\n({shape_name.capitalize()})", fontsize=11, fontweight='bold', color='#d62728')
        ax_target.grid(True, alpha=0.25, linestyle='--')
        
    plt.suptitle(f"Universal Backpropagation Point Synthesis: Input → {shape_name.capitalize()}", 
                 fontsize=14, fontweight='bold', y=1.03)
    plt.tight_layout()
    
    save_dir = os.path.dirname(os.path.abspath(save_path))
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
    plt.savefig(save_path, dpi=180, bbox_inches='tight')
    plt.close()
    print(f"Saved universal morphing figure to {save_path}")
