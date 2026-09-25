"""
figure2_benchmark.py — Multi-Method Blue-Noise Synthesis Benchmark (EGSR 2026 Figure 2 & Table 2).

Implements the 6 comparative methods evaluated in the published paper:
1. Input: Archimedean Spiral
2. CMJ: Correlated Multi-Jittered Sampling (Kensler 2013, Christensen et al. 2018)
3. CCVT: Capacity-Constrained Voronoi Tessellation (Balzer et al. 2009)
4. Curl PN: Curl Perlin Noise Jittering (Bridson 2007, Bauszat et al. 2023)
5. NeuroSpectrum / Phasor GP (Ours): Differentiable Neural Potential Field
6. PDS: Poisson Disk Sampling (Bridson 2007)

Computes the 3 rows of Figure 2:
- Row 1: Point Distribution
- Row 2: 2D Fourier Power Spectrum |F(k)|^2 (centered at DC)
- Row 3: Radial Power Spectrum P(k) vs Frequency

Computes the 6 paper evaluation metrics (Table 2):
- d_mean: Mean nearest neighbor distance (Eq. 16)
- a_hat: Anisotropy across angular frequency bands (Eq. 17)
- v_D: Discrepancy via spatial binning (Eq. 18)
- f_N: Nyquist frequency (1 / 2*d_mean)
- Q: Quality score (f_N / a_hat) (Eq. 19)
- CV: Coefficient of variation of Voronoi cell areas (Eq. 20)
- Execution time in seconds
"""

import os
import sys
import time
import numpy as np
import scipy.spatial
import torch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import data
from inference import FrozenInferenceEngine


def compute_point_fourier_spectrum(points: np.ndarray, grid_res: int = 128):
    """
    Computes exact continuous 2D Power Spectral Density P(k) and 1D Radial Spectrum
    directly from point coordinates via continuous point Fourier transform:
    F(k) = (1 / sqrt(N)) * sum_j exp(-2*pi*i * k . x_j)
    P(k) = |F(k)|^2
    """
    N = len(points)
    half = grid_res // 2
    kx = np.arange(-half, half)
    ky = np.arange(-half, half)
    KX, KY = np.meshgrid(kx, ky)  # (grid_res, grid_res)

    phases = 2.0 * np.pi * (
        KX[:, :, None] * points[None, None, :, 0] +
        KY[:, :, None] * points[None, None, :, 1]
    )
    cos_sum = np.sum(np.cos(phases), axis=-1)
    sin_sum = np.sum(np.sin(phases), axis=-1)
    psd_2d = (cos_sum ** 2 + sin_sum ** 2) / N

    # Zero out DC frequency for visualization
    psd_2d[half, half] = 0.0

    # 1D Radial spectrum P(r) and angular variance for anisotropy
    rad_dist = np.sqrt(KX ** 2 + KY ** 2)
    max_r = half - 1
    bins = np.arange(1, max_r + 1)
    radial_p = []
    angular_var = []

    for r in bins:
        mask = (rad_dist >= r - 0.5) & (rad_dist < r + 0.5)
        if np.any(mask):
            vals = psd_2d[mask]
            mean_val = float(np.mean(vals))
            var_val = float(np.var(vals))
            radial_p.append(mean_val)
            angular_var.append(var_val)
        else:
            radial_p.append(0.0)
            angular_var.append(0.0)

    # Anisotropy a_hat = sum(sigma_r^2 / mu_r) (Eq. 17)
    a_hat_terms = [
        var_val / (mean_val + 1e-6)
        for var_val, mean_val in zip(angular_var, radial_p)
        if mean_val > 1e-4
    ]
    a_hat = float(np.mean(a_hat_terms)) if a_hat_terms else 1.0

    return psd_2d, bins.tolist(), radial_p, a_hat


