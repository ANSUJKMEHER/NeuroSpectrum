"""
run_all_phases.py — Master End-to-End Test and Execution Harness.
Runs every single phase from Phase 0 to Phase 14 sequentially:
- Phase 0-2: Foundations & Spectral Analyzer (tests/test_foundations.py)
- Phase 3-5: Neural Physics & Conservation Laws (tests/test_physics.py)
- Phase 6: Multi-Objective & Anti-Collapse Losses (tests/test_losses.py)
- Forensic Deep Audit: SciPy Slope, Dirac Comb, dE/dt <= 0 (tests/test_deep_audit.py)
- Phase 9: Milestone 3 Unseen Gamma Interpolation (tests/test_interpolation.py)
- Phase 10: Classical Baseline Comparison (tests/test_benchmark_baseline.py)
- Phase 11: 5 Systematic Ablations (tests/test_ablations.py)
- Phase 12: O(Nk) k-NN Scalability Benchmark (tests/test_scalability.py)
- Phase 13: Multi-Seed Statistical Significance (tests/test_statistical_significance.py)
- Phase 14: Downstream Graphics Applications (tests/test_graphics_applications.py)
"""

import os
import sys
import time
import subprocess

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PHASE_SCRIPTS = [
    ("Phase 0-2: Foundations & Analyzer", "tests/test_foundations.py"),
    ("Phase 3-5: Neural Physics & Dynamics", "tests/test_physics.py"),
    ("Phase 6: Multi-Objective Loss Engine", "tests/test_losses.py"),
    ("Forensic Deep Audit (Math & Physics)", "tests/test_deep_audit.py"),
    ("Phase 9: Unseen Gamma Interpolation", "tests/test_interpolation.py"),
    ("Phase 10: Classical Baseline Comparison", "tests/test_benchmark_baseline.py"),
    ("Phase 11: Systematic Ablation Studies", "tests/test_ablations.py"),
    ("Phase 12: k-NN Scalability Benchmark", "tests/test_scalability.py"),
    ("Phase 13: Multi-Seed Statistical Significance", "tests/test_statistical_significance.py"),
    ("Phase 14: Downstream Graphics Applications", "tests/test_graphics_applications.py"),
    ("Engineering Edge-Case & Boundary Stress Tests", "tests/test_edge_cases.py"),
]

def main():
    print("=" * 80)
    print("NEUROSPECTRUM: SEQUENTIAL MASTER EXECUTION OF ALL PHASES (0-14)")
    print("=" * 80)
    
    python_bin = sys.executable
    results = []
    total_start = time.perf_counter()
    
    for idx, (name, script_path) in enumerate(PHASE_SCRIPTS, 1):
        print(f"\n[{idx:2d}/{len(PHASE_SCRIPTS)}] Running {name} ({script_path}) ...")
        t0 = time.perf_counter()
        
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.abspath("src")
        env["PYTHONIOENCODING"] = "utf-8"
        mpl_dir = os.path.join(os.path.expanduser("~"), ".cache", "matplotlib")
        os.makedirs(mpl_dir, exist_ok=True)
        env["MPLCONFIGDIR"] = mpl_dir
        
        proc = subprocess.run(
            [python_bin, "-X", "utf8", script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env
        )
        elapsed = time.perf_counter() - t0
        
        if proc.returncode == 0:
            print(f"  [PASSED] in {elapsed:.2f}s")
            results.append((name, script_path, "PASSED [OK]", elapsed, ""))
        else:
            print(f"  [FAILED] in {elapsed:.2f}s with code {proc.returncode}")
            print(f"  Stderr: {proc.stderr[:400]}")
            results.append((name, script_path, f"FAILED [ERR {proc.returncode}]", elapsed, proc.stderr))
            
    total_elapsed = time.perf_counter() - total_start
    
    print("\n" + "=" * 90)
    print("MASTER EXECUTION SUMMARY REPORT:")
    print("=" * 90)
    print(f"{'Phase / Suite':<45} | {'Status':<15} | {'Time (s)':<10}")
    print("-" * 90)
    
    all_passed = True
    for name, path, status, elapsed, err in results:
        print(f"{name:<45} | {status:<15} | {elapsed:8.2f}s")
        if "FAILED" in status:
            all_passed = False
            
    print("=" * 90)
    print(f"Total Execution Time: {total_elapsed:.2f} seconds")
    if all_passed:
        print("ALL TEST SUITES / PHASES PASSED WITH 100% SUCCESS!")
    else:
        print("SOME PHASES FAILED. PLEASE INSPECT LOGS ABOVE.")
    print("=" * 90)

if __name__ == "__main__":
    main()
