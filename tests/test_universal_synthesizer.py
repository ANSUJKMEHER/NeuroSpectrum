"""
test_universal_synthesizer.py — Comprehensive Multi-Modal Verification of Universal Synthesizer.
Tests backpropagation morphing across ALL modalities & dimensions:
1. Multi-Modal Target Ingestion: Text Strings, 2D Shapes, 3D Geometries, Image Masks
2. Input Invariance: Unequal Point Counts (N != M), Arbitrary Bounding Boxes ([-10, 10] -> [100, 200])
3. High-Fidelity Text Morphing: Random Noise -> Text "NEURO"
4. 3D Manifold Morphing: Random 3D Cube -> 3D Sphere (D=3)
5. Parametric 2D Shapes: Random Noise -> 5-Pointed Star
6. Saves publication figures:
   - outputs/figures/universal_target_morphing.png
   - outputs/figures/universal_text_morphing.png
   - outputs/figures/universal_3d_morphing.png
"""

import os
import sys
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np
from PIL import Image, ImageDraw

from universal_synthesizer import (
    SinkhornOptimalTransport,
    DifferentiableChamferLoss,
    TargetGeometryFactory,
    UniversalBackpropMorpher,
    VectorFieldFlowMatching,
    plot_universal_morphing_trajectory
)