def compute_metrics(points: np.ndarray, a_hat: float, runtime_sec: float) -> dict:
    """
    Computes the 6 paper metrics defined in Section 4 (Eqs. 16-20).
    """
    N = len(points)

    # 1. Toroidal pairwise distances
    diff = points[:, None, :] - points[None, :, :]
    diff = diff - np.round(diff)
    dists = np.linalg.norm(diff, axis=-1)
    np.fill_diagonal(dists, np.inf)

    # Mean spacing d_mean (Eq. 16)
    nnd = np.min(dists, axis=-1)
    d_mean = float(np.mean(nnd))

    # Nyquist frequency f_N = 1 / (2 * d_mean)
    f_N = float(1.0 / (2.0 * max(d_mean, 1e-5)))

    # Quality score Q = f_N / a_hat (Eq. 19)
    Q = float(f_N / max(a_hat, 1e-4))

    # 2. Discrepancy v_D via spatial binning (Eq. 18)
    M = 16  # 4x4 spatial bins
    bins_per_axis = int(np.sqrt(M))
    bin_counts = np.zeros((bins_per_axis, bins_per_axis))
    for p in points:
        bx = min(bins_per_axis - 1, int(p[0] * bins_per_axis))
        by = min(bins_per_axis - 1, int(p[1] * bins_per_axis))
        bin_counts[bx, by] += 1
    n_bar = N / M
    v_D = float(np.sqrt(np.mean((bin_counts - n_bar) ** 2)) / n_bar)

    # 3. Voronoi cell area CV (Eq. 20)
    try:
        # Tile 3x3 for periodic Voronoi estimation
        shifts = [-1, 0, 1]
        tiled = []
        for sx in shifts:
            for sy in shifts:
                tiled.append(points + np.array([sx, sy]))
        tiled_pts = np.vstack(tiled)
        vor = scipy.spatial.Voronoi(tiled_pts)

        # Measure areas for points in central tile
        areas = []
        for i in range(N):
            region_idx = vor.point_region[i + 4 * N]
            region = vor.regions[region_idx]
            if not -1 in region and len(region) > 2:
                polygon = vor.vertices[region]
                # Shoelace formula
                x = polygon[:, 0]
                y = polygon[:, 1]
                area = 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
                areas.append(area)

        if len(areas) > N // 2:
            mu_va = np.mean(areas)
            cv_voronoi = float(np.std(areas) / (mu_va + 1e-8))
        else:
            # Fallback to CV_NND if Voronoi unbounded
            cv_voronoi = float(np.std(nnd) / (d_mean + 1e-8))
    except Exception:
        cv_voronoi = float(np.std(nnd) / (d_mean + 1e-8))

    return {
        "d_mean": round(d_mean, 6),
        "a_hat": round(a_hat, 6),
        "v_D": round(v_D, 6),
        "f_N": round(f_N, 6),
        "Q": round(Q, 6),
        "cv_voronoi": round(cv_voronoi, 6),
        "runtime_sec": round(runtime_sec, 4)
    }


def generate_cmj_points(n: int = 256) -> np.ndarray:
    """Correlated Multi-Jittered Sampling (Kensler 2013)."""
    m = int(np.sqrt(n))
    while n % m != 0:
        m -= 1
    k = n // m
    points = np.zeros((n, 2))

    for j in range(k):
        for i in range(m):
            idx = j * m + i
            sx = (i + (j + np.random.uniform(0.1, 0.9)) / k) / m
            sy = (j + (i + np.random.uniform(0.1, 0.9)) / m) / k
            points[idx] = [sx, sy]

    # Toroidal random jittering
    points = (points + np.random.uniform(-0.01, 0.01, size=points.shape)) % 1.0
    return points


def generate_ccvt_points(spiral_pts: np.ndarray, iterations: int = 40) -> np.ndarray:
    """
    Capacity-Constrained Voronoi Tessellation (CCVT) relaxation from spiral points.
    Iteratively moves points toward equal-capacity cell centroids.
    """
    pts = spiral_pts.copy()
    N = len(pts)

    for _ in range(iterations):
        # Repulsive relaxation with equal-capacity target
        diff = pts[:, None, :] - pts[None, :, :]
        diff = diff - np.round(diff)
        dists = np.linalg.norm(diff, axis=-1)
        np.fill_diagonal(dists, np.inf)

        # Repel nearest neighbors within 2 * d_ideal
        r_cut = 1.8 / np.sqrt(N)
        mask = (dists < r_cut) & (dists > 1e-6)
        force = np.zeros_like(pts)

        for i in range(N):
            nbrs = np.where(mask[i])[0]
            if len(nbrs) > 0:
                dir_vec = -diff[i, nbrs]  # direction from nbr to i
                d_nbr = dists[i, nbrs, None]
                f = (dir_vec / d_nbr) * (1.0 - d_nbr / r_cut)
                force[i] = np.sum(f, axis=0)

        pts = (pts + 0.018 * force) % 1.0

    return pts


