"""
experiments/run_ablations.py - Systematic Ablation Studies (refined proposal §16).

Ablations (each uses the same core pipeline; only ONE factor changes):
  A1 vs A2 : spectral-only loss vs spectral + NND regularity
  B1 vs B2 : fully learned energy vs learned energy + fixed soft-core stabilizer
  C1 vs C2 : layer-FiLM gamma conditioning vs plain concatenation conditioning
  D1 vs D2 : global pairwise vs kNN local interactions (frozen inference)
  E1 vs E2 : discrete-gamma training vs continuous-gamma training

Every run reports measured values only (slope error, PSD error, CV_NND,
runtime) and writes a JSON log for reproducibility.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import numpy as np
import torch

from train import train_continuous_gamma, probe_model
from inference import FrozenInferenceEngine

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LOG_DIR = os.path.join(PROJECT_ROOT, "outputs", "experiment_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def _log(name, payload):
    with open(os.path.join(LOG_DIR, name), "w") as f:
        json.dump(payload, f, indent=2)
    print(f"[log] outputs/experiment_logs/{name}")


def _frozen_eval(ckpt_path: str, gammas, n=128, steps=40, seeds=(0, 1, 2),
                 use_knn=False, k=16):
    engine = FrozenInferenceEngine(ckpt_path, device="cpu")
    rows = []
    for g in gammas:
        errs, psds, cvs = [], [], []
        for s in seeds:
            r = engine.run_inference(target_gamma=g, initial_points="random",
                                     n_particles=n, num_steps=steps, seed=s,
                                     use_knn=use_knn, k=k)
            errs.append(r["results"]["absolute_error"])
            psds.append(r["results"]["psd_mse"])
            cvs.append(r["results"]["cv_nnd"])
        rows.append({"target_gamma": g,
                     "mean_abs_error": float(np.mean(errs)),
                     "mean_psd_mse": float(np.mean(psds)),
                     "mean_cv_nnd": float(np.mean(cvs))})
    return rows


def ablation_loss_term(quick: bool):
    """A1: spectral-only vs A2: spectral + regularity."""
    print("\n=== Ablation A: spectral-only vs spectral+regularity ===")
    results = {}
    for label, lam_sp in [("A1_spectral_only", 0.0), ("A2_spectral_plus_regularity", 0.5)]:
        model, hist = train_continuous_gamma(
            num_epochs=30 if quick else 200, batch_size=2,
            n_particles=96 if quick else 256, num_steps=20 if quick else 40,
            truncated_bptt_steps=14 if quick else 32,
            lambda_spacing=lam_sp, seed=1, probe_every=30)
        probe = probe_model(model, [-1.5, 0.0, 1.5], n_particles=96 if quick else 256,
                            num_steps=20 if quick else 40, dt=0.5)
        results[label] = {"lambda_spacing": lam_sp, "final_probe": probe,
                          "final_loss": hist["loss"][-1]}
        print(f"  {label}: probe={probe}")
    _log("ablation_A_loss_terms.json", results)
    return results


def ablation_stabilizer(quick: bool):
    """B1: fully learned vs B2: learned + fixed repulsive stabilizer."""
    print("\n=== Ablation B: learned-only vs learned + fixed stabilizer ===")
    results = {}
    for label, prior in [("B1_learned_only", False), ("B2_with_stabilizer", True)]:
        model, hist = train_continuous_gamma(
            num_epochs=30 if quick else 200, batch_size=2,
            n_particles=96 if quick else 256, num_steps=20 if quick else 40,
            truncated_bptt_steps=14 if quick else 32,
            use_divergence_prior=prior, eps_divergence=0.005,
            r_repulsion=0.045, seed=1, probe_every=30)
        probe = probe_model(model, [-1.5, 0.0, 1.5], n_particles=96 if quick else 256,
                            num_steps=20 if quick else 40, dt=0.5)
        results[label] = {"use_divergence_prior": prior, "final_probe": probe,
                          "final_loss": hist["loss"][-1]}
        print(f"  {label}: probe={probe}")
    _log("ablation_B_stabilizer.json", results)
    return results


def ablation_conditioning(quick: bool):
    """C1: layer-FiLM vs C2: concat gamma conditioning."""
    print("\n=== Ablation C: layer-FiLM vs concat conditioning ===")
    results = {}
    for label, cond in [("C1_film_every_layer", "film_every_layer"), ("C2_concat", "concat")]:
        model, hist = train_continuous_gamma(
            num_epochs=30 if quick else 200, batch_size=2,
            n_particles=96 if quick else 256, num_steps=20 if quick else 40,
            truncated_bptt_steps=14 if quick else 32,
            gamma_conditioning=cond, seed=1, probe_every=30)
        probe = probe_model(model, [-1.5, 0.0, 1.5], n_particles=96 if quick else 256,
                            num_steps=20 if quick else 40, dt=0.5)
        results[label] = {"gamma_conditioning": cond, "final_probe": probe,
                          "final_loss": hist["loss"][-1]}
        print(f"  {label}: probe={probe}")
    _log("ablation_C_conditioning.json", results)
    return results


def ablation_knn():
    """D1: global vs D2: kNN frozen inference on the same checkpoint."""
    print("\n=== Ablation D: global vs kNN local interactions ===")
    ckpt = os.path.join(PROJECT_ROOT, "outputs", "checkpoints",
                        "checkpoint_continuous_gamma.pt")
    if not os.path.exists(ckpt):
        print("  [skip] no trained checkpoint (run M3 first)")
        return {}
    results = {
        "D1_global": _frozen_eval(ckpt, [-1.0, 0.0, 1.0], use_knn=False),
        "D2_knn_k32": _frozen_eval(ckpt, [-1.0, 0.0, 1.0], use_knn=True, k=32),
    }
    _log("ablation_D_knn.json", results)
    return results


def ablation_gamma_sampling(quick: bool):
    """E1: discrete-gamma vs E2: continuous-gamma training."""
    print("\n=== Ablation E: discrete vs continuous gamma training ===")
    results = {}
    # continuous (with hold-outs)
    model, hist = train_continuous_gamma(
        num_epochs=30 if quick else 200, batch_size=2,
        n_particles=96 if quick else 256, num_steps=20 if quick else 40,
        truncated_bptt_steps=14 if quick else 32,
        curriculum="random", holdout_points=[-1.5, -0.7, 0.3, 0.8, 1.5],
        seed=1, probe_every=30)
    results["E2_continuous"] = {"final_probe":
        probe_model(model, [-1.5, 0.0, 1.5], n_particles=96 if quick else 256,
                    num_steps=20 if quick else 40, dt=0.5)}
    # discrete (curriculum over fixed extremes)
    model2, hist2 = train_continuous_gamma(
        num_epochs=30 if quick else 200, batch_size=2,
        n_particles=96 if quick else 256, num_steps=20 if quick else 40,
        truncated_bptt_steps=14 if quick else 32,
        curriculum="alternate", seed=1, probe_every=30)
    results["E1_discrete_extremes"] = {"final_probe":
        probe_model(model2, [-1.5, 0.0, 1.5], n_particles=96 if quick else 256,
                    num_steps=20 if quick else 40, dt=0.5)}
    _log("ablation_E_gamma_sampling.json", results)
    return results


ABLATIONS = {
    "A": ablation_loss_term, "B": ablation_stabilizer,
    "C": ablation_conditioning, "D": ablation_knn,
    "E": ablation_gamma_sampling,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ablation", choices=list(ABLATIONS) + ["ALL"],
                        default="A")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    if args.ablation == "ALL":
        for name in "ABCDE":
            ABLATIONS[name](quick=args.quick)
    else:
        ABLATIONS[args.ablation](quick=args.quick)


if __name__ == "__main__":
    main()
