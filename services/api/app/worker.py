"""Optional standalone worker: disable the API's embedded worker when using this."""
import signal
from .db import Store
from .harness import Harness

if __name__ == "__main__":
    worker = Harness(Store())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stop_event.set())
    worker.loop()