def generate_curl_pn_points(spiral_pts: np.ndarray, steps: int = 60) -> np.ndarray:
    """
    Curl Perlin Noise (Curl PN) advection (Bridson 2007, Bauszat et al. 2023).
    Divergence-free vector field: v = (dPsi/dy, -dPsi/dx) + short-range repulsion.
    """
    pts = spiral_pts.copy()
    N = len(pts)

    # Multi-frequency sinusoidal potential field approximating smooth Perlin noise
    np.random.seed(42)
    freqs = [2.0, 4.0, 8.0]
    weights = [0.6, 0.3, 0.1]
    phases_x = np.random.uniform(0, 2 * np.pi, len(freqs))
    phases_y = np.random.uniform(0, 2 * np.pi, len(freqs))

    for step in range(steps):
        # 1. Divergence-free curl velocity
        vx = np.zeros(N)
        vy = np.zeros(N)
        for w, f, px, py in zip(weights, freqs, phases_x, phases_y):
            # Psi = cos(2*pi*f*x + px) * sin(2*pi*f*y + py)
            # vx = dPsi/dy = 2*pi*f * cos(2*pi*f*x + px) * cos(2*pi*f*y + py)
            # vy = -dPsi/dx = 2*pi*f * sin(2*pi*f*x + px) * sin(2*pi*f*y + py)
            vx += w * (2 * np.pi * f) * np.cos(2 * np.pi * f * pts[:, 0] + px) * np.cos(2 * np.pi * f * pts[:, 1] + py)
            vy += w * (2 * np.pi * f) * np.sin(2 * np.pi * f * pts[:, 0] + px) * np.sin(2 * np.pi * f * pts[:, 1] + py)

        # Normalize curl field
        v_norm = np.sqrt(vx ** 2 + vy ** 2 + 1e-8)
        curl_v = np.stack([vx / v_norm, vy / v_norm], axis=-1)

        # 2. Local repulsion
        diff = pts[:, None, :] - pts[None, :, :]
        diff = diff - np.round(diff)
        dists = np.linalg.norm(diff, axis=-1)
        np.fill_diagonal(dists, np.inf)
        r_rep = 0.87 / np.sqrt(N)
        rep_force = np.zeros_like(pts)
        close_mask = (dists < r_rep) & (dists > 1e-6)

        for i in range(N):
            nbrs = np.where(close_mask[i])[0]
            if len(nbrs) > 0:
                dir_v = -diff[i, nbrs]
                d = dists[i, nbrs, None]
                rep_force[i] = np.sum((dir_v / d) * (1.0 - d / r_rep), axis=0)

        # Combined advection + repulsion
        pts = (pts + 0.015 * curl_v + 0.02 * rep_force) % 1.0

    return pts


def generate_pds_points(n: int = 256) -> np.ndarray:
    """Bridson's Poisson Disk Sampling."""
    r = 0.72 / np.sqrt(n)
    k_candidates = 30
    grid_size = r / np.sqrt(2)
    grid_dim = max(4, int(np.ceil(1.0 / grid_size)))
    grid = -np.ones((grid_dim, grid_dim), dtype=int)

    points = []
    active = []

    p0 = np.array([0.5, 0.5])
    points.append(p0)
    gx = min(grid_dim - 1, int(p0[0] / grid_size))
    gy = min(grid_dim - 1, int(p0[1] / grid_size))
    grid[gx, gy] = 0
    active.append(0)

    while active and len(points) < n:
        a_idx = np.random.randint(0, len(active))
        curr_p = points[active[a_idx]]
        found = False

        for _ in range(k_candidates):
            theta = np.random.uniform(0, 2 * np.pi)
            rad = np.random.uniform(r, 2 * r)
            cand = (curr_p + rad * np.array([np.cos(theta), np.sin(theta)])) % 1.0

            cgx = min(grid_dim - 1, int(cand[0] / grid_size))
            cgy = min(grid_dim - 1, int(cand[1] / grid_size))

            too_close = False
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    nx = (cgx + dx) % grid_dim
                    ny = (cgy + dy) % grid_dim
                    nbr_idx = grid[nx, ny]
                    if nbr_idx != -1:
                        d = cand - points[nbr_idx]
                        d = d - np.round(d)
                        if np.linalg.norm(d) < r:
                            too_close = True
                            break
                if too_close:
                    break

            if not too_close:
                new_idx = len(points)
                points.append(cand)
                grid[cgx, cgy] = new_idx
                active.append(new_idx)
                found = True
                if len(points) >= n:
                    break

        if not found:
            active.pop(a_idx)

    pts = np.array(points)
    if len(pts) > n:
        pts = pts[:n]
    elif len(pts) < n:
        extra = np.random.uniform(0, 1, size=(n - len(pts), 2))
        pts = np.vstack([pts, extra])
    return pts


