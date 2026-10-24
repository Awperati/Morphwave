"""Morphwave's shaders compile and link (an offscreen OpenGL 3.3 context), and Cube Cascade keeps its fixes.

Run: python -m unittest discover -s tests
"""

import unittest

import moderngl

from morphwave import shaders


class ShadersCompile(unittest.TestCase):
    def test_scene_and_present_link(self):
        ctx = moderngl.create_standalone_context()
        try:
            ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.SCENE)
            ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.PRESENT)
        finally:
            ctx.release()


class CubeCascade(unittest.TestCase):
    """The owner: "Theres lots of artifacting on the Cubes scene"."""

    def cube_source(self):
        src = shaders.SCENE
        return src[src.index("float sdRBox"):src.index("vec3 cubeCascade")]

    def test_steps_never_pass_a_neighbouring_tile(self):
        # a step inside the tiles' layer is bounded by the cell border plus the gap round a neighbour's tile
        self.assertIn("leave + 0.5 * cell - szMax", self.cube_source())

    def test_cubes_across_a_face_seam_are_whole(self):
        # near a seam the next face's tile is checked too (judged from one face, it came out as a pyramid)
        self.assertIn("faceTile(p, ap, f2, R, cell, shellId)", self.cube_source())

    def test_inner_shells_shrink_instead_of_being_cut(self):
        self.assertNotIn("0.6 - m", self.cube_source())
        self.assertIn("smoothstep(0.3, 0.75, R)", self.cube_source())

    def test_no_over_relaxed_steps(self):
        src = shaders.SCENE
        march = src[src.index("vec3 cubeCascade"):src.index("// ---------------- All-Seeing Spiral")]
        self.assertNotIn("1.15", march)


if __name__ == "__main__":
    unittest.main()
