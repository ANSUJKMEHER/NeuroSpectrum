"""
NeuroSpectrum: Learning Spectral Point Distributions with Neural Energy Fields.
"""

import os
mpl_cache = os.path.join(os.path.expanduser("~"), ".cache", "matplotlib")
try:
    os.makedirs(mpl_cache, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", mpl_cache)
except Exception:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp")

from .data import generate_uniform_random, generate_poisson_disk, generate_clustered_red_noise, sample_target_gamma
from .energy import NeuralPairwiseEnergy, PhysicsConsistentNeuralEnergy
from .dynamics import DifferentiableSimulationEngine, compute_neural_force, step_euler, step_velocity_verlet
from .rasterize import PeriodicGaussianSplat2D, gaussian_splat
from .spectrum import DifferentiableSpectralAnalyzer
from .losses import CompositeSpectralLoss
from .baseline import ClassicalPerTargetOptimizer
from .evaluate import compute_pair_correlation_2d, compute_spatial_statistics
from .visualize import plot_point_set_analysis, plot_training_history
from .applications import (
    generate_synthetic_stipple_target,
    sample_importance_points,
    neural_stipple_relaxation,
    evaluate_monte_carlo_integration
)
from .universal_synthesizer import (
    SinkhornOptimalTransport,
    DifferentiableChamferLoss,
    TargetGeometryFactory,
    UniversalBackpropMorpher,
    VectorFieldFlowMatching,
    plot_universal_morphing_trajectory
)

__all__ = [
    "generate_uniform_random",
    "generate_poisson_disk",
    "generate_clustered_red_noise",
    "sample_target_gamma",
    "NeuralPairwiseEnergy",
    "DifferentiableSimulationEngine",
    "compute_neural_force",
    "step_euler",
    "PeriodicGaussianSplat2D",
    "gaussian_splat",
    "DifferentiableSpectralAnalyzer",
    "CompositeSpectralLoss",
    "ClassicalPerTargetOptimizer",
    "compute_pair_correlation_2d",
    "compute_spatial_statistics",
    "plot_point_set_analysis",
    "plot_training_history",
    "generate_synthetic_stipple_target",
    "sample_importance_points",
    "neural_stipple_relaxation",
    "evaluate_monte_carlo_integration",
    "SinkhornOptimalTransport",
    "DifferentiableChamferLoss",
    "TargetGeometryFactory",
    "UniversalBackpropMorpher",
    "VectorFieldFlowMatching",
    "plot_universal_morphing_trajectory"
]
