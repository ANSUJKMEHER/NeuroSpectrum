"""
train.py — Training engine for Neural Energy Spectral Point Distributions.
Implements Stage 8 and Stage 9 of the research proposal:
- Single-Target Overfit Training (Milestone 1: gamma = +1.0)
- Continuous Multi-Target Training (gamma ~ U[-2, +2])
- Integrated Diagnostic Evaluation (g(r), CV_NND, and log-PSD tracking)
"""

import os
import sys
import time
import torch
import torch.nn as nn
import numpy as np
from typing import Optional, Dict, Any, List

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from data import generate_uniform_random, sample_target_gamma
import data
from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from losses import CompositeSpectralLoss
from evaluate import compute_spatial_statistics
from visualize import plot_point_set_analysis, plot_training_history
from checkpoints import save_checkpoint, save_canonical_model, load_checkpoint


def train_single_target_overfit(target_gamma_val: float = 1.0, 
                                num_epochs: int = 50, 
                                n_particles: int = 256,
                                num_steps: int = 20,
                                dt: float = 0.02,
                                lr: float = 5e-4,
                                eps_divergence: float = 0.005,
                                r_repulsion: float = 0.045,
                                device: str = None):
    """
    Phase 7 / Milestone 1: Train neural energy field on ONE target condition (gamma = +1.0).
    Verifies that the neural energy field discovers repulsion dynamics and drives loss down.
    """
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
    print("\n" + "="*65, flush=True)
    print(f"STARTING MILESTONE 1: Single-Target Overfit Training (γ* = {target_gamma_val:+.2f})", flush=True)
    print(f"Config: N={n_particles}, Steps={num_steps}, Divergence Prior={eps_divergence:.3f}, r_rep={r_repulsion:.3f}", flush=True)
    print("="*65, flush=True)
    
    torch.manual_seed(42)
    np.random.seed(42)
    
    # 1. Initialize core architectural components with smooth soft-core repulsion
    energy_model = NeuralPairwiseEnergy(
        hidden_dim=64, 
        num_layers=3,
        use_divergence_prior=True,
        eps_divergence=eps_divergence,
        r_repulsion=r_repulsion
    ).to(device)
    dynamics = DifferentiableSimulationEngine(
        energy_model=energy_model,
        num_steps=num_steps,
        dt=dt,
        L=1.0,
        use_checkpointing=True,
        integrator="verlet",
        damping=0.97,
        truncated_bptt_steps=min(25, num_steps)
    ).to(device)
    rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02).to(device)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24).to(device)
    loss_fn = CompositeSpectralLoss(
        lambda_spec=1.0,
        lambda_gamma=2.0,
        lambda_spacing=3.0,
        n_particles=n_particles
    ).to(device)
    
    optimizer = torch.optim.Adam(energy_model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-4)
    
    # Fixed initial particle configuration for clean single-instance memorization
    pts_init_fixed = generate_uniform_random(batch_size=1, n_particles=n_particles, device=device)
    target_gamma_tensor = torch.tensor([target_gamma_val], device=device, dtype=torch.float32)
    
    history = {
        'epoch': [],
        'loss': [],
        'l_spec': [],
        'l_gamma': [],
        'l_spacing': [],
        'cv_nnd': [],
        'measured_gamma': [],
        'target_gamma': [target_gamma_val] * num_epochs
    }
    
    t_start = time.perf_counter()
    
    for epoch in range(1, num_epochs + 1):
        optimizer.zero_grad()
        
        # 1. Unroll particle simulation under learned neural energy
        points_final = dynamics(pts_init_fixed, target_gamma_tensor)
        
        # 2. Differentiable periodic Gaussian splatting
        density = rasterizer(points_final)
        
        # 3. Spectral analysis
        spectral_out = analyzer(density)
        
        # 4. Composite loss
        loss_dict = loss_fn(points_final, target_gamma_tensor, spectral_out)
        loss = loss_dict['loss']
        
        # 5. Backprop through unrolled simulation into MLP parameters
        loss.backward()
        torch.nn.utils.clip_grad_norm_(energy_model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()
        
        # Record metrics
        history['epoch'].append(epoch)
        history['loss'].append(loss.item())
        history['l_spec'].append(loss_dict['l_spec'])
        history['l_gamma'].append(loss_dict['l_gamma'])
        history['l_spacing'].append(loss_dict['l_spacing'])
        history['cv_nnd'].append(loss_dict['cv_nnd'])
        history['measured_gamma'].append(loss_dict['measured_gamma'])
        
        if epoch % 20 == 0 or epoch == 1 or epoch == num_epochs:
            print(f"[Epoch {epoch:3d}/{num_epochs}] Total Loss: {loss.item():.4f} | "
                  f"Measured γ̂: {loss_dict['measured_gamma']:+.3f} (Target: {target_gamma_val:+.2f}) | "
                  f"CV_NND: {loss_dict['cv_nnd']:.4f}", flush=True)
            
    total_time = time.perf_counter() - t_start
    print(f"\nTraining completed in {total_time:.2f} seconds ({total_time/num_epochs*1000:.1f} ms/epoch).", flush=True)
    
    # Diagnostic evaluation and plotting
    with torch.no_grad():
        pts_final = dynamics(pts_init_fixed, target_gamma_tensor)
        density = rasterizer(pts_final)
        spectral_out = analyzer(density)
        
        pts_final_np = pts_final[0].cpu().numpy()
        psd2d_np = spectral_out['psd_2d'][0].cpu().numpy()
        radial_psd_np = spectral_out['radial_psd'][0].cpu().numpy()
        freqs_np = spectral_out['freqs'].cpu().numpy()
        final_gamma = spectral_out['gamma'][0].item()
        
    # Spatial diagnosis (Section 6, Stage 7)
    spatial_stats = compute_spatial_statistics(pts_final_np)
    print("\n" + "="*65)
    print("SPATIAL DIAGNOSTIC CHECK (Section 6, Stage 7):")
    print(f"[*] Final Measured γ̂:      {final_gamma:+.4f} (Target: {target_gamma_val:+.2f})")
    print(f"[*] Nearest-Neighbor CV:   {spatial_stats['cv_nnd']:.4f} (Lower = More regular)")
    print(f"[*] Collapse Score (g(r)): {spatial_stats['collapse_score']:.2f}")
    print(f"[*] Sub-kernel Collapse:   {'DETECTED ❌' if spatial_stats['has_collapsed'] else 'NONE ✅ (Well-Spaced)'}")
    print("="*65)
    
    # Ensure output directories exist at project root
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    fig_dir = os.path.join(project_root, "outputs", "figures")
    ckpt_dir = os.path.join(project_root, "outputs", "checkpoints")
    os.makedirs(fig_dir, exist_ok=True)
    os.makedirs(ckpt_dir, exist_ok=True)
    
    # Save artifacts
    hist_path = os.path.join(fig_dir, "training_history_m1.png")
    result_path = os.path.join(fig_dir, "point_set_m1_result.png")
    ckpt_path = os.path.join(ckpt_dir, "checkpoint_m1_overfit.pt")
    
    plot_training_history(history, save_path=hist_path)
    plot_point_set_analysis(
        points=pts_final_np,
        psd_2d=psd2d_np,
        freqs=freqs_np,
        radial_psd=radial_psd_np,
        target_gamma=target_gamma_val,
        measured_gamma=final_gamma,
        save_path=result_path,
        title_prefix=f"Milestone 1 (γ* = {target_gamma_val:+.2f}) "
    )
    
    # Save checkpoint
    save_checkpoint(
        filepath=ckpt_path,
        energy_model=energy_model,
        optimizer=optimizer,
        scheduler=scheduler,
        epoch=num_epochs,
        iteration=num_epochs,
        history=history
    )
    print(f"Saved model checkpoint to {ckpt_path}")
    
    return energy_model, history


def train_continuous_gamma(num_epochs: int = 80, 
                           batch_size: int = 4,
                           n_particles: int = 256,
                           num_steps: int = 20,
                           dt: float = 0.02,
                           lr: float = 5e-4,
                           eps_divergence: float = 0.005,
                           r_repulsion: float = 0.045,
                           device: str = None):
    """
    Phase 8 / Milestone 2: Train single neural model across continuous range of target gammas gamma ~ U[-2, +2].
    """
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
    print("\n" + "="*65, flush=True)
    print(f"STARTING PHASE 8: Continuous Multi-Target Training (gamma ~ U[-2.0, +2.0])", flush=True)
    print(f"Config: Batch={batch_size}, Particles={n_particles}, Steps={num_steps}, Epochs={num_epochs}", flush=True)
    print("="*65, flush=True)
    
    torch.manual_seed(42)
    np.random.seed(42)
    
    energy_model = NeuralPairwiseEnergy(
        hidden_dim=64, 
        num_layers=3,
        use_divergence_prior=True,
        eps_divergence=eps_divergence,
        r_repulsion=r_repulsion
    ).to(device)
    
    dynamics = DifferentiableSimulationEngine(
        energy_model=energy_model, 
        num_steps=num_steps, 
        dt=dt, 
        L=1.0,
        use_checkpointing=True,
        integrator="verlet",
        damping=0.97,
        truncated_bptt_steps=min(25, num_steps)
    ).to(device)
    
    rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02).to(device)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24).to(device)
    loss_fn = CompositeSpectralLoss(
        lambda_spec=1.0, 
        lambda_gamma=2.0, 
        lambda_spacing=2.0, 
        n_particles=n_particles
    ).to(device)
    
    optimizer = torch.optim.Adam(energy_model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-4)
    
    # Multi-distribution generators for data augmentation
    init_generators = [
        lambda bs, n, dev: generate_uniform_random(bs, n, device=dev),
        lambda bs, n, dev: data.generate_jittered_grid(bs, n, device=dev),
        lambda bs, n, dev: data.generate_archimedean_spiral(bs, n, device=dev),
    ]
    
    history = {
        'epoch': [],
        'loss': [],
        'l_spec': [],
        'l_gamma': [],
        'l_spacing': [],
        'gamma_mae': []
    }
    
    t_start = time.perf_counter()
    
    for epoch in range(1, num_epochs + 1):
        optimizer.zero_grad()
        
        # Multi-distribution data augmentation: randomly pick initialization type each epoch
        gen_fn = init_generators[epoch % len(init_generators)]
        pts_init = gen_fn(batch_size, n_particles, device)
        target_gamma = sample_target_gamma(batch_size=batch_size, gamma_min=-2.0, gamma_max=2.0, device=device)
        
        # Unroll physics simulation and compute spectral outputs
        points_final = dynamics(pts_init, target_gamma)
        density = rasterizer(points_final)
        spectral_out = analyzer(density)
        loss_dict = loss_fn(points_final, target_gamma, spectral_out)
        
        loss = loss_dict['loss']
        loss.backward()
        torch.nn.utils.clip_grad_norm_(energy_model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()
        
        # Track MAE across the batch
        with torch.no_grad():
            gamma_mae = torch.mean(torch.abs(spectral_out['gamma'] - target_gamma)).item()
            
        history['epoch'].append(epoch)
        history['loss'].append(loss.item())
        history['l_spec'].append(loss_dict['l_spec'])
        history['l_gamma'].append(loss_dict['l_gamma'])
        history['l_spacing'].append(loss_dict['l_spacing'])
        history['gamma_mae'].append(gamma_mae)
        
        if epoch % 10 == 0 or epoch == 1 or epoch == num_epochs:
            print(f"[Epoch {epoch:3d}/{num_epochs}] Total Loss: {loss.item():.4f} | "
                  f"L_spec: {loss_dict['l_spec']:.4f} | L_gamma: {loss_dict['l_gamma']:.4f} | "
                  f"Slope MAE: {gamma_mae:.3f}", flush=True)
            
    total_time = time.perf_counter() - t_start
    print(f"\nContinuous training completed in {total_time:.2f} seconds ({total_time/num_epochs*1000:.1f} ms/epoch).", flush=True)
    
    # Save artifacts
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    ckpt_dir = os.path.join(project_root, "outputs", "checkpoints")
    os.makedirs(ckpt_dir, exist_ok=True)
    
    ckpt_path = os.path.join(ckpt_dir, "checkpoint_continuous_gamma.pt")
    save_checkpoint(
        filepath=ckpt_path,
        energy_model=energy_model,
        optimizer=optimizer,
        scheduler=scheduler,
        epoch=num_epochs,
        iteration=num_epochs,
        history=history
    )
    print(f"Saved continuous model checkpoint to {ckpt_path}", flush=True)
    
    canonical_path = os.path.join(project_root, "neurospectrum_model.pt")
    save_canonical_model(
        model=energy_model,
        save_path=canonical_path,
        extra_metadata={
            "total_training_iterations": num_epochs,
            "final_loss": history["loss"][-1] if history["loss"] else None,
            "trained_gamma_range": [-2.0, 2.0]
        }
    )
    print(f"Exported canonical production model to {canonical_path}", flush=True)
    
    return energy_model, history


class StatefulTrainingSession:
    """
    Research-Grade Stateful Training Session for NeuroSpectrum.
    
    Guarantees:
    1. NEVER reinitializes weights θ between steps unless explicitly requested.
    2. Progressively optimizes θ(t+1) = θ(t) - η ∇_θ L.
    3. Seamless pause, resume, checkpoint save/load, and canonical export.
    4. Real-time streaming of all metrics (losses, energy, forces, NND CV, grad norm).
    """
    def __init__(
        self,
        energy_model: Optional[NeuralPairwiseEnergy] = None,
        checkpoint_path: Optional[str] = None,
        lr: float = 5e-4,
        weight_decay: float = 1e-5,
        hidden_dim: int = 64,
        num_layers: int = 3,
        use_divergence_prior: bool = True,
        eps_divergence: float = 0.005,
        r_repulsion: float = 0.045,
        n_particles: int = 256,
        num_steps: int = 20,
        dt: float = 0.02,
        grid_size: int = 64,
        sigma: float = 0.02,
        f_min: int = 2,
        f_max: int = 24,
        lambda_spec: float = 1.0,
        lambda_gamma: float = 2.0,
        lambda_spacing: float = 2.0,
        device: Optional[torch.device] = None
    ):
        from checkpoints import get_optimal_device, save_checkpoint, load_checkpoint, save_canonical_model
        self.device = device or get_optimal_device()
        self.save_checkpoint_fn = save_checkpoint
        self.load_checkpoint_fn = load_checkpoint
        self.save_canonical_fn = save_canonical_model
        
        self.model_config = {
            "hidden_dim": hidden_dim,
            "num_layers": num_layers,
            "use_divergence_prior": use_divergence_prior,
            "eps_divergence": eps_divergence,
            "r_repulsion": r_repulsion
        }
        self.simulation_config = {
            "n_particles": n_particles,
            "num_steps": num_steps,
            "dt": dt,
            "L": 1.0
        }
        self.loss_config = {
            "lambda_spec": lambda_spec,
            "lambda_gamma": lambda_gamma,
            "lambda_spacing": lambda_spacing
        }
        
        # 1. Initialize or load model
        if checkpoint_path is not None and os.path.exists(checkpoint_path):
            self.energy_model, _, _, ckpt_data = self.load_checkpoint_fn(checkpoint_path, device=self.device)
            self.iteration = ckpt_data.get("iteration", 0)
            self.epoch = ckpt_data.get("epoch", 0)
            loaded_hist = ckpt_data.get("history") or {}
            default_keys = [
                "iteration", "loss", "l_spec", "l_gamma", "l_spacing",
                "target_gamma", "measured_gamma", "gamma_mae",
                "cv_nnd", "energy", "grad_norm"
            ]
            self.history = {k: list(loaded_hist.get(k, [])) for k in default_keys}
        elif energy_model is not None:
            self.energy_model = energy_model.to(self.device)
            self.iteration = 0
            self.epoch = 0
            self.history = {
                "iteration": [], "loss": [], "l_spec": [], "l_gamma": [], "l_spacing": [],
                "target_gamma": [], "measured_gamma": [], "gamma_mae": [],
                "cv_nnd": [], "energy": [], "grad_norm": []
            }
        else:
            self.energy_model = NeuralPairwiseEnergy(
                hidden_dim=hidden_dim,
                num_layers=num_layers,
                use_divergence_prior=use_divergence_prior,
                eps_divergence=eps_divergence,
                r_repulsion=r_repulsion
            ).to(self.device)
            self.iteration = 0
            self.epoch = 0
            self.history = {
                "iteration": [], "loss": [], "l_spec": [], "l_gamma": [], "l_spacing": [],
                "target_gamma": [], "measured_gamma": [], "gamma_mae": [],
                "cv_nnd": [], "energy": [], "grad_norm": []
            }
            
        # 2. Dynamics and analyzers
        self.dynamics = DifferentiableSimulationEngine(
            energy_model=self.energy_model,
            num_steps=num_steps,
            dt=dt,
            L=1.0,
            use_checkpointing=True,
            integrator="verlet",
            damping=0.97,
            truncated_bptt_steps=min(25, num_steps)
        ).to(self.device)
        self.rasterizer = PeriodicGaussianSplat2D(grid_size=grid_size, sigma=sigma).to(self.device)
        self.analyzer = DifferentiableSpectralAnalyzer(grid_size=grid_size, sigma=sigma, f_min=f_min, f_max=f_max).to(self.device)
        self.loss_fn = CompositeSpectralLoss(
            lambda_spec=lambda_spec,
            lambda_gamma=lambda_gamma,
            lambda_spacing=lambda_spacing,
            n_particles=n_particles
        ).to(self.device)
        
        # 3. Optimizer & state
        self.lr = lr
        self.optimizer = torch.optim.Adam(self.energy_model.parameters(), lr=lr, weight_decay=weight_decay)
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(self.optimizer, T_0=200, T_mult=2, eta_min=1e-5)
        
        # Execution control flags
        self.is_training = False
        self.is_paused = False
        self.is_stopped = False

    def train_step(self, target_gamma_val: Optional[float] = None, batch_size: int = 4) -> Dict[str, Any]:
        """
        Executes a single stateful backpropagation step.
        Updates model parameters progressively.
        """
        self.energy_model.train()
        self.optimizer.zero_grad()
        
        device = self.device
        n_p = self.simulation_config["n_particles"]
        
        # 1. Prepare target gamma & initial points
        if target_gamma_val is not None:
            target_gamma = torch.full((batch_size,), target_gamma_val, device=device, dtype=torch.float32)
        else:
            target_gamma = sample_target_gamma(batch_size, -2.0, 2.0, device=device)
            
        pts_init = generate_uniform_random(batch_size, n_p, device=device)
        
        # 2. Forward simulation
        points_final = self.dynamics(pts_init, target_gamma)
        
        # 3. Rasterization & Spectrum
        density = self.rasterizer(points_final)
        spectral_out = self.analyzer(density)
        
        # 4. Multi-objective loss
        loss_dict = self.loss_fn(points_final, target_gamma, spectral_out)
        loss = loss_dict['loss']
        
        # 5. Backpropagate and update θ
        loss.backward()
        grad_norm = torch.nn.utils.clip_grad_norm_(self.energy_model.parameters(), max_norm=1.0).item()
        self.optimizer.step()
        self.scheduler.step()
        
        # 6. Record step stats
        self.iteration += 1
        measured_gamma_mean = float(spectral_out['gamma'].mean().item())
        target_gamma_mean = float(target_gamma.mean().item())
        gamma_mae = float(torch.mean(torch.abs(spectral_out['gamma'] - target_gamma)).item())
        
        step_metrics = {
            "iteration": self.iteration,
            "total_loss": float(loss.item()),
            "l_spec": float(loss_dict['l_spec']),
            "l_gamma": float(loss_dict['l_gamma']),
            "l_spacing": float(loss_dict['l_spacing']),
            "target_gamma": target_gamma_mean,
            "measured_gamma": measured_gamma_mean,
            "gamma_mae": gamma_mae,
            "cv_nnd": float(loss_dict['cv_nnd']),
            "grad_norm": float(grad_norm),
            "lr": float(self.optimizer.param_groups[0]['lr']),
            "device": str(self.device),
            "particles_sample": points_final[0].detach().cpu().numpy().tolist()
        }
        
        for k in ["iteration", "loss", "l_spec", "l_gamma", "l_spacing", "target_gamma", "measured_gamma", "gamma_mae", "cv_nnd", "grad_norm"]:
            metric_key = "loss" if k == "loss" else k
            val = step_metrics["total_loss"] if k == "loss" else step_metrics[k]
            if k in self.history:
                self.history[k].append(val)
                
        return step_metrics

    def save_checkpoint(self, filepath: str) -> str:
        """Saves current state (weights, optimizer, history) without reinitializing."""
        return self.save_checkpoint_fn(
            filepath=filepath,
            energy_model=self.energy_model,
            optimizer=self.optimizer,
            scheduler=self.scheduler,
            epoch=self.epoch,
            iteration=self.iteration,
            model_config=self.model_config,
            simulation_config=self.simulation_config,
            loss_config=self.loss_config,
            history=self.history
        )

    def load_checkpoint(self, filepath: str) -> None:
        """Restores training state from checkpoint without random reset."""
        self.energy_model, self.optimizer, self.scheduler, ckpt_data = self.load_checkpoint_fn(
            filepath, device=self.device, energy_model=self.energy_model, optimizer=self.optimizer, scheduler=self.scheduler
        )
        self.iteration = ckpt_data.get("iteration", self.iteration)
        self.epoch = ckpt_data.get("epoch", self.epoch)
        if "history" in ckpt_data and isinstance(ckpt_data["history"], dict):
            for k in ["iteration", "loss", "l_spec", "l_gamma", "l_spacing", "target_gamma", "measured_gamma", "gamma_mae", "cv_nnd", "energy", "grad_norm"]:
                if k not in self.history:
                    self.history[k] = []
                if k in ckpt_data["history"]:
                    self.history[k] = list(ckpt_data["history"][k])

    def export_canonical_model(self, filepath: str = "neurospectrum_model.pt") -> str:
        """Exports the frozen research-grade production model."""
        loss_list = self.history.get("loss", [])
        return self.save_canonical_fn(
            model=self.energy_model,
            save_path=filepath,
            model_config=self.model_config,
            extra_metadata={
                "total_training_iterations": self.iteration,
                "final_loss": loss_list[-1] if len(loss_list) > 0 else None,
                "trained_gamma_range": [-2.0, 2.0]
            }
        )


if __name__ == "__main__":
    train_continuous_gamma(num_epochs=80, batch_size=4)