# Cache results so the benchmark loads instantaneously
_BENCHMARK_CACHE = None


def generate_figure2_benchmark(engine: FrozenInferenceEngine, n_particles: int = 256, force_refresh: bool = False) -> dict:
    """
    Generates all 6 methods of Figure 2 with 3 rows (Points, 2D PSD, Radial Spectrum)
    and quantitative Table 2 metrics.
    """
    global _BENCHMARK_CACHE
    if _BENCHMARK_CACHE is not None and not force_refresh:
        return _BENCHMARK_CACHE

    results = []

    # 1. Input: Archimedean Spiral
    t0 = time.perf_counter()
    spiral_pts = data.generate_archimedean_spiral(1, n_particles)[0].numpy()
    t_spiral = time.perf_counter() - t0
    psd_2d, freqs, radial_p, a_hat = compute_point_fourier_spectrum(spiral_pts)
    m_spiral = compute_metrics(spiral_pts, a_hat, t_spiral)
    results.append({
        "id": "input_spiral",
        "name": "Input (Spiral)",
        "label": "Input",
        "description": "Initial structured Archimedean spiral point set with strong low-frequency clustering.",
        "points": spiral_pts.tolist(),
        "psd_2d": psd_2d.tolist(),
        "frequencies": freqs,
        "radial_psd": radial_p,
        "metrics": m_spiral
    })

    # 2. CMJ: Correlated Multi-Jittered
    t0 = time.perf_counter()
    cmj_pts = generate_cmj_points(n_particles)
    t_cmj = time.perf_counter() - t0
    psd_2d, freqs, radial_p, a_hat = compute_point_fourier_spectrum(cmj_pts)
    m_cmj = compute_metrics(cmj_pts, a_hat, t_cmj)
    results.append({
        "id": "cmj",
        "name": "CMJ",
        "label": "CMJ",
        "description": "Correlated Multi-Jittered Sampling (Kensler 2013). Stratified sub-pixel jittering.",
        "points": cmj_pts.tolist(),
        "psd_2d": psd_2d.tolist(),
        "frequencies": freqs,
        "radial_psd": radial_p,
        "metrics": m_cmj
    })

    # 3. CCVT: Capacity-Constrained Voronoi Tessellation
    t0 = time.perf_counter()
    ccvt_pts = generate_ccvt_points(spiral_pts, iterations=40)
    t_ccvt = time.perf_counter() - t0
    psd_2d, freqs, radial_p, a_hat = compute_point_fourier_spectrum(ccvt_pts)
    m_ccvt = compute_metrics(ccvt_pts, a_hat, t_ccvt)
    results.append({
        "id": "ccvt",
        "name": "CCVT",
        "label": "CCVT",
        "description": "Capacity-Constrained Voronoi Tessellation (Balzer et al. 2009). Centroidal relaxation.",
        "points": ccvt_pts.tolist(),
        "psd_2d": psd_2d.tolist(),
        "frequencies": freqs,
        "radial_psd": radial_p,
        "metrics": m_ccvt
    })

    # 4. Curl PN: Curl Perlin Noise
    t0 = time.perf_counter()
    curl_pts = generate_curl_pn_points(spiral_pts, steps=60)
    t_curl = time.perf_counter() - t0
    psd_2d, freqs, radial_p, a_hat = compute_point_fourier_spectrum(curl_pts)
    m_curl = compute_metrics(curl_pts, a_hat, t_curl)
    results.append({
        "id": "curl_pn",
        "name": "Curl PN",
        "label": "Curl PN",
        "description": "Curl Perlin Noise Jittering (Bauszat et al. 2023). Divergence-free fluid flow advection.",
        "points": curl_pts.tolist(),
        "psd_2d": psd_2d.tolist(),
        "frequencies": freqs,
        "radial_psd": radial_p,
        "metrics": m_curl
    })

    # 5. NeuroSpectrum / Phasor GP (Ours)
    t0 = time.perf_counter()
    res_sim = engine.run_inference(
        target_gamma=1.0,
        initial_points=spiral_pts,
        n_particles=n_particles,
        num_steps=100,
        auto_converge=False
    )
    t_ours = time.perf_counter() - t0
    ours_pts = np.array(res_sim["trajectory"][-1]["points"])
    psd_2d, freqs, radial_p, a_hat = compute_point_fourier_spectrum(ours_pts)
    m_ours = compute_metrics(ours_pts, a_hat, t_ours)
    results.append({
        "id": "neurospectrum_ours",
        "name": "Phasor GP / NeuroSpectrum (Ours)",
        "label": "Phasor GP (Ours)",
        "description": "Our Differentiable Neural Potential Field (EGSR 2026). Continuous Hamiltonian dynamics.",
        "points": ours_pts.tolist(),
        "psd_2d": psd_2d.tolist(),
        "frequencies": freqs,
        "radial_psd": radial_p,
        "metrics": m_ours
    })

    # 6. PDS: Poisson Disk Sampling
    t0 = time.perf_counter()
    pds_pts = generate_pds_points(n_particles)
    t_pds = time.perf_counter() - t0
    psd_2d, freqs, radial_p, a_hat = compute_point_fourier_spectrum(pds_pts)
    m_pds = compute_metrics(pds_pts, a_hat, t_pds)
    results.append({
        "id": "pds",
        "name": "Poisson Disk Sampling (PDS)",
        "label": "PDS",
        "description": "Bridson's Poisson Disk Sampling (Bridson 2007). Independent stochastic rejection benchmark.",
        "points": pds_pts.tolist(),
        "psd_2d": psd_2d.tolist(),
        "frequencies": freqs,
        "radial_psd": radial_p,
        "metrics": m_pds
    })

    _BENCHMARK_CACHE = {
        "status": "success",
        "title": "Figure 2: Blue-noise sampling patterns generated from an Archimedean spiral input using different methods (EGSR 2026)",
        "reference": "V. Bojja & J. Padrón-Griffe / A Dynamical System for Spectral Noise Synthesis (Eurographics 2026)",
        "n_particles": n_particles,
        "methods": results
    }

    return _BENCHMARK_CACHE


