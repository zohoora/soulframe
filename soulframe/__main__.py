"""Soul Frame — entry point.

Usage:
    python -m soulframe              # Run the installation
    python -m soulframe --authoring  # Run the authoring tool
    python -m soulframe --vision     # Run vision process only (debug)
    python -m soulframe --display    # Run display process only (debug)
    python -m soulframe --audio      # Run audio process only (debug)
"""

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="soulframe",
        description="Soul Frame — interactive art installation",
    )
    parser.add_argument(
        "--authoring", action="store_true", help="Launch the web authoring tool"
    )
    parser.add_argument(
        "--vision", action="store_true", help="Run vision process only (debug)"
    )
    parser.add_argument(
        "--display", action="store_true", help="Run display process only (debug)"
    )
    parser.add_argument(
        "--audio", action="store_true", help="Run audio process only (debug)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("soulframe")

    if args.authoring:
        logger.info("Starting Soul Frame authoring tool...")
        from authoring.backend.app import main as run_authoring

        run_authoring()
        return

    if args.vision:
        from multiprocessing import Queue

        from soulframe.vision.process import run_vision_process

        logger.info("Starting vision process (debug mode)...")
        q = Queue()
        run_vision_process(q)
        return

    if args.display:
        from multiprocessing import Queue

        from soulframe.display.process import run_display_process

        logger.info("Starting display process (debug mode)...")
        q = Queue()
        run_display_process(q)
        return

    if args.audio:
        from multiprocessing import Queue

        from soulframe.audio.process import run_audio_process

        logger.info("Starting audio process (debug mode)...")
        q = Queue()
        run_audio_process(q)
        return

    # Default: run the full installation
    logger.info("Starting Soul Frame...")
    from soulframe.brain.coordinator import start

    start()


if __name__ == "__main__":
    main()
