"""Uploads models to a Holo-CAD relay, off the GUI thread.

The local server inside FreeCAD is instant, because the lens fetches the
bytes itself and nothing large passes through the push. A relay is the
opposite: the GLB goes up over the internet, which can take seconds on a
slow connection and would freeze FreeCAD if it happened inline.

So uploads go to a worker thread, and the queue holds at most one payload
per model. In live mode a recompute can easily outpace an upload, and
sending every intermediate version of a bracket nobody will ever see is
worse than useless: it delays the one that matters. The newest wins.

Standard library only, like everything else that runs inside FreeCAD.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

TIMEOUT = 120.0
# The relay refuses pushes that arrive faster than its own limit, so pace
# to just outside it rather than relying on being told off.
MIN_INTERVAL = 0.35
RETRY_PAUSE = 0.5
MAX_RETRIES = 6


class RelayClient:
    def __init__(self, log=None, warn=None):
        self._lock = threading.Condition()
        self._pending = {}       # model id -> (kind, payload)
        self._order = []         # model ids, oldest first
        self._thread = None
        self._stop = False
        self._base = ""
        self._code = ""
        self._log = log or (lambda message: None)
        self._warn = warn or (lambda message: None)
        self._retries = {}
        self._last_sent = 0.0
        self.last_error = ""

    # ---- configuration ----

    def configure(self, base_url: str, code: str) -> None:
        with self._lock:
            self._base = (base_url or "").rstrip("/")
            self._code = (code or "").strip().upper()

    @property
    def configured(self) -> bool:
        return bool(self._base and self._code)

    # ---- queueing ----

    def push(self, model_id: str, glb: bytes, metadata: dict) -> None:
        self._enqueue(model_id, ("push", glb, metadata))

    def remove(self, model_id: str) -> None:
        self._enqueue(model_id, ("remove", None, None))

    def _enqueue(self, model_id: str, item) -> None:
        if not self.configured:
            return
        with self._lock:
            if model_id not in self._pending:
                self._order.append(model_id)
            # Replacing rather than appending is the coalescing: only the
            # newest state of each model is worth uploading.
            self._pending[model_id] = item
            self._ensure_worker()
            self._lock.notify()

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._run, name="HoloCAD relay upload", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop = True
            self._pending.clear()
            self._order = []
            self._lock.notify_all()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._order)

    # ---- the worker ----

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._order and not self._stop:
                    self._lock.wait(timeout=30)
                    if not self._order:
                        # Nothing arrived, so let the thread go rather than
                        # keeping one alive for a session that is done.
                        self._thread = None
                        return
                if self._stop:
                    self._thread = None
                    return
                model_id = self._order.pop(0)
                kind, glb, metadata = self._pending.pop(model_id)
                base, code = self._base, self._code

            # Pace outside the relay's limit. Live mode can queue a burst,
            # and being refused for going too fast would mean dropping the
            # final state of a model, which is the one that matters.
            gap = time.time() - self._last_sent
            if gap < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - gap)

            try:
                if kind == "push":
                    self._do_push(base, code, glb, metadata)
                else:
                    self._do_remove(base, code, model_id)
                self.last_error = ""
                self._retries.pop(model_id, None)
                self._last_sent = time.time()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:200]
                self.last_error = "{0} {1}".format(e.code, body)
                self._last_sent = time.time()
                if e.code == 429:
                    if self._requeue(model_id, (kind, glb, metadata)):
                        time.sleep(RETRY_PAUSE)
                        continue
                    self._warn(
                        "the relay kept refusing {0} as too fast, giving "
                        "up on it".format(model_id))
                elif e.code == 404:
                    self._warn(
                        "the relay has no lens with code {0}. Check the code "
                        "shown on the glasses.".format(code))
                else:
                    self._warn("relay refused {0}: {1}".format(
                        model_id, self.last_error))
            except urllib.error.URLError as e:
                self.last_error = str(e.reason)
                self._warn("could not reach the relay at {0}: {1}".format(
                    base, e.reason))
            except Exception as e:
                self.last_error = str(e)
                self._warn("relay upload failed: {0}".format(e))

    def _requeue(self, model_id, item) -> bool:
        """Put a refused item back, unless something newer replaced it.

        Returns False once an item has been retried too often, so a relay
        that is permanently unhappy cannot spin this thread forever.
        """
        attempts = self._retries.get(model_id, 0) + 1
        if attempts > MAX_RETRIES:
            self._retries.pop(model_id, None)
            return False
        self._retries[model_id] = attempts
        with self._lock:
            if model_id in self._pending:
                # A newer version arrived while this one was in flight, so
                # the refused payload is already obsolete.
                return True
            self._pending[model_id] = item
            self._order.insert(0, model_id)
            self._lock.notify()
        return True

    def _do_push(self, base, code, glb, metadata) -> None:
        request = urllib.request.Request(
            "{0}/push/{1}".format(base, code),
            data=glb,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Holocad-Meta": json.dumps(metadata),
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
            result = json.loads(r.read().decode("utf-8"))
        self._log("relay took {0} v{1}, {2} lens(es) saw it".format(
            result.get("id"), result.get("version"), result.get("lenses")))

    def _do_remove(self, base, code, model_id) -> None:
        request = urllib.request.Request(
            "{0}/remove/{1}/{2}".format(base, code, model_id),
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
            r.read()
        self._log("relay dropped {0}".format(model_id))
