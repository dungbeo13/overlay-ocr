"""Python's cyclic garbage collector, run only on the GUI thread.

CPython starts a collection in whichever thread happens to allocate when generation 0 fills up, and that thread
then frees every unreachable cycle, whatever it holds. When it is a worker thread (OCR, translation, the game
lookup) and the garbage holds Python-owned Qt objects that are still alive (closed dialogs in a cycle with their
slots), their C++ objects are destroyed outside the GUI thread and the process crashes: reproduced every time with
a few closed QDialogs and a worker parsing JSON. tests.test_lookup_ui crashed natively in 2 of 25 runs, an access
violation in python312.dll with "Garbage-collecting" in the lookup worker's faulthandler stack (inside json.loads)
while the GUI thread pumped events; which object that collection was freeing is not known. Either way: no cyclic
collection off the GUI thread.

install() turns automatic collection off and lets a GUI-thread timer collect instead. Generation 0 goes on every
tick that has new objects, so a cycle that dies young waits at most one tick; the older generations follow
CPython's thresholds, so a cycle whose objects were already promoted waits for its generation, as without this
module. Reference counting still frees everything that is not in a cycle at once, in the thread that drops the
last reference, as before. Nothing is collected while the GUI thread is blocked, and automatic gc stays off for
the rest of the process (in a test run too: test modules that never run a Qt event loop collect nothing).
"""
from __future__ import annotations

import gc
import logging

import shiboken6
from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer

log = logging.getLogger(__name__)

INTERVAL_MS = 500

_installed: GuiThreadGC | None = None


class GuiThreadGC(QObject):
    """Owns the timer; gc stays disabled for the life of the process once this exists."""

    def __init__(self, parent: QObject, interval_ms: int = INTERVAL_MS) -> None:
        super().__init__(parent)
        self.threshold = gc.get_threshold()
        self.collections = [0, 0, 0]  # per generation, for tests and the debug log
        gc.disable()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)
        self._timer.start(interval_ms)

    def check(self) -> int | None:
        """Collect what the last interval left; the generation collected, None when nothing new was allocated.

        Generation 0 whenever it holds anything, not only over its threshold: CPython reacts to the count's peaks
        as they happen, a sample every tick does not see them (a worker that churns temporaries keeps the net
        count low, and the cycles it leaves would wait for hundreds more). The older ones as CPython does: the
        oldest generation over its threshold (younger collections since its last one) goes with the younger ones.
        """
        counts = gc.get_count()
        if not counts[0]:
            return None
        gen = 0
        for i in (2, 1):
            if counts[i] > self.threshold[i]:
                gen = i
                break
        gc.collect(gen)
        self.collections[gen] += 1
        return gen


def install(app: QCoreApplication | None = None, interval_ms: int = INTERVAL_MS) -> GuiThreadGC:
    """Idempotent; call from the GUI thread once the QApplication exists."""
    global _installed
    app = app or QCoreApplication.instance()
    if app is None:
        raise RuntimeError("qt_gc.install() needs a QApplication")
    if QThread.currentThread() != app.thread():
        raise RuntimeError("qt_gc.install() must run on the GUI thread")
    if _installed is None or not shiboken6.isValid(_installed):  # gone with an earlier QApplication: a new one
        _installed = GuiThreadGC(app, interval_ms)
        log.debug("automatic gc off; collecting on the GUI thread every %d ms (thresholds %s)",
                  interval_ms, _installed.threshold)
    return _installed