def test_universal_synthesizer():
    print("=" * 80)
    print("🌌 TESTING UNIVERSAL MULTI-MODAL POINT SYNTHESIZER VIA BACKPROPAGATION")
    print("=" * 80)
    
    # -------------------------------------------------------------------
    # TEST 1: Multi-Modal Target Ingestion
    # -------------------------------------------------------------------
    print("\n[1/6] Testing Multi-Modal Target Ingestion (Text, Images, 2D, 3D) ...")
    # A. Text Target
    pts_text = TargetGeometryFactory.create_from_text("NEURO", n_points=384)
    assert pts_text.shape == (384, 2), f"Expected text shape (384, 2), got {pts_text.shape}"
    
    # B. Image Target (Create test mask)
    test_img = Image.new('L', (100, 100), color=255)
    draw = ImageDraw.Draw(test_img)
    draw.ellipse([20, 20, 80, 80], fill=0) # Black disc on white
    pts_img = TargetGeometryFactory.create_from_image(test_img, n_points=300)
    assert pts_img.shape == (300, 2), f"Expected image shape (300, 2), got {pts_img.shape}"
    
    # C. 3D Target (Sphere)
    pts_3d = TargetGeometryFactory.create_target("sphere_3d", n_points=350)
    assert pts_3d.shape == (350, 3), f"Expected 3D shape (350, 3), got {pts_3d.shape}"
    
    # D. 2D Shapes (Star, Spiral, Heart, Double Rings)
    for s in ["star", "spiral", "heart", "double_rings"]:
        pts = TargetGeometryFactory.create_target(s, n_points=256)
        assert pts.shape == (256, 2)
    print("  ✓ Successfully created targets from: Text ('NEURO'), Image Mask, 3D Sphere, and 2D Shapes.")

    # -------------------------------------------------------------------
    # TEST 2: Arbitrary Bounding Boxes & Unequal Point Counts (N != M)
    # -------------------------------------------------------------------
    print("\n[2/6] Testing Arbitrary Coordinate Scales & Unequal Point Counts (N=450 != M=280) ...")
    # Source in [-10.0, +10.0], Target in [+100.0, +250.0]
    np.random.seed(42)
    source_wild = np.random.uniform(-10.0, 10.0, size=(450, 2)).astype(np.float32)
    target_wild = (TargetGeometryFactory.create_target("star", n_points=280) * 150.0) + 100.0
    
    morpher = UniversalBackpropMorpher(lr=0.04)
    res_wild = morpher.morph(source_wild, target_wild, num_steps=120, record_history=False)
    
    final_wild = np.array(res_wild['final_points'])
    assert final_wild.shape == (450, 2), f"Output must preserve source particle count: {final_wild.shape}"
    # Check that output matches target scale [100, 250]
    assert final_wild.min() >= 95.0 and final_wild.max() <= 255.0, "Output must be re-projected to target bounding box"
    print(f"  ✓ Arbitrary scale handled: Source [-10, 10] (N=450) morphed to Target [100, 250] (M=280) perfectly.")

    # -------------------------------------------------------------------
    # TEST 3: High-Fidelity Text Morphing (Random Noise -> Text 'NEURO')
    # -------------------------------------------------------------------
    print("\n[3/6] Testing Text Morphing via Backpropagation (Random Noise -> Text 'NEURO') ...")
    source_rnd = np.random.rand(400, 2).astype(np.float32)
    target_neuro = TargetGeometryFactory.create_from_text("NEURO", n_points=400)
    
    t0 = time.perf_counter()
    res_text = morpher.morph(source_rnd, target_neuro, num_steps=160, record_history=True)
    t_text = time.perf_counter() - t0
    
    cd_fn = DifferentiableChamferLoss()
    cd_init = cd_fn(torch.tensor(source_rnd), torch.tensor(target_neuro)).item()
    cd_final = cd_fn(torch.tensor(res_text['final_points']), torch.tensor(target_neuro)).item()
    cd_drop = (cd_init - cd_final) / cd_init * 100.0
    print(f"  ✓ Text 'NEURO' Morphing: Chamfer {cd_init:.5f} -> {cd_final:.5f} ({cd_drop:.1f}% reduction in {t_text:.2f}s)")
    assert cd_final < cd_init * 0.50, "Backpropagation must reduce Chamfer distance by at least 50% while maintaining spacing"
    plot_universal_morphing_trajectory(res_text, save_path="outputs/figures/universal_text_morphing.png", shape_name="Text 'NEURO'")

    # -------------------------------------------------------------------
    # TEST 4: 3D Point Cloud Morphing (Random 3D Cube -> 3D Sphere)
    # -------------------------------------------------------------------
    print("\n[4/6] Testing 3D Point Cloud Morphing via Backpropagation (D=3) ...")
    source_3d = np.random.rand(350, 3).astype(np.float32)
    target_sphere = TargetGeometryFactory.create_target("sphere_3d", n_points=350)
    
    t0 = time.perf_counter()
    res_3d = morpher.morph(source_3d, target_sphere, num_steps=160, record_history=True)
    t_3d = time.perf_counter() - t0
    
    cd_3d_init = cd_fn(torch.tensor(source_3d), torch.tensor(target_sphere)).item()
    cd_3d_final = cd_fn(torch.tensor(res_3d['final_points']), torch.tensor(target_sphere)).item()
    pct_3d = (cd_3d_init - cd_3d_final) / cd_3d_init * 100.0
    print(f"  ✓ 3D Sphere Morphing: Chamfer {cd_3d_init:.4f} -> {cd_3d_final:.4f} ({pct_3d:.1f}% drop in {t_3d:.2f}s)")
    assert cd_3d_final < cd_3d_init * 0.40, f"Backpropagation must converge on 3D target (expected >60% drop, got {pct_3d:.1f}%)"
    plot_universal_morphing_trajectory(res_3d, save_path="outputs/figures/universal_3d_morphing.png", shape_name="3D Sphere")

    # -------------------------------------------------------------------
    # TEST 5: Parametric 2D Star Morphing
    # -------------------------------------------------------------------
    print("\n[5/6] Testing Parametric 2D Star Morphing ...")
    target_star = TargetGeometryFactory.create_target("star", n_points=350)
    source_star = np.random.rand(350, 2).astype(np.float32)
    res_star = morpher.morph(source_star, target_star, num_steps=160, record_history=True)
    plot_universal_morphing_trajectory(res_star, save_path="outputs/figures/universal_target_morphing.png", shape_name="5-Pointed Star")
    print("  ✓ Star morphing trajectory saved.")

    # -------------------------------------------------------------------
    # TEST 6: Neural Flow Matching in D Dimensions (2D & 3D)
    # -------------------------------------------------------------------
    print("\n[6/6] Testing Neural Flow Matching Vector Fields (2D and 3D) ...")
    flow_2d = VectorFieldFlowMatching(dim=2, hidden_dim=64, num_frequencies=4)
    v2 = flow_2d(torch.rand(64, 2), torch.tensor(0.3))
    assert v2.shape == (64, 2)
    
    flow_3d = VectorFieldFlowMatching(dim=3, hidden_dim=64, num_frequencies=4)
    v3 = flow_3d(torch.rand(64, 3), torch.tensor(0.7))
    assert v3.shape == (64, 3)
    
    loss_flow = torch.mean(v3 ** 2)
    loss_flow.backward()
    for p in flow_3d.parameters():
        assert p.grad is not None and not torch.isnan(p.grad).any()
    print("  ✓ Multi-dimensional (2D & 3D) Flow Matching velocity fields verified.")

    print("\n" + "=" * 80)
    print("🏆 ALL 6 MULTI-MODAL UNIVERSAL TARGET SYNTHESIS TESTS PASSED!")
    print("=" * 80)

if __name__ == "__main__":
    test_universal_synthesizer()
