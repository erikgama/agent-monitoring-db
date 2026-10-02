"""Short-lived watchdog: child tree cleanup even if the runner dies."""

import asyncio
import sys
import time

import psutil

from .runner import ProcessTree


def main() -> None:
    try:
        parent = psutil.Process(int(sys.argv[1]))
        tree = ProcessTree(int(sys.argv[2]))
    except psutil.NoSuchProcess:
        return
    while True:
        tree.capture()
        if not tree.alive():
            return
        try:
            parent_alive = (
                parent.is_running() and parent.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.NoSuchProcess:
            parent_alive = False
        if not parent_alive:
            asyncio.run(tree.stop())
            return
        time.sleep(0.2)


if __name__ == "__main__":
    main()
