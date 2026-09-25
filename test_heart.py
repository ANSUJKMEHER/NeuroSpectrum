import numpy as np
from PIL import Image, ImageDraw

M = 4000
t = np.linspace(0, 2 * np.pi, M)
x_raw = 16 * (np.sin(t) ** 3)
y_raw = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
x = 0.5 + (x_raw / 38.0)
y = 0.52 - (y_raw / 38.0)

size = 256
img = Image.new("L", (size, size), 0)
draw = ImageDraw.Draw(img)
poly = [(int(p[0] * size), int(p[1] * size)) for p in zip(x, y)]
draw.polygon(poly, fill=255)
img.save('heart_mask.png')
