# NeuroSpectrum — Comprehensive Project Audit & Enhancement Report

**Prepared by:** f's momo (Principal AI Research Engineer / Scientific Software Architect)
**Date:** 2026-09-23
**Repository:** `https://github.com/ANSUJKMEHER/NeuroSpectrum` (cloned to `/tmp/NeuroSpectrum`, 74 files)
**Audit method:** Full source inspection + **live execution** of every pipeline (tests, benchmarks, backend API, WebSocket, frontend), verified against 5 reference papers supplied in `General/`.

> **Verdict in one line:** the codebase is well-structured and genuinely *runs* end-to-end, but the headline scientific claims ("continuous spectral control", "2.3× more accurate than classical", "30 ms neural forward", "60 FPS via requestAnimationFrame", "<35 MB RAM") are **not reproducible with the bundled model** — the shipped "trained" checkpoint was trained for **5 iterations** and collapses to a bimodal (red-ish vs. blue-ish) attractor instead of controlling $\gamma \in [-2,+2]$ continuously.

---

## 1. Executive Summary & Health Score

### 1.1 What the project claims vs. what actually runs

| Claim (README / docs) | Measured reality | Verdict |
|---|---|---|
| Continuous spectral control $\gamma \in [-2,+2]$ | Model outputs ~3 fixed states (≈−1.0, ≈−0.26, ≈+1.2); white-noise target $\gamma=0$ yields $\hat\gamma\approx+1.06$ | ❌ **False** |
| "2.3× more accurate than classical Adam" | Classical optimizer MAE **0.006–0.081**; neural MAE **0.34–1.59** (≈10–20× *worse*) | ❌ **Inverted** |
| "30.1 ms" neural forward | Measured **2.2–7.4 s** per simulation (≈70–250× slower) | ❌ **False** |
| "84.4× speedup @ N=4096" (O(Nk) vs O(N²)) | 75.6× *measured*, but O(N²) baseline is **quadratically extrapolated** for N>1024, not measured | ⚠️ **Partially real / misleading** |
| "2.0× MC variance reduction" | Measured **0.7–1.9×** (often *worse* than random; jittered grid dominates) | ⚠️ **Marginal / overstated** |
| "<35 MB RAM" (gradient checkpointing) | Single N=256 training step peaks at **1.6 GB RSS** | ❌ **False (~46× off)** |
| "60 FPS via requestAnimationFrame" | Frontend uses **`setInterval(..., 35)`** (≈28.5 FPS); **0** occurrences of `requestAnimationFrame` | ❌ **False** |
| Blue-noise "low-frequency void" in spectrum | PDS reference (genuine blue noise, g(r)=0 void) does **not** show the void under the repo's own spectral tooling | ⚠️ **Tooling bug** |

### 1.2 Health scorecard (per subsystem)

| Subsystem | Score | Notes |
|---|---|---|
| Core physics (`energy.py`, `dynamics.py`) | **B** | Correct minimum-image convention, Newton's 3rd law, conservative forces. But training uses **Euler**, not the advertised Verlet; `num_layers` ignored; displacement clamping breaks "exact physics". |
| Spectrum (`spectrum.py`, `rasterize.py`) | **B** | Differentiable FFT/radial averaging is correct and gradient-checked. Deconvolution clamp (100×) is a crude but functional safeguard. |
| Losses (`losses.py`) | **B** | Well-designed; anti-collapse spacing loss works. Chunked path has a subtle in-place-autograd risk (N>512). |
| Training (`train.py`) | **C+** | Stateful session is solid; but default/continuous training barely ran (checkpoint = 5 iters). |
| Inference (`inference.py`) | **C** | Correct freeze semantics; **mock metrics** injected for non-milestone frames (`cached_cv=0.45`, `cached_min_dist=0.01`). |
| Benchmarks (`tests/`) | **B−** | All 48 pytest tests pass; script benchmarks run but expose the untrained model; O(N²) extrapolation. |
| Experiments (`experiments/`) | **D** | **Stale/broken** — imports `probe_model`, `run_unseen_gamma_evaluation`, and kwargs that no longer exist in `src/`. |
| Backend API (`backend/`) | **B** | FastAPI + WebSocket work; but endpoints differ from spec (`/api/health`, `/api/simulate`, `/api/export/*` → 404); hardcoded `cuda_available: False`. |
| Universal synthesizer | **C** | OT+Chamfer morphing genuinely works, but returns **hardcoded** `cv_nnd=0.14`, `target_reached=True`, `measured_gamma_hat=1.0`. |
| Frontend | **B−** | Polished UI (light+dark), Chart.js, video export; but `setInterval` not `rAF`; velocity-trails **coordinate bug**; fabricated Figure-2 "live" metrics. |
| **Overall** | **C+ / 68%** | Runs everywhere, but "publication-grade" claims are not backed by the bundled artifacts. |

