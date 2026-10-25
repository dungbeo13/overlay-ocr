"""Tests for app.qt_gc: cyclic garbage is collected on the GUI thread only, never in a worker thread.

Run: python -m unittest tests.test_qt_gc -v
"""
from __future__ import annotations

import gc
import threading
import unittest
from unittest import mock

import shiboken6

from app import qt_gc
from tests.test_app import pump_until, qapp


class _Cycle:
    """A reference cycle whose finalizer records the thread that collected it."""

    def __init__(self, seen: list) -> None:
        self.me = self
        self.seen = seen

    def __del__(self) -> None:
        self.seen.append(threading.current_thread())


class GuiThreadGCTests(unittest.TestCase):
    def setUp(self):
        self.app = qapp()
        self.gc = qt_gc.install(self.app)

    def test_installed_once_and_automatic_gc_off(self):
        self.assertIs(qt_gc.install(self.app), self.gc)
        self.assertFalse(gc.isenabled())
        self.assertEqual(len(self.gc.threshold), 3)

    def test_worker_garbage_is_collected_on_the_gui_thread(self):
        seen: list[threading.Thread] = []

        def worker() -> None:
            for _ in range(20):  # like the OCR / lookup loops: a burst of temporaries, one cycle left behind
                junk = [[] for _ in range(5_000)]  # would start automatic collections in this thread
                _Cycle(seen)
                del junk  # ... and its frees keep the net gen-0 count far under the threshold

        gc.collect()
        before = sum(self.gc.collections)
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(seen, [])  # the worker never collected
        self.assertTrue(pump_until(lambda: len(seen) == 20, qt_gc.INTERVAL_MS * 4 / 1000), len(seen))  # the timer
        self.assertEqual(set(seen), {threading.main_thread()})
        self.assertGreater(sum(self.gc.collections), before)

    def test_check_collects_new_objects_every_tick_and_older_generations_by_threshold(self):
        t0, t1, t2 = self.gc.threshold
        cases = [((0, t1 + 1, t2 + 1), None),  # nothing allocated since the last collection
                 ((1, 0, 0), 0),  # one new object is enough: no waiting for t0 of them
                 ((t0 + 1, t1, t2), 0),
                 ((1, t1 + 1, t2), 1),
                 ((1, t1 + 1, t2 + 1), 2),
                 ((1, 0, t2 + 1), 2)]  # as CPython: the oldest generation over its threshold
        for counts, want in cases:
            with self.subTest(counts=counts), mock.patch.object(qt_gc.gc, "get_count", return_value=counts), \
                    mock.patch.object(qt_gc.gc, "collect") as collect:
                self.assertEqual(self.gc.check(), want)
                self.assertEqual(collect.call_args_list, [] if want is None else [mock.call(want)])

    def test_replaced_when_its_qapplication_is_gone(self):
        shiboken6.delete(self.gc)  # what destroying the QApplication does to its child
        new = qt_gc.install(self.app)
        self.assertIsNot(new, self.gc)
        self.assertTrue(shiboken6.isValid(new))
        self.assertIs(qt_gc.install(self.app), new)
        self.assertFalse(gc.isenabled())

    def test_install_needs_the_gui_thread(self):
        errors: list[Exception] = []

        def worker() -> None:
            try:
                qt_gc.install(self.app)
            except RuntimeError as exc:
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
