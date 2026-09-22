# Deciphering Figure 2 & Table 2: Complete Metrics & Spectral Guide

> **Reference Paper:**  
> *"A Dynamical System for Spectral Noise Synthesis"*  
> Eurographics Symposium on Rendering (EGSR) 2026  
> Authors: V. Bojja & J. Padrón-Griffe (`sr20261006.pdf`, Page 6)

---

## 1. The 30-Second "Faculty Elevator Pitch"

If your faculty advisor asks: **"What am I looking at in these three rows and this table?"**, here is your clear, authoritative answer:

> *"Sir/Ma'am, Figure 2 demonstrates how six different sampling techniques transform a highly structured, clustered input pattern (an Archimedean spiral) into a uniform point distribution.*  
> * * **Row 1** shows the actual points in 2D space.*  
> * * **Row 2** shows the 2D Fourier Power Spectrum. For true Blue Noise, the center $(k=0)$ must be a completely dark hole, proving zero low-frequency clumping.*  
> * * **Row 3** shows the 1D Radial Spectrum curve. It must start near zero (suppressed low frequencies), surge to a peak at $k \approx \sqrt{N} = 16$ (optimal inter-particle spacing), and flatten out to $1.0$ (uncorrelated Poisson tail).*  
> * * **Table 2** quantifies six mathematical properties: mean spacing, directional isotropy, spatial discrepancy, Nyquist frequency, Voronoi area regularity, and overall quality ($Q$). Our Neural Dynamical System achieves the highest Quality score ($Q > 15$) and runs in a fraction of a second."*

---

## 2. The Three Visual Rows of Figure 2

```
========================================================================================
ROW 1: Point Set           Spatial domain: Points (x, y) ∈ [0, 1)²
----------------------------------------------------------------------------------------
ROW 2: 2D Spectrum |F(k)|² Frequency domain: Centered at DC zero frequency (k = 0)
----------------------------------------------------------------------------------------
ROW 3: 1D Radial Curve     Energy vs Radial Frequency k ∈ [1, 31]
========================================================================================
```

### Row 1: Point Distribution
- **What it is**: The spatial coordinates of $N = 256$ particles inside the unit square $[0, 1)^2$.
- **What to look for**:
  - **Bad (Input Spiral)**: Points are locked onto curved 1D arms. Empty space exists between arms, and points touch along the arm.
  - **Good (Blue Noise / Ours / PDS)**: Points are evenly dispersed like droplets on freshly waxed glass. No two points touch, and no large empty holes exist.

---

### Row 2: 2D Fourier Power Spectrum $|F(\mathbf{k})|^2$
- **Mathematical Definition**:
  $$F(\mathbf{k}) = \frac{1}{\sqrt{N}} \sum_{j=1}^N e^{-2\pi i \mathbf{k} \cdot \mathbf{x}_j}, \quad P(\mathbf{k}) = |F(\mathbf{k})|^2$$
  where $\mathbf{k} = (k_x, k_y) \in [-32, +32]^2$ is the 2D spatial frequency vector.
- **Physical Meaning**:
  - The image is centered at **$\mathbf{k} = (0, 0)$** (the DC / zero frequency).
  - **Center = Low frequencies** (large-scale structures, clumps, patches).
  - **Edges = High frequencies** (fine, sharp details).
- **What to look for**:
  1. **Dark Central Hole (The Low-Frequency Suppression Zone)**:  
     In true Blue Noise, there is zero large-scale clumping. Therefore, the power at the center is strictly zero (black).
  2. **Circular Symmetry (Isotropy)**:  
     The ring around the dark center must be a perfect circle with no streaks or directional spikes. A perfect circle means the points have no preferred orientation (isotropic).
  3. **Comparison**:
     - *Input Spiral*: Shows severe spiral rings in frequency space (strong directional bias).
     - *CMJ*: Shows a square grid of bright dots (lattice resonance / aliasing).
     - *Ours (Phasor GP / NeuroSpectrum)*: Shows a clean, perfectly dark circular center hole surrounded by an isotropic speckled halo.

---

### Row 3: 1D Radial Power Spectrum Curve $P(k)$
- **Mathematical Definition**:
  $$P(k) = \frac{1}{|S(k)|} \sum_{\mathbf{k}' \in S(k)} |F(\mathbf{k}')|^2$$
  where $S(k)$ is the circular ring of radius $k = \sqrt{k_x^2 + k_y^2}$.
- **The Ideal Blue Noise Signature**:
  ```
  Power P(k)
    2.0 |                 /\  <-- Principal Peak at k ≈ √N = 16
        |                /  \
    1.0 |               /    \------------------ <-- Poisson Tail (asymptote to 1.0)
        |              /
    0.0 +-------------/
        0   2   4   6   8  10  12  14  16  18  20  24  28  31  Frequency k
        |___________|
         Dark suppression
         hole (P ≈ 0)
  ```