---

## 2. Complete File-by-File Lineage & Architecture Audit

### 2.1 Data lineage / pipeline flow

```
raw config (γ, N, init geometry)  ─▶  src/data.py  (generate_*_noise / spiral / grid)
        │
        ▼
src/energy.py  NeuralPairwiseEnergy:  RBF(dist) → FiLM(γ) → MLP → e_ij
        │  E(X,γ) = Σ_{i<j} e_ij   (permutation-invariant, min-image distances)
        ▼
src/dynamics.py  F_i = -∇_{x_i} E  →  integrator step  →  X_{t+1} = remainder(X_t + Δ, L)
        │  (training: checkpointed EULER; inference: velocity VERLET, both damped+clamped)
        ▼
src/rasterize.py  PeriodicGaussianSplat2D  →  density grid (M×M)
        ▼
src/spectrum.py  DifferentiableSpectralAnalyzer:  FFT2D → |·|² → radial W·PSD → slope γ̂
        ▼
src/losses.py  L = λ_spec·L_spec + λ_γ·L_γ + λ_sp·L_spacing (+ λ_a·L_aniso)
        ▼
src/train.py  backprop through unrolled sim → Adam → checkpoint
src/inference.py  frozen forward → trajectory + spectra + spatial stats
        ▼
backend/api.py  REST (inference/benchmarks/morph) + /ws (training telemetry only)
        ▼
frontend/index.html  fetch trajectory → client-side canvas playback + Chart.js
```

### 2.2 File-by-file findings

#### `src/energy.py` — `NeuralPairwiseEnergy`
- **Role:** conditional pairwise energy $E_\theta(r_{ij}, \gamma)$.
- **Real:** RBF (16 centers) → FiLM (γ-conditioned scale/shift) → 3-layer MLP → cosine cutoff; plus a soft-core Gaussian repulsion + quadratic exclusion barrier (blue-noise gate).
- **Issues:** `num_layers` is **ignored** (MLP is hardcoded 3 layers, `energy.py:107`). Parameter count is **7,904**, not the "~9K" advertised. Energy is genuinely computed (no mock here).

#### `src/dynamics.py` — `DifferentiableSimulationEngine`
- **Role:** unrolled integrator; `compute_neural_force`, `step_euler`, `step_velocity_verlet`.
- **Real:** conservative forces via `torch.autograd.grad`; min-image; NaN/Inf/0-particle input rejection; toroidal canonicalization.
- **Issues (important):**
  1. **Integrator mismatch:** `forward()` only uses Verlet when `not points.requires_grad` (i.e., inference). **During training it silently uses 1st-order Euler** (`_single_step_euler`), contradicting the "Velocity Verlet — DEFAULT" docstring and README architecture.
  2. **Displacement clamp** (`max_displacement=0.02/0.03`) + `damping=0.97–0.98` mean the "exact physics / energy conservation" narrative is only approximate.
  3. `_single_step_euler` does **not** pass `use_knn`, so the O(Nk) path is never used in the training Euler branch (kNN is inference-only).

#### `src/spectrum.py` — `DifferentiableSpectralAnalyzer`
- **Real & correct:** FFT2D → PSD (÷ grid⁴), precomputed radial projection `W`, differentiable slope regression over `[f_min, f_max]`, Gaussian deconvolution with gain clamp (100×).
- **Issue:** `compute_continuous_point_spectrum` (used by Figure 2 & universal studio) radial-bins a 64×64 k-grid; the **f=1 ring has only ~8 samples**, so the blue-noise void is not reliably visible (see §3.4).

#### `src/rasterize.py` — `PeriodicGaussianSplat2D`
- **Real:** analytic Gaussian splat with min-image wrap, chunked for N>512. Correct, gradient-checked.

#### `src/losses.py` — `CompositeSpectralLoss`
- **Real:** log-PSD MSE, slope squared error, CV-NND + absolute-collapse penalty, anisotropy term.
- **Issue:** chunked spacing-loss branch (`N>512`) does in-place assignment on a tensor that participates in the autograd graph (`dist_c[:, idx_c, i+idx_c] = 1e6`) — a latent in-place/autograd hazard.

#### `src/data.py` — reference distributions
- **Real:** uniform, jittered grid, Poisson-disk (Bridson), clustered, regular grid, Archimedean spiral; `disambiguate_coincident_points` (verified by edge-case tests).
- **Issue:** `generate_poisson_disk` uses global `np.random` (non-seeded/non-reproducible), and its dart-fill fallback relaxes to `r_min*0.5` then to pure random — so "blue noise" purity degrades for large N.

