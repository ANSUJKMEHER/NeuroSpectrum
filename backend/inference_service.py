"""
inference_service.py — Inference & Simulation Service for NeuroSpectrum.

Manages the frozen model instance and executes particle simulations.
Ensures zero retraining occurs and delivers step-by-step trajectory streams.
"""

import os
import sys
import torch
import threading
from typing import Dict, Any, Optional, Union

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from inference import FrozenInferenceEngine
from checkpoints import get_optimal_device, get_device_name, inspect_checkpoint
from experience import ExperienceBuffer


class InferenceService:
    def __init__(self, default_model_path: str = "neurospectrum_model.pt"):
        self.device = torch.device("cpu") # Default to CPU for instant low-latency simulation
        self.model_path = default_model_path if os.path.exists(default_model_path) else "outputs/checkpoints/checkpoint_continuous_gamma.pt"
        self.engine: Optional[FrozenInferenceEngine] = None
        self.experience_buffer = ExperienceBuffer()
        self._model_lock = threading.Lock()  # Guards concurrent model load/run
        
        if os.path.exists(self.model_path):
            self.load_model(self.model_path)

    def load_model(self, model_path: str) -> Dict[str, Any]:
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
        
        with self._model_lock:
            self.model_path = model_path
            self.engine = FrozenInferenceEngine(model_path, device=self.device)
            return {
                "status": "loaded",
                "model_path": model_path,
                "device": str(self.device),
                "device_name": get_device_name(self.device),
                "is_frozen": self.engine.verify_parameters_frozen()
            }

    def run_simulation(
        self,
        target_gamma: float,
        initial_points: str = "random",
        n_particles: int = 256,
        num_steps: int = 50,
        dt: float = 0.02,
        capture_interval: int = 5,
        auto_adapt: bool = False,
        auto_converge: bool = False,
        convergence_threshold: float = 0.0015,
        min_steps: int = 60
    ) -> Dict[str, Any]:
        if self.engine is None:
            raise RuntimeError("No model is loaded for inference!")
        
        with self._model_lock:
            res = self.engine.run_inference(
                target_gamma=target_gamma,
                initial_points=initial_points,
                n_particles=n_particles,
                num_steps=num_steps,
                dt=dt,
                capture_interval=capture_interval,
                auto_converge=auto_converge,
                convergence_threshold=convergence_threshold,
                min_steps=min_steps
            )
        
        # Record simulation trajectory into Experience Buffer for future training
        run_record = self.experience_buffer.record_simulation(
            target_gamma=target_gamma,
            initial_distribution=initial_points if isinstance(initial_points, str) else "custom",
            n_particles=n_particles,
            trajectory=res.get("trajectory", []),
            final_metrics=res.get("results", {})
        )
        
        res["training_data"] = {
            "run_id": run_record["run_id"],
            "initial_energy": run_record["initial_energy"],
            "final_energy": run_record["final_energy"],
            "energy_dissipation": run_record["energy_dissipation"],
            "num_transitions": run_record["num_recorded_steps"],
            "total_accumulated_transitions": self.experience_buffer.total_transitions,
            "dataset_file": self.experience_buffer.dataset_file,
            "training_active": True,
            "help_further": "Experience logged. Future simulations benefit from this energy descent trajectory."
        }
        
        # Self-improve model online if requested
        if auto_adapt and "initial_points" in res:
            with self._model_lock:
                init_tensor = torch.tensor(res["initial_points"]).unsqueeze(0).to(self.device)
                adapt_res = self.experience_buffer.adapt_model_from_run(
                    energy_model=self.engine.model,
                    initial_points=init_tensor,
                    target_gamma=target_gamma,
                    device=self.device
                )
                res["training_data"]["adaptation"] = adapt_res
                # Refresh model snapshot after adaptation
                self.engine._param_snapshot = [p.clone().detach() for p in self.engine.model.parameters()]
            
        return res

    def generate_target_preview(self, target_spec: Dict[str, Any], n_points: int = 256) -> Dict[str, Any]:
        """Generates target point cloud coordinates for UI preview."""
        import numpy as np
        from universal_synthesizer import TargetGeometryFactory
        from spectrum import compute_continuous_point_spectrum

        tgt_type = target_spec.get("type", "text").lower()
        fill_mode = target_spec.get("fill_mode", "outline")
        if tgt_type == "text":
            text_str = target_spec.get("text", "NEURO").strip() or "NEURO"
            pts = TargetGeometryFactory.create_from_text(text_str, n_points=n_points)
            label = f"Text: \"{text_str}\""
        elif tgt_type == "shape":
            shape_name = target_spec.get("shape", "heart").lower()
            pts = TargetGeometryFactory.create_target(shape_name, n_points=n_points, fill_mode=fill_mode)
            label = f"Shape: {shape_name.capitalize()} ({fill_mode.capitalize()})"
        elif tgt_type in ["upload", "points", "draw", "image"]:
            img_b64 = target_spec.get("image_base64", "")
            raw_pts = target_spec.get("points", [])
            if img_b64:
                import io, base64
                from PIL import Image
                if "," in img_b64:
                    img_b64 = img_b64.split(",")[1]
                img_bytes = base64.b64decode(img_b64)
                img = Image.open(io.BytesIO(img_bytes))
                pts = TargetGeometryFactory.create_from_image(img, n_points=n_points)
                label = "Uploaded Image Silhouette"
            elif raw_pts and len(raw_pts) > 0:
                if tgt_type == "draw":
                    pts = TargetGeometryFactory.create_from_polyline(raw_pts, n_points=n_points)
                    label = "Hand-Drawn Contour"
                else:
                    pts = TargetGeometryFactory.create_from_point_cloud(raw_pts, n_points=n_points)
                    label = f"Uploaded Points ({len(pts)} pts)"
            else:
                pts = TargetGeometryFactory.create_target("star", n_points=n_points)
                label = "Star (Default)"
        elif tgt_type in ["gamma", "power_law"]:
            gamma_val = float(target_spec.get("gamma", 1.0))
            pts = TargetGeometryFactory.create_power_law_distribution(gamma_val, n_points=n_points)
            label = f"Normal Distribution (γ* = {gamma_val:+.2f})"
        else:
            pts = TargetGeometryFactory.create_target("heart", n_points=n_points)
            label = "Heart Shape"

        psd_2d, freqs, radial_p = compute_continuous_point_spectrum(pts)
        return {
            "points": pts.tolist(),
            "n_points": len(pts),
            "label": label,
            "psd_2d": psd_2d.tolist(),
            "radial_psd": radial_p,
            "frequencies": freqs
        }

    def run_universal_morph(
        self,
        source_points: Any,
        target_spec: Dict[str, Any],
        num_steps: int = 60,
        capture_interval: int = 2,
        lr: float = 0.04,
        repulsion_weight: float = 0.25,
        target_spacing: Optional[float] = None,
        n_particles: int = 256,
        spectral_weight: float = 0.15,
        adaptive_mode: str = "none",
        zone_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Runs universal any-to-any point morphing via Optimal Transport and Backpropagation
        with exact pairwise particle spacing preservation and anti-collision guarantees.
        """
        import numpy as np
        from universal_synthesizer import TargetGeometryFactory, UniversalBackpropMorpher

        # Parse source points
        if isinstance(source_points, str):
            src_str = source_points.lower()
            if src_str in ["spiral", "star", "heart", "butterfly", "infinity", "gear", "flower", "yinyang", "rings", "double_rings"]:
                src_pts = TargetGeometryFactory.create_target(src_str, n_points=n_particles)
            elif src_str == "grid":
                import data
                src_pts = data.generate_regular_grid(1, n_particles)[0].numpy()
            elif src_str == "jittered":
                import data
                src_pts = data.generate_jittered_grid(1, n_particles)[0].numpy()
            else:
                src_pts = np.random.uniform(0.05, 0.95, size=(n_particles, 2)).astype(np.float32)
        elif isinstance(source_points, dict) and "points" in source_points:
            raw_pts = source_points["points"]
            if len(raw_pts) > 1:
                src_pts = TargetGeometryFactory.create_from_polyline(raw_pts, n_points=256)
            else:
                src_pts = np.asarray(raw_pts, dtype=np.float32)
        elif isinstance(source_points, list):
            src_pts = np.asarray(source_points, dtype=np.float32)
        else:
            src_pts = np.asarray(source_points, dtype=np.float32)

        N = len(src_pts)

        # Parse target points
        tgt_type = target_spec.get("type", "text").lower()
        fill_mode = target_spec.get("fill_mode", "outline")
        if tgt_type == "text":
            text_str = target_spec.get("text", "NEURO").strip() or "NEURO"
            tgt_pts = TargetGeometryFactory.create_from_text(text_str, n_points=N)
        elif tgt_type == "shape":
            shape_name = target_spec.get("shape", "heart").lower()
            tgt_pts = TargetGeometryFactory.create_target(shape_name, n_points=N, fill_mode=fill_mode)
        elif tgt_type in ["upload", "points", "draw", "image"]:
            img_b64 = target_spec.get("image_base64", "")
            raw_pts = target_spec.get("points", [])
            if img_b64:
                import io, base64
                from PIL import Image
                if "," in img_b64:
                    img_b64 = img_b64.split(",")[1]
                img_bytes = base64.b64decode(img_b64)
                img = Image.open(io.BytesIO(img_bytes))
                tgt_pts = TargetGeometryFactory.create_from_image(img, n_points=N)
            elif raw_pts and len(raw_pts) > 0:
                if tgt_type == "draw":
                    tgt_pts = TargetGeometryFactory.create_from_polyline(raw_pts, n_points=N)
                else:
                    tgt_pts = TargetGeometryFactory.create_from_point_cloud(raw_pts, n_points=N)
            else:
                tgt_pts = TargetGeometryFactory.create_target("star", n_points=N)
        elif tgt_type in ["gamma", "power_law"]:
            gamma_val = float(target_spec.get("gamma", 1.0))
            tgt_pts = TargetGeometryFactory.create_power_law_distribution(gamma_val, n_points=N)
        else:
            tgt_pts = TargetGeometryFactory.create_target("heart", n_points=N)

        # Handle 1D contour gap issue by adjusting spacing
        is_1d_outline = False
        if tgt_type == "draw":
            is_1d_outline = True
        elif tgt_type == "shape" and fill_mode == "outline":
            is_1d_outline = True
            
        if target_spacing is None and is_1d_outline:
            # 1D curve perimeter is roughly O(1), so spacing is O(1/N).
            # We use a much smaller barrier radius to prevent gap tearing.
            target_spacing = 3.0 / max(N, 1)

        morpher = UniversalBackpropMorpher(lr=lr)
        res = morpher.morph(
            src_pts,
            tgt_pts,
            num_steps=num_steps,
            capture_interval=capture_interval,
            target_spacing=target_spacing,
            repulsion_weight=repulsion_weight,
            spectral_weight=spectral_weight,
            target_gamma=float(target_spec.get("gamma", 1.0)),
            adaptive_mode=adaptive_mode,
            zone_params=zone_params
        )
        return res
