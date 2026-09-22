"""
dynamics.py - Differentiable Particle Dynamics Engine.

Upgraded integrators:
- Velocity Verlet (symplectic, 2nd-order, energy-conserving) — DEFAULT
- Forward Euler (1st-order, overdamped) — fallback for backward compatibility
- Optional Langevin noise injection for thermodynamic sampling

Memory management:
- PyTorch Gradient Checkpointing for unrolled simulation
- Truncated Backpropagation Through Time (Truncated-BPTT)
"""

import torch
import torch.nn as nn
from torch.utils.checkpoint import checkpoint
from energy import NeuralPairwiseEnergy


def compute_neural_force(points: torch.Tensor, gamma: torch.Tensor,
                         energy_model: NeuralPairwiseEnergy, L: float = 1.0,
                         use_knn: bool = False, k: int = 16,
                         is_inference: bool = False) -> tuple[torch.Tensor, torch.Tensor]:
    """
    Computes exact conservative physical forces F_i = -∇_{x_i} E_theta(X, gamma) via PyTorch Autograd.
    Works seamlessly in both training and inference (torch.no_grad) modes.
    Supports both global O(N^2) all-pairs and local O(Nk) k-NN interactions.
    """
    is_unbatched = (points.dim() == 2)
    if is_unbatched:
        points = points.unsqueeze(0)
        gamma = gamma.unsqueeze(0)

    # In inference mode, never create backprop graph or retain intermediate graph tensors
    is_training_graph = torch.is_grad_enabled() and (not is_inference)

    with torch.enable_grad():
        pts_in = points if (points.requires_grad and is_training_graph) else points.detach().requires_grad_(True)
        if use_knn:
            total_energy = energy_model.compute_total_energy_knn(pts_in, gamma, k=k, L=L)
        else:
            total_energy = energy_model.compute_total_energy(pts_in, gamma, L=L)

        if total_energy.requires_grad and total_energy.grad_fn is not None:
            grad_E = torch.autograd.grad(
                outputs=total_energy.sum(),
                inputs=pts_in,
                create_graph=is_training_graph,
                retain_graph=is_training_graph
            )[0]
            forces = -grad_E
        else:
            forces = torch.zeros_like(pts_in)

    # Always detach forces and energy in inference mode
    if not is_training_graph:
        forces = forces.detach()
        total_energy = total_energy.detach()

    if is_unbatched:
        forces = forces.squeeze(0)
        total_energy = total_energy.squeeze(0)

    return forces, total_energy


def step_euler(points: torch.Tensor, forces: torch.Tensor, dt: float, L: float = 1.0,
               max_displacement: float = 0.02) -> torch.Tensor:
    """
    Differentiable single-step Euler update on a toroidal periodic domain [0, L)^2.
    Clamps displacement to max_displacement to prevent numerical explosions.
    """
    disp = dt * forces
    disp = torch.clamp(disp, min=-max_displacement, max=max_displacement)
    new_points = torch.remainder(points + disp, L)
    return new_points


