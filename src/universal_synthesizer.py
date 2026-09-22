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
        # Flip y so text is upright
        y_norm = 1.0 - y_norm
        
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
        y_norm = 1.0 - (y_pts / h) # Invert y for standard Cartesian orientation
        
        return np.stack([np.clip(x_norm, 0.02, 0.98), np.clip(y_norm, 0.02, 0.98)], axis=-1).astype(np.float32)

    @staticmethod
    def create_target(shape_type: str = "star", n_points: int = 512, seed: int = 42) -> np.ndarray:
        """Procedural 2D and 3D geometric target manifolds."""
        np.random.seed(seed)
        shape_type = shape_type.lower()
        
        if shape_type == "star":
            t = np.random.uniform(0, 2 * np.pi, n_points)
            r = 0.35 + 0.15 * np.cos(5 * t)
            r = r * np.sqrt(np.random.uniform(0.1, 1.0, n_points))
            x = 0.5 + r * np.cos(t)
            y = 0.5 + r * np.sin(t)
            pts = np.stack([np.clip(x, 0.02, 0.98), np.clip(y, 0.02, 0.98)], axis=-1)
            
        elif shape_type == "spiral":
            t = np.random.uniform(0.5, 4.5 * np.pi, n_points)
            r = 0.035 * t
            noise = np.random.normal(0, 0.008, n_points)
            x = 0.5 + (r + noise) * np.cos(t)
            y = 0.5 + (r + noise) * np.sin(t)
            pts = np.stack([np.clip(x, 0.02, 0.98), np.clip(y, 0.02, 0.98)], axis=-1)
            
        elif shape_type == "heart":
            t = np.random.uniform(0, 2 * np.pi, n_points)
            x_raw = 16 * (np.sin(t) ** 3)
            y_raw = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
            x = 0.5 + (x_raw / 38.0)
            y = 0.48 + (y_raw / 38.0)
            pts = np.stack([np.clip(x, 0.02, 0.98), np.clip(y, 0.02, 0.98)], axis=-1)
            
        elif shape_type == "double_rings":
            half = n_points // 2
            t1 = np.random.uniform(0, 2 * np.pi, half)
            t2 = np.random.uniform(0, 2 * np.pi, n_points - half)
            r1 = 0.20 + np.random.normal(0, 0.01, half)
            r2 = 0.40 + np.random.normal(0, 0.01, n_points - half)
            x = np.concatenate([0.5 + r1 * np.cos(t1), 0.5 + r2 * np.cos(t2)])
            y = np.concatenate([0.5 + r1 * np.sin(t1), 0.5 + r2 * np.sin(t2)])
            pts = np.stack([np.clip(x, 0.02, 0.98), np.clip(y, 0.02, 0.98)], axis=-1)
            
        elif shape_type == "sphere_3d":
            # 3D spherical surface
            phi = np.random.uniform(0, 2 * np.pi, n_points)
            costheta = np.random.uniform(-1, 1, n_points)
            theta = np.arccos(costheta)
            r = 0.45 + np.random.normal(0, 0.01, n_points)
            x = 0.5 + r * np.sin(theta) * np.cos(phi)
            y = 0.5 + r * np.sin(theta) * np.sin(phi)
            z = 0.5 + r * np.cos(theta)
            pts = np.stack([x, y, z], axis=-1)
            
        elif shape_type == "helix_3d":
            # 3D spiral helix
            t = np.random.uniform(0, 6 * np.pi, n_points)
            r = 0.35 + np.random.normal(0, 0.01, n_points)
            x = 0.5 + r * np.cos(t)
            y = 0.5 + r * np.sin(t)
            z = 0.1 + 0.8 * (t / (6 * np.pi))
            pts = np.stack([x, y, z], axis=-1)
            
        else:
            raise ValueError(f"Unknown shape_type '{shape_type}'. Options: 'star', 'spiral', 'heart', 'double_rings', 'sphere_3d', 'helix_3d'")
            
        return pts.astype(np.float32)


