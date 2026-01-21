import numpy as np
import os
import json

path = "cache/tiles/ne/0.100deg/hrrr/run_20260121_03/t2m.npz"
meta_path = "cache/tiles/ne/0.100deg/hrrr/run_20260121_03/t2m.meta.json"

if not os.path.exists(path):
    print(f"File not found: {path}")
    exit(1)

data = np.load(path)
print("Keys:", data.files)
means = data["means"]
print(f"Shape: {means.shape}")
print(f"Global Min: {np.nanmin(means)}")
print(f"Global Max: {np.nanmax(means)}")
print(f"Global Mean: {np.nanmean(means)}")

# Philly index from previous diagnostic
ix = 45
iy = 20

print(f"Value at ix={ix}, iy={iy} (all hours):")
print(means[:, iy, ix])

with open(meta_path) as f:
    print("Meta:", f.read())
