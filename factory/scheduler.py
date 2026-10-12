"""Bounded local GPU leases and cumulative production budgets.

Uses the existing lock namespace. It never evicts another process or treats
allocated device memory as idle/free memory.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import socket
import subprocess
import threading
import time
import urllib.error
from pathlib import Path

from studio.comfy import ComfyWorker, GpuLease as _LegacyGpuLease, api
from studio.core import COMFY, ROOT, GPU_LOCK_DIR, write_json


class ProductionPaused(RuntimeError):
    def __init__(self, message="Production paused at a safe boundary", reason="paused", details=None):
        super().__init__(message)
        self.reason, self.details = reason, details or {}


class ProductionBudgetExceeded(ProductionPaused):
    def __init__(self, message="The authorized production budget is exhausted", details=None):
        super().__init__(message, "budget_exceeded", details)


class ProductionNeedsDecision(RuntimeError):
    def __init__(self, message, reason="needs_decision", details=None):
        super().__init__(message)
        self.reason, self.details = reason, details or {}



def gpu_snapshot(cards=None, *, timeout=60, check=None, heartbeat=None):
    """Read NVML once, with cancellation and a deadline for this owned query process."""
    selector = ["-i", ",".join(map(str, cards))] if cards is not None else []
    argv = ["nvidia-smi", *selector, "--query-gpu=index,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits"]
    if check:
        check()
    deadline = time.monotonic() + float(timeout)
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while True:
            if check:
                check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The bounded GPU inventory query timed out")
            try:
                stdout, stderr = process.communicate(timeout=min(1.0, remaining))
                break
            except subprocess.TimeoutExpired:
                if heartbeat:
                    heartbeat()
        if check:
            check()
        if process.returncode:
            raise RuntimeError("GPU inventory query failed: " + stderr[-1000:])
        return {int(index): {"free_mib": int(free), "utilization": int(util)}
                for index, free, util in (row.split(",") for row in stdout.strip().splitlines())}
    finally:
        # Only the nvidia-smi child created above is terminated, never a model or another service.
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=1)


class GpuLease(_LegacyGpuLease):
    """Keep the existing advisory locks; make the post-lock hardware check cancellable."""
    def __init__(self, cards, min_free=15000, snapshot=None):
        super().__init__(cards, min_free=min_free)
        self.snapshot = snapshot or gpu_snapshot

    def __enter__(self):
        locks = GPU_LOCK_DIR
        locks.mkdir(parents=True, exist_ok=True)
        try:
            for card in sorted(self.cards):
                handle = (locks / f"gpu{card}.lock").open("a+")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    raise RuntimeError(f"GPU {card} is leased by another production")
                self.files.append(handle)
            try:
                snapshot = self.snapshot(self.cards)
            except ProductionPaused:
                raise
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                raise ProductionPaused("GPU availability could not be verified after locking",
                                       "gpu_probe_unavailable", {"error": str(exc)[:500]}) from exc
            for card in self.cards:
                if card not in snapshot or snapshot[card]["free_mib"] < self.min_free or snapshot[card]["utilization"] >= 10:
                    raise RuntimeError(f"GPU {card} is occupied; existing job preserved")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

class Budget:
    def __init__(self, config, previous=None):
        previous = previous or {}
        self.max_wall = float(config.get("max_wall_seconds", 21600))
        self.max_gpu = float(config.get("max_gpu_seconds", 7200))
        if not 0 < self.max_wall <= 604800 or not 0 < self.max_gpu <= 604800 * 8:
            raise ValueError("Production budgets must be positive and bounded")
        self.base_wall = max(0.0, float(previous.get("elapsed_seconds", 0)))
        self.base_gpu = max(0.0, float(previous.get("gpu_seconds", 0)))
        self.started = time.monotonic()
        self.active = {}
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            gpu = self.base_gpu + sum((now - start) * count for start, count in self.active.values())
            return {"elapsed_seconds": round(self.base_wall + now - self.started, 3),
                    "gpu_seconds": round(gpu, 3), "gpu_minutes": round(gpu / 60, 3),
                    "max_wall_seconds": self.max_wall, "max_gpu_seconds": self.max_gpu,
                    "accounting": "leased_device_wall_seconds_including_service_startup",
                    "active_gpu_leases": sum(count for _, count in self.active.values())}

    def remaining(self, gpu_count=0):
        state = self.snapshot()
        wall = self.max_wall - state["elapsed_seconds"]
        gpu = self.max_gpu - state["gpu_seconds"]
        if gpu_count:
            return min(wall, gpu / max(1, gpu_count, state["active_gpu_leases"]))
        return wall

    def check(self, control=None, require_gpu=False):
        if control is not None:
            signal = control()
            pause = signal is True or signal in ("pause", "paused", "cancel", "cancelled")
            if isinstance(signal, dict):
                pause = bool(signal.get("pause") or signal.get("cancel") or
                             signal.get("status") in ("paused", "cancelled", "pausing"))
            if pause:
                raise ProductionPaused()
        state = self.snapshot()
        if state["elapsed_seconds"] >= self.max_wall or (require_gpu and state["gpu_seconds"] >= self.max_gpu):
            raise ProductionBudgetExceeded(details=state)

    def timeout(self, limit=1800, gpu_count=0):
        seconds = min(float(limit), self.remaining(gpu_count))
        if seconds <= 0:
            raise ProductionBudgetExceeded(details=self.snapshot())
        return max(0.05, seconds)

    @contextlib.contextmanager
    def gpu_operation(self, count=1):
        self.check(require_gpu=True)
        token = object()
        with self.lock:
            self.active[token] = (time.monotonic(), int(count))
        try:
            yield
        finally:
            with self.lock:
                started, used = self.active.pop(token)
                self.base_gpu += (time.monotonic() - started) * used


class StartupLock:
    """The existing cold-start lock, with an explicit remaining-time deadline."""
    def __init__(self, timeout, check=None, heartbeat=None):
        self.timeout, self.check, self.heartbeat = timeout, check, heartbeat

    def __enter__(self):
        path = ROOT / "runtime/gpu_startup.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+")
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                if self.check:
                    self.check()
                try:
                    fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ProductionPaused("Another local GPU service is still starting", "gpu_busy")
                    if self.heartbeat:
                        self.heartbeat()
                    time.sleep(min(.25, max(.01, deadline - time.monotonic())))
        except BaseException:
            self.file.close()
            raise

    def __exit__(self, *args):
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


class FactoryComfyWorker(ComfyWorker):
    """Existing Comfy API adapter with a configurable, verified free-memory gate."""
    def __init__(self, cards, port, tag, min_free=15000, startup_timeout=240, reserve_vram=1.5,
                 resource_probe=None, resource_check=None, resource_heartbeat=None):
        super().__init__(cards, port, tag, precision="fp16")
        self.min_free = int(min_free)
        self.startup_timeout = float(startup_timeout)
        self.reserve_vram = float(reserve_vram)
        self.resource_probe = resource_probe
        self.resource_check, self.resource_heartbeat = resource_check, resource_heartbeat

    def __enter__(self):
        with StartupLock(self.startup_timeout, self.resource_check, self.resource_heartbeat):
            return self._start()

    def _start(self):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", self.port)) == 0:
                raise ProductionPaused("The configured Comfy port is already in use; its service was preserved",
                                       "gpu_busy", {"port": self.port})
        self.lease = GpuLease(self.cards, min_free=self.min_free, snapshot=self.resource_probe)
        self.lease.__enter__()
        try:
            for name in ("input", "output", "temp", "user"):
                (self.folder / name).mkdir(parents=True, exist_ok=True)
            extra = self.folder / "models.yaml"
            extra.write_text("drama_studio:\n  base_path: " + str(ROOT / "models") +
                             "\n  diffusion_models: diffusion_models\n  text_encoders: text_encoders"
                             "\n  vae: vae\n  loras: loras\n  upscale_models: upscale_models"
                             "\n  custom_nodes: ../nodes\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(CUDA_VISIBLE_DEVICES=",".join(map(str, self.cards)),
                       OMP_NUM_THREADS="6", MKL_NUM_THREADS="6", PYTHONUNBUFFERED="1")
            argv = [str(COMFY / ".venv/bin/python"), str(COMFY / "main.py"),
                    "--listen", "127.0.0.1", "--port", str(self.port),
                    "--disable-auto-launch", "--disable-dynamic-vram",
                    "--reserve-vram", str(self.reserve_vram), "--use-pytorch-cross-attention",
                    "--disable-api-nodes", "--preview-method", "none",
                    "--extra-model-paths-config", str(extra),
                    "--output-directory", str(self.folder / "output"),
                    "--input-directory", str(self.folder / "input"),
                    "--temp-directory", str(self.folder / "temp"),
                    "--user-directory", str(self.folder / "user"),
                    "--database-url", "sqlite:///" + str(self.folder / "comfyui.db"),
                    "--fp16-unet", "--fp16-text-enc", "--fp32-vae"]
            self.log = (self.folder / "comfy.log").open("a")
            self.child = subprocess.Popen(argv, cwd=COMFY, env=env, stdout=self.log,
                                          stderr=subprocess.STDOUT)
            write_json(self.folder / "launch.json",
                       {"argv": argv, "physical_gpus": self.cards, "pid": self.child.pid,
                        "started": time.time(), "owner": "autonomous_factory",
                        "minimum_free_mib": self.min_free})
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self.child.poll() is not None:
                    raise RuntimeError("Comfy startup failed: " + str(self.folder / "comfy.log"))
                try:
                    timeout = max(.1, min(3, deadline - time.monotonic()))
                    api(self.base, "/system_stats", timeout=timeout)
                    self.info = api(self.base, "/object_info", timeout=timeout)
                    return self
                except (OSError, urllib.error.URLError, ValueError):
                    time.sleep(min(1, max(.01, deadline - time.monotonic())))
            raise TimeoutError("Comfy startup exceeded the remaining production budget")
        except BaseException:
            self.__exit__(None, None, None)
            raise


class Scheduler:
    def __init__(self, config, budget, event_callback=None):
        self.config, self.budget, self.event_callback = config, budget, event_callback
        self.cards = [int(c) for c in config.get("gpu_cards", list(range(8)))]
        if not self.cards or len(set(self.cards)) != len(self.cards) or any(c < 0 for c in self.cards):
            raise ValueError("gpu_cards must be a nonempty unique list of physical GPU indices")
        self.min_free = int(config.get("image_min_free_mib", config.get("gpu_min_free_mib", 15000)))
        if self.min_free < 12000:
            raise ValueError("This local image profile requires a minimum free-memory gate of 12000 MiB")
        self.port = int(config.get("comfy_start_port", 8391))
        if not 1024 <= self.port <= 65400:
            raise ValueError("Invalid local worker port")
        self.wait_seconds = float(config.get("resource_wait_seconds", 180))
        self.poll_seconds = float(config.get("resource_poll_seconds", 10))
        self.probe_seconds = float(config.get("resource_probe_timeout_seconds", 60))
        if not 0 <= self.wait_seconds <= 3600 or not 1 <= self.poll_seconds <= 60 or not 1 <= self.probe_seconds <= 120:
            raise ValueError("Resource wait, polling and probe timeouts must be finite and bounded")
        self.cursor = 0

    def _context(self, minimum, stage):
        now = time.monotonic()
        return {"started": now, "deadline": now + self.wait_seconds, "stage": stage,
                "minimum": minimum, "snapshot": {}, "probe_count": 0,
                "last_event_at": float("-inf"), "last_status": None, "last_reason": None}

    def _resource_event(self, context, status, reason, *, force=False, **details):
        now = time.monotonic()
        if (not force and context["last_status"] == status and context["last_reason"] == reason
                and now - context["last_event_at"] < 5):
            return None
        context.update(last_event_at=now, last_status=status, last_reason=reason)
        value = {"status": status, "reason": reason, "stage": context["stage"],
                 "waited_seconds": round(max(0, context.get("acquired_at", now) - context["started"]), 3),
                 "max_wait_seconds": self.wait_seconds, "poll_seconds": self.poll_seconds,
                 "probe_timeout_seconds": self.probe_seconds, "probe_count": context["probe_count"],
                 "minimum_free_mib": context["minimum"], "utilization_exclusive_upper_bound_percent": 10,
                 "snapshot": context["snapshot"], **details}
        if context.get("error"):
            value["last_error"] = context["error"]
        if self.event_callback:
            self.event_callback(value)
        return value

    def _timeout(self, context):
        event = self._resource_event(context, "timed_out", "resource_timeout", force=True)
        raise ProductionPaused("GPU resources did not become available within the bounded wait",
                               "resource_timeout", event)

    def _check(self, context, control):
        try:
            self.budget.check(control, require_gpu=True)
        except ProductionBudgetExceeded as exc:
            event = self._resource_event(context, "budget_exceeded", "budget_exceeded", force=True)
            exc.details = {**exc.details, "resource_wait": event}
            raise
        except ProductionPaused as exc:
            event = self._resource_event(context, "cancelled", exc.reason, force=True)
            exc.details = {**exc.details, "resource_wait": event}
            raise
        if self.wait_seconds and time.monotonic() >= context["deadline"]:
            self._timeout(context)

    def _probe(self, context, cards, control, leased=False):
        self._check(context, control)
        limit = min(self.probe_seconds, self.budget.remaining())
        if self.wait_seconds:
            limit = min(limit, context["deadline"] - time.monotonic())
        context["probe_count"] += 1
        self._resource_event(context, "checking", "checking_gpu", force=True, queried_cards=list(cards))
        snapshot = gpu_snapshot(cards, timeout=max(.001, limit),
                                check=lambda: self._check(context, control),
                                heartbeat=lambda: self._resource_event(context, "checking", "checking_gpu",
                                                                        queried_cards=list(cards)))
        self._check(context, control)
        context["snapshot"].update(snapshot)
        context.pop("error", None)
        if leased and all(card in snapshot and snapshot[card]["free_mib"] >= context["minimum"]
                          and snapshot[card]["utilization"] < 10 for card in cards):
            context["acquired_at"] = time.monotonic()
            self._resource_event(context, "ready", "gpu_ready", force=True, selected_gpu=cards[0])
        return snapshot

    def _delay(self, context, control, reason):
        self._check(context, control)
        if not self.wait_seconds:
            self._timeout(context)
        until = min(context["deadline"], time.monotonic() + self.poll_seconds)
        self._resource_event(context, "waiting", reason, force=True,
                             next_probe_in_seconds=round(until - time.monotonic(), 3))
        while time.monotonic() < until:
            self._check(context, control)
            self._resource_event(context, "waiting", reason,
                                 next_probe_in_seconds=round(max(0, until - time.monotonic()), 3))
            time.sleep(min(1.0, max(0, until - time.monotonic()), max(0, self.budget.remaining())))
        self._check(context, control)

    def choose_card(self, min_free=None, control=None, _context=None):
        minimum = self.min_free if min_free is None else int(min_free)
        context = _context or self._context(minimum, "gpu_selection")
        while True:
            self._check(context, control)
            try:
                snapshot = self._probe(context, self.cards, control)
            except ProductionPaused:
                raise
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                context["error"] = type(exc).__name__ + ": " + str(exc)[:500]
                self._delay(context, control, "gpu_probe_unavailable")
                continue
            rotated = self.cards[self.cursor:] + self.cards[:self.cursor]
            candidates = [card for card in rotated if card in snapshot and snapshot[card]["free_mib"] >= minimum
                          and snapshot[card]["utilization"] < 10]
            if candidates:
                card = candidates[0]
                self.cursor = (self.cards.index(card) + 1) % len(self.cards)
                self._resource_event(context, "ready", "gpu_candidate_ready", force=True, selected_gpu=card)
                return card
            self._delay(context, control, "gpu_busy")

    @staticmethod
    def _resource_error(exc):
        if isinstance(exc, ProductionPaused):
            return exc.reason in ("gpu_busy", "gpu_probe_unavailable")
        return isinstance(exc, RuntimeError) and any(part in str(exc).lower() for part in
                                                     ("is occupied", "leased by another", "port is already in use"))

    @contextlib.contextmanager
    def _acquire(self, stage, control, minimum, create):
        context = self._context(minimum, stage)
        while True:
            self._check(context, control)
            card = self.choose_card(minimum, control, _context=context)
            stack = contextlib.ExitStack()
            try:
                stack.enter_context(self.budget.gpu_operation())
                manager = create(card,
                                 lambda cards: self._probe(context, cards, control, leased=True),
                                 lambda: self._check(context, control),
                                 lambda: self._resource_event(context, "waiting", "gpu_startup_lock"))
                resource = stack.enter_context(manager)
            except BaseException as exc:
                stack.close()
                if not self._resource_error(exc):
                    raise
                context.pop("acquired_at", None)
                context["error"] = str(exc)[:500]
                self._delay(context, control, "gpu_lease_busy")
                continue
            # Once __enter__ succeeded, generation is never replayed by this resource-wait loop.
            with stack:
                self.budget.check(control, require_gpu=True)
                yield card, resource
            return

    @contextlib.contextmanager
    def worker(self, tag, control=None, min_free=None):
        minimum = self.min_free if min_free is None else int(min_free)
        def create(card, probe, check, heartbeat):
            return FactoryComfyWorker([card], self.port + card, tag, min_free=minimum,
                                      startup_timeout=self.budget.timeout(240, 1),
                                      reserve_vram=self.config.get("reserve_vram_gib", 1.5),
                                      resource_probe=probe, resource_check=check,
                                      resource_heartbeat=heartbeat)
        with self._acquire("comfy", control, minimum, create) as (_, worker):
            yield worker

    @contextlib.contextmanager
    def audio_lease(self, control=None):
        minimum = int(self.config.get("audio_min_free_mib", self.min_free))
        with self._acquire("tts", control, minimum,
                           lambda card, probe, check, heartbeat: GpuLease([card], min_free=minimum,
                                                                         snapshot=probe)) as (card, _):
            yield card
