"""
experiments/run_experiments.py - Reproducible Milestone Runner.

Executes the research milestones M1-M6 of the refined proposal with stored
configurations, seeds, checkpoints and JSON experiment logs:

    M1  differentiable rasterization + FFT/PSD gradients   (gradcheck suite)
    M2  single-target learned energy (gamma = +1.0)        (train + eval)
    M3  continuous gamma conditioning (U[-2,+2], holdouts) (train + eval)
    M4  unseen/intermediate gamma generalization          (frozen eval)
    M5  classical per-target baseline + amortized cost     (comparison)
    M6  scalability: global vs kNN local interactions      (benchmark)

Usage:
    python experiments/run_experiments.py --milestone M2 [--quick]

Every run writes: checkpoints, a JSON experiment log and figures into
outputs/ (config + seeds + measured values only - no fabricated numbers).
"""

import argparse
import json
import os
import sys
import time
import numpy as np
import torch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine
from rasterize import PeriodicGaussianSplat2D
from spectrum import DifferentiableSpectralAnalyzer
from losses import CompositeSpectralLoss
from train import train_continuous_gamma, probe_model
from inference import FrozenInferenceEngine
from baseline import ClassicalPerTargetOptimizer
from generalize import run_unseen_gamma_evaluation, run_amortized_cost_comparison
from evaluate import compute_spatial_statistics
from checkpoints import save_checkpoint

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(PROJECT_ROOT, "outputs")
CKPT_DIR = os.path.join(OUT, "checkpoints")
LOG_DIR = os.path.join(OUT, "experiment_logs")
os.makedirs(CKPT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

HELDOUT = [-1.5, -0.7, 0.3, 0.8, 1.5]     # held-out intermediate values
PROBE_GAMMAS = [-2.0, -1.5, -0.7, 0.0, 0.3, 0.8, 1.5, 2.0]


def _write_log(name: str, payload: dict):
    path = os.path.join(LOG_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"[log] {path}")


# --------------------------------------------------------------------------- #
#  M1: gradient validation of the differentiable pipeline                     #
# --------------------------------------------------------------------------- #

def milestone_m1(quick: bool = False):
    print("\n=== M1: differentiable rasterization + FFT/PSD gradients ===")
    grid = 16 if quick else 64
    rasterizer = PeriodicGaussianSplat2D(grid_size=grid, sigma=0.08)
    analyzer = DifferentiableSpectralAnalyzer(grid_size=grid, sigma=0.08,
                                              f_min=2, f_max=max(4, grid // 4))

    def loss_fn(pts):
        return analyzer(rasterizer(pts))['gamma'].sum()

    pts = torch.tensor([[
        [0.999, 0.500], [0.001, 0.500], [0.500, 0.999], [0.500, 0.001],
        [0.999, 0.999], [0.500, 0.500]
    ]], dtype=torch.float64, requires_grad=True)
    gradcheck_ok = torch.autograd.gradcheck(loss_fn, (pts,), eps=1e-6, atol=1e-4)

    from data import generate_uniform_random, generate_poisson_disk, \
        generate_clustered_red_noise
    with torch.no_grad():
        g_red = analyzer(rasterizer(generate_clustered_red_noise(4, 256)))['gamma'].mean().item()
        g_white = analyzer(rasterizer(generate_uniform_random(4, 256)))['gamma'].mean().item()
        g_blue = analyzer(rasterizer(generate_poisson_disk(2, 256)))['gamma'].mean().item()

    payload = {
        "gradcheck_passed": bool(gradcheck_ok),
        "reference_ordering": {"red": g_red, "white": g_white, "blue": g_blue},
        "ordering_ok": bool(g_red < g_white < g_blue),
    }
    _write_log("M1_gradients", payload)
    print(payload)
    return payload


# --------------------------------------------------------------------------- #
#  M2: single target (gamma = +1) with the learned energy                     #
# --------------------------------------------------------------------------- #

def milestone_m2(quick: bool = False):
    print("\n=== M2: single-target learned energy (gamma = +1) ===")
    n, steps, epochs, lr = (96, 20, 60, 3e-3) if quick else (256, 40, 120, 1e-3)
    torch.manual_seed(0)
    model = NeuralPairwiseEnergy(hidden_dim=64, num_layers=3)
    sim = DifferentiableSimulationEngine(model, num_steps=steps, dt=0.5,
                                         use_checkpointing=True)
    rast = PeriodicGaussianSplat2D(grid_size=64, sigma=0.02)
    ana = DifferentiableSpectralAnalyzer(grid_size=64, sigma=0.02,
                                         f_min=2, f_max=24)
    loss_fn = CompositeSpectralLoss(lambda_spec=1.0, lambda_gamma=3.0,
                                    lambda_spacing=1.0, n_particles=n)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    gt = torch.tensor([1.0])
    hist = []
    for it in range(1, epochs + 1):
        x = torch.rand(1, n, 2)
        xf = sim(x, gt)
        ld = loss_fn(xf, gt, ana(rast(xf)))
        opt.zero_grad(); ld['loss'].backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        hist.append({"it": it, "loss": ld['loss'],
                     "gamma_hat": ld['measured_gamma']})
        if it % 20 == 0 or it == 1:
            print(f"  it={it}: loss={ld['loss']:.3f} ghat={ld['measured_gamma']:+.3f}")

    probe = probe_model(model, [1.0], n_particles=n, num_steps=steps, dt=0.5)
    ckpt_path = os.path.join(CKPT_DIR, "checkpoint_m1_overfit.pt")
    save_checkpoint(ckpt_path, energy_model=model,
                    model_config=model.architecture_summary(),
                    simulation_config={"n_particles": n, "num_steps": steps,
                                       "dt": 0.5, "normalize_forces": False},
                    history={"iterations": hist})
    payload = {"final_probe": probe, "config": {"n": n, "steps": steps,
                                                "epochs": epochs, "lr": lr},
               "checkpoint": ckpt_path}
    _write_log("M2_single_target", payload)
    print(payload)
    return payload


# --------------------------------------------------------------------------- #
#  M3: continuous gamma conditioning (with hold-outs)                         #
# --------------------------------------------------------------------------- #

def milestone_m3(quick: bool = False):
    print("\n=== M3: continuous gamma conditioning ===")
    if quick:
        model, history = train_continuous_gamma(
            num_epochs=60, batch_size=2, n_particles=96, num_steps=20,
            truncated_bptt_steps=14, holdout_points=HELDOUT,
            probe_every=20, seed=0,
            log_path=os.path.join(LOG_DIR, "M3_training_log.json"))
    else:
        model, history = train_continuous_gamma(
            num_epochs=240, batch_size=2, n_particles=256, num_steps=40,
            truncated_bptt_steps=32, holdout_points=HELDOUT,
            probe_every=40, seed=0,
            log_path=os.path.join(LOG_DIR, "M3_training_log.json"))
    payload = {"final_probe": history["probes"][-1] if history["probes"] else {},
               "history_length": len(history["loss"]),
               "checkpoint": os.path.join(CKPT_DIR,
                                          "checkpoint_continuous_gamma.pt")}
    _write_log("M3_continuous_gamma", payload)
    print(payload)
    return payload


# --------------------------------------------------------------------------- #
#  M4: unseen-gamma generalization (frozen)                                   #
# --------------------------------------------------------------------------- #

def milestone_m4(quick: bool = False):
    print("\n=== M4: unseen-gamma generalization (FROZEN) ===")
    ckpt = os.path.join(CKPT_DIR, "checkpoint_continuous_gamma.pt")
    if not os.path.exists(ckpt):
        raise FileNotFoundError(f"{ckpt} missing - run M3 first")
    engine = FrozenInferenceEngine(ckpt, device="cpu")
    res = run_unseen_gamma_evaluation(
        engine, interpolation_gammas=HELDOUT, extrapolation_gammas=[-2.5, 2.5],
        n_particles=96 if quick else 256, num_steps=20 if quick else 40,
        seeds=(0, 1, 2))
    res["frozen_verified"] = engine.verify_parameters_frozen()
    _write_log("M4_unseen_gamma", res)
    print(json.dumps(res, indent=2)[:2000])
    return res


# --------------------------------------------------------------------------- #
#  M5: classical baseline & amortized comparison                              #
# --------------------------------------------------------------------------- #

def milestone_m5(quick: bool = False):
    print("\n=== M5: classical per-target baseline vs frozen neural ===")
    ckpt = os.path.join(CKPT_DIR, "checkpoint_continuous_gamma.pt")
    if not os.path.exists(ckpt):
        raise FileNotFoundError(f"{ckpt} missing - run M3 first")
    engine = FrozenInferenceEngine(ckpt, device="cpu")
    res = run_amortized_cost_comparison(
        engine, test_gammas=[-1.0, 0.0, 1.0],
        n_particles=96 if quick else 256,
        classical_iters=60 if quick else 200, neural_steps=20 if quick else 40,
        seeds=(0, 1, 2))
    res["frozen_verified"] = engine.verify_parameters_frozen()
    _write_log("M5_amortized_comparison", res)
    print(json.dumps(res, indent=2)[:2000])
    return res


# --------------------------------------------------------------------------- #
#  M6: scalability - global vs kNN                                            #
# --------------------------------------------------------------------------- #

def milestone_m6():
    print("\n=== M6: global vs kNN local interactions ===")
    ckpt = os.path.join(CKPT_DIR, "checkpoint_continuous_gamma.pt")
    if not os.path.exists(ckpt):
        raise FileNotFoundError(f"{ckpt} missing - run M3 first")
    engine = FrozenInferenceEngine(ckpt, device="cpu")
    rows = []
    for N in [256, 512, 1024]:
        for use_knn, k in [(False, None), (True, 16), (True, 32)]:
            times, ghs = [], []
            for seed in (0, 1):
                t0 = time.perf_counter()
                res = engine.run_inference(target_gamma=0.8, initial_points="random",
                                           n_particles=N, num_steps=30,
                                           use_knn=use_knn, k=k or 16, seed=seed)
                times.append(time.perf_counter() - t0)
                ghs.append(res["results"]["measured_gamma_hat"])
            rows.append({"N": N, "mode": "kNN(k=%d)" % k if use_knn else "global",
                         "mean_time_s": float(np.mean(times)),
                         "mean_gamma_hat": float(np.mean(ghs))})
    payload = {"benchmark": rows, "note": "same frozen checkpoint for all rows"}
    _write_log("M6_scalability", payload)
    print(json.dumps(rows, indent=2))
    return payload


MILESTONES = {
    "M1": milestone_m1, "M2": milestone_m2, "M3": milestone_m3,
    "M4": milestone_m4, "M5": milestone_m5, "M6": milestone_m6,
}


def main():
    parser = argparse.ArgumentParser(description="NeuroSpectrum milestone runner")
    parser.add_argument("--milestone", choices=list(MILESTONES) + ["ALL"],
                        default="M1")
    parser.add_argument("--quick", action="store_true",
                        help="reduced sizes for fast smoke runs")
    args = parser.parse_args()

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    if args.milestone == "ALL":
        for name in ["M1", "M2", "M3", "M4", "M5", "M6"]:
            MILESTONES[name](quick=args.quick)
    else:
        MILESTONES[args.milestone](quick=args.quick)


if __name__ == "__main__":
    main()
