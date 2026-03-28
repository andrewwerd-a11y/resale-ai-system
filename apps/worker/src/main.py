"""
Background worker — optional component for async processing.
Currently a stub; analysis is driven by scripts/analyze_all.py.
"""
import time
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


def main() -> None:
    log.info("Worker started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Worker stopped.")


if __name__ == "__main__":
    main()
