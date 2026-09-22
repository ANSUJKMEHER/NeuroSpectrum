# 🌌 NeuroSpectrum: Neural Simulation of Continuous Spectral Point Distributions

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyTorch 2.0+](https://img.shields.io/badge/PyTorch-2.0+-ee4c2c.svg)](https://pytorch.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

> **Official Implementation of the NeuroSpectrum Research Project**  
> Continuous synthesis, interpolation, and control of 2D spatial point distributions across the full power-law spectral continuum $P^*(f) = C \cdot f^\gamma, \gamma \in [-2.0, +2.0]$ using differentiable physics-informed neural simulation.

---

## 📌 Key Highlights

- **Continuous Spectral Control:** Single compact neural energy field $E_\theta(X, \gamma)$ generates the complete spectral spectrum—from clustered red noise ($\gamma < 0$) to Poisson white noise ($\gamma \approx 0$) and hyperuniform blue noise ($\gamma > 0$).
- **Conservative Differentiable Dynamics:** Exact physical forces computed via autograd $F_i = -\nabla_{x_i} E_\theta$ guarantee zero spurious work and strict momentum conservation ($\sum F_i = 0$).
- **Anti-Collapse Geometric Regularity:** Integrated scale-adaptive nearest-neighbor spacing loss ($\text{CV}_{\text{NND}}$) eliminates sub-kernel particle pairing while maintaining exact spectral power laws.
- **Constant Memory Footprint:** Gradient Checkpointing enables unrolling $T=50-100$ simulation steps on Mac M2 hardware with $<35\text{ MB}$ RAM footprint.
- **$O(Nk)$ Scalability for $N=4,096$:** Local $k$-NN graph interactions deliver up to **$84.4\times$ speedup** over classical $O(N^2)$ all-pairs formulations.
- **Downstream Graphics Utility:** Applied to density-weighted non-photorealistic stippling and Monte Carlo rendering quadrature ($2.0\times$ variance reduction).

---

## 🏛 Architecture Overview

```
Initial Uniform State X_0 ~ U[0, 1)^{B x N x 2}  +  Target Exponent γ ∈ [-2, +2]
                          │
                          ▼
   ┌────────────────────────────────────────────────────────┐
   │ Differentiable Euler Physics Engine (Gradient Checkpointed) │
   │   for t in 1 .. T:                                      │
   │     1. Energy: E_θ(X_t, γ) = Σ e_ij                     │
   │     2. Conservative Force: F_i = -∇_{x_i} E_θ            │
   │     3. Toroidal Euler Step: X_{t+1} = (X_t + dt*F) % 1  │
   └────────────────────────────────────────────────────────┘
                          │
                          ▼
   ┌────────────────────────────────────────────────────────┐
   │ Differentiable Toroidal Gaussian Splatting (M x M Grid) │
   └────────────────────────────────────────────────────────┘
                          │
                          ▼
   ┌────────────────────────────────────────────────────────┐
   │ Differentiable FFT2D & Radial Operator W_radial        │
   │ -> Measured PSD P_θ(f) & Spectral Slope γ̂               │
   └────────────────────────────────────────────────────────┘
                          │
                          ▼
   ┌────────────────────────────────────────────────────────┐
   │ Composite Multi-Objective Loss Engine                  │
   │   L = λ_spec * L_spec + λ_γ * L_γ + λ_q * L_spacing    │
   └────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Installation
```bash
# Clone repository
git clone https://github.com/your-username/NeuroSpectrum.git
cd NeuroSpectrum

# Create environment
python -m venv src/.venv
source src/.venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Comprehensive Test & Deep Audit Suite
```bash
source src/.venv/bin/activate
python tests/test_foundations.py
python tests/test_physics.py
python tests/test_losses.py
python tests/test_deep_audit.py
```

### 3. Reproduce All Benchmarks & Figures
```bash
# Run Milestone 3 (Unseen Gamma Interpolation)
python tests/test_interpolation.py

# Run Classical Baseline Benchmark
python tests/test_benchmark_baseline.py

# Run 5 Systematic Ablation Studies
python tests/test_ablations.py

# Run O(Nk) k-NN Scalability Benchmark (N=4096)
python tests/test_scalability.py

# Run Multi-Seed Statistical Significance (10 seeds/target)
python tests/test_statistical_significance.py

# Run Downstream Computer Graphics Applications
python tests/test_graphics_applications.py
```

---

## 📊 Benchmark Scorecards

### 1. Classical Baseline Comparison (Phase 10)
| Method | Wall-Clock Time (ms) | Target $\gamma^*$ | Measured $\hat{\gamma}$ | MAE |
| :--- | :---: | :---: | :---: | :---: |
| **Classical Adam (Per-Target)** | $73.2\text{ ms}$ | $+1.00$ | $+1.14$ | $0.140$ |
| **NeuroSpectrum (Neural Forward)** | **$30.1\text{ ms}$** | $+1.00$ | **$+1.06$** | **$0.060$** |
| **Speedup Factor** | **$2.4\times$ Faster** | — | — | **$2.3\times$ More Accurate** |

### 2. $O(Nk)$ Scalability Benchmark (Phase 12)
| Particle Count $N$ | All-Pairs $O(N^2)$ Runtime | Local $k$-NN $O(Nk)$ Runtime | Speedup Multiplier |
| :---: | :---: | :---: | :---: |
| $256$ | $151.5\text{ ms}$ | $13.4\text{ ms}$ | **$11.3\times$** |
| $512$ | $311.7\text{ ms}$ | $18.0\text{ ms}$ | **$17.3\times$** |
| $1,024$ | $1,873.7\text{ ms}$ | $48.4\text{ ms}$ | **$38.7\times$** |
| $2,048$ | $7,494.7\text{ ms}$ | $105.5\text{ ms}$ | **$71.0\times$** |
| **$4,096$** | **$29,978.7\text{ ms}$** | **$355.3\text{ ms}$** | **$84.4\times\ ⚡$** |

### 3. Downstream Monte Carlo Variance Reduction (Phase 14)
| Sample Budget $N$ | Random Uniform (White) RMSE | NeuroSpectrum (Blue) RMSE | Empirical Variance Reduction |
| :---: | :---: | :---: | :---: |
| $128$ | $0.07447$ | **$0.05301$** | **$2.0\times$** |
| $256$ | $0.04646$ | **$0.03337$** | **$1.9\times$** |
| $512$ | $0.02710$ | **$0.02154$** | **$1.6\times$** |
| $1,024$ | $0.02137$ | **$0.01723$** | **$1.5\times$** |

---

## 🖥 Interactive Scientific Web Dashboard & EGSR 2026 Reproduction

Launch the interactive research studio with real-time WebSocket telemetry, interactive particle simulation playback, video export, and published paper benchmarks:

```bash
# Install dependencies
pip install -r requirements.txt

# Launch Dashboard
python run_dashboard.py
```
Open **`http://localhost:8000`** in your browser.

### Key Dashboard Features:
1. **Interactive Simulation Studio**:
   - Continuous parameterization: target spectral slope $\gamma \in [-2.0, +2.0]$, particle count $N$, and steps.
   - Initial topological configurations: **Archimedean Spiral**, **Jittered Grid**, **Uniform Random**, **Regular Grid**.
   - **Video Playback & 1-Click `.webm` Video Export**: Play, pause, step frame-by-frame, and download high-resolution simulation videos.
   - **Live Spectral Verification**: 2D Fourier Power Spectrum $|F(\mathbf{k})|^2$ and 1D Radial Power Spectrum $P(k)$ updating frame-by-frame.
2. **EGSR 2026 Paper Benchmark Reproduction (Figure 2 & Table 2)**:
   - Full 6-method side-by-side comparison: **Input (Spiral)** | **CMJ** | **CCVT** | **Curl PN** | **Phasor GP / NeuroSpectrum (Ours)** | **PDS**.
   - Evaluates all 6 quantitative metrics: Mean spacing ($d_{\text{mean}}$), Anisotropy ($\hat{a}$), Discrepancy ($v_D$), Nyquist frequency ($f_N$), Quality ($Q = f_N / \hat{a}$), and Voronoi area regularity ($CV$).
   - 1-Click high-resolution publication PNG export (`/api/benchmark/figure2/image`).
3. **Comprehensive Metrics Guide**:
   - See [FIGURE2_AND_TABLE2_METRICS_EXPLAINED.md](FIGURE2_AND_TABLE2_METRICS_EXPLAINED.md) for full mathematical derivations, physical explanations, and the faculty presentation cheat sheet.

---

## 📜 Citation & Research Attribution

If you use **NeuroSpectrum** in your research, please cite:

```bibtex
@article{neurospectrum2026,
  title={NeuroSpectrum: Differentiable Neural Simulation of Continuous Spectral Point Distributions},
  author={NeuroSpectrum Research Team},
  journal={IEEE Transactions on Visualization and Computer Graphics / ACM Transactions on Graphics},
  year={2026}
}
```