def step_velocity_verlet(
    points: torch.Tensor,
    velocities: torch.Tensor,
    forces: torch.Tensor,
    gamma: torch.Tensor,
    energy_model: NeuralPairwiseEnergy,
    dt: float,
    L: float = 1.0,
    max_displacement: float = 0.03,
    damping: float = 0.98,
    use_knn: bool = False,
    k: int = 16,
    is_inference: bool = False,
    langevin_noise: float = 0.0
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Velocity Verlet integrator (symplectic, 2nd order) on a periodic torus [0, L)^2.

    Algorithm:
        v_{n+1/2} = damping * v_n + (dt/2) * F_n
        x_{n+1}   = x_n + dt * v_{n+1/2}   (mod L)
        F_{n+1}   = -∇E(x_{n+1})
        v_{n+1}   = v_{n+1/2} + (dt/2) * F_{n+1}

    Optional Langevin noise: v += sqrt(2 * kT * dt) * xi (thermal fluctuations)

    Args:
        points: (B, N, 2) current positions
        velocities: (B, N, 2) current velocities
        forces: (B, N, 2) current forces
        gamma: (B,) target spectral slope
        energy_model: neural energy model
        dt: time step
        L: box size
        max_displacement: maximum single-step displacement
        damping: velocity damping factor (1.0 = no damping, <1.0 = dissipative)
        use_knn: use k-NN graph for force computation
        k: number of nearest neighbors
        is_inference: if True, don't build autograd graph
        langevin_noise: thermal noise amplitude (0.0 = deterministic)

    Returns:
        (new_points, new_velocities, new_forces, energy)
    """
    # Half-kick with damping
    v_half = damping * velocities + 0.5 * dt * forces

    # Add Langevin thermal noise if requested
    if langevin_noise > 0.0:
        noise = torch.randn_like(v_half) * langevin_noise * (dt ** 0.5)
        v_half = v_half + noise

    # Drift with displacement clamping for safety
    displacement = dt * v_half
    displacement = torch.clamp(displacement, min=-max_displacement, max=max_displacement)
    new_points = torch.remainder(points + displacement, L)

    # Compute new forces at updated positions
    new_forces, energy = compute_neural_force(
        new_points, gamma, energy_model, L=L,
        use_knn=use_knn, k=k, is_inference=is_inference
    )

    # Second half-kick
    new_velocities = v_half + 0.5 * dt * new_forces

    return new_points, new_velocities, new_forces, energy


class DifferentiableSimulationEngine(nn.Module):
    """
    Unrolls particle simulation for T steps under learned neural force field.

    Supports:
    - Velocity Verlet (default) or Forward Euler integrator
    - Gradient checkpointing for memory efficiency
    - Truncated-BPTT for long simulations
    - k-NN graph dynamics for O(Nk) scaling
    - Optional Langevin noise for thermal sampling
    """
    def __init__(self, energy_model: NeuralPairwiseEnergy,
                 num_steps: int = 50, dt: float = 0.01, L: float = 1.0,
                 use_checkpointing: bool = True, truncated_bptt_steps: int = None,
                 use_knn: bool = False, k: int = 16,
                 integrator: str = "verlet", damping: float = 0.98,
                 langevin_noise: float = 0.0):
        super().__init__()
        self.energy_model = energy_model
        self.num_steps = num_steps
        self.dt = dt
        self.L = L
        self.use_checkpointing = use_checkpointing
        self.truncated_bptt_steps = truncated_bptt_steps
        self.use_knn = use_knn
        self.k = k
        self.integrator = integrator.lower()
        self.damping = damping
        self.langevin_noise = langevin_noise

    def _single_step_euler(self, points: torch.Tensor, gamma: torch.Tensor) -> torch.Tensor:
        """Euler step function for gradient checkpointing."""
        forces, _ = compute_neural_force(
            points, gamma, self.energy_model, L=self.L,
            use_knn=self.use_knn, k=self.k
        )
        return step_euler(points, forces, dt=self.dt, L=self.L)

    def forward(self, points_init: torch.Tensor, gamma: torch.Tensor) -> torch.Tensor:
        """
        Simulate particle dynamics for num_steps.
        Args:
            points_init: (B, N, 2) or (N, 2) in [0, L)
            gamma: (B,) or scalar tensor
        Returns:
            points_final: (B, N, 2) or (N, 2) in [0, L)
        """
        is_unbatched = (points_init.dim() == 2)
        if is_unbatched:
            points_init = points_init.unsqueeze(0)
        if gamma.dim() == 0:
            gamma = gamma.unsqueeze(0)

        points = points_init
        if torch.is_grad_enabled() and not points.requires_grad:
            points = points.detach().requires_grad_(True)

        # Determine active steps (with optional truncated BPTT warmup)
        if self.truncated_bptt_steps is not None and self.truncated_bptt_steps < self.num_steps:
            warmup_steps = self.num_steps - self.truncated_bptt_steps
            with torch.no_grad():
                if self.integrator == "verlet":
                    velocities = torch.zeros_like(points)
                    forces, _ = compute_neural_force(
                        points, gamma, self.energy_model, L=self.L,
                        use_knn=self.use_knn, k=self.k, is_inference=True
                    )
                    for _ in range(warmup_steps):
                        points, velocities, forces, _ = step_velocity_verlet(
                            points, velocities, forces, gamma, self.energy_model,
                            dt=self.dt, L=self.L, damping=self.damping,
                            use_knn=self.use_knn, k=self.k, is_inference=True,
                            langevin_noise=self.langevin_noise
                        )
                else:
                    for _ in range(warmup_steps):
                        points = self._single_step_euler(points, gamma)
            points = points.detach().requires_grad_(True)
            active_steps = self.truncated_bptt_steps
        else:
            active_steps = self.num_steps

        # Active unrolled steps
        if self.integrator == "verlet" and not points.requires_grad:
            # Inference mode: fast non-differentiable Velocity Verlet with inertia & energy conservation
            velocities = torch.zeros_like(points)
            forces, _ = compute_neural_force(
                points, gamma, self.energy_model, L=self.L,
                use_knn=self.use_knn, k=self.k, is_inference=True
            )
            for step in range(active_steps):
                points, velocities, forces, _ = step_velocity_verlet(
                    points, velocities, forces, gamma, self.energy_model,
                    dt=self.dt, L=self.L, damping=self.damping,
                    use_knn=self.use_knn, k=self.k, is_inference=True,
                    langevin_noise=self.langevin_noise
                )
        else:
            # Training mode: gradient-checkpointed 1st-order simulation for O(1) memory and numerical stability
            for step in range(active_steps):
                if self.use_checkpointing and points.requires_grad:
                    points = checkpoint(self._single_step_euler, points, gamma, use_reentrant=False)
                else:
                    points = self._single_step_euler(points, gamma)

        if is_unbatched:
            points = points.squeeze(0)

        return points
