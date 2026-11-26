"""Check the inward gray boundary without resizing or changing inner holes."""
from pathlib import Path
import sys
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pack_assets import gray_background_edge, BG_EDGE_COLOR

a = np.full((12, 16, 4), (80, 115, 65, 255), dtype=np.uint8)
a[0, :, :3] = a[-1, :, :3] = 255
a[:, 0, :3] = a[:, -1, :3] = 255
a[5, 6] = (0, 0, 0, 0)
a[6, 8] = (255, 255, 255, 255)
image, edge = gray_background_edge(Image.fromarray(a))
out = np.array(image)
assert image.size == (16, 12)
assert edge.sum() == 2 * 16 + 2 * 12 - 4
assert np.all(out[edge, :3] == BG_EDGE_COLOR)
assert np.array_equal(out[:, :, 3], a[:, :, 3])
assert not edge[4:7, 5:8].any(), 'Enclosed transparency gained an outline'
assert tuple(out[6, 8]) == (255, 255, 255, 255), 'Interior highlight was changed'
assert len(np.unique(out[out[:, :, 3] > 0, :3], axis=0)) <= 16
print('PASS 1px gray exterior edge, unchanged dimensions/alpha, inner holes and white highlight preserved, <=16 colors')
