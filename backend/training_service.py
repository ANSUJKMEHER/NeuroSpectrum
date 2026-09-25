"""
training_service.py — Background Training Worker & Stateful Session Manager.

Runs training asynchronously in a dedicated worker thread so the web API stays responsive.
Broadcasts per-step metrics and snapshots to connected WebSockets.
"""

import os
import sys
import time
import asyncio
import threading
from typing import Dict, Any, Optional, Callable, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from train import StatefulTrainingSession
from checkpoints import get_optimal_device, get_device_name, inspect_checkpoint


class TrainingService:
    def __init__(self):
        self.session: Optional[StatefulTrainingSession] = None
        self.is_running = False
        self.is_paused = False
        self.stop_requested = False
        self.target_iterations = 0
        self.start_time = 0.0
        self.on_training_completed_callback: Optional[Callable] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.subscribers: List[asyncio.Queue] = []
        self._lock = threading.Lock()  # Guards subscribers and state flags
        
        # Default initialization with pre-trained checkpoint if present
        default_ckpt = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "neurospectrum_model.pt")
        if not os.path.exists(default_ckpt):
            default_ckpt = "outputs/checkpoints/checkpoint_continuous_gamma.pt"
            
        if os.path.exists(default_ckpt):
            try:
                self.session = StatefulTrainingSession(checkpoint_path=default_ckpt)
            except Exception as e:
                print(f"[TrainingService] Notice: Could not pre-load checkpoint: {e}")
                self.session = StatefulTrainingSession()
        else:
            self.session = StatefulTrainingSession()

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_on_completed_callback(self, cb: Callable):
        self.on_training_completed_callback = cb

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        with self._lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _broadcast_sync(self, event: Dict[str, Any]):
        """Dispatches an event from the training worker thread into the async loop."""
        if self._loop is None:
            return
        with self._lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                pass

    def start_training(
        self,
        num_iterations: int = 50,
        target_gamma: Optional[float] = None,
        batch_size: int = 4,
        lr: float = 5e-4
    ) -> Dict[str, Any]:
        """Starts or continues training in background."""
        if self.is_running:
            if self.is_paused:
                self.is_paused = False
                self._broadcast_sync({
                    "type": "training_resumed",
                    "iteration": self.session.iteration if self.session else 0,
                    "target_iterations": self.target_iterations
                })
                return {"status": "resumed", "iteration": self.session.iteration if self.session else 0}
            return {"status": "already_running", "iteration": self.session.iteration if self.session else 0}

        self.is_running = True
        self.is_paused = False
        self.stop_requested = False
        self.target_iterations = num_iterations
        self.start_time = time.time()

        def worker():
            try:
                start_iter = self.session.iteration if self.session else 0
                self._broadcast_sync({
                    "type": "training_started",
                    "start_iteration": start_iter,
                    "target_iterations": num_iterations,
                    "target_gamma": target_gamma,
                    "batch_size": batch_size,
                    "lr": lr
                })
                
                for step_i in range(1, num_iterations + 1):
                    if self.stop_requested:
                        break
                        
                    while self.is_paused and not self.stop_requested:
                        time.sleep(0.1)
                        
                    if self.stop_requested:
                        break

                    step_metrics = self.session.train_step(
                        target_gamma_val=target_gamma,
                        batch_size=batch_size
                    )
                    
                    elapsed = time.time() - self.start_time
                    progress_pct = round((step_i / num_iterations) * 100.0, 1)
                    cur_it = step_metrics.get("iteration", self.session.iteration)
                    t_loss = step_metrics.get("total_loss", 0.0)

                    # Flat keys + nested metrics to support both legacy and direct callers
                    event_payload = {
                        "type": "training_step",
                        "iteration": cur_it,
                        "loss": t_loss,
                        "total_loss": t_loss,
                        "l_spec": step_metrics.get("l_spec", 0.0),
                        "l_gamma": step_metrics.get("l_gamma", 0.0),
                        "l_spacing": step_metrics.get("l_spacing", 0.0),
                        "cv_nnd": step_metrics.get("cv_nnd", 0.0),
                        "target_gamma": step_metrics.get("target_gamma", target_gamma if target_gamma is not None else 0.0),
                        "measured_gamma": step_metrics.get("measured_gamma", 0.0),
                        "gamma_mae": step_metrics.get("gamma_mae", 0.0),
                        "grad_norm": step_metrics.get("grad_norm", 0.0),
                        "lr": step_metrics.get("lr", lr),
                        "step_in_run": step_i,
                        "target_iterations": num_iterations,
                        "progress_pct": progress_pct,
                        "elapsed_seconds": round(elapsed, 1),
                        "metrics": step_metrics
                    }
                    
                    # Broadcast step
                    self._broadcast_sync(event_payload)
                    time.sleep(0.01)

                final_loss = self.session.history["loss"][-1] if (self.session and self.session.history.get("loss")) else 0.0
                
                # Auto-save canonical model on completion so new simulations immediately benefit
                try:
                    self.session.export_canonical_model(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "neurospectrum_model.pt"))
                except Exception as save_err:
                    print(f"[TrainingService] Warning: canonical export failed: {save_err}")

                if self.on_training_completed_callback:
                    try:
                        self.on_training_completed_callback()
                    except Exception as cb_err:
                        print(f"[TrainingService] Notice on_completed callback error: {cb_err}")
                
                self._broadcast_sync({
                    "type": "training_completed",
                    "final_iteration": self.session.iteration if self.session else 0,
                    "target_iterations": num_iterations,
                    "loss": final_loss,
                    "total_loss": final_loss,
                    "progress_pct": 100.0,
                    "message": "Training completed successfully. Model checkpoint updated."
                })
            except Exception as e:
                print(f"[TrainingService] Error in worker loop: {e}", flush=True)
                self._broadcast_sync({
                    "type": "training_error",
                    "error": str(e),
                    "iteration": self.session.iteration if self.session else 0
                })
            finally:
                self.is_running = False
                self.is_paused = False
                self.stop_requested = False

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        return {
            "status": "started",
            "iteration": self.session.iteration if self.session else 0,
            "target_iterations": num_iterations,
            "target_gamma": target_gamma
        }

    def pause(self) -> Dict[str, Any]:
        if self.is_running and not self.is_paused:
            self.is_paused = True
            self._broadcast_sync({"type": "training_paused", "iteration": self.session.iteration if self.session else 0})
            return {"status": "paused", "iteration": self.session.iteration if self.session else 0}
        return {"status": "not_running_or_already_paused"}

    def resume(self) -> Dict[str, Any]:
        if self.is_running and self.is_paused:
            self.is_paused = False
            self._broadcast_sync({"type": "training_resumed", "iteration": self.session.iteration if self.session else 0})
            return {"status": "resumed", "iteration": self.session.iteration if self.session else 0}
        return {"status": "not_paused"}

    def stop(self) -> Dict[str, Any]:
        if self.is_running:
            self.stop_requested = True
            if self._thread:
                self._thread.join(timeout=2.0)
            self.is_running = False
            self.is_paused = False
            self._broadcast_sync({"type": "training_stopped", "iteration": self.session.iteration if self.session else 0})
            return {"status": "stopped", "iteration": self.session.iteration if self.session else 0}
        return {"status": "not_running"}

    def reset_session(self) -> Dict[str, Any]:
        if self.is_running:
            self.stop()
        default_ckpt = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "neurospectrum_model.pt")
        if not os.path.exists(default_ckpt):
            default_ckpt = "outputs/checkpoints/checkpoint_continuous_gamma.pt"
        if os.path.exists(default_ckpt):
            self.session = StatefulTrainingSession(checkpoint_path=default_ckpt)
        else:
            self.session = StatefulTrainingSession()
        self._broadcast_sync({"type": "training_reset", "iteration": self.session.iteration})
        return {"status": "reset", "iteration": self.session.iteration}

    def save_checkpoint(self, filename: str = "checkpoint.pt") -> str:
        out_dir = "outputs/checkpoints"
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, filename)
        saved = self.session.save_checkpoint(path)
        self._broadcast_sync({"type": "checkpoint_saved", "path": saved, "iteration": self.session.iteration})
        return saved

    def load_checkpoint(self, filepath: str) -> Dict[str, Any]:
        if self.is_running:
            self.stop()
        self.session.load_checkpoint(filepath)
        self._broadcast_sync({"type": "checkpoint_loaded", "path": filepath, "iteration": self.session.iteration})
        return {"status": "loaded", "iteration": self.session.iteration}

    def get_status(self) -> Dict[str, Any]:
        dev = self.session.device if self.session else get_optimal_device()
        elapsed = time.time() - self.start_time if (self.is_running and self.start_time > 0) else 0.0
        cur_it = self.session.iteration if self.session else 0
        latest_metrics = {}
        if self.session and self.session.history.get("loss"):
            latest_metrics = {
                "iteration": cur_it,
                "loss": self.session.history["loss"][-1],
                "total_loss": self.session.history["loss"][-1],
                "l_spec": self.session.history.get("l_spec", [0])[-1] if self.session.history.get("l_spec") else 0.0,
                "l_gamma": self.session.history.get("l_gamma", [0])[-1] if self.session.history.get("l_gamma") else 0.0,
                "l_spacing": self.session.history.get("l_spacing", [0])[-1] if self.session.history.get("l_spacing") else 0.0,
                "cv_nnd": self.session.history.get("cv_nnd", [0])[-1] if self.session.history.get("cv_nnd") else 0.0,
                "measured_gamma": self.session.history.get("measured_gamma", [0])[-1] if self.session.history.get("measured_gamma") else 0.0,
            }
        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "current_iteration": cur_it,
            "target_iterations": self.target_iterations,
            "progress_pct": round((cur_it / max(1, self.target_iterations)) * 100.0, 1) if self.target_iterations > 0 else 0.0,
            "elapsed_seconds": round(elapsed, 1),
            "device": str(dev),
            "device_name": get_device_name(dev),
            "history_length": len(self.session.history.get("loss", [])) if self.session else 0,
            "latest_loss": self.session.history["loss"][-1] if (self.session and self.session.history.get("loss")) else None,
            "latest_metrics": latest_metrics,
            "loss_history": self.session.history.get("loss", [])[-50:] if self.session else []
        }

