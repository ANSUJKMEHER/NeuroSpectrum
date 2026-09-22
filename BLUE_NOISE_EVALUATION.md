# How We Mathematically Prove Points Are Blue Noise

This guide explains the quantitative evaluation metrics used by **NeuroSpectrum** to verify that generated particle distributions are genuinely **Blue Noise**, rather than white noise, red noise, or a regular crystal grid.

---

## 1. Quick Reference Cheat Sheet

When looking at the dashboard or evaluating output coordinates, here are the target numbers:

| Metric | Symbol | Blue Noise (Ideal) | White Noise (Poisson) | Red Noise (Clustered) | Regular Grid |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Spectral Exponent** | **γ (gamma)** | **+1.00 to +1.50** | **0.00** | **-1.00 to -2.00** | Discrete peaks |
| **Nearest-Neighbor CV** | **CV_NND** | **0.32 to 0.38** | **~0.523** | **> 0.60** | **0.00** |
| **Exclusion Zone** | **g(r < r_min)** | **Exactly 0.0** | **1.0** (no void) | **>> 1.0** (clumped) | Multi-step 0 |
| **Coincident Overlaps** | **Count** | **0** | Occasional | High | **0** |
| **Visual Appearance** | — | **Uniform glass-like dots** | Random television static | Clumps and voids | Rigid honeycomb |

---

## 2. Spatial Metrics (Calculated Directly on (x, y) Points)

These metrics test the physical spacing between particles in 2D space under toroidal boundary wrap.

### A. The Exclusion Zone: Pair Correlation Function g(r)
The Pair Correlation Function `g(r)` is the gold standard diagnostic in statistical mechanics and computer graphics.

* **What it measures**: The probability of finding a particle at distance `r` from any other particle, compared to pure random chance.
* **Blue Noise Signature**:
  1. **Void Zone**: `g(r) = 0.0` for any distance `r < r_min`. Every particle possesses an impenetrable protective disk where no other particle can enter.
  2. **First Coordination Shell**: A prominent peak right at the average distance `r ≈ 1 / sqrt(N)`.
  3. **Decay to 1.0**: For large distances, `g(r)` smoothly settles to `1.0`.

```
Pair Correlation g(r) Profile:

g(r)
3.0 |
2.0 |             /\  <-- First coordination shell (peaks at ~0.06)
1.0 |            /  \----------------- (Decays smoothly to 1.0)
0.0 +-----------/
    0        r_min (Exclusion zone: strictly ZERO particles here)
```

* **Why it matters**: If `g(r)` has a spike near `0`, particles are collapsing into clumps (failure). If `g(r)` is flat at `1.0`, it is pure white noise. Blue noise MUST have an exclusion hole.

---

### B. Spatial Regularity: Coefficient of Variation (CV_NND)
For every particle, we find the distance to its single nearest neighbor (`NND`). Then we compute:

`CV_NND = (Standard Deviation of NND) / (Mean of NND)`

* **Pure Random Points (White Noise / Poisson)**:
  - Points land independently, creating both empty gaps and close pairs.
  - The theoretical mathematical value in 2D is:
    `CV_Poisson = sqrt(4 - pi) / sqrt(pi) ≈ 0.523`
* **Blue Noise**:
  - Particles repel each other, equalizing mutual distance across the entire domain.
  - As a result, the distance variance is drastically lower:
    `CV_BlueNoise ≈ 0.32 to 0.38`
* **Crystal Grid**:
  - Every point has identical neighbor distance: `CV = 0.0`.
* **Red / Clustered Noise**:
  - High variance between tight clumps and voids: `CV > 0.60`.

---

### C. Minimum Particle Separation (r_min) & Coincident Pairs
* **Coincident Pairs**: Two points occupying the exact same coordinates (`r < 1e-4`). True blue noise requires **0 coincident pairs**.
* **Minimum Separation**: For `N = 256` particles in a `1.0 x 1.0` box:
  - Theoretical maximum packing distance is `~0.06`.
  - Blue noise consistently achieves `r_min ≈ 0.035 to 0.040`.

---

## 3. Spectral Metrics (Fourier Frequency Domain)

Robert Ulichney originally defined Blue Noise in 1987 by its frequency spectrum: **low frequencies must be completely absent, with power increasing towards high frequencies.**

### A. Radial Power Spectral Density (Radial PSD)
We take the 2D Fourier Transform of the points and average across concentric circles of frequency radius `k`:

1. **Sub-Nyquist Void**: Near `k = 0`, power is practically zero (`P(k) ≈ 0`). This proves there are no large-scale brightness variations or clustering.
2. **High-Frequency Ramp**: Power rises smoothly into the high frequencies.

```
Radial Power Spectrum P(k):

Power P(k)
 ^
 |                /-- High-frequency power
 |               /
 |              /
 |             /
 |            /
 +-----------+---------------------> Spatial Frequency k
 0         k_cutoff
 (Zero Low-Freq Energy)
```

---

### B. The Spectral Slope: γ (Gamma)
We fit a linear regression in log-log space: `log(P(k)) = γ * log(k) + C`:

* **γ ≈ +1.0 to +1.5 (Blue Noise)**: Power rises with frequency.
* **γ ≈ 0.0 (White Noise)**: Power is flat across all frequencies.
* **γ ≈ -1.0 to -2.0 (Red / Pink Noise)**: Power drops with frequency (energy trapped in low frequencies).

---

### C. Rotational Symmetry (Spectral Anisotropy)
True blue noise must be statistically isotropic (it looks identical when rotated at any angle):

`Anisotropy(k) = 10 * log10( Variance_angle / Mean_power^2 )`

* A value below **-10 dB** proves the pattern is free of directional biases, diagonal streaks, or grid-like lines.

---

## 4. Where to Find This in the Codebase

1. **Spatial Statistics & Pair Correlation**:
   - File: [`src/evaluate.py`](file:///d:/CAREER/uropnew/src/evaluate.py)
   - Function: `compute_spatial_statistics(points)` (lines 74–180)
   - Function: `compute_pair_correlation_2d(...)` (lines 15–72)

2. **Differentiable Fourier & PSD Slope**:
   - File: [`src/spectrum.py`](file:///d:/CAREER/uropnew/src/spectrum.py)
   - Class: `DifferentiableSpectralAnalyzer` (lines 16–165)

3. **Live Dashboard Telemetry**:
   - File: [`frontend/index.html`](file:///d:/CAREER/uropnew/frontend/index.html)
   - Cards:
     - Metric 1: `Target vs Measured γ` (displays measured slope `γ_hat` and error `Δ`)
     - Metric 2: `Spatial Regularity (CV)` (displays `CV_NND` value, e.g. `0.354`)
     - Metric 4: `Minimum Separation` (displays `r_min` and coincident pairs count `0`)
     - Metric 5: `Equilibrium Status` (shows auto-convergence step)