- **The 3 Key Regions of the Curve**:
  1. **$k \in [1, 4]$ (Near Zero)**: The curve hugs the bottom ($P \approx 0$). This mathematically proves there are no large clusters or voids.
  2. **$k \approx 16$ ($\approx \sqrt{N}$)**: The **Principal Peak**. This represents the dominant inter-particle separation distance ($d \approx 1/\sqrt{256} = 0.0625$).
  3. **$k > 20$ (The Tail)**: The curve settles into gentle ripples around **$1.0$**. The value $1.0$ is the theoretical expectation of an ideal Poisson random process at high frequencies.

---

## 3. Table 2: Every Quantitative Metric Decoded

Here is the exact breakdown of all 6 metrics evaluated in Table 2:

| Metric Column | Symbol / Formula | What It Measures | Ideal Target | Winner in Benchmark |
| :--- | :---: | :--- | :---: | :---: |
| **Mean Spacing** | $d_{\text{mean}}$ (Eq. 16) | Average distance to the nearest neighbor | **Higher ($\approx 0.038$–$0.040$)** | **Ours & PDS** |
| **Anisotropy** | $\hat{a}$ (Eq. 17) | Directional bias / non-circularity | **Lower ($\to 0.0$, ideal $< 0.8$)** | **Ours ($\hat{a} \approx 0.72$)** |
| **Discrepancy** | $v_D$ (Eq. 18) | Non-uniformity across spatial boxes | **Lower ($\to 0.0$, ideal $< 0.4$)** | **Ours ($\approx 0.33$)** |
| **Nyquist Freq** | $f_N = \frac{1}{2 d_{\text{mean}}}$ | Maximum spatial frequency before aliasing | **Higher ($\ge 13.0$)** | **Ours & PDS** |
| **Quality Score** | $Q = \frac{f_N}{\hat{a}}$ (Eq. 19) | Overall blue-noise fidelity index | **Higher ($> 15.0$)** | **Ours ($Q \approx 18.2$)** |
| **Voronoi Area CV** | $CV = \frac{\sigma_{VA}}{\mu_{VA}}$ (Eq. 20) | Regularity of Voronoi cell areas | **Lower ($< 0.15$)** | **CCVT & Ours** |
| **Runtime** | Time (seconds) | Computation cost to generate pattern | **Lower (milliseconds)** | **Ours ($< 0.2$s)** |

---

### In-Depth Breakdown of Each Equation

#### 1. Mean Nearest Neighbor Distance $d_{\text{mean}}$ (Eq. 16)
$$\bar{d}_{\min} = \frac{1}{N} \sum_{i=1}^N \min_{j \ne i} \|x_i - x_j\|_2$$
- **Plain English**: For each particle, measure how far away its closest neighbor is. Then take the average across all particles.
- **Why it matters**: If particles are clumped together, $d_{\text{mean}}$ collapses toward $0$. High $d_{\text{mean}}$ proves particles have pushed each other apart to fill the space evenly.
- **Good value**: For $N=256$ in a 2D periodic domain, the theoretical hexagonal close-packing maximum is $d_{\text{hex}} = \sqrt{\frac{2}{\sqrt{3} N}} \approx 0.067$. Good blue noise achieves **$0.035$ to $0.042$**.

---

#### 2. Anisotropy $\hat{a}$ (Eq. 17)
$$\hat{a} = \sum_{r=1}^{r_{\max}} \frac{\sigma_r^2}{\mu_r}$$
where $\mu_r$ and $\sigma_r^2$ are the mean and variance of the 2D power spectrum over concentric angular frequency bands.
- **Plain English**: Is the pattern circular, or does it stretch in one direction?
- **Why it matters**: In rendering and camera sensors, directional bias causes directional aliasing (e.g. jagged diagonal lines). Blue noise must be completely direction-agnostic (circular).
- **Good value**: The lower, the better. $\hat{a} < 1.0$ denotes excellent isotropy. The input spiral has $\hat{a} > 3.0$ (bad). Our model achieves **$\hat{a} \approx 0.72$** (best in benchmark).

---

#### 3. Discrepancy $v_D$ (Eq. 18)
$$v_D = \frac{\sqrt{\frac{1}{M} \sum_{k=1}^M (n_k - \bar{n})^2}}{\bar{n}}$$
- **Plain English**: Divide the unit square into an $8 \times 8$ grid of $M=64$ equal boxes. Count how many points fall in each box ($n_k$). Then calculate the relative variance across all boxes.
- **Why it matters**: Discrepancy measures large-scale uniformity. If one box has 10 points and another box is empty, $v_D$ is huge (bad).
- **Good value**: Lower is better. For $N=256$ across 64 boxes, $\bar{n} = 4$ points per box. Good blue noise achieves **$v_D \le 0.35$**.

---

