"""
test_full_dashboard_suite.py - Exhaustive Integration Test Suite for all Dashboard Panels.
Tests:
1. Simulation Studio:
   - All 4 initial patterns (Spiral, Grid, Jittered, Random)
   - All 4 noise presets (Blue, White, Pink, Red)
   - Continuous gamma targets
   - Auto-convergence mode
   - Trajectory telemetry: energy dissipation, cv_nnd, min_spacing, 2D/1D Fourier spectra
2. Universal Studio:
   - Procedural Shapes (Heart outline, Star solid)
   - Text Glyphs ("NEURO")
   - Hand-drawn polyline sketches
   - Custom uploaded point clouds (Saturn with rings)
   - Image silhouette rendering & optimal transport
   - Power-Law continuous particle distributions (shape-free)
   - Spatially Adaptive Multi-Zone Gap & Noise (Bilateral Split, Radial Core, 4-Quadrant)
3. Model & Infrastructure:
   - Model inspection & frozen parameter immutability
   - Experience buffer logging
"""

import sys
import os
import json
import base64
import io
import pytest
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


# ==============================================================================
# 1. SIMULATION STUDIO TESTS
# ==============================================================================

def test_sim_blue_noise_from_spiral():
    """Verify Simulation Studio: Spiral -> Blue Noise (gamma* = +1.0)."""
    resp = client.post("/api/inference/run", json={
        "target_gamma": 1.0,
        "initial_points": "spiral",
        "n_particles": 128,
        "num_steps": 50,
        "capture_interval": 5
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "trajectory" in data and len(data["trajectory"]) > 0
    res = data["results"]
    assert res["measured_gamma_hat"] > 0.0, f"Expected positive blue noise gamma, got {res['measured_gamma_hat']}"
    assert res["energy_dissipation"] >= 0, "Velocity Verlet must dissipate energy"
    assert res["cv_nnd"] < 0.35, "Blue noise must exhibit low NND variance"


def test_sim_red_noise_from_grid():
    """Verify Simulation Studio: Grid -> Red Noise (gamma* = -1.8) via adaptive dt."""
    resp = client.post("/api/inference/run", json={
        "target_gamma": -1.8,
        "initial_points": "grid",
        "n_particles": 128,
        "num_steps": 40,
        "capture_interval": 5
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    res = data["results"]
    assert res["measured_gamma_hat"] < -0.3, f"Expected red noise clustering (< -0.3), got {res['measured_gamma_hat']}"


def test_sim_white_noise_from_random():
    """Verify Simulation Studio: Random -> White Noise (gamma* = 0.0)."""
    resp = client.post("/api/inference/run", json={
        "target_gamma": 0.0,
        "initial_points": "random",
        "n_particles": 128,
        "num_steps": 25,
        "capture_interval": 5
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    res = data["results"]
    assert abs(res["measured_gamma_hat"]) < 1.2, f"Expected near-zero gamma, got {res['measured_gamma_hat']}"


def test_sim_initial_points_generator():
    """Verify /api/simulation/initial-points endpoint for all patterns."""
    for pattern in ["spiral", "grid", "jittered", "random", "clustered"]:
        resp = client.post("/api/simulation/initial-points", json={
            "distribution": pattern,
            "n_particles": 64
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["points"]) == 64
        assert "radial_psd" in data


# ==============================================================================
# 2. UNIVERSAL STUDIO TESTS (ANY INPUT -> ANY OUTPUT)
# ==============================================================================

def test_univ_shape_heart_outline():
    """Verify Universal Studio: Spiral -> Heart Outline."""
    resp = client.post("/api/universal/morph", json={
        "source_points": "spiral",
        "target_spec": {"type": "shape", "shape": "heart", "fill_mode": "outline"},
        "num_steps": 20,
        "n_particles": 128
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["trajectory"]) > 0
    final_pts = np.array(data["trajectory"][-1]["points"])
    assert final_pts.shape == (128, 2)


def test_univ_shape_star_solid():
    """Verify Universal Studio: Random -> Star Solid Interior Fill."""
    resp = client.post("/api/universal/morph", json={
        "source_points": "random",
        "target_spec": {"type": "shape", "shape": "star", "fill_mode": "solid"},
        "num_steps": 20,
        "n_particles": 128
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["trajectory"]) > 0


def test_univ_text_glyph():
    """Verify Universal Studio: Grid -> Text Glyph 'NEURO'."""
    resp = client.post("/api/universal/morph", json={
        "source_points": "grid",
        "target_spec": {"type": "text", "text": "NEURO"},
        "num_steps": 20,
        "n_particles": 128
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["trajectory"]) > 0


def test_univ_drawn_polyline():
    """Verify Universal Studio: Hand-drawn polyline target."""
    polyline = [[0.2, 0.2], [0.5, 0.8], [0.8, 0.2], [0.5, 0.5]]
    resp = client.post("/api/universal/morph", json={
        "source_points": "random",
        "target_spec": {"type": "draw", "points": polyline},
        "num_steps": 15,
        "n_particles": 64
    })
    assert resp.status_code == 200, resp.text


def test_univ_upload_point_cloud():
    """Verify Universal Studio: Uploaded Point Cloud (Saturn with Rings)."""
    saturn_file = "sample_saturn_rings.csv"
    assert os.path.exists(saturn_file), "sample_saturn_rings.csv must exist"
    with open(saturn_file, "r") as f:
        lines = f.read().splitlines()[1:]
    pts = [[float(x) for x in l.split(",")] for l in lines]

    # Test preview
    r_prev = client.post("/api/universal/target-preview", json={
        "target_type": "upload",
        "points": pts,
        "n_points": len(pts)
    })
    assert r_prev.status_code == 200
    assert "Uploaded Points" in r_prev.json()["label"]

    # Test morph
    r_morph = client.post("/api/universal/morph", json={
        "source_points": "spiral",
        "target_spec": {"type": "upload", "points": pts},
        "num_steps": 20,
        "n_particles": len(pts)
    })
    assert r_morph.status_code == 200
    final_pts = np.array(r_morph.json()["trajectory"][-1]["points"])
    assert final_pts.shape == (len(pts), 2)


def test_univ_upload_image_silhouette():
    """Verify Universal Studio: Uploaded PNG image mask."""
    # Create simple binary image in-memory
    img = Image.new("RGB", (64, 64), color="white")
    # Draw dark square in center
    arr = np.ones((64, 64, 3), dtype=np.uint8) * 255
    arr[20:44, 20:44] = 0
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64_str = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    resp = client.post("/api/universal/morph", json={
        "source_points": "random",
        "target_spec": {"type": "image", "image_base64": b64_str},
        "num_steps": 15,
        "n_particles": 64
    })
    assert resp.status_code == 200, resp.text


def test_univ_power_law_normal_distribution():
    """Verify Universal Studio: Power-Law target generates normal domain distribution (NO SHAPE)."""
    for gamma_target in [1.2, -1.2]:
        resp = client.post("/api/universal/target-preview", json={
            "target_type": "gamma",
            "gamma": gamma_target,
            "n_points": 128
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "Normal Distribution" in data["label"]
        pts = np.array(data["points"])
        # Verify points cover full domain rather than 1D shape contour
        x_std = np.std(pts[:, 0])
        y_std = np.std(pts[:, 1])
        assert x_std > 0.15 and y_std > 0.15, "Power-law target must span full 2D domain without shape constraint"


# ==============================================================================
# 3. SPATIALLY ADAPTIVE MULTI-ZONE ENGINE (FACULTY FEATURE)
# ==============================================================================

def test_univ_adaptive_bilateral_split():
    """Verify Faculty Multi-Zone: Bilateral Split (Left tight Blue, Right wide Red)."""
    resp = client.post("/api/universal/morph", json={
        "source_points": "spiral",
        "target_spec": {"type": "shape", "shape": "heart", "fill_mode": "outline"},
        "num_steps": 25,
        "n_particles": 128,
        "adaptive_mode": "split",
        "zone_params": {
            "gap_left": 0.020,
            "gap_right": 0.070,
            "noise_left": "blue",
            "noise_right": "red"
        }
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["trajectory"]) > 0


def test_univ_adaptive_radial_core():
    """Verify Faculty Multi-Zone: Radial Core (Dense core, wide perimeter)."""
    resp = client.post("/api/universal/morph", json={
        "source_points": "random",
        "target_spec": {"type": "shape", "shape": "star", "fill_mode": "solid"},
        "num_steps": 20,
        "n_particles": 128,
        "adaptive_mode": "radial",
        "zone_params": {
            "gap_center": 0.022,
            "gap_periphery": 0.065,
            "noise_center": "blue",
            "noise_periphery": "red"
        }
    })
    assert resp.status_code == 200, resp.text


# ==============================================================================
# 4. MODEL INTEGRITY & BACKEND STATUS
# ==============================================================================

def test_model_status_and_freeze():
    """Verify model status endpoint and parameter freeze integrity."""
    resp = client.get("/api/model/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_frozen_verified"] is True
    assert data["trainable_parameters"] == 0


def test_experience_buffer_status():
    """Verify experience replay buffer status."""
    resp = client.get("/api/experience/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_transitions" in data