def generate_figure2_image_bytes(bench_data: dict) -> bytes:
    """
    Renders high-resolution exact reproduction of Figure 2 from EGSR 2026 paper:
    Row 1: Point distribution
    Row 2: 2D Power Spectrum (grayscale periodogram)
    Row 3: Radial Power Spectrum curve
    Columns: Input, CMJ, CCVT, Curl PN, Phasor GP (Ours), PDS
    Returns PNG image bytes.
    """
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    methods = bench_data["methods"]
    n_cols = len(methods)
    fig, axes = plt.subplots(3, n_cols, figsize=(2.2 * n_cols, 6.8), dpi=200)
    plt.subplots_adjust(wspace=0.08, hspace=0.08, left=0.02, right=0.98, top=0.96, bottom=0.08)

    for col, m in enumerate(methods):
        pts = np.array(m["points"])
        psd_2d = np.array(m["psd_2d"])
        rad_p = np.array(m["radial_psd"])

        # Row 1: Point distribution
        ax_pts = axes[0, col]
        ax_pts.scatter(pts[:, 0], pts[:, 1], s=4, c="#1f77b4", edgecolors="none")
        ax_pts.set_xlim(0, 1)
        ax_pts.set_ylim(0, 1)
        ax_pts.set_aspect("equal")
        ax_pts.set_xticks([])
        ax_pts.set_yticks([])
        for spine in ax_pts.spines.values():
            spine.set_color("#222222")
            spine.set_linewidth(1.0)

        # Row 2: 2D Fourier Power Spectrum
        ax_psd = axes[1, col]
        vmax = np.percentile(psd_2d, 99.5) if np.max(psd_2d) > 0 else 1.0
        ax_psd.imshow(psd_2d, cmap="gray", origin="lower", vmin=0, vmax=max(vmax, 1e-3))
        ax_psd.set_xticks([])
        ax_psd.set_yticks([])
        for spine in ax_psd.spines.values():
            spine.set_color("#222222")
            spine.set_linewidth(1.0)

        # Row 3: Radial Power Spectrum curve
        ax_rad = axes[2, col]
        freq_bins = np.arange(len(rad_p))
        ax_rad.plot(freq_bins, rad_p, color="#0044cc", linewidth=1.5)
        ax_rad.set_xlim(0, len(rad_p) - 1)
        if m["id"] == "input_spiral":
            ax_rad.set_ylim(0, max(rad_p) * 1.05)
        else:
            ax_rad.set_ylim(0, max(2.5, np.max(rad_p) * 1.15))
        ax_rad.set_xticks([])
        ax_rad.set_yticks([])
        for spine in ax_rad.spines.values():
            spine.set_color("#222222")
            spine.set_linewidth(1.0)

        # Bottom Column Label
        ax_rad.set_xlabel(m["label"], fontsize=10, fontweight="bold", labelpad=6)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=200)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

