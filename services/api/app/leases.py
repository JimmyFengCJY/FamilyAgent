"""Per-attempt heartbeat; bounded renewal is separate from result fencing."""
import logging
import sqlite3
import threading
import time


class LeaseHeartbeat:
    def __init__(self, store, run, worker_stop, interval, duration):
        self.store, self.run = store, run
        self.worker_stop = worker_stop
        self.interval, self.duration = interval, duration
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True,
                                       name='lease-' + run['attempt_id'])
        # Wall time is persisted for other processes; monotonic time bounds this thread.
        self.monotonic_deadline = time.monotonic() + max(0, run['lease_deadline'] - time.time())

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.finished.set()
        self.thread.join(timeout=1)

    def loop(self):
        while not self.finished.wait(self.interval):
            if self.worker_stop.is_set() or time.monotonic() >= self.monotonic_deadline:
                return
            try:
                if not self.store.renew_lease(self.run['id'], self.run['worker'], self.run['attempt_id'],
                                             self.run['lease_deadline'], self.duration):
                    return
            except sqlite3.OperationalError as exc:
                if getattr(exc, 'sqlite_errorcode', None) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    logging.getLogger('familyagent').warning('Lease heartbeat stopped after database failure')
                    return
                # A busy writer is retried on the next interval; expiry still wins.
            except Exception:
                logging.getLogger('familyagent').warning('Lease heartbeat stopped after infrastructure failure')
                return
