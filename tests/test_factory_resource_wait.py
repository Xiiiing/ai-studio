"""CPU-only tests for bounded GPU resource waits and lease retry boundaries.

All GPU probes, leases, workers and subprocess creation are replaced by fixtures.
The real Budget shares the virtual monotonic clock with Scheduler.
"""
from __future__ import annotations

import contextlib
import copy
import subprocess
import unittest
from unittest import mock

from factory.scheduler import (
    Budget, ProductionBudgetExceeded, ProductionPaused, Scheduler, StartupLock, gpu_snapshot,
)


def card(free=15000, utilization=0):
    return {"free_mib": free, "utilization": utilization}


BUSY = {0: card(14999, 0), 1: card(30000, 10), 7: card(40000, 0)}
FREE = {0: card(15000, 9), 1: card(30000, 10), 7: card(40000, 0)}


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        if seconds < 0:
            raise AssertionError("Clock cannot move backwards")
        self.now += seconds
        if self.now > 10000:
            raise AssertionError("Resource wait escaped its bounded deadline")

    def sleep(self, seconds):
        if not 0 < seconds <= 1:
            raise AssertionError("Waits must remain cancellable at <= 1 second")
        self.sleeps.append(seconds)
        self.advance(seconds)


class ResourceWaitTests(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.events = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch("factory.scheduler.time.monotonic",
                                            self.clock.monotonic))
        self.stack.enter_context(mock.patch("factory.scheduler.time.sleep",
                                            self.clock.sleep))
        for name in ("subprocess.Popen", "subprocess.run", "subprocess.check_output"):
            self.stack.enter_context(mock.patch(
                name, side_effect=AssertionError("CPU test must not start a subprocess")))
        self.probe = self.stack.enter_context(mock.patch("factory.scheduler.gpu_snapshot"))
        self.probe.side_effect = lambda cards=None, **kwargs: copy.deepcopy(FREE)
        self.audio = self.stack.enter_context(mock.patch(
            "factory.scheduler.GpuLease",
            side_effect=AssertionError("Test must explicitly provide its lease fixture")))
        self.worker = self.stack.enter_context(mock.patch(
            "factory.scheduler.FactoryComfyWorker",
            side_effect=AssertionError("Test must explicitly provide its worker fixture")))

    def make_scheduler(self, previous=None, **overrides):
        config = {
            "gpu_cards": [0, 1], "image_min_free_mib": 15000,
            "audio_min_free_mib": 15000,
            "resource_wait_seconds": 180, "resource_poll_seconds": 10,
            "resource_probe_timeout_seconds": 60,
            "max_wall_seconds": 1000, "max_gpu_seconds": 1000,
        }
        config.update(overrides)
        budget = Budget(config, previous)
        scheduler = Scheduler(config, budget,
                              event_callback=lambda event: self.events.append(copy.deepcopy(event)))
        return scheduler, budget

    def last_event(self, status):
        self.assertTrue(self.events)
        event = self.events[-1]
        self.assertEqual(event["status"], status)
        required = {"reason", "waited_seconds", "max_wait_seconds", "probe_count",
                    "minimum_free_mib", "snapshot", "stage"}
        self.assertTrue(required <= event.keys(), event)
        return event

    def slow_probe(self, result, seconds=30):
        def invoke(cards=None, *, timeout=60, check=None, heartbeat=None, **kwargs):
            self.assertTrue(callable(check))
            self.assertTrue(callable(heartbeat))
            for _ in range(seconds):
                self.clock.advance(1)
                check()
                heartbeat()
            return copy.deepcopy(result)
        return invoke

    def test_busy_then_free_keeps_memory_utilization_and_authorized_card_gates(self):
        snapshots = iter([BUSY, FREE])
        self.probe.side_effect = lambda cards=None, **kwargs: copy.deepcopy(next(snapshots))
        scheduler, budget = self.make_scheduler()
        self.assertEqual(scheduler.choose_card(), 0)
        self.assertEqual(self.probe.call_count, 2)
        self.assertGreaterEqual(self.clock.now, 10)
        self.assertLessEqual(self.clock.now, 11)
        self.assertTrue(self.clock.sleeps)
        self.assertTrue(any(e["status"] == "waiting" for e in self.events))
        event = self.last_event("ready")
        self.assertEqual(event["minimum_free_mib"], 15000)
        self.assertEqual(event["probe_count"], 2)
        self.assertEqual(budget.snapshot()["gpu_seconds"], 0)
        for call in self.probe.call_args_list:
            self.assertEqual(list(call.args[0]), [0, 1])

    def test_persistent_busy_stops_at_180_seconds_without_fast_polling(self):
        self.probe.side_effect = lambda cards=None, **kwargs: copy.deepcopy(BUSY)
        scheduler, budget = self.make_scheduler()
        with self.assertRaises(ProductionPaused) as caught:
            scheduler.choose_card()
        self.assertEqual(caught.exception.reason, "resource_timeout")
        self.assertAlmostEqual(self.clock.now, 180, delta=1)
        self.assertLessEqual(self.probe.call_count, 20)
        self.last_event("timed_out")
        self.assertEqual(budget.snapshot()["gpu_seconds"], 0)

    def test_accumulated_wall_budget_stops_wait_before_resource_deadline(self):
        self.probe.side_effect = lambda cards=None, **kwargs: copy.deepcopy(BUSY)
        scheduler, budget = self.make_scheduler(
            previous={"elapsed_seconds": 20, "gpu_seconds": 12},
            max_wall_seconds=25)
        with self.assertRaises(ProductionBudgetExceeded):
            scheduler.choose_card()
        self.assertAlmostEqual(self.clock.now, 5, delta=1)
        self.last_event("budget_exceeded")
        self.assertGreaterEqual(budget.snapshot()["elapsed_seconds"], 25)
        self.assertEqual(budget.snapshot()["gpu_seconds"], 12)

    def test_cancel_during_wait_never_returns_a_card(self):
        self.probe.side_effect = lambda cards=None, **kwargs: copy.deepcopy(BUSY)
        scheduler, _ = self.make_scheduler()
        with self.assertRaises(ProductionPaused) as caught:
            scheduler.choose_card(control=lambda: "cancel" if self.clock.now >= 3 else None)
        self.assertEqual(caught.exception.reason, "paused")
        self.assertLessEqual(self.clock.now, 4)
        self.assertEqual(self.probe.call_count, 1)
        self.last_event("cancelled")

    def test_cancel_inside_slow_probe_is_checked_before_its_free_result(self):
        self.probe.side_effect = self.slow_probe(FREE)
        scheduler, _ = self.make_scheduler()
        with self.assertRaises(ProductionPaused) as caught:
            scheduler.choose_card(control=lambda: "pause" if self.clock.now >= 3 else None)
        self.assertEqual(caught.exception.reason, "paused")
        self.assertLessEqual(self.clock.now, 4)
        self.assertEqual(self.probe.call_count, 1)
        self.last_event("cancelled")

    def test_slow_probe_cannot_overrun_wall_budget(self):
        self.probe.side_effect = self.slow_probe(FREE)
        scheduler, budget = self.make_scheduler(max_wall_seconds=5)
        with self.assertRaises(ProductionBudgetExceeded):
            scheduler.choose_card()
        self.assertLessEqual(self.clock.now, 6)
        self.last_event("budget_exceeded")
        self.assertEqual(budget.snapshot()["gpu_seconds"], 0)

    def test_slow_probe_cannot_overrun_resource_deadline(self):
        self.probe.side_effect = self.slow_probe(FREE)
        scheduler, _ = self.make_scheduler(resource_wait_seconds=5)
        with self.assertRaises(ProductionPaused) as caught:
            scheduler.choose_card()
        self.assertEqual(caught.exception.reason, "resource_timeout")
        self.assertLessEqual(self.clock.now, 6)
        self.last_event("timed_out")

    def test_slow_probes_count_as_wall_time_and_are_spaced_apart(self):
        snapshots = iter([BUSY, FREE])
        def probe(cards=None, **kwargs):
            return self.slow_probe(next(snapshots))(cards, **kwargs)
        self.probe.side_effect = probe
        scheduler, budget = self.make_scheduler()
        self.assertEqual(scheduler.choose_card(), 0)
        self.assertEqual(self.probe.call_count, 2)
        self.assertGreaterEqual(self.clock.now, 70)
        self.assertLessEqual(self.clock.now, 71)
        self.assertEqual(budget.snapshot()["elapsed_seconds"], self.clock.now)
        self.assertEqual(budget.snapshot()["gpu_seconds"], 0)
        self.last_event("ready")

    def test_probe_failure_retries_without_lowering_the_gate(self):
        snapshots = iter([OSError("NVML query temporarily unavailable"), BUSY, FREE])
        def probe(cards=None, **kwargs):
            result = next(snapshots)
            if isinstance(result, BaseException):
                raise result
            return copy.deepcopy(result)
        self.probe.side_effect = probe
        scheduler, _ = self.make_scheduler()
        self.assertEqual(scheduler.choose_card(), 0)
        self.assertEqual(self.probe.call_count, 3)
        self.assertGreaterEqual(self.clock.now, 20)
        self.assertTrue(all(e["minimum_free_mib"] == 15000 for e in self.events))
        self.last_event("ready")

    def test_exhausted_budget_never_queries_nvml(self):
        scheduler, _ = self.make_scheduler(
            previous={"elapsed_seconds": 25}, max_wall_seconds=25)
        with self.assertRaises(ProductionBudgetExceeded):
            scheduler.choose_card()
        self.probe.assert_not_called()
        self.last_event("budget_exceeded")

    def test_zero_wait_does_not_retry_or_sleep(self):
        self.probe.side_effect = lambda cards=None, **kwargs: copy.deepcopy(BUSY)
        scheduler, _ = self.make_scheduler(resource_wait_seconds=0)
        with self.assertRaises(ProductionPaused) as caught:
            scheduler.choose_card()
        self.assertEqual(caught.exception.reason, "resource_timeout")
        self.assertLessEqual(self.probe.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])
        self.last_event("timed_out")

    def lease_context(self, scheduler, kind):
        return scheduler.audio_lease() if kind == "audio" else scheduler.worker("cpu-fixture")

    def assert_entry_race_recovers(self, kind):
        blocked, ready = mock.MagicMock(), mock.MagicMock()
        blocked.__enter__.side_effect = RuntimeError("GPU 0 is occupied")
        ready.__enter__.return_value = ready
        ready.__exit__.return_value = False
        factory = self.audio if kind == "audio" else self.worker
        factory.side_effect = [blocked, ready]
        scheduler, budget = self.make_scheduler()
        body_calls = 0
        with self.lease_context(scheduler, kind):
            body_calls += 1
            self.clock.advance(2)
        self.assertEqual(body_calls, 1)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(blocked.__enter__.call_count, 1)
        self.assertEqual(ready.__enter__.call_count, 1)
        self.assertLessEqual(self.clock.now, 182)
        self.assertEqual(budget.snapshot()["active_gpu_leases"], 0)
        for call in factory.call_args_list:
            self.assertEqual(len(call.args[0]), 1)
            self.assertIn(call.args[0][0], [0, 1])
            self.assertEqual(call.kwargs["min_free"], 15000)

    def test_audio_entry_race_can_wait_and_recover(self):
        self.assert_entry_race_recovers("audio")

    def test_worker_entry_race_can_wait_and_recover(self):
        self.assert_entry_race_recovers("worker")

    def assert_payload_error_is_not_replayed(self, kind):
        ready = mock.MagicMock()
        ready.__enter__.return_value = ready
        ready.__exit__.return_value = False
        factory = self.audio if kind == "audio" else self.worker
        factory.side_effect = lambda *args, **kwargs: ready
        scheduler, budget = self.make_scheduler()
        error = RuntimeError("GPU occupied after media task started")
        body_calls = 0
        with self.assertRaises(RuntimeError) as caught:
            with self.lease_context(scheduler, kind):
                body_calls += 1
                raise error
        self.assertIs(caught.exception, error)
        self.assertEqual(body_calls, 1)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(ready.__enter__.call_count, 1)
        self.assertEqual(ready.__exit__.call_count, 1)
        self.assertEqual(budget.snapshot()["active_gpu_leases"], 0)

    def test_audio_payload_failure_never_reacquires_or_replays(self):
        self.assert_payload_error_is_not_replayed("audio")

    def test_worker_payload_failure_never_reacquires_or_replays(self):
        self.assert_payload_error_is_not_replayed("worker")

    def test_repeated_entry_race_shares_one_deadline(self):
        blocked = mock.MagicMock()
        blocked.__enter__.side_effect = RuntimeError("GPU 0 is occupied")
        self.audio.side_effect = lambda *args, **kwargs: blocked
        scheduler, budget = self.make_scheduler(
            resource_wait_seconds=3, resource_poll_seconds=1)
        def probe(cards=None, **kwargs):
            if self.probe.call_count > 20:
                raise AssertionError("Lease race reset its resource deadline")
            return copy.deepcopy(FREE)
        self.probe.side_effect = probe
        with self.assertRaises(ProductionPaused) as caught:
            with scheduler.audio_lease():
                self.fail("Busy entry must not start a media task")
        self.assertEqual(caught.exception.reason, "resource_timeout")
        self.assertLessEqual(self.clock.now, 4)
        self.last_event("timed_out")
        self.assertEqual(budget.snapshot()["active_gpu_leases"], 0)


