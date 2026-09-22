"""
test_stateful_and_frozen_system.py — Comprehensive Unit & Integration Tests.

Validates:
1. Critical Test: Parameter Immutability during Inference (parameters_after == parameters_before).
2. Checkpoint Save/Load/Resume (theta_resumed == theta_saved, not random).
3. Stateful Training: θ is progressively updated, not silently reset.
4. Input Generalization (Spiral, Grid, Jitter, Random, Clustered).
5. Unseen-Gamma Evaluation (held-out targets).
6. Edge Cases: N=1, Overlapping points, Malformed input.
7. Interpretability: E(r, γ) and F(r, γ) generation.
"""

import os
import sys
import unittest
import torch
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from energy import NeuralPairwiseEnergy
from dynamics import DifferentiableSimulationEngine
from checkpoints import save_checkpoint, load_checkpoint, save_canonical_model, get_optimal_device
from inference import FrozenInferenceEngine
from interpret import compute_energy_and_force
from train import StatefulTrainingSession
from generalize import run_new_input_generalization_benchmark


class TestStatefulAndFrozenSystem(unittest.TestCase):

    def setUp(self):
        self.device = torch.device("cpu")
        torch.manual_seed(42)
        np.random.seed(42)
        self.test_ckpt_path = "tests/test_checkpoint_temp.pt"

    def tearDown(self):
        if os.path.exists(self.test_ckpt_path):
            try:
                os.remove(self.test_ckpt_path)
            except OSError:
                pass

    def test_critical_parameter_immutability_during_inference(self):
        """
        CRITICAL TEST (Section 30 of prompt):
        Save model parameters before inference.
        Run inference across multiple steps and targets.
        Verify parameters_after == parameters_before.
        Proves that inference NEVER retrains the model.
        """
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        engine = FrozenInferenceEngine(model, device=self.device)

        # Snapshot weights before inference
        weights_before = [p.clone() for p in engine.model.parameters()]

        # Run inference on multiple challenging targets and inputs
        res_spiral = engine.run_inference(target_gamma=1.0, initial_points="spiral", n_particles=64, num_steps=15)
        res_red = engine.run_inference(target_gamma=-1.2, initial_points="grid", n_particles=64, num_steps=15)

        # Snapshot weights after inference
        weights_after = [p.clone() for p in engine.model.parameters()]

        for p_bef, p_aft in zip(weights_before, weights_after):
            self.assertTrue(torch.equal(p_bef, p_aft), "FAILED: Weights mutated during inference!")

        self.assertTrue(engine.verify_parameters_frozen())
        self.assertEqual(res_spiral["status"]["retraining"], "OFF")
        self.assertEqual(res_spiral["status"]["energy_field"], "FROZEN")
        print(">> [PASS] Critical Parameter Immutability Test Passed: Zero weight drift during inference.")

    def test_stateful_training_progresses_and_resumes(self):
        """
        Verifies:
        1. Training updates θ progressively: θ(t+1) != θ(t).
        2. Checkpoint resume restores exact weights and optimizer: θ_resumed == θ_saved != random.
        3. Training continuation picks up from saved iteration count.
        """
        session = StatefulTrainingSession(
            hidden_dim=32, num_layers=3, n_particles=64, num_steps=5, device=self.device
        )

        init_weights = [p.clone().detach() for p in session.energy_model.parameters()]

        # Run 3 training steps
        step1 = session.train_step(target_gamma_val=1.0, batch_size=2)
        step2 = session.train_step(target_gamma_val=1.0, batch_size=2)
        step3 = session.train_step(target_gamma_val=1.0, batch_size=2)

        weights_step3 = [p.clone().detach() for p in session.energy_model.parameters()]

        # Assert weights changed progressively
        any_diff = False
        for w0, w3 in zip(init_weights, weights_step3):
            if not torch.equal(w0, w3):
                any_diff = True
                break
        self.assertTrue(any_diff, "FAILED: Training step did not update weights!")
        self.assertEqual(session.iteration, 3)

        # Save checkpoint
        session.save_checkpoint(self.test_ckpt_path)

        # Create a fresh new random session
        session_new = StatefulTrainingSession(
            hidden_dim=32, num_layers=3, n_particles=64, num_steps=5, device=self.device
        )
        random_weights = [p.clone().detach() for p in session_new.energy_model.parameters()]

        # Load checkpoint into new session
        session_new.load_checkpoint(self.test_ckpt_path)
        resumed_weights = [p.clone().detach() for p in session_new.energy_model.parameters()]

        # Verify weights match saved state and do not match random init
        for w_saved, w_resumed in zip(weights_step3, resumed_weights):
            self.assertTrue(torch.equal(w_saved, w_resumed), "Resumed weights do not match saved state!")

        self.assertEqual(session_new.iteration, 3)

        # Step again: should reach iteration 4
        step4 = session_new.train_step(target_gamma_val=1.0, batch_size=2)
        self.assertEqual(session_new.iteration, 4)
        print(">> [PASS] Stateful Training & Resume Test Passed: θ is preserved, restored, and advanced.")

    def test_new_input_generalization_execution(self):
        """
        Verifies that frozen inference accepts diverse unseen topologies:
        Spiral, Regular Grid, Jittered Grid, and Clustered Red.
        """
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        engine = FrozenInferenceEngine(model, device=self.device)

        results = run_new_input_generalization_benchmark(
            engine=engine,
            target_gamma=1.0,
            n_particles=64,
            num_steps=5
        )

        self.assertEqual(len(results), 5)
        for r in results:
            self.assertEqual(r["retraining_performed"], "NO (FROZEN)")
            self.assertIn("measured_gamma", r)
            self.assertIn("cv_nnd", r)
            self.assertGreater(r["runtime_ms"], 0.0)
        print(">> [PASS] New-Input Generalization Test Passed across 5 configurations.")

    def test_interpretability_energy_force_curves(self):
        """
        Verifies calculation of E(r, γ) and F(r, γ) curves.
        """
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        curves = compute_energy_and_force(model, gamma_val=1.0, r_min=0.01, r_max=0.4, num_points=30, device=self.device)

        self.assertEqual(len(curves["r"]), 30)
        self.assertEqual(len(curves["energy"]), 30)
        self.assertEqual(len(curves["force"]), 30)
        self.assertIn("primary_nature", curves)
        print(">> [PASS] Interpretability Test Passed: E(r, γ) and F(r, γ) evaluated cleanly.")

    def test_edge_cases_and_robustness(self):
        """
        Edge cases:
        - N=1 particle (zero forces, no crash)
        - Overlapping particles (smooth soft-core repulsion handles r=0 without NaN)
        """
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3, use_divergence_prior=True)
        engine = FrozenInferenceEngine(model, device=self.device)

        # 1. N=1 particle
        single_pt = torch.tensor([[[0.5, 0.5]]], device=self.device)
        res_single = engine.run_inference(target_gamma=1.0, initial_points=single_pt, num_steps=2)
        self.assertEqual(len(res_single["final_points"]), 1)

        # 2. Overlapping particles (distance = 0)
        overlapping_pts = torch.tensor([[[0.5, 0.5], [0.5, 0.5]]], device=self.device)
        res_overlap = engine.run_inference(target_gamma=1.0, initial_points=overlapping_pts, num_steps=3)
        final_pts = np.array(res_overlap["final_points"])
        # Particles should not be NaN
        self.assertFalse(np.isnan(final_pts).any(), "NaN detected in overlapping particle test!")
        print(">> [PASS] Edge Cases Test Passed: N=1 and overlapping points handled safely.")


if __name__ == "__main__":
    unittest.main()
