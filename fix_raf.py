import re

with open("frontend/index.html", "r", encoding="utf-8") as f:
    content = f.read()

# Replace the setInterval block for playbackTimer
pattern = re.compile(
    r"appState\.playbackTimer = setInterval\(\(\) => \{(.*?)\}, 35\);", 
    re.DOTALL
)

replacement = r'''let lastTime = 0;
            const animLoop = (timestamp) => {
                if (!appState.isPlaying) return;
                if (timestamp - lastTime >= 35) {
\1
                    lastTime = timestamp;
                }
                appState.playbackTimer = requestAnimationFrame(animLoop);
            };
            appState.playbackTimer = requestAnimationFrame(animLoop);'''

# Wait, the inner block has an else { clearInterval... } which we need to fix
def replacer(match):
    inner = match.group(1)
    inner = inner.replace("clearInterval(appState.playbackTimer);", "cancelAnimationFrame(appState.playbackTimer); return;")
    return f'''let lastTime = 0;
            const animLoop = (timestamp) => {{
                if (!appState.isPlaying) return;
                if (timestamp - lastTime >= 35) {{
{inner}
                    lastTime = timestamp;
                }}
                appState.playbackTimer = requestAnimationFrame(animLoop);
            }};
            appState.playbackTimer = requestAnimationFrame(animLoop);'''

new_content = pattern.sub(replacer, content)

# Also fix togglePause/resetSimulation
new_content = new_content.replace("clearInterval(appState.playbackTimer)", "cancelAnimationFrame(appState.playbackTimer)")

with open("frontend/index.html", "w", encoding="utf-8") as f:
    f.write(new_content)
print("Replaced with regex!")
