import os
import base64
import zipfile
import io

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk('src'):
        for f in files:
            if f.endswith('.py'):
                z.write(os.path.join(root, f), arcname=f) # flat inside zip

b64_str = base64.b64encode(buf.getvalue()).decode('utf-8')

script = f'''import os
import sys
import base64
import zipfile

_base = "/kaggle/working"
_b64 = "{b64_str}"
_zpath = os.path.join(_base, "_ns_src.zip")
with open(_zpath, "wb") as f:
    f.write(base64.b64decode(_b64))

with zipfile.ZipFile(_zpath) as z:
    z.extractall(_base)

sys.path.insert(0, _base)

# Run the training
import train
print("Starting training via train.py directly...")
train.train_continuous_gamma(num_epochs=200, batch_size=4)
'''

with open('kaggle_job/kaggle_train_v3.py', 'w', encoding='utf-8') as f:
    f.write(script)
print('Generated kaggle script with embedded zip.')