#### `src/train.py` — training engines
- **Real:** `train_single_target_overfit`, `train_continuous_gamma`, `StatefulTrainingSession` (correct stateful resume semantics).
- **Issues:**
  1. The shipped `checkpoint_continuous_gamma.pt` has **`epoch=5, iteration=5`** — the "continuous" model was trained for **5 iterations** (see §3.2).
  2. `train_continuous_gamma` signature is much smaller than what `experiments/` expects (see §2.4).

#### `src/inference.py` — `FrozenInferenceEngine`
- **Real:** strict weight-freeze + immutability assertion; Verlet inference; full trajectory/PSD capture; coincident-point disambiguation; auto-converge.
- **Issues:**
  1. **Mock telemetry:** non-milestone capture frames get hardcoded `cached_cv = 0.45`, `cached_min_dist = 0.01` (`inference.py:202–203`) — intermediate frames in the public trajectory API report **fake** spatial metrics.
  2. The "direct point-Fourier fallback" that overrides `final_gamma_hat` only triggers on large discrepancy — masking, not fixing, the model's poor conditioning.

#### `src/evaluate.py` — spatial diagnostics
- **Real & correct:** g(r) with min-image, CV-NND, collapse score, Poisson Rayleigh reference. Verified by tests.

#### `src/baseline.py` — `ClassicalPerTargetOptimizer`
- **Real:** Adam on raw coordinates against the *same* differentiable loss. This is the honest comparison — and it **beats the neural model** on accuracy (see §3.3).

