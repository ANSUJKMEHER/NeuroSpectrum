"""
run_dashboard.py — Production Launch Script for NeuroSpectrum Scientific Dashboard.

Starts the FastAPI server with WebSocket streaming on port 8000.
"""

import os
import sys
import socket
import uvicorn

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

root_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(root_dir, 'src'))
sys.path.insert(0, os.path.join(root_dir, 'backend'))

from api import app

def find_available_port(start_port: int = 8000) -> int:
    for port in range(start_port, start_port + 50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                return port
        except OSError:
            continue
    return start_port

if __name__ == "__main__":
    requested_port = int(os.environ.get("PORT", 0))
    port = requested_port if requested_port > 0 else find_available_port(8000)
    print(f"\n" + "=" * 60)
    print("🚀 NEUROSPECTRUM SCIENTIFIC DASHBOARD")
    print(f"📡 Serving at: http://localhost:{port}")
    print("🔬 Differentiable Neural Simulation of Continuous Spectral Point Distributions")
    print("=" * 60 + "\n")
    uvicorn.run("api:app", host="0.0.0.0", port=port, log_level="info", reload=True, reload_dirs=[os.path.join(root_dir, 'backend'), os.path.join(root_dir, 'src')])
