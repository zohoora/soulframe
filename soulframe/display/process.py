"""Display process — runs the pyglet window and rendering loop in a child process."""

import logging
import queue
import time

import pyglet
from pyglet import gl

from soulframe import config
from soulframe.shared.types import Command, CommandType
from soulframe.display.effects import EffectManager
from soulframe.display.renderer import Renderer

logger = logging.getLogger(__name__)


def run_display_process(cmd_queue):
    """Main entry point for the display child process."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Display process starting")

    display = pyglet.canvas.get_display()
    screens = display.get_screens()
    screen = screens[0]
    logger.info("Using screen: %dx%d", screen.width, screen.height)

    window = pyglet.window.Window(
        width=config.DISPLAY_WIDTH,
        height=config.DISPLAY_HEIGHT,
        fullscreen=True,
        screen=screen,
        vsync=True,
        caption="Soul Frame",
    )
    window.set_mouse_visible(False)
    gl.glClearColor(0.0, 0.0, 0.0, 1.0)

    renderer = Renderer(window)
    effect_manager = EffectManager()

    gaze_x = 0.5
    gaze_y = 0.5
    should_exit = False

    @window.event
    def on_draw():
        window.clear()

    def update(dt):
        nonlocal gaze_x, gaze_y, should_exit

        # Drain pending commands
        while True:
            try:
                cmd = cmd_queue.get_nowait()
            except queue.Empty:
                break

            if not isinstance(cmd, Command):
                logger.warning("Received non-Command object: %s", type(cmd))
                continue

            _handle_command(cmd, renderer, effect_manager)

            if cmd.cmd_type == CommandType.SHUTDOWN:
                should_exit = True
                pyglet.app.exit()
                return

        effect_manager.update(dt)
        uniforms = effect_manager.get_uniforms()
        renderer.render(uniforms, gaze_x, gaze_y, dt)

    def _handle_command(cmd, rend, effects):
        nonlocal gaze_x, gaze_y
        p = cmd.params or {}

        logger.debug("Handling command: %s", cmd.cmd_type)

        if cmd.cmd_type == CommandType.LOAD_IMAGE:
            rend.load_image(p.get("path", ""))

        elif cmd.cmd_type == CommandType.CROSSFADE_IMAGE:
            rend.crossfade_to(p.get("path", ""), p.get("duration_ms", 2000.0))

        elif cmd.cmd_type == CommandType.SET_EFFECT:
            effect_type = p.get("effect_type", "")
            effects.set_effect(effect_type, p)

        elif cmd.cmd_type == CommandType.SET_EFFECT_INTENSITY:
            effects.set_intensity(p.get("effect_type", ""), p.get("intensity", 0.0))

        elif cmd.cmd_type == CommandType.SET_VIGNETTE:
            effects.set_effect("vignette", {
                "intensity": p.get("intensity", 0.5),
                "softness": p.get("softness", 0.45),
                "radius": p.get("radius", 0.75),
            })

        elif cmd.cmd_type == CommandType.SET_PARALLAX:
            gaze_x = p.get("gaze_x", gaze_x)
            gaze_y = p.get("gaze_y", gaze_y)
            params = {}
            if "intensity" in p:
                params["intensity"] = p["intensity"]
            if "depth_scale" in p:
                params["depth_scale"] = p["depth_scale"]
            if params:
                effects.set_effect("parallax", params)

        elif cmd.cmd_type == CommandType.SHUTDOWN:
            logger.info("Shutdown command received")

        else:
            logger.warning("Unknown command type: %s", cmd.cmd_type)

    pyglet.clock.schedule_interval(update, 1.0 / config.DISPLAY_FPS)

    logger.info(
        "Display process running at %d FPS (%dx%d)",
        config.DISPLAY_FPS, config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT,
    )

    try:
        pyglet.app.run()
    except Exception:
        logger.exception("Display process encountered an error")
    finally:
        window.close()
        logger.info("Display process shut down")
