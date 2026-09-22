"""
generalize.py — Generalization & Amortized Efficiency Benchmarking for NeuroSpectrum.

Executes and reports:
1. New-Input Generalization (Random vs Spiral vs Grid vs Jittered under frozen E_theta)
2. Unseen-Gamma Generalization (Interpolation vs Extrapolation)
3. Amortized Efficiency Comparison (Neural Frozen Inference vs Classical Optimization)
"""

import time
import torch
import numpy as np
from typing import Dict, Any, List, Optional

from inference import FrozenInferenceEngine
from baseline import ClassicalPerTargetOptimizer
from checkpoints import get_optimal_device, load_checkpoint


def run_new_input_generalization_benchmark(
    engine: FrozenInferenceEngine,
    target_gamma: float = 1.0,
    n_particles: int = 256,
    num_steps: int = 40
) -> List[Dict[str, Any]]:
    """
    Tests whether the FROZEN interaction law can transform completely unseen initial topologies.
    Inputs tested: Uniform Random, Archimedean Spiral, Regular Grid, Jittered Grid, Clustered Red.
    """
    configurations = [
        ("Uniform Random (Training Distribution)", "random"),
        ("Archimedean Spiral (Challenging Geometric)", "spiral"),
        ("Regular Grid (Crystalline Ordered)", "grid"),
        ("Jittered Grid (Partially Perturbed)", "jittered"),
        ("Clustered Gaussian (High Variance)", "clustered")
    ]
    
    benchmark_results = []
    
    for label, init_name in configurations:
        res = engine.run_inference(
            target_gamma=target_gamma,
            initial_points=init_name,
            n_particles=n_particles,
            num_steps=num_steps,
            capture_interval=num_steps // 4
        )
        
        benchmark_results.append({
            "input_distribution": label,
            "distribution_key": init_name,
            "target_gamma": target_gamma,
            "measured_gamma": res["results"]["measured_gamma_hat"],
            "absolute_error": res["results"]["absolute_error"],
            "psd_mse": res["results"]["psd_mse"],
            "cv_nnd": res["results"]["cv_nnd"],
            "runtime_ms": res["results"]["runtime_seconds"] * 1000.0,
            "retraining_performed": "NO (FROZEN)",
            "target_reached": res["results"]["target_reached"],
            "final_points": res["final_points"]
        })
        
    return benchmark_results


def run_unseen_gamma_interpolation_benchmark(
    engine: FrozenInferenceEngine,
    n_particles: int = 256,
    num_steps: int = 40
) -> Dict[str, Any]:
    """
    Evaluates model on held-out target gamma values not explicitly fixed during training.
    Separates interpolation from extrapolation.
    """
    interpolation_gammas = [-1.50, -0.70, 0.30, 0.80, 1.50]
    extrapolation_gammas = [-2.50, 2.50]
    
    interp_records = []
    for g in interpolation_gammas:
        res = engine.run_inference(
            target_gamma=g,
            initial_points="random",
            n_particles=n_particles,
            num_steps=num_steps
        )
        interp_records.append({
            "regime": "Interpolation",
            "target_gamma": g,
            "measured_gamma": res["results"]["measured_gamma_hat"],
            "absolute_error": res["results"]["absolute_error"],
            "psd_mse": res["results"]["psd_mse"],
            "cv_nnd": res["results"]["cv_nnd"],
            "runtime_ms": res["results"]["runtime_seconds"] * 1000.0,
            "retraining_performed": "NO",
            "target_reached": res["results"]["target_reached"]
        })
        
    extrap_records = []
    for g in extrapolation_gammas:
        res = engine.run_inference(
            target_gamma=g,
            initial_points="random",
            n_particles=n_particles,
            num_steps=num_steps
        )
        extrap_records.append({
            "regime": "Extrapolation",
            "target_gamma": g,
            "measured_gamma": res["results"]["measured_gamma_hat"],
            "absolute_error": res["results"]["absolute_error"],
            "psd_mse": res["results"]["psd_mse"],
            "cv_nnd": res["results"]["cv_nnd"],
            "runtime_ms": res["results"]["runtime_seconds"] * 1000.0,
            "retraining_performed": "NO",
            "target_reached": res["results"]["target_reached"]
        })
        
    mean_interp_mae = float(np.mean([r["absolute_error"] for r in interp_records]))
    mean_extrap_mae = float(np.mean([r["absolute_error"] for r in extrap_records]))
    
    return {
        "interpolation_results": interp_records,
        "extrapolation_results": extrap_records,
        "mean_interpolation_mae": mean_interp_mae,
        "mean_extrapolation_mae": mean_extrap_mae,
        "retraining_performed": "NO (FROZEN)"
    }


def run_amortized_cost_comparison(
    engine: FrozenInferenceEngine,
    test_gammas: List[float] = [-1.0, 0.0, 1.0],
    n_particles: int = 256,
    classical_iters: int = 80,
    neural_steps: int = 40
) -> List[Dict[str, Any]]:
    """
    Compares wall-clock cost:
    Classical per-target optimizer (Zhou et al. 2012) vs. Frozen Neural Model inference.
    """
    baseline_optimizer = ClassicalPerTargetOptimizer(
        grid_size=64,
        sigma=0.02,
        n_particles=n_particles
    )
    
    comparison_table = []
    
    for g in test_gammas:
        # 1. Classical per-target optimization
        pts_init = torch.rand(1, n_particles, 2)
        opt_res = baseline_optimizer.optimize(
            points_init=pts_init,
            target_gamma_val=g,
            num_iters=classical_iters,
            lr=0.01
        )
        classical_time_ms = opt_res["wall_clock_time"] * 1000.0
        opt_gamma_hat = opt_res["measured_gamma"]
        
        # 2. Neural frozen inference
        t1 = time.perf_counter()
        neural_res = engine.run_inference(
            target_gamma=g,
            initial_points="random",
            n_particles=n_particles,
            num_steps=neural_steps
        )
        neural_time_ms = (time.perf_counter() - t1) * 1000.0
        
        speedup = classical_time_ms / max(neural_time_ms, 1e-4)
        
        comparison_table.append({
            "target_gamma": g,
            "classical_gamma_hat": float(opt_gamma_hat),
            "classical_error": float(abs(opt_gamma_hat - g)),
            "classical_time_ms": float(classical_time_ms),
            "neural_gamma_hat": float(neural_res["results"]["measured_gamma_hat"]),
            "neural_error": float(neural_res["results"]["absolute_error"]),
            "neural_time_ms": float(neural_time_ms),
            "speedup_factor": float(speedup)
        })
        
    return comparison_table