class QueryProcessFixture:
    def __init__(self, clock, mode):
        self.clock, self.mode = clock, mode
        self.returncode = None
        self.terminated = self.killed = False
        self.actions, self.timeouts = [], []

    def communicate(self, timeout):
        self.actions.append("communicate")
        self.timeouts.append(timeout)
        if self.mode == "success":
            self.returncode = 0
            return "0, 15000, 9\n1, 30000, 10\n", ""
        if self.killed:
            self.returncode = -9
            return "", ""
        if self.terminated and self.mode == "cancel":
            self.returncode = -15
            return "", ""
        self.clock.advance(timeout)
        raise subprocess.TimeoutExpired("controlled-nvml-fixture", timeout)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.actions.append("terminate")
        self.terminated = True

    def kill(self):
        self.actions.append("kill")
        self.killed = True


class ResourceInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch("factory.scheduler.time.monotonic",
                                            self.clock.monotonic))
        self.stack.enter_context(mock.patch("factory.scheduler.time.sleep", self.clock.sleep))
        self.popen = self.stack.enter_context(mock.patch(
            "factory.scheduler.subprocess.Popen",
            side_effect=AssertionError("Explicit process fixture required")))
        for name in ("subprocess.run", "subprocess.check_output"):
            self.stack.enter_context(mock.patch(
                name, side_effect=AssertionError("CPU test must not start a subprocess")))

    def process(self, mode):
        process = QueryProcessFixture(self.clock, mode)
        self.popen.side_effect = lambda *args, **kwargs: process
        return process

    def test_query_success_parses_csv_and_reaps_exited_process(self):
        process = self.process("success")
        result = gpu_snapshot([0, 1], timeout=5)
        self.assertEqual(result, {0: card(15000, 9), 1: card(30000, 10)})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.actions, ["communicate"])
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args.args[0][:3], ["nvidia-smi", "-i", "0,1"])

    def test_query_cancel_terminates_and_reaps_owned_process(self):
        process = self.process("cancel")
        heartbeat = mock.Mock()
        def check():
            if self.clock.now >= 1:
                raise ProductionPaused()
        with self.assertRaises(ProductionPaused) as caught:
            gpu_snapshot([0], timeout=60, check=check, heartbeat=heartbeat)
        self.assertEqual(caught.exception.reason, "paused")
        self.assertEqual(process.actions, ["communicate", "terminate", "communicate"])
        self.assertEqual(process.returncode, -15)
        self.assertFalse(process.killed)
        self.assertLessEqual(self.clock.now, 1)
        self.assertTrue(heartbeat.called)

    def test_query_timeout_escalates_terminate_to_kill_and_reaps(self):
        process = self.process("stubborn")
        with self.assertRaises(TimeoutError):
            gpu_snapshot([0], timeout=2)
        self.assertEqual(process.actions,
                         ["communicate", "communicate", "terminate",
                          "communicate", "kill", "communicate"])
        self.assertEqual(process.returncode, -9)
        self.assertTrue(process.terminated and process.killed)
        self.assertTrue(all(0 < timeout <= 1 for timeout in process.timeouts))
        self.assertLessEqual(self.clock.now, 3)

    def blocked_startup(self, control, expected_reason, expected_status, expected_time):
        events = []
        budget = Budget({"max_wall_seconds": 1000, "max_gpu_seconds": 1000})
        scheduler = Scheduler({"gpu_cards": [0], "image_min_free_mib": 15000,
                               "resource_wait_seconds": 180, "resource_poll_seconds": 10},
                              budget, event_callback=lambda event: events.append(dict(event)))
        handle = mock.MagicMock()
        def create_worker(*args, **kwargs):
            class LockOnlyWorker:
                def __enter__(self):
                    self.lock = StartupLock(240, check=kwargs["resource_check"],
                                            heartbeat=kwargs["resource_heartbeat"])
                    return self.lock.__enter__()

                def __exit__(self, *args):
                    return self.lock.__exit__(*args)
            return LockOnlyWorker()
        with mock.patch("pathlib.Path.mkdir"), \
             mock.patch("pathlib.Path.open", return_value=handle), \
             mock.patch("factory.scheduler.fcntl.flock", side_effect=BlockingIOError), \
             mock.patch("factory.scheduler.gpu_snapshot", return_value={0: card()}), \
             mock.patch("factory.scheduler.FactoryComfyWorker", side_effect=create_worker) as factory:
            with self.assertRaises(ProductionPaused) as caught:
                with scheduler.worker("cpu-startup-lock", control=control):
                    self.fail("An occupied startup lock must not start the media task")
        self.assertEqual(caught.exception.reason, expected_reason)
        self.assertEqual(events[-1]["status"], expected_status)
        self.assertAlmostEqual(self.clock.now, expected_time, delta=.25)
        self.assertTrue(all(seconds <= .25 for seconds in self.clock.sleeps))
        handle.close.assert_called_once()
        factory.assert_called_once()
        self.popen.assert_not_called()
        self.assertEqual(budget.snapshot()["active_gpu_leases"], 0)

    def test_startup_lock_busy_obeys_scheduler_cancel(self):
        self.blocked_startup(lambda: "cancel" if self.clock.now >= 2 else None,
                             "paused", "cancelled", 2)

    def test_startup_lock_busy_uses_same_180_second_resource_deadline(self):
        self.blocked_startup(None, "resource_timeout", "timed_out", 180)


if __name__ == "__main__":
    unittest.main()
