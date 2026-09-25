import numpy as np
from PIL import Image

def mock_target(N):
    t = np.linspace(0, 2*np.pi, N, endpoint=False)
    return np.stack([np.cos(t), np.sin(t)], axis=-1)

print("Morphing outline without repulsion...")
