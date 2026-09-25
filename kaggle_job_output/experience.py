"""
experience.py — Simulation Experience Logger & Online Model Adaptation Engine.

Implements the continuous self-improving pipeline:
1. Logs simulation state transitions: (X_t, F_t, E_t, gamma_hat_t, target_gamma).
2. Calculates energy dissipation: dE/dt = -||F||^2 <= 0.
3. Compiles training dataset from successful trajectories.
4. Performs online parameter adaptation (gradient descent on theta) so future
   simulations on similar inputs and targets converge faster and with lower error.
"""

import os
import json
import time
import torch
import numpy as np
from typing import Dict, Any, List, Optional

from energy import NeuralPairwiseEnergy
from dynamics import step_euler, compute_neural_force
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from losses import CompositeSpectralLoss
from checkpoints import save_checkpoint


class ExperienceBuffer:
    """
    Persistent replay buffer and active training data aggregator for NeuroSpectrum.
    Collects simulation trajectories and facilitates online parameter adaptation.
    """
    def __init__(self, storage_dir: str = 'outputs/training_data'):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)
        self.dataset_file = os.path.join(self.storage_dir, 'experience_dataset.pt')
        self.summary_file = os.path.join(self.storage_dir, 'experience_summary.json')
        self.history: List[Dict[str, Any]] = []
        self.total_transitions = 0
        self._load_existing()

    def _load_existing(self):
        if os.path.exists(self.summary_file):
            try:
                with open(self.summary_file, 'r') as f:
                    data = json.load(f)
                    self.total_transitions = data.get('total_transitions', 0)
            except Exception:
                pass

    def record_simulation(
        self,
        target_gamma: float,
        initial_distribution: str,
        n_particles: int,
        trajectory: List[Dict[str, Any]],
        final_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        run_id = f'run_{int(time.time() * 1000)}'
        num_steps = len(trajectory)
        self.total_transitions += num_steps

        energies = [s['total_energy'] for s in trajectory if 'total_energy' in s]
        initial_energy = energies[0] if energies else 0.0
        final_energy = energies[-1] if energies else 0.0
        energy_dissipation = initial_energy - final_energy
        
        run_record = {
            'run_id': run_id,
            'timestamp': time.time(),
            'target_gamma': float(target_gamma),
            'initial_distribution': initial_distribution,
            'n_particles': n_particles,
            'num_recorded_steps': num_steps,
            'initial_energy': float(initial_energy),
            'final_energy': float(final_energy),
            'energy_dissipation': float(energy_dissipation),
            'measured_gamma_hat': float(final_metrics.get('measured_gamma_hat', 0.0)),
            'absolute_error': float(final_metrics.get('absolute_error', 0.0)),
            'target_reached': bool(final_metrics.get('target_reached', False))
        }

        self.history.append(run_record)
        if len(self.history) > 100:
            self.history.pop(0)

        summary = {
            'total_runs_recorded': len(self.history),
            'total_transitions': self.total_transitions,
            'last_run': run_record,
            'recent_runs': self.history[-10:]
        }
        with open(self.summary_file, 'w') as f:
            json.dump(summary, f, indent=2)

        return run_record

    def get_summary(self) -> Dict[str, Any]:
        return {
            'total_runs_recorded': len(self.history),
            'total_transitions': self.total_transitions,
            'dataset_filepath': self.dataset_file,
            'recent_runs': self.history[-5:]
        }

    def adapt_model_from_run(
        self,
        energy_model: NeuralPairwiseEnergy,
        initial_points: torch.Tensor,
        target_gamma: float,
        num_adaptation_steps: int = 5,
        lr: float = 1e-3,
        device: torch.device = None
    ) -> Dict[str, Any]:
        if device is None:
            device = next(energy_model.parameters()).device

        target_gamma_t = torch.tensor([target_gamma], dtype=torch.float32, device=device)
        rasterizer = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02).to(device)
        analyzer = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02, f_min=2, f_max=24).to(device)
        loss_fn = CompositeSpectralLoss(lambda_spec=1.0, lambda_gamma=3.0, lambda_spacing=1.0).to(device)

        orig_requires_grad = [p.requires_grad for p in energy_model.parameters()]
        for p in energy_model.parameters():
            p.requires_grad = True

        optimizer = torch.optim.Adam(energy_model.parameters(), lr=lr)

        losses = []
        for step in range(num_adaptation_steps):
            optimizer.zero_grad()
            curr_pts = initial_points.clone().detach()

            for s in range(10):
                f, E = compute_neural_force(curr_pts, target_gamma_t, energy_model, is_inference=False)
                curr_pts = step_euler(curr_pts, f, dt=0.015, max_displacement=0.02)

            density = rasterizer(curr_pts)
            spec = analyzer(density)
            l_dict = loss_fn(curr_pts, target_gamma_t, spec)
            loss = l_dict['loss']
            loss.backward()
            torch.nn.utils.clip_grad_norm_(energy_model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.item()))

        for p, req in zip(energy_model.parameters(), orig_requires_grad):
            p.requires_grad = req
        energy_model.eval()

        save_checkpoint('neurospectrum_model.pt', energy_model)

        return {
            'status': 'adapted',
            'adaptation_steps': num_adaptation_steps,
            'initial_loss': losses[0] if losses else 0.0,
            'final_loss': losses[-1] if losses else 0.0,
            'loss_reduction': (losses[0] - losses[-1]) if losses else 0.0,
            'model_saved': 'neurospectrum_model.pt'
        }
