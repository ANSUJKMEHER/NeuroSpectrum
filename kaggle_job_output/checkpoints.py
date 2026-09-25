"""
checkpoints.py — Model and Checkpoint Lifecycle Management for NeuroSpectrum.

Handles:
- Stateful checkpoint saving and loading (model, optimizer, scheduler, epoch, history)
- Device autodetection and cross-device remap (Apple MPS / CUDA / CPU)
- Architecture metadata inspection (parameters, layers, conditioning)
- Canonical release export: 'neurospectrum_model.pt'
- Safe resumption without reinitialization
"""

import os
import time
import torch
import torch.nn as nn
from typing import Dict, Any, Tuple, Optional

from energy import NeuralPairwiseEnergy


def get_optimal_device() -> torch.device:
    """
    Detects best available computing backend:
    1. CUDA (NVIDIA GPU)
    2. MPS (Apple Silicon GPU)
    3. CPU (Fallback)
    """
    if torch.cuda.is_available():
        return torch.device("cuda")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    else:
        return torch.device("cpu")


def get_device_name(device: torch.device) -> str:
    """Human-readable hardware label."""
    if device.type == "cuda":
        return f"NVIDIA CUDA ({torch.cuda.get_device_name(0)})"
    elif device.type == "mps":
        return "Apple Silicon (MPS Acceleration)"
    else:
        return "Host CPU"


def get_model_metadata(model: NeuralPairwiseEnergy, model_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract structural metadata and parameter statistics."""
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    
    config = model_config or {
        "hidden_dim": getattr(model, "hidden_dim", 64),
        "num_layers": getattr(model, "num_layers", 3),
        "num_rbf": getattr(model, "num_rbf", 16),
        "r_cut": getattr(model, "r_cut", 0.5),
        "use_divergence_prior": getattr(model, "use_divergence_prior", True),
        "eps_divergence": getattr(model, "eps_divergence", 0.02),
        "r_repulsion": getattr(model, "r_repulsion", 0.04)
    }
    
    return {
        "model_name": "NeuroSpectrum Physics-Consistent Neural Energy Field",
        "total_parameters": total_params,
        "trainable_parameters": trainable_params,
        "architecture": "RBF + FiLM + Cosine Cutoff",
        "input_features": ["gaussian_rbf_distance_basis", "fourier_gamma_embedding"],
        "hidden_dim": config.get("hidden_dim", 64),
        "num_rbf": config.get("num_rbf", 16),
        "r_cut": config.get("r_cut", 0.5),
        "num_layers": config.get("num_layers", 3),
        "activation": "SiLU (Swish)",
        "use_divergence_prior": config.get("use_divergence_prior", True),
        "eps_divergence": config.get("eps_divergence", 0.02),
        "r_repulsion": config.get("r_repulsion", 0.04),
        "conditioning_range": [-2.0, 2.0]
    }


def save_checkpoint(
    filepath: str,
    energy_model: NeuralPairwiseEnergy,
    optimizer: Optional[torch.optim.Optimizer] = None,
    scheduler: Optional[Any] = None,
    epoch: int = 0,
    iteration: int = 0,
    model_config: Optional[Dict[str, Any]] = None,
    simulation_config: Optional[Dict[str, Any]] = None,
    loss_config: Optional[Dict[str, Any]] = None,
    history: Optional[Dict[str, Any]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Saves stateful checkpoint containing all weights, optimizer state, and experiment parameters.
    """
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    
    metadata = get_model_metadata(energy_model, model_config)
    if extra_metadata:
        metadata.update(extra_metadata)
    metadata["timestamp"] = time.time()
    metadata["formatted_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    metadata["active_device"] = str(next(energy_model.parameters()).device)
    
    checkpoint = {
        "energy_model_state_dict": energy_model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict() if optimizer is not None else None,
        "scheduler_state_dict": scheduler.state_dict() if scheduler is not None else None,
        "epoch": epoch,
        "iteration": iteration,
        "model_config": model_config or {},
        "simulation_config": simulation_config or {},
        "loss_config": loss_config or {},
        "history": history or {},
        "metadata": metadata,
        "version": "1.0.0"
    }
    
    torch.save(checkpoint, filepath)
    return filepath


def load_checkpoint(
    filepath: str,
    device: Optional[torch.device] = None,
    energy_model: Optional[NeuralPairwiseEnergy] = None,
    optimizer: Optional[torch.optim.Optimizer] = None,
    scheduler: Optional[Any] = None
) -> Tuple[NeuralPairwiseEnergy, Optional[torch.optim.Optimizer], Optional[Any], Dict[str, Any]]:
    """
    Loads checkpoint and restores exact model parameters and optimizer state.
    If energy_model is not provided, instantiates one with the stored configuration.
    """
    if device is None:
        device = get_optimal_device()
        
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Checkpoint not found at: {filepath}")
        
    checkpoint = torch.load(filepath, map_location=device)
    
    # 1. Instantiate or verify model
    if energy_model is None:
        cfg = checkpoint.get("model_config", {})
        hidden_dim = cfg.get("hidden_dim", 64)
        num_layers = cfg.get("num_layers", 3)
        num_rbf = cfg.get("num_rbf", 16)
        r_cut = cfg.get("r_cut", 0.5)
        use_divergence_prior = cfg.get("use_divergence_prior", True)
        eps_divergence = cfg.get("eps_divergence", 0.02)
        r_repulsion = cfg.get("r_repulsion", 0.04)
        
        energy_model = NeuralPairwiseEnergy(
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            num_rbf=num_rbf,
            r_cut=r_cut,
            use_divergence_prior=use_divergence_prior,
            eps_divergence=eps_divergence,
            r_repulsion=r_repulsion
        ).to(device)
    else:
        energy_model = energy_model.to(device)
        
    # Support both full checkpoint schema and legacy checkpoints
    state_dict = checkpoint.get("energy_model_state_dict", checkpoint)
    energy_model.load_state_dict(state_dict)
    
    # 2. Restore optimizer if provided and state exists
    if optimizer is not None and checkpoint.get("optimizer_state_dict") is not None:
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        
    # 3. Restore scheduler if provided
    if scheduler is not None and checkpoint.get("scheduler_state_dict") is not None:
        scheduler.load_state_dict(checkpoint["scheduler_state_dict"])
        
    return energy_model, optimizer, scheduler, checkpoint


def save_canonical_model(
    model: NeuralPairwiseEnergy,
    save_path: str = "neurospectrum_model.pt",
    model_config: Optional[Dict[str, Any]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Saves the canonical frozen research-grade production model 'neurospectrum_model.pt'.
    """
    return save_checkpoint(
        filepath=save_path,
        energy_model=model,
        model_config=model_config,
        extra_metadata=extra_metadata
    )


def inspect_checkpoint(filepath: str) -> Dict[str, Any]:
    """Reads metadata from checkpoint without executing model computation."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
        
    data = torch.load(filepath, map_location="cpu")
    if isinstance(data, dict) and "metadata" in data:
        return {
            "metadata": data["metadata"],
            "epoch": data.get("epoch", 0),
            "iteration": data.get("iteration", 0),
            "model_config": data.get("model_config", {}),
            "simulation_config": data.get("simulation_config", {}),
            "loss_config": data.get("loss_config", {})
        }
    else:
        # Legacy checkpoint
        state_dict = data.get("energy_model_state_dict", data)
        param_count = sum(p.numel() for p in state_dict.values())
        return {
            "metadata": {
                "model_name": "Legacy Checkpoint",
                "total_parameters": param_count,
                "note": "Pre-v1.0 checkpoint loaded"
            },
            "epoch": 0,
            "iteration": 0
        }
