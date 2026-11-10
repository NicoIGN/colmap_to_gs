import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from tools.postprocess import export_ply as export
from tools.postprocess.merge_splats import SPLAT_DTYPE


class BlockWorldFrameTest(unittest.TestCase):
    def test_independently_normalized_blocks_export_to_same_world_frame(self):
        world = np.array([[0.001, -0.003, 0.002], [1, 2, 3], [-4, 5, 6]])
        world_scales = np.array([[0.01, 0.02, 0.03]] * 3)
        world_rotation = Rotation.from_euler("xyz", [0.2, -0.5, 0.7])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = []
            for index, scale in enumerate((0.001, 0.5, 2, 1000)):
                rotation = Rotation.from_euler("xyz", [0.3 * index, 0.4, -0.2])
                matrix = np.eye(4)
                matrix[:3, :3] = scale * rotation.as_matrix()
                matrix[:3, 3] = [index * 0.01, -0.2, 0.3]
                transform = root / f"transform{index}.json"
                transform.write_text(json.dumps({
                    "format": "spirula-scene-transform", "version": 1,
                    "train_from_world": {"matrix_4x4": matrix.tolist()}}))
                train_rotation = (rotation * world_rotation).as_quat()[[3, 0, 1, 2]]
                splats = {
                    "means": export.apply_transform(world, matrix),
                    "scales": np.log(world_scales * scale),
                    "quats": np.tile(train_rotation, (3, 1)),
                    "opacity": np.zeros(3), "dc": np.zeros((3, 3)),
                }
                output = root / f"block{index}.splat"
                with contextlib.redirect_stdout(io.StringIO()):
                    loaded, r, s = export.load_transform(transform)
                    export.write_world_splat(splats, output, loaded, r, s, 2)
                result = np.fromfile(output, dtype=SPLAT_DTYPE)
                np.testing.assert_allclose(result["position"], world, atol=1e-7)
                np.testing.assert_allclose(result["scale"], world_scales, rtol=1e-6)
                decoded = (result["rotation"].astype(float) - 128) / 128
                decoded /= np.linalg.norm(decoded, axis=1, keepdims=True)
                recovered = Rotation.from_quat(decoded[:, [1, 2, 3, 0]])
                self.assertLess(float(np.max((recovered.inv() * world_rotation).magnitude())), 0.02)
                results.append(result)
            for result in results[1:]:
                np.testing.assert_allclose(result["position"], results[0]["position"], atol=1e-7)
                np.testing.assert_allclose(result["scale"], results[0]["scale"], rtol=1e-6)


if __name__ == "__main__":
    unittest.main()