#### `src/applications.py` — stippling + MC integration
- **Real:** rejection-sampled density maps, neural stipple relaxation, MC quadrature over 40 trials. Numbers are computed live (but the "blue" sample sets aren't actually blue — see §3.6).

#### `src/universal_synthesizer.py` — `UniversalBackpropMorpher`
- **Real:** Sinkhorn OT + bidirectional Chamfer + anti-collision barrier; text/image/2D/3D target factories; works (morph to "AI"/"NEURO" verified live).
- **Issue:** `morph()` return payload **hardcodes** `measured_gamma_hat=1.0`, `absolute_error=0.0`, `target_reached=True`, `cv_nnd=0.14` (`universal_synthesizer.py:461–474`) and `gamma_hat:1.0` in every trajectory frame. These are placeholder metrics presented as results.

#### `src/interpret.py`, `src/generalize.py`, `src/checkpoints.py`, `src/experience.py`, `src/visualize.py`
- **Real:** E(r,γ)/F(r,γ) curves + phase diagram; generalization/amortized-cost harnesses; checkpoint lifecycle; experience logger; publication figures. All function correctly (interpret curves + generalization exercised via API/tests).
- **Issues:** `generalize.py` exposes `run_unseen_gamma_interpolation_benchmark`, but `experiments/run_experiments.py` imports a **nonexistent** `run_unseen_gamma_evaluation`. `checkpoints.py`'s `save_canonical_model` always writes `epoch=0, iteration=0` (loses training position). `experience.py` docstring claims $dE/dt = -\|F\|^2 \le 0$ as a hard law, but the integrator's damping/clamp makes that approximate.

#### `backend/api.py`, `inference_service.py`, `training_service.py`, `figure2_benchmark.py`
- **Real:** FastAPI app mounts, all defined endpoints + WebSocket respond correctly (verified live, §3.5).
- **Issues:**
  1. **API surface ≠ spec:** `/api/health`, `/api/simulate`, `/api/export/*` do **not exist** (404). Actual endpoints are `/api/status`, `/api/inference/run`, `/api/benchmark/figure2[/image]`, `/api/universal/*`, `/api/train/*`, `/api/interpret/*`, `/api/generalize/*`, `/ws`.
  2. `/api/status` **hardcodes** `"cuda_available": False` (`api.py:148`) even when CUDA is present.
  3. `figure2_benchmark.py` reimplements CMJ/CCVT/Curl-PN/PDS as lightweight approximations (CMJ is a crude 2-line stratification — its measured anisotropy `a_hat=11.5` is implausible for true CMJ). The "Figure 2" comparison is therefore *not* a faithful EGSR reproduction.
  4. `figure2_benchmark.compute_metrics` reports `d_mean` from min-distance; in the frontend's "live" mode it **fabricates** `d_mean = min_spacing * 1.6` and substitutes `cv_voronoi = cv_nnd` (not the same metric).

#### `experiments/run_ablations.py` & `experiments/run_experiments.py` — **BROKEN (stale)**
- Import `from train import ... probe_model` → **`probe_model` does not exist anywhere** in `src/`.
- Import `from generalize import run_unseen_gamma_evaluation` → does not exist.
- Call `train_continuous_gamma(..., truncated_bptt_steps=…, lambda_spacing=…, seed=…, probe_every=…, use_divergence_prior=…, gamma_conditioning=…, curriculum=…, holdout_points=…)` → none of those kwargs are accepted by the current signature.
- `FrozenInferenceEngine.run_inference(..., seed=…)` → `seed` is not a parameter.
- **Both scripts fail at import/runtime.** The `tests/test_ablations.py` script (current API) is the *working* ablation harness.

#### `frontend/index.html` — 4,193 lines, single-file SPA
- **Real & polished:** Tailwind + Chart.js, 6 tabs (sim / universal / analytics / figure2 / benchmarks / train), light/dark theme (defaults light), noise presets, sliders, scrubber, Voronoi lattice / velocity trails / density splat toggles, canvas tooltips, `.webm` video export via `canvas.captureStream(30)` + `MediaRecorder`, Figure 2 + Table 2 UI, WebSocket training charts.
- **Issues:** see §4.

### 2.3 Real vs. mock data audit (summary)

| Artifact | Computed live? | Notes |
|---|---|---|
| PSD / radial PSD / γ̂ (spectral analyzer) | ✅ Real | gradient-checked |
| g(r), CV-NND, collapse score | ✅ Real | correct min-image |
| Training loss curves | ✅ Real | |
| Trajectory positions / forces / energies | ✅ Real | |
| Universal-morph final points / OT loss | ✅ Real | |
| Universal-morph `cv_nnd`, `target_reached`, `γ̂` | ❌ **Mock** (0.14 / True / 1.0) | `universal_synthesizer.py:461–474` |
| Intermediate inference frames' `cv_nnd`/`min_spacing` | ❌ **Mock** (0.45 / 0.01) | `inference.py:202–203` |
| Figure-2 "live" `d_mean`, `cv_voronoi` | ❌ **Proxy/fabricated** | `index.html` live-mode |
| O(N²) baseline @ N=2048/4096 | ⚠️ **Extrapolated** | `test_scalability.py:87–89` |
| README headline benchmark tables | ❌ **Not reproducible** | see §3.3–3.6 |

### 2.4 Numerical-stability & edge-case status (verified by running `test_edge_cases.py`, 18/18 ✅)

- N=0/1 degeneracy, coincident-point (r=0) finite forces, unbatched/batched shapes, corner/torus wrap, k≥N & k=1 clamp, γ=±3.0, full collapse spacing loss, B=8×N=128 bwd, non-contiguous tensors, NumPy inputs, N=1024 chunked spacing, scalar/0D γ, NaN/Inf & zero-particle rejection, modulo canonicalization, deconvolution clamp — **all pass**.
- **Residual risks:** the chunked spacing-loss in-place op (`losses.py`); `torch.log(freqs)` at `f_min=2` is safe, but `estimate_gamma` divides by `denominator+1e-12` (OK); radial binning `torch.round(radial_dist)` is fine but coarse.

---

## 3. Verification Results (Test Logs, Real-Data Audits, Performance Profiling)

### 3.1 Test-suite execution logs

**`pytest tests/ -v`** → **48 passed, 0 failed** in **65.73 s** (Python 3.11.2, torch 2.14.0+cpu, numpy 2.4.6). Full list verified green: `test_core_physics` (9), `test_deep_audit` (3), `test_edge_cases` (1), `test_foundations` (2), `test_frozen_checkpoint` (7), `test_knn_consistency` (3), `test_losses` (2), `test_periodic_boundary` (13), `test_physics` (2), `test_stateful_and_frozen_system` (5), `test_universal_synthesizer` (1).

**`python tests/test_edge_cases.py`** → **18/18 PASSED** in **7.5 s**.

**Script-style benchmarks** (run directly, not via pytest): interpolation ✅, baseline ✅, scalability ✅, statistical-significance ✅, graphics-applications ✅, ablations ❌ (OOM, see below).

### 3.2 Model / checkpoint authenticity

| Checkpoint | epoch/iter | final_loss | Reality |
|---|---|---|---|
| `neurospectrum_model.pt` (canonical, drives dashboard) | 0/0 | 2.44 | exported canonical; metadata claims `trained_gamma_range [-2,2]` but loss 2.44 = essentially untrained |
| `outputs/checkpoints/checkpoint_continuous_gamma.pt` | **5 / 5** | — | the "continuous γ" model is **5 training iterations** |
| `outputs/checkpoints/checkpoint_m1_overfit.pt` | 25 / 25 | — | 25-iter single-target overfit |

Params = 7,904 (README says "~9K").

### 3.3 Unseen-γ interpolation + baseline (real numbers)

`test_interpolation.py` (checkpoint_continuous_gamma.pt, 256 pts, 25 steps):

| target γ* | measured γ̂ | \|Δγ\| |
|---|---|---|
| −1.50 | −0.998 | 0.502 |
| −0.70 | −0.997 | 0.297 |
| +0.30 | +0.019 | 0.281 |
| +0.80 | +0.019 | 0.781 |
| +1.50 | +0.019 | 1.481 |

`test_benchmark_baseline.py` (classical Adam 100 iters vs neural, 256 pts):

| target γ* | classical time | classical \|Δγ\| | neural time | neural \|Δγ\| | speedup |
|---|---|---|---|---|---|---|
| −1.50 | 5.94 s | **0.006** | 2.33 s | 0.666 | 2.6× |
| 0.00 | 5.32 s | **0.036** | 2.23 s | 0.339 | 2.4× |
| +1.00 | 5.68 s | **0.066** | 2.18 s | 1.090 | 2.6× |
| +1.50 | 4.98 s | **0.081** | 2.15 s | 1.590 | 2.3× |

**Conclusion:** the classical per-target optimizer is **accurate** (≤0.08 error); the neural model is **10–20× less accurate** and ~2.4× faster — the opposite of the README's "2.3× more accurate". README's "73.2 ms / 30.1 ms" are not reproduced (both are ~100× slower in reality).

**Frozen-inference γ-sweep** (`neurospectrum_model.pt`, 256 pts, 50 steps, ~7 s/run): γ=−2→−1.36, −1.5→−1.33, −1.0→−1.53, −0.7→−1.27, **0.0→+1.06**, +0.3→+1.30, +0.8→+1.46, +1.0→+1.16, +1.5→+1.19, +2.0→+1.17. The model has **no continuous control** — it maps red→≈−1.3 and everything else→≈+1.2, and badly misses the white-noise target.

### 3.4 Scalability (real + extrapolated)

`test_scalability.py` (target γ=+1.0, k=16, T=5, `checkpoint_continuous_gamma.pt`):

| N | all-pairs | k-NN | speedup | note |
|---|---|---|---|---|
| 256 | 672 ms | 60 ms | 11.2× | measured |
| 512 | 3,117 ms | 115 ms | 27.2× | measured |
| 1024 | 14,072 ms | 324 ms | 43.4× | measured |
| 2048 | 56,287 ms | 841 ms | 67.0× | **all-pairs extrapolated** |
| 4096 | 225,148 ms | 2,977 ms | 75.6× | **all-pairs extrapolated** |

- The **O(Nk) advantage is real** (11–75×), but the headline "84.4× @ 355 ms" is **not** reproducible: measured k-NN @4096 = **2,977 ms** (8× slower than claimed) and the O(N²) baseline for N>1024 is **quadratic extrapolation**, not measurement (`test_scalability.py:87–89`).
- **kNN/global divergence:** γ̂_all-pairs ≈ −0.91 at every N, but γ̂_knn degrades to −1.59 (N=2048) and −2.05 (N=4096). The `test_knn_consistency.py` "kNN matches global" assertion only holds at N=64/k=32 — it does **not** generalize to scale. This is an unacknowledged accuracy/speed trade-off.

### 3.5 Backend / WebSocket / API verification (live)

- `run_dashboard.py` boots cleanly (uvicorn 0.0.0.0:8000). ✅
- `GET /api/status` ✅ (but `cuda_available` hardcoded False). `GET /api/model/info` ✅ (7904 params, frozen).
- `POST /api/inference/run` ✅ (target 1.0, 128 pts, 20 steps → measured **−0.261**, error 1.261 — model fails blue-noise target).
- `POST /api/universal/morph` ✅ (morphs to "AI" text, 256 pts; but `cv_nnd=0.14`, `target_reached=True` are placeholders).
- `GET /api/benchmark/figure2` ✅ — real 6-method run. "Ours" wins Q=13.4 & lowest anisotropy a_hat=0.72, but takes **11.53 s** (vs 0.001–0.35 s for classical methods — contradicting "instant neural inference").
- `WS /ws` ✅ — `connection_ready` + `ping`→`pong` verified. **Note:** the WebSocket streams *training* telemetry only; simulation frames are delivered as one REST payload, not streamed.
- **Missing (404):** `/api/health`, `/api/simulate`, `/api/export/*` (the endpoint names in the audit brief do not exist in this codebase).

### 3.6 Downstream graphics + blue-noise void

`test_graphics_applications.py` (checkpoint_continuous_gamma.pt): MC variance-reduction vs random = **0.7× / 0.7× / 1.2× / 1.9× / 1.4× / 1.1× / 1.5×** for N=16→1024 — i.e., often *worse* than white noise, and **jittered grid beats it by 4–10×** (RMSE 0.0191 vs 0.0540 @ N=128). The "2.0× variance reduction" claim is not robust.

Blue-noise void: the repo's own `generate_pds_points(256)` is genuine blue noise (min_d=0.045, cv_nnd=0.095, g(r) void = 0, no collapse), **but** `compute_point_fourier_spectrum` reports *higher* low-frequency power than white noise at f=1 (3.16 vs 0.60). The continuous point-Fourier radial tooling does not cleanly render the void at 256 points / 64² k-grid.

### 3.7 Performance & memory profiling (CPU, this sandbox)

- **Inference:** ~**6.5–7.4 s** per 256-pt / 50-step run (≈130 ms/step); ~11.5 s in the Figure-2 path (100 steps).
- **Training:** single N=256 / 20-step step = **11.3 s**, **1.61 GB peak RSS** (N=128: 3.4 s / 1.49 GB; N=64: 1.0 s / 0.59 GB). "Constant memory <35 MB" is **~46× off**.
- **Ablations (`test_ablations.py`)**: **killed by OOM (exit 137)** on this 3.8 GB box — the 15-model ablation run (30 epochs × N=256 unrolls) does not fit here. A single 10-epoch trial also OOM'd. Training is memory-heavy and not "constant-footprint".

---

## 4. Frontend & Animation Smoothness Assessment

**Layout & aesthetics:** ✅ Strong. Single-file SPA, clean light theme (default) with a working dark toggle, 6-tab nav, noise presets, metric HUD, scrubber/filmstrip, Chart.js. No obvious promotional clutter; the only "promo" is a single "1-Click Optimal Blue Noise" button.

**Canvas rendering:** ✅ Mostly correct. Particle core 2.2 px white over a 4.5 px indigo halo (good core/halo contrast); density-splat mode uses `globalCompositeOperation='lighter'` radial gradients; coincident-pair amber beacons; Voronoi lattice via O(N²) distance threshold (acceptable ≤~512 pts, will jank at N≥2048 — should be Delaunay/Voronoi or capped).

**Animation smoothness:** ❌ **Does not meet the stated 60 FPS / requestAnimationFrame target.**
- **0** occurrences of `requestAnimationFrame`; **5** occurrences of `setInterval`.
- Playback uses `setInterval(..., 35)` → **~28.5 FPS**, plus a separate 60 ms progress-poll timer. This is exactly the "setInterval stutter" the README claims to have eliminated.
- Rendering is **synchronous on the main thread** in `renderFrame()`; at N≥1024 the O(N²) mesh + O(N²) coincident-beacon loops will drop frames.

**Bugs found:**
1. **Velocity-trails coordinate bug** (`frontend/index.html:2037`): `ctx.moveTo(prevPts[i][0]*w, prevPts[i][0]*h)` — the Y coordinate uses the **X** value (`prevPts[i][0]`) instead of `prevPts[i][1]`. Trails are drawn diagonally/horizontally wrong whenever a particle's x ≠ y.
2. **Fabricated "live" Figure-2 metrics** — `d_mean = min_spacing * 1.6` and `cv_voronoi = cv_nnd` are proxies presented as the real metrics.
3. Minor: `recordSimulationVideo` frames the canvas at `captureStream(30)` while stepping with a 45 ms `await` — the recorded video is real but tied to the slow playback cadence.

**Missing feature (explicitly requested, Priority 1):** the **4-Pane Transition Export (Input, Output, Radial PSD profile, Convergence metrics) in PDF + PNG** does **not** exist anywhere (only `.webm` video and Figure-2 PNG via `/api/benchmark/figure2/image`). No `jsPDF`/PDF generation is present.

---

## 5. Master Fixes & Implementation Backlog (Ranked P0 → P2)

### Priority 0 — Critical (correctness / crashes / broken claims)

| # | Fix | Where | Effort |
|---|---|---|---|
| P0-1 | **Retrain the model properly** (continuous γ, ≥200–500 epochs, real hold-outs) and re-export `neurospectrum_model.pt`. The current 5-iteration checkpoint cannot control γ and silently produces wrong science. | `train.py` / checkpoint artifacts | High |
| P0-2 | **Remove mock metrics** — replace hardcoded `cv_nnd=0.14` / `target_reached=True` / `measured_gamma_hat=1.0` in `morph()`, and `cached_cv=0.45` / `cached_min_dist=0.01` in `run_inference()` with real per-frame computation (or omit the field). | `universal_synthesizer.py:461–474`, `inference.py:202–203` | Low |
| P0-3 | **Fix `experiments/` staleness** — either delete `probe_model`/`run_unseen_gamma_evaluation` references and the unknown `train_continuous_gamma` kwargs, or (better) re-point `experiments/` at the current API. Both scripts currently crash. | `experiments/*` | Medium |
| P0-4 | **Fix velocity-trails Y-coordinate bug** (`prevPts[i][0]*h` → `prevPts[i][1]*h`). | `frontend/index.html:2037` | Trivial |
| P0-5 | **Stop mislabeling extrapolation as measurement** — mark the N>1024 O(N²) baseline as "estimated" in the scalability table and the README. | `test_scalability.py:87–89`, README | Low |
| P0-6 | **Correct the README benchmark tables** (or regenerate them from a genuinely trained model). Current numbers (30 ms, 84.4×, 2.3× accuracy, <35 MB, 60 FPS) are unreproducible. | README | Low |

### Priority 1 — Visual & UX polish

| # | Fix | Where | Effort |
|---|---|---|---|
| P1-1 | **Add the 4-Pane Transition Export** (Input point cloud, Output point cloud, Radial PSD vs target, Convergence metrics) in **both PDF and PNG** — client-side via a hidden high-DPI canvas + `canvas.toDataURL('image/png')` for PNG and `jsPDF` (or server-side `matplotlib`) for PDF. | `frontend/index.html`, new `backend` endpoint | Medium |
| P1-2 | **Replace `setInterval` playback with `requestAnimationFrame`** with a delta-time accumulator for true ~60 FPS and pause/resume; keep `setInterval` only for the progress-poll. | `frontend/index.html` | Medium |
| P1-3 | **Fix Figure-2 "live" metrics** — compute `d_mean` and `cv_voronoi` server-side from the actual points (`figure2_benchmark.compute_metrics`) instead of `min_spacing*1.6` / `cv_nnd` proxies. | `frontend` + `backend/figure2_benchmark.py` | Low |
| P1-4 | Confirm light-theme fidelity (default `class="light"`), sweep the "1-Click Optimal Blue Noise" button copy, and ensure the sidebar/nav is collapsible on narrow viewports. | `frontend/index.html` | Low |
| P1-5 | Verify Vector lattice / velocity field / target-ghost toggles render faithfully after the P0-4 trail fix; add a "target ghost" overlay toggle parity between sim and universal studios. | `frontend/index.html` | Low |

### Priority 2 — Engine optimization

| # | Fix | Where | Effort |
|---|---|---|---|
| P2-1 | **WebGL / OffscreenCanvas particle rendering** when N>2048 — render particles as GPU point sprites instead of 2D-context arcs; move the O(N²) lattice/beacon loops to a spatial grid. | `frontend/index.html` | High |
| P2-2 | **Batched Sinkhorn / OT speedup** for universal morphing — log-domain Sinkhorn is already used, but vectorize marginal updates and add early-stop tolerance + ε-annealing; move to torch on the inference device instead of CPU. | `universal_synthesizer.py` | Medium |
| P2-3 | **Unify the integrator** — make Verlet the real training integrator (or rename/downscope the docstring claim); pass `use_knn` through `_single_step_euler` so the O(Nk) path works in training. | `dynamics.py` | Medium |
| P2-4 | **Fix the in-place autograd hazard** in chunked spacing loss (`losses.py`) by using `masked_fill` on a cloned tensor. | `losses.py` | Low |
| P2-5 | **Honest kNN/global consistency at scale** — either increase `k` adaptively with N or report the γ̂ divergence; extend `test_knn_consistency` to N=1024/2048. | `energy.py`, `test_knn_consistency.py` | Medium |
| P2-6 | Reduce inference latency (~7 s → sub-second): precompute the radial projection on the right dtype, avoid re-running the direct point-Fourier fallback every call, and add `torch.inference_mode()` fast paths. | `inference.py`, `spectrum.py` | Medium |

---

## 6. Scientific Research Gaps & Novel Contribution Opportunities

The supplied literature (de Goes et al. *BNOT* 2012, Zhou et al. *DDA* 2012, Heck et al. *Step/Peak* 2013, Na et al. *LJL* 2024, Öztireli & Gross 2012, Zhang et al. 2019, Kipf et al. *NRI* 2018, Yang et al. *PointFlow* 2019) and the repo's own EGSR-2026 target (*Bojja & Padrón-Griffe, "A Dynamical System for Spectral Noise Synthesis"*) define the frontier. The target paper is a **hand-designed Langevin system** (phasor advection + repulsion + optional attraction) — *not* learned. That is exactly the opening for a **learned** version.

### 6.1 State of the art vs. NeuroSpectrum

| Axis | SOTA | NeuroSpectrum today | Gap |
|---|---|---|---|
| Spectrum matching | Classical per-target (DDA, BNOT) hits arbitrary P(f) exactly | Neural model **cannot** hit γ targets (bimodal) | Large |
| Amortization | LJL (plug-in, zero retrain) | Amortized in principle; broken in practice | Medium |
| Mechanism | Hand-designed forces (Langevin/phasor/LJ) | Learned energy field (interpretable) | *This is the real novelty — but currently under-delivered* |
| Generality | 2D mostly; LJL→3D generative | 2D torus only | Large |
| Interpretability | Analytic potentials | E(r,γ)/F(r,γ) curves + phase diagram (real) | Strength to lean on |

**Key insight (already flagged in the repo's own `outputs/proposal1_text.txt`):** "an MLP representing a pairwise energy / force-as-gradient / backprop-through-unrolled-dynamics / rasterized-PSD-loss" is **not** a sufficient novelty — it has precedent (NRI, learned force fields). The defensible contribution must be **continuous conditional interaction-law learning + unseen-γ generalization + interpretability + amortization**. The audit shows the *pipeline* for all four exists, but the *evidence* does not (5-iteration checkpoint).

### 6.2 Open research gaps → concrete modules to build now

**Gap A — Continuous spectral interpolation (γ ∈ [−2,+2] + non-power-law targets).**
- **Why now:** this is the *minimum* claim the project must make true. Current FiLM conditioning is weak; the model saturates.
- **Build:** (1) a `CurriculumGammaSampler` (anneal from single-γ overfit → full range); (2) a **spectral-Sinkhorn / log-PSD earth-mover** loss that matches the *shape* of arbitrary target P(f), not just a power-law slope; (3) a `NonPowerLawTarget` dataset class (step/peak blue-noise spectra from Heck et al.) to prove "any spectrum", not just "any slope".
- **Deliverable:** a Figure replacing Table 2 with a continuous γ-axis heatmap of |Δγ|.

**Gap B — Higher dimensions & manifolds (S², 3D meshes, volumetric).**
- **Why now:** `universal_synthesizer` already handles 3D (sphere/helix), but the *spectral engine* is 2D-torus only; LJL and PointFlow show 3D is where the field is heading.
- **Build:** a `ManifoldSpectralEngine` that (1) defines geodesic distances on a mesh (precomputed) and (2) computes a graph/harmonic PSD instead of FFT2D. Start with S² using spherical-harmonic PSD (natural analog of the radial spectrum) — this is a clean, publishable extension.
- **Deliverable:** blue-noise sampling on S² with the same γ-conditioned energy field.

**Gap C — Anisotropic & feature-oriented sampling.**
- **Why now:** Zhou et al.'s DDA handles anisotropic spectra; the EGSR target shows stippling/placement; NeuroSpectrum currently enforces *isotropy* (an anisotropy-loss term exists but is unused at λ=0).
- **Build:** condition the energy on a local tensor field $\mathbf{T}(x)$ (edge orientation / importance map) — e.g., anisotropic RBF kernels `φ(r; T)` — and add an `AnisotropicTarget` that accepts an image importance map. The "anisotropy" knob flips from *penalty* to *feature*.
- **Deliverable:** stippling that follows edges while preserving a locally-blue-noise spectrum.

**Gap D — Perceptual & physical benchmarks.**
- **Why now:** the current "2.0× MC variance reduction" is not robust and the blue-noise void isn't even visualized correctly.
- **Build:** (1) fix the spectral tooling (windowed/ensemble PSD, more k-space samples) so the void/peak are measurable; (2) a `RenderingBenchmark` that reports *perceptual* metrics (PSNR on structured-texture undersampling à la Heck et al.) and *MC* variance with error bars across seeds; (3) compare against true BNOT/CCVT and LJL baselines, not lightweight approximations.

**Gap E — Provable stability / interpretability as the differentiator.**
- **Why now:** the conservative-force + Newton's-3rd-law structure is the *strongest* defensible claim; it's currently undermined by damping+clamping.
- **Build:** a `ConservationProof` module: (1) assert $\sum_i F_i = 0$ and $\sum_i F_i \cdot \dot x_i = -dE/dt$ to numerical precision (already partly in `test_physics.py`); (2) quantify how damping/clamping violate it; (3) publish the E(r,γ)/F(r,γ) phase diagram with equilibrium-radii trajectory as a *scientific* result, not a dashboard widget.

### 6.3 A headline-worthy framing (recommended)

> **"Continuous Spectral Interaction Laws"** — *one* conditional neural energy field $E_\theta(r,\gamma)$ whose conservative dynamics synthesize the *entire* blue→pink→red→violet continuum, with (a) unseen-γ generalization, (b) an interpretable learned phase diagram $r_0(\gamma)$, and (c) provable conservative structure — benchmarked against the hand-designed Langevin system of Bojja & Padrón-Griffe (EGSR 2026).

This is achievable **only after** P0-1 (retrain), P0-2 (de-mock), and Gap A. Until then, the honest position is: *the architecture and pipeline are in place and correct; the model has not yet been trained to the point where the central claim is true.*

---

### Appendix — reproducibility commands used

```bash
python -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt pytest
export PYTHONPATH="$PWD/src"
pytest tests/ -v                                   # 48 passed (65.7s)
python tests/test_edge_cases.py                    # 18/18 passed (7.5s)
python tests/test_interpolation.py
python tests/test_benchmark_baseline.py
python tests/test_scalability.py
python tests/test_statistical_significance.py
python tests/test_graphics_applications.py
python tests/test_ablations.py                     # OOM (exit 137)
python run_dashboard.py                            # uvicorn :8000
```

*All timings in this report are from a CPU-only sandbox (3.8 GB RAM, torch 2.14.0+cpu); GPU/MPS timings will differ, but the accuracy/quality findings are hardware-independent.*