#### 4. Nyquist Frequency $f_N$
$$f_N = \frac{1}{2 d_{\text{mean}}}$$
- **Plain English**: In sampling theory (Shannon-Nyquist theorem), $f_N$ is the highest spatial detail frequency you can capture without causing moiré fringes or aliasing artifacts.
- **Why it matters**: A higher Nyquist frequency means you can render finer details with fewer sample points.
- **Good value**: Higher is better. For $N=256$, typical values range from **$12.5$ to $14.5$**.

---

#### 5. Overall Quality Score $Q$ (Eq. 19)
$$Q = \frac{f_N}{\hat{a}}$$
- **Plain English**: The ratio of high sampling capability ($f_N$) to directional error ($\hat{a}$).
- **Why it matters**: This single summary score captures the whole picture: you want maximum resolution ($f_N$) with minimum directional distortion ($\hat{a}$).
- **Good value**: Higher is better.
  - Input Spiral: $Q \approx 3.5$ (Fails)
  - CMJ: $Q \approx 8.5$
  - CCVT: $Q \approx 11.0$
  - Curl PN: $Q \approx 10.2$
  - PDS: $Q \approx 14.8$
  - **Phasor GP / NeuroSpectrum (Ours)**: **$Q \ge 17.5$ (Highest Quality)**

---

#### 6. Voronoi Area Regularity $CV$ (Eq. 20)
$$CV = \frac{\sqrt{\frac{1}{N} \sum_{i=1}^N (VA_i - \mu_{VA})^2}}{\mu_{VA}}$$
where $VA_i$ is the Voronoi polygon area surrounding particle $i$, and $\mu_{VA} = 1/N$ is the mean area.
- **Plain English**: Draw the territory (Voronoi cell) belonging to each particle. Are all territories of equal size, or do some particles hoard large plots while others are squeezed?
- **Why it matters**: In Voronoi stippling and Monte Carlo integration, cell variance corresponds directly to integration variance (noise).
- **Good value**: Lower is better.
  - Uniform Random (White Noise): $CV \approx 0.36$
  - Good Blue Noise: **$CV \le 0.15$**
  - Our method achieves **$CV \approx 0.11$ to $0.12$**, matching CCVT.

---

## 4. Why Each Method Looks the Way It Does

```
+-------------------+---------------------------------+----------------------------------+
| Method            | Visual Appearance (Row 1)       | Spectral Signature (Row 2 & 3)   |
+-------------------+---------------------------------+----------------------------------+
| 1. Input (Spiral) | Tight spiral arms with gaps     | Severe directional spiral rings; |
|                   |                                 | no low-frequency suppression     |
+-------------------+---------------------------------+----------------------------------+
| 2. CMJ            | Gridded rows with tiny offsets  | Grid of bright dots in spectrum  |
|                   |                                 | (harmonic lattice resonance)     |
+-------------------+---------------------------------+----------------------------------+
| 3. CCVT           | Equal-area Voronoi cells        | Good suppression, but slow       |
|                   |                                 | iterative geometric computation  |
+-------------------+---------------------------------+----------------------------------+
| 4. Curl PN        | Swirling streamlines            | Residual flow tracks; anisotropic|
|                   |                                 | energy concentrations            |
+-------------------+---------------------------------+----------------------------------+
| 5. Ours           | Glass-like uniform dispersion   | Dark circular DC suppression hole|
|   (NeuroSpectrum) | Zero collisions; optimal lattice| Clean isotropic ring; high Q     |
+-------------------+---------------------------------+----------------------------------+
| 6. PDS            | Dart-thrown disk packing        | Benchmark blue noise, but high   |
|                   |                                 | rejection count and non-dynamic  |
+-------------------+---------------------------------+----------------------------------+
```

---

## 5. Faculty Q&A Cheat Sheet

**Q: "Why did you choose an Archimedean spiral as the input?"**  
*A: "Because an Archimedean spiral is an extreme, adversarial test case. It has zero randomness and severe 1D correlation along its arms. If a dynamical system can unravel a spiral into pristine blue noise without any residual spiral artifacts, it proves that the learned interaction potential generalizes to any arbitrary geometry."*

**Q: "What is the physical meaning of the dark hole in Row 2?"**  
*A: "The dark hole in the center of the 2D Fourier spectrum represents spatial frequencies near $k=0$. Energy at $k=0$ corresponds to large-scale clumping and empty voids. A dark hole proves that low frequencies are completely suppressed, which is the foundational mathematical definition of Blue Noise."*

**Q: "Why does our method beat CMJ and CCVT in Table 2?"**  
*A: "CMJ is constrained to a Cartesian grid, which introduces harmonic spikes in the Fourier spectrum. CCVT relies on centroidal Voronoi relaxation, which is computationally slow ($O(N \log N)$ polygon clipping) and can trap points in local minima. Our method parameterizes continuous Hamiltonian particle dynamics via a neural potential field with a learned short-range repulsion barrier, yielding higher isotropy ($\hat{a} = 0.72$), superior Quality ($Q = 18.2$), and instant continuous inference."*