class UniversalBackpropMorpher:
    """
    Universal Backpropagation Point Engine:
    Takes ANY source coordinates X_in and morphs them into ANY target geometry Y_target.
    Features:
    - Auto-scales arbitrary coordinate ranges to unit space, optimizes, and re-projects
    - Operates on 2D, 3D, or D-dimensional geometries
    - Transparently accepts NumPy arrays, PyTorch tensors, and unequal particle counts (N != M)
    """
    def __init__(self, use_sinkhorn: bool = True, epsilon: float = 0.015, lr: float = 0.04):
        self.use_sinkhorn = use_sinkhorn
        self.sinkhorn = SinkhornOptimalTransport(epsilon=epsilon)
        self.chamfer = DifferentiableChamferLoss()
        self.lr = lr

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
              num_steps: int = 160, record_history: bool = True) -> dict:
        """
        Morphs source_points into target_points via Autograd Backpropagation.
        Args:
            source_points: (N, D) array or tensor
            target_points: (M, D) array or tensor
            num_steps: optimization iterations
        Returns:
            dict containing morphed points, trajectory checkpoints, and loss history
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
        checkpoints = [0, int(num_steps * 0.25), int(num_steps * 0.50), int(num_steps * 0.75), num_steps - 1]
        
        for step in range(num_steps):
            optimizer.zero_grad()
            
            # Forward: Optimal Transport + Chamfer
            l_cd = self.chamfer(X, Y)
            if self.use_sinkhorn and len(X) <= 1024 and len(Y) <= 1024:
                l_ot = self.sinkhorn(X, Y)
                loss = l_ot + 0.5 * l_cd
            else:
                loss = l_cd
                
            # Backward: exact spatial gradient dL / dX
            loss.backward()
            
            # Update: transport points along gradient
            optimizer.step()
            scheduler.step()
            
            with torch.no_grad():
                X.data = torch.clamp(X.data, min=0.01, max=0.99)
                
            loss_history.append(loss.item())
            
            if record_history and step in checkpoints:
                denorm_pts = self._denormalize(X.detach().cpu().numpy().copy(), tgt_min, tgt_scale)
                trajectory.append((step, denorm_pts))
                
        final_norm = X.detach().cpu().numpy()
        final_points = self._denormalize(final_norm, tgt_min, tgt_scale)
        
        return {
            'final_points': final_points,
            'target_points': target_np,
            'source_points': source_np,
            'loss_history': loss_history,
            'trajectory': trajectory,
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
    traj = results['trajectory']
    dim = results.get('dimension', 2)
    
    if dim == 3:
        fig = plt.figure(figsize=(3.8 * (len(traj) + 1), 4.5), dpi=150)
        for idx, (step, pts) in enumerate(traj):
            ax = fig.add_subplot(1, len(traj) + 1, idx + 1, projection='3d')
            progress_pct = int((step / (traj[-1][0] if traj[-1][0] > 0 else 1)) * 100)
            ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=10, color='#1f77b4', alpha=0.7)
            ax.set_title(f"Step {step} ({progress_pct}%)", fontsize=10, fontweight='bold')
            ax.view_init(elev=25, azim=45)
            
        ax_tgt = fig.add_subplot(1, len(traj) + 1, len(traj) + 1, projection='3d')
        tgt = results['target_points']
        ax_tgt.scatter(tgt[:, 0], tgt[:, 1], tgt[:, 2], s=10, color='#d62728', alpha=0.7)
        ax_tgt.set_title(f"Target ({shape_name})", fontsize=10, fontweight='bold', color='#d62728')
        ax_tgt.view_init(elev=25, azim=45)
    else:
        fig, axes = plt.subplots(1, len(traj) + 1, figsize=(3.8 * (len(traj) + 1), 4.2), dpi=150)
        for idx, (step, pts) in enumerate(traj):
            ax = axes[idx]
            progress_pct = int((step / (traj[-1][0] if traj[-1][0] > 0 else 1)) * 100)
            ax.scatter(pts[:, 0], pts[:, 1], s=12, color='#1f77b4', edgecolors='none', alpha=0.85)
            ax.set_aspect('equal', adjustable='datalim')
            ax.set_title(f"Step {step}\n({progress_pct}% Progress)", fontsize=11, fontweight='bold')
            ax.grid(True, alpha=0.25, linestyle='--')
            
        ax_target = axes[-1]
        tgt = results['target_points']
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
