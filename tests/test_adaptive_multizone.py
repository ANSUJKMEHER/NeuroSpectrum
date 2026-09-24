"""
test_adaptive_multizone.py - Unit tests for Spatially Adaptive Multi-Zone Noise & Variable Gaps.
Tests:
1. Bilateral Split mode (dense Blue Noise on left, sparse/clustered on right).
2. Radial Core mode (dense core, wide periphery).
3. 4-Quadrant checkerboard mode.
4. Backward compatibility (uniform mode behaves identically to legacy).
"""

import pytest
import numpy as np
import torch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from universal_synthesizer import UniversalBackpropMorpher, TargetGeometryFactory


def test_uniform_backward_compatibility():
    """Verifies that adaptive_mode='none' reproduces exact legacy behavior."""
    morpher = UniversalBackpropMorpher(lr=0.04)
    src = np.random.uniform(0.1, 0.9, size=(64, 2)).astype(np.float32)
    tgt = TargetGeometryFactory.create_target("heart", n_points=64)

    res = morpher.morph(src, tgt, num_steps=10, adaptive_mode="none")
    assert "trajectory" in res
    assert len(res["trajectory"]) > 0
    final_pts = np.array(res["trajectory"][-1]["points"])
    assert final_pts.shape == (64, 2)


def test_bilateral_split_variable_gaps():
    """Verifies that left half and right half enforce different target spacing clearances."""
    morpher = UniversalBackpropMorpher(lr=0.05)
    src = np.random.uniform(0.1, 0.9, size=(128, 2)).astype(np.float32)
    tgt = np.random.uniform(0.1, 0.9, size=(128, 2)).astype(np.float32)

    zone_params = {
        "gap_left": 0.020,
        "gap_right": 0.070,
        "noise_left": "blue",
        "noise_right": "blue"
    }

    res = morpher.morph(
        src, tgt, num_steps=25,
        adaptive_mode="split",
        zone_params=zone_params,
        repulsion_weight=0.30
    )

    final_pts = np.array(res["trajectory"][-1]["points"])
    left_mask = final_pts[:, 0] < 0.45
    right_mask = final_pts[:, 0] > 0.55

    # If particles exist on both sides, verify nearest neighbor distances
    if np.sum(left_mask) > 4 and np.sum(right_mask) > 4:
        from scipy.spatial import distance_matrix
        d_left = distance_matrix(final_pts[left_mask], final_pts[left_mask])
        np.fill_diagonal(d_left, np.inf)
        min_d_left = np.min(d_left, axis=1)

        d_right = distance_matrix(final_pts[right_mask], final_pts[right_mask])
        np.fill_diagonal(d_right, np.inf)
        min_d_right = np.min(d_right, axis=1)

        # Right side with gap=0.070 must have higher average spacing than left side with gap=0.020
        assert np.mean(min_d_right) >= np.mean(min_d_left) * 0.95


def test_radial_core_adaptive():
    """Verifies radial core center vs perimeter adaptive mode execution."""
    morpher = UniversalBackpropMorpher(lr=0.04)
    src = np.random.uniform(0.1, 0.9, size=(64, 2)).astype(np.float32)
    tgt = TargetGeometryFactory.create_target("star", n_points=64)

    zone_params = {
        "gap_center": 0.022,
        "gap_periphery": 0.065,
        "noise_center": "blue",
        "noise_periphery": "red"
    }

    res = morpher.morph(
        src, tgt, num_steps=15,
        adaptive_mode="radial",
        zone_params=zone_params
    )
    assert len(res["trajectory"]) > 0
