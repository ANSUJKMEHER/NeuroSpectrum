"""
api.py — FastAPI Application & WebSocket Service for NeuroSpectrum.

Exposes research endpoints and live WebSocket streaming:
- Stateful Training lifecycle (Start, Pause, Resume, Save, Load)
- Frozen Inference with particle trajectory streaming
- Learned Interaction Laws interpretability (E(r,γ), F(r,γ), Phase Diagrams)
- Generalization & Amortized Cost benchmarks
- Live status and hardware detection (Apple MPS / CUDA / CPU)
"""

import os
import sys
import time
import asyncio
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

# Ensure src/ and backend/ are on Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from checkpoints import get_optimal_device, get_device_name, inspect_checkpoint, get_model_metadata
from interpret import compute_energy_and_force, compute_phase_diagram, compute_multi_gamma_curves
from generalize import (
    run_new_input_generalization_benchmark,
    run_unseen_gamma_interpolation_benchmark,
    run_amortized_cost_comparison
)
from figure2_benchmark import generate_figure2_benchmark, generate_figure2_image_bytes

from training_service import TrainingService
from inference_service import InferenceService

app = FastAPI(
    title="NeuroSpectrum Research Dashboard API",
    description="Differentiable Neural Simulation of Continuous Spectral Point Distributions",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

training_service = TrainingService()
inference_service = InferenceService()


# --- Pydantic Request Models ---
class TrainingStartRequest(BaseModel):
    num_iterations: int = 50
    target_gamma: Optional[float] = None
    batch_size: int = 4
    lr: float = 5e-4


class InferenceRunRequest(BaseModel):
    target_gamma: float = 1.0
    initial_distribution: str = "random"
    n_particles: int = 256
    num_steps: int = 50
    dt: float = 0.02
    capture_interval: int = 5
    auto_adapt: bool = False
    auto_converge: bool = False
    convergence_threshold: float = 0.0015
    min_steps: int = 60


class InitialPointsRequest(BaseModel):
    distribution: str = "spiral"
    n_particles: int = 256


class ModelLoadRequest(BaseModel):
    checkpoint_path: str


class CheckpointSaveRequest(BaseModel):
    filename: str = "checkpoint_custom.pt"


# --- Lifespan / Startup Hook ---
@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    training_service.set_event_loop(loop)
    
    def on_train_finish():
        if os.path.exists("neurospectrum_model.pt"):
            try:
                inference_service.load_model("neurospectrum_model.pt")
                print("[API] Inference service reloaded newly trained canonical model.")
            except Exception as e:
                print(f"[API] Error reloading model after training: {e}")
                
    training_service.set_on_completed_callback(on_train_finish)


# --- REST API Endpoints ---
@app.get("/api/status")
async def get_system_status():
    dev = get_optimal_device()
    t_status = training_service.get_status()
    m_info = {}
    if inference_service.engine:
        m_info = {
            "model_path": inference_service.model_path,
            "is_frozen": inference_service.engine.verify_parameters_frozen(),
            "active_device": str(inference_service.device),
            "device_name": get_device_name(inference_service.device)
        }
    return {
        "system": {
            "device": str(dev),
            "device_name": get_device_name(dev),
            "mps_available": hasattr(torch.backends, "mps") and torch.backends.mps.is_available() if "torch" in globals() else False,
            "cuda_available": False
        },
        "training": t_status,
        "inference": m_info
    }


@app.get("/api/model/info")
async def get_model_info():
    if inference_service.engine is None:
        raise HTTPException(status_code=404, detail="No model loaded")
    metadata = get_model_metadata(inference_service.engine.model)
    metadata["model_file"] = inference_service.model_path
    metadata["is_frozen_verified"] = inference_service.engine.verify_parameters_frozen()
    metadata["mode"] = "INFERENCE (FROZEN)"
    return metadata


@app.post("/api/model/load")
def load_model(req: ModelLoadRequest):
    try:
        res = inference_service.load_model(req.checkpoint_path)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/model/export")
def export_canonical_model():
    if training_service.session is None:
        raise HTTPException(status_code=400, detail="Training session unavailable")
    path = training_service.session.export_canonical_model("neurospectrum_model.pt")
    # Refresh inference engine with canonical
    inference_service.load_model(path)
    return {"status": "exported", "filepath": path}


@app.post("/api/inference/run")
def run_inference(req: InferenceRunRequest):
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="No inference engine loaded")
    try:
        res = inference_service.run_simulation(
            target_gamma=req.target_gamma,
            initial_points=req.initial_distribution,
            n_particles=req.n_particles,
            num_steps=req.num_steps,
            dt=req.dt,
            capture_interval=req.capture_interval,
            auto_adapt=req.auto_adapt,
            auto_converge=req.auto_converge,
            convergence_threshold=req.convergence_threshold,
            min_steps=req.min_steps
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference execution failed: {str(e)}")


@app.get("/api/experience/summary")
async def get_experience_summary():
    return inference_service.experience_buffer.get_summary()


@app.post("/api/experience/adapt")
async def adapt_model_endpoint():
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Inference engine not loaded")
    import data
    pts = data.generate_archimedean_spiral(1, 256, device=inference_service.device)
    res = inference_service.experience_buffer.adapt_model_from_run(
        energy_model=inference_service.engine.model,
        initial_points=pts,
        target_gamma=1.0,
        num_adaptation_steps=5,
        device=inference_service.device
    )
    inference_service.engine._param_snapshot = [p.clone().detach() for p in inference_service.engine.model.parameters()]
    return res


@app.post("/api/train/start")
async def start_training(req: TrainingStartRequest):
    res = training_service.start_training(
        num_iterations=req.num_iterations,
        target_gamma=req.target_gamma,
        batch_size=req.batch_size,
        lr=req.lr
    )
    return res


@app.post("/api/train/pause")
async def pause_training():
    return training_service.pause()


@app.post("/api/train/resume")
async def resume_training():
    return training_service.resume()


@app.post("/api/train/stop")
async def stop_training():
    return training_service.stop()


@app.get("/api/train/status")
async def get_training_status():
    return training_service.get_status()


@app.post("/api/train/reset")
async def reset_training_session():
    return training_service.reset_session()


@app.post("/api/train/save")
async def save_checkpoint(req: CheckpointSaveRequest):
    path = training_service.save_checkpoint(req.filename)
    return {"status": "saved", "filepath": path}


@app.post("/api/simulation/initial-points")
async def get_initial_points_preview(req: InitialPointsRequest):
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Inference engine not loaded")
    import data
    from evaluate import compute_spatial_statistics

    n_p = req.n_particles
    dist = req.distribution.lower()
    dev = inference_service.device

    if dist == "spiral":
        pts = data.generate_archimedean_spiral(1, n_p, device=dev)
    elif dist == "grid":
        pts = data.generate_regular_grid(1, n_p, device=dev)
        pts = pts + torch.randn_like(pts) * 0.003
    elif dist == "jittered":
        pts = data.generate_jittered_grid(1, n_p, device=dev)
    elif dist == "clustered":
        pts = data.generate_clustered_red_noise(1, n_p, device=dev)
    else:  # random
        pts = data.generate_uniform_random(1, n_p, device=dev)

    pts = torch.remainder(pts, 1.0)
    pts = data.disambiguate_coincident_points(pts, min_separation=0.003, L=1.0)

    with torch.no_grad():
        density = inference_service.engine.rasterizer(pts)
        spec_dict = inference_service.engine.analyzer(density)

        pts_np = pts[0].detach().cpu().numpy()
        psd_2d_list = spec_dict["psd_2d"][0].cpu().numpy().tolist()
        radial_list = spec_dict["radial_psd"][0].cpu().numpy().tolist()
        freqs_list = spec_dict["freqs"].cpu().numpy().tolist()
        gamma_init = float(spec_dict["gamma"][0].item())
        spatial = compute_spatial_statistics(pts_np)

    return {
        "distribution": dist,
        "n_particles": n_p,
        "points": pts_np.tolist(),
        "gamma_hat": gamma_init,
        "psd_2d": psd_2d_list,
        "radial_psd": radial_list,
        "frequencies": freqs_list,
        "cv_nnd": float(spatial["cv_nnd"]),
        "min_spacing": float(spatial["min_distance"]),
        "effective_unique_particles": int(spatial.get("effective_unique_particles", n_p)),
        "coincident_pairs_count": int(spatial.get("coincident_pairs_count", 0)),
        "overlapping_pairs_count": int(spatial.get("overlapping_pairs_count", 0)),
        "has_coincident_points": bool(spatial.get("has_coincident_points", False))
    }



@app.get("/api/interpret/curves")
def get_interaction_curves(gamma: float = Query(1.0, ge=-2.5, le=2.5)):
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Model unavailable")
    data = compute_energy_and_force(
        energy_model=inference_service.engine.model,
        gamma_val=gamma,
        device=inference_service.device
    )
    return data


@app.get("/api/interpret/multi-curves")
def get_multi_gamma_curves():
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Model unavailable")
    data = compute_multi_gamma_curves(
        energy_model=inference_service.engine.model,
        gammas=[-1.0, 0.0, 1.0],
        device=inference_service.device
    )
    return data


@app.get("/api/interpret/phase-diagram")
def get_phase_diagram():
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Model unavailable")
    diagram = compute_phase_diagram(
        energy_model=inference_service.engine.model,
        gamma_min=-2.0,
        gamma_max=2.0,
        num_gamma=31,
        num_r=40,
        device=inference_service.device
    )
    return diagram


@app.get("/api/generalize/new-inputs")
def get_new_inputs_benchmark(target_gamma: float = Query(1.0)):
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Model unavailable")
    res = run_new_input_generalization_benchmark(
        engine=inference_service.engine,
        target_gamma=target_gamma,
        n_particles=256,
        num_steps=20
    )
    return {"results": res, "target_gamma": target_gamma}


@app.get("/api/generalize/unseen-gammas")
def get_unseen_gammas_benchmark():
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Model unavailable")
    res = run_unseen_gamma_interpolation_benchmark(
        engine=inference_service.engine,
        n_particles=256,
        num_steps=20
    )
    return res


@app.get("/api/generalize/amortized-cost")
def get_amortized_cost_benchmark():
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Model unavailable")
    res = run_amortized_cost_comparison(
        engine=inference_service.engine,
        test_gammas=[-1.0, 0.0, 1.0],
        n_particles=256,
        classical_iters=40,
        neural_steps=20
    )
    return {"comparison": res}


# --- Figure 2 Multi-Method Benchmark (EGSR 2026 Paper Comparison) ---
@app.get("/api/benchmark/figure2")
def get_figure2_benchmark(refresh: bool = False):
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Inference engine not loaded")
    return generate_figure2_benchmark(inference_service.engine, n_particles=256, force_refresh=refresh)


@app.get("/api/benchmark/figure2/image")
def get_figure2_image(refresh: bool = False):
    if inference_service.engine is None:
        raise HTTPException(status_code=400, detail="Inference engine not loaded")
    bench_data = generate_figure2_benchmark(inference_service.engine, n_particles=256, force_refresh=refresh)
    img_bytes = generate_figure2_image_bytes(bench_data)
    return Response(content=img_bytes, media_type="image/png")


# --- WebSocket Streaming ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = training_service.subscribe()
    try:
        # Initial greeting
        await websocket.send_json({
            "type": "connection_ready",
            "message": "Connected to NeuroSpectrum Real-Time Telemetry"
        })
        
        while True:
            # Wait for event from training queue or client message
            get_event = asyncio.create_task(queue.get())
            recv_msg = asyncio.create_task(websocket.receive_text())
            
            done, pending = await asyncio.wait(
                [get_event, recv_msg],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in pending:
                task.cancel()
                
            if recv_msg in done:
                try:
                    client_text = recv_msg.result()
                    # Client ping or command
                    if "ping" in client_text.lower():
                        await websocket.send_json({"type": "pong", "time": time.time()})
                except Exception:
                    break
                    
            if get_event in done:
                event = get_event.result()
                await websocket.send_json(event)
                
    except WebSocketDisconnect:
        pass
    finally:
        training_service.unsubscribe(queue)


# --- Serve Frontend Static Files ---
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
