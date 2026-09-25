with open("README.md", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(".329\\text{ s}$", "$2.329\\text{ s}$")
content = content.replace(".175$", "$0.175$")
content = content.replace(".1\\times$ Faster", "$2.1\\times$ Faster")

with open("README.md", "w", encoding="utf-8") as f:
    f.write(content)
