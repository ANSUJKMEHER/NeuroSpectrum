"""
test_frozen_checkpoint.py - Frozen Inference & Stateful Training Guarantees.

The two properties that define the research claim:
  1. FROZEN INFERENCE: loading a checkpoint and running inference NEVER
     changes the model parameters (bit-identical before/after), performs
     no optimizer steps, and reports mode=FROZEN INFERENCE / retraining=NO.
  2. STATEFUL TRAINING: training updates the SAME parameters in place
     (theta(t+1) != theta(t)), and checkpoint save/load/resume restores
     them exactly - not a fresh random model.
"""

import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import torch
import numpy as np

from energy import NeuralPairwiseEnergy
from checkpoints import save_checkpoint, load_checkpoint, save_canonical_model
from inference import FrozenInferenceEngine
from train import StatefulTrainingSession


class TestFrozenCheckpoint(unittest.TestCase):

    def setUp(self):
        torch.manual_seed(3)
        self.tmpdir = tempfile.mkdtemp()
        self.ckpt = os.path.join(self.tmpdir, "test_ckpt.pt")

    # ------------------------------------------------------------------ #

    def test_checkpoint_save_load_roundtrip(self):
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        save_checkpoint(self.ckpt, energy_model=model, epoch=2, iteration=17,
                        model_config=model.architecture_summary())
        loaded, _, _, data = load_checkpoint(self.ckpt, device="cpu")
        for p_a, p_b in zip(model.parameters(), loaded.parameters()):
            self.assertTrue(torch.equal(p_a.detach(), p_b.detach()))
        self.assertEqual(data["iteration"], 17)

    def test_frozen_inference_parameters_identical(self):
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        save_checkpoint(self.ckpt, energy_model=model,
                        model_config=model.architecture_summary())
        engine = FrozenInferenceEngine(self.ckpt, device="cpu")
        before = [p.clone() for p in engine.model.parameters()]

        for g, init in [(1.0, "random"), (-1.2, "grid"), (0.4, "spiral")]:
            engine.run_inference(target_gamma=g, initial_points=init,
                                 n_particles=48, num_steps=10)

        after = [p.clone() for p in engine.model.parameters()]
        for pb, pa in zip(before, after):
            self.assertTrue(torch.equal(pb, pa),
                            "FROZEN violation: weights changed in inference!")
        self.assertTrue(engine.verify_parameters_frozen())

    def test_frozen_inference_status_flags(self):
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        save_checkpoint(self.ckpt, energy_model=model,
                        model_config=model.architecture_summary())
        engine = FrozenInferenceEngine(self.ckpt, device="cpu")
        res = engine.run_inference(target_gamma=0.5, initial_points="random",
                                   n_particles=48, num_steps=8)
        st = res["status"]
        self.assertEqual(st["mode"], "FROZEN INFERENCE")
        self.assertEqual(st["energy_field"], "FROZEN")
        self.assertIn(st["retraining"], ["OFF", "NO"])
        self.assertTrue(st["is_frozen_verified"])
        self.assertTrue(all(not p.requires_grad
                            for p in engine.model.parameters()))

    def test_stateful_training_updates_parameters(self):
        session = StatefulTrainingSession(
            hidden_dim=32, num_layers=3, n_particles=48, num_steps=4,
            device=torch.device("cpu"))
        w0 = [p.clone().detach() for p in session.energy_model.parameters()]
        session.train_step(target_gamma_val=1.0, batch_size=2)
        session.train_step(target_gamma_val=1.0, batch_size=2)
        w2 = [p.clone().detach() for p in session.energy_model.parameters()]
        changed = any(not torch.equal(a, b) for a, b in zip(w0, w2))
        self.assertTrue(changed,
                        "stateful training must update theta in place")
        self.assertEqual(session.iteration, 2)

    def test_stateful_resume_preserves_parameters(self):
        s1 = StatefulTrainingSession(hidden_dim=32, num_layers=3,
                                     n_particles=48, num_steps=4,
                                     device=torch.device("cpu"))
        for _ in range(3):
            s1.train_step(target_gamma_val=0.5, batch_size=2)
        s1.save_checkpoint(self.ckpt)
        w_saved = [p.clone().detach()
                   for p in s1.energy_model.parameters()]

        s2 = StatefulTrainingSession(hidden_dim=32, num_layers=3,
                                     n_particles=48, num_steps=4,
                                     device=torch.device("cpu"))
        s2.load_checkpoint(self.ckpt)
        for a, b in zip(w_saved, s2.energy_model.parameters()):
            self.assertTrue(torch.equal(a, b.detach()),
                            "resume must restore the exact trained weights")
        self.assertEqual(s2.iteration, 3)

    def test_trajectory_recording_complete(self):
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        engine = FrozenInferenceEngine(model, device="cpu")
        res = engine.run_inference(target_gamma=0.0, initial_points="random",
                                   n_particles=48, num_steps=20,
                                   capture_interval=2)
        traj = res["trajectory"]
        self.assertGreaterEqual(len(traj), 2)
        self.assertEqual(traj[0]["step"], 0)
        self.assertEqual(traj[-1]["step"], 20)
        self.assertEqual(len(res["energy_trajectory"]), 21)
        self.assertEqual(len(res["gamma_trajectory"]), 21)
        self.assertEqual(len(res["evolution_stages"]), 6)
        # no fabricated placeholders: every recorded entry has real numbers
        for entry in traj:
            self.assertIsInstance(entry["cv_nnd"], float)
            self.assertGreaterEqual(entry["cv_nnd"], 0.0)
            self.assertIsInstance(entry["min_spacing"], float)

    def test_canonical_export(self):
        model = NeuralPairwiseEnergy(hidden_dim=32, num_layers=3)
        path = save_canonical_model(model, save_path=self.ckpt,
                                    model_config=model.architecture_summary())
        self.assertTrue(os.path.exists(path))
        engine = FrozenInferenceEngine(path, device="cpu")
        self.assertTrue(engine.verify_parameters_frozen())


if __name__ == "__main__":
    unittest.main()
