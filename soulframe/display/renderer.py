"""Renderer — handles all OpenGL rendering via pyglet for the display engine."""

import logging
import os
import time

import pyglet
from pyglet import gl
from pyglet.graphics.shader import Shader, ShaderProgram

logger = logging.getLogger(__name__)

# Path to the shaders directory
_SHADER_DIR = os.path.join(os.path.dirname(__file__), "shaders")


def _load_shader_source(filename: str) -> str:
    """Load shader source code from the shaders directory."""
    path = os.path.join(_SHADER_DIR, filename)
    with open(path, "r") as f:
        return f.read()


# Fullscreen quad vertices: position (x, y) and texcoord (u, v)
# Two triangles covering the entire clip space (-1 to 1)
_QUAD_VERTICES = [
    # x,    y,    u,   v
    -1.0, -1.0,  0.0, 0.0,
    1.0, -1.0,  1.0, 0.0,
    1.0,  1.0,  1.0, 1.0,
    -1.0, -1.0,  0.0, 0.0,
    1.0,  1.0,  1.0, 1.0,
    -1.0,  1.0,  0.0, 1.0,
]


class Renderer:
    """Handles OpenGL rendering: shaders, textures, and the fullscreen quad."""

    def __init__(self, window: pyglet.window.Window):
        """Initialize the renderer.

        Args:
            window: The pyglet window to render into.
        """
        self._window = window
        self._time_start = time.monotonic()
        self._elapsed_time = 0.0

        # Crossfade state
        self._crossfade_progress = 1.0  # 1.0 = fully showing current texture
        self._crossfade_duration = 0.0
        self._crossfading = False

        # Compile shaders and create the program
        self._program = self._compile_shaders()

        # Create the fullscreen quad geometry
        self._batch = pyglet.graphics.Batch()
        self._vertex_list = self._program.vertex_list(
            6,
            gl.GL_TRIANGLES,
            batch=self._batch,
            position=("f", [v for i, v in enumerate(_QUAD_VERTICES) if i % 4 < 2]),
            texcoord=("f", [v for i, v in enumerate(_QUAD_VERTICES) if i % 4 >= 2]),
        )

        # Texture slots
        self._texture_current = None
        self._texture_prev = None

        # Create a 1x1 white fallback texture
        self._fallback_texture = self._create_fallback_texture()

        logger.info("Renderer initialized")

    def _compile_shaders(self) -> ShaderProgram:
        """Compile vertex and composite fragment shaders into a program."""
        vertex_src = _load_shader_source("vertex.glsl")
        fragment_src = _load_shader_source("composite.glsl")

        vertex_shader = Shader(vertex_src, "vertex")
        fragment_shader = Shader(fragment_src, "fragment")

        program = ShaderProgram(vertex_shader, fragment_shader)
        logger.info("Shader program compiled and linked")
        return program

    def _create_fallback_texture(self):
        """Create a 1x1 white texture as a fallback."""
        tex = pyglet.image.ImageData(1, 1, "RGBA", bytes([255, 255, 255, 255])).get_texture()
        return tex

    def _load_texture_from_path(self, image_path: str):
        """Load an image file into a pyglet texture.

        Args:
            image_path: Path to the image file.

        Returns:
            A pyglet texture object, or None on failure.
        """
        try:
            image = pyglet.image.load(image_path)
            texture = image.get_texture()
            # Enable linear filtering for smooth scaling
            gl.glBindTexture(gl.GL_TEXTURE_2D, texture.id)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
            gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
            logger.info("Loaded texture from: %s (%dx%d)", image_path, image.width, image.height)
            return texture
        except Exception:
            logger.exception("Failed to load image: %s", image_path)
            return None

    def load_image(self, image_path: str):
        """Load an image into the current texture. The previous current becomes the prev texture.

        Args:
            image_path: Path to the image file to load.
        """
        texture = self._load_texture_from_path(image_path)
        if texture is None:
            return

        # Shift current to previous for potential crossfade
        self._texture_prev = self._texture_current
        self._texture_current = texture
        # No crossfade — immediate switch
        self._crossfade_progress = 1.0
        self._crossfading = False
        logger.info("Image loaded (immediate): %s", image_path)

    def crossfade_to(self, image_path: str, duration_ms: float):
        """Start a crossfade transition to a new image.

        Args:
            image_path: Path to the new image file.
            duration_ms: Crossfade duration in milliseconds.
        """
        texture = self._load_texture_from_path(image_path)
        if texture is None:
            return

        # Shift current to previous
        self._texture_prev = self._texture_current
        self._texture_current = texture

        # Start crossfade
        self._crossfade_duration = duration_ms / 1000.0  # convert to seconds
        self._crossfade_progress = 0.0
        self._crossfading = True
        logger.info(
            "Crossfade started to: %s (duration: %.1f ms)", image_path, duration_ms
        )

    def render(self, effect_uniforms: dict, gaze_x: float, gaze_y: float, dt: float):
        """Render a single frame.

        Args:
            effect_uniforms: Dict of shader uniform values from EffectManager.
            gaze_x: Normalized gaze X position (0-1).
            gaze_y: Normalized gaze Y position (0-1).
            dt: Delta time in seconds since last frame.
        """
        # Update time
        self._elapsed_time = time.monotonic() - self._time_start

        # Update crossfade
        if self._crossfading:
            if self._crossfade_duration > 0:
                self._crossfade_progress += dt / self._crossfade_duration
            else:
                self._crossfade_progress = 1.0
            if self._crossfade_progress >= 1.0:
                self._crossfade_progress = 1.0
                self._crossfading = False
                logger.debug("Crossfade complete")

        # Clear the framebuffer
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        # Bind the shader program
        self._program.use()

        try:
            # Bind textures
            tex_current = self._texture_current or self._fallback_texture
            tex_prev = self._texture_prev or self._fallback_texture

            # Bind current texture to unit 0
            gl.glActiveTexture(gl.GL_TEXTURE0)
            gl.glBindTexture(gl.GL_TEXTURE_2D, tex_current.id)

            # Bind previous texture to unit 1
            gl.glActiveTexture(gl.GL_TEXTURE1)
            gl.glBindTexture(gl.GL_TEXTURE_2D, tex_prev.id)

            # Set texture sampler uniforms
            self._set_uniform("u_texture", 0)
            self._set_uniform("u_texture_prev", 1)

            # Set global uniforms
            self._set_uniform("u_time", self._elapsed_time)
            self._set_uniform("u_gaze_pos", (gaze_x, gaze_y))
            self._set_uniform("u_crossfade", self._crossfade_progress)

            # Set effect uniforms
            for name, value in effect_uniforms.items():
                self._set_uniform(name, value)

            # Draw the fullscreen quad
            self._batch.draw()

        finally:
            self._program.stop()
            gl.glActiveTexture(gl.GL_TEXTURE0)

    def _set_uniform(self, name: str, value):
        """Safely set a shader uniform, logging a warning if it doesn't exist.

        Args:
            name: The uniform name.
            value: The value to set (float, int, tuple, etc.).
        """
        try:
            self._program[name] = value
        except KeyError:
            # Uniform not found in shader — may be optimized out
            pass
        except Exception:
            logger.debug("Could not set uniform '%s'", name, exc_info=True)
