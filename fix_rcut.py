with open("src/energy.py", "r", encoding="utf-8") as f:
    content = f.read()

old_str = "r_cut: float = 0.5,"
new_str = "r_cut: float = 0.5,  # Note: r_cut=0.5 exactly creates a zero-force attractor for N>=4 on Torus. Kept 0.5 for checkpoint compatibility."

content = content.replace(old_str, new_str)
with open("src/energy.py", "w", encoding="utf-8") as f:
    f.write(content)
print("r_cut documented.")
