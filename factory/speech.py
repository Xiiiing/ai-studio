"""Measured dialogue fitting and provenance-checked local speech generation.

The rewriter only changes approved dialogue wording. GPU processes and their
leases have finished before the optional text-only callback is invoked.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import math
import os
import re
import subprocess
import time
from pathlib import Path

from studio.core import ROOT, fingerprint, media_receipt, read_json, sha256, within, write_json
from studio.media import SR
from .scheduler import ProductionNeedsDecision, ProductionPaused
from .production_profiles import explicit as explicit_profile, voices_for, profile_signature


def spoken_length(text):
    return sum(character.isalnum() for character in str(text))


class SpeechMixin:
    def _speech_sources(self, lines, voices):
        """Match the installed driver's real per-line source digest, without loading a model."""
        if self.config.get("audio_connection", {}).get("enabled") is False:
            raise ProductionNeedsDecision("请先配置并启用配音连接", "audio_unconfigured")
        if self.config.get("gpu_service") and self.config.get("audio_connection", {}).get("provider", "legacy") == "legacy":
            from .gpu_client import GPUClient
            voices=self._remote_voice_subset(lines,voices)
            expected=GPUClient(self.config["gpu_service"]).sources(lines,voices,self._remote_speech_options(),
                       self.project.get("production_profile"))
            write_json(self.folder / "audio/tts/remote_sources" / (fingerprint({"lines":lines,"voices":voices})[:24]+".json"),expected)
            return expected
        driver_path = ROOT / "audio/tts.py"
        spec = importlib.util.spec_from_file_location("_factory_tts_source", driver_path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        model = Path(self.config.get("audio_connection", {}).get("model_dir", ROOT / "models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice"))
        provenance_path = model / "download_receipt.json"
        provenance = read_json(provenance_path)
        model_state = {"receipt_sha256": sha256(provenance_path), "files": {}}
        for name in provenance["files"]:
            path = within(model, name)
            stat = path.stat()
            model_state["files"][name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        dtype = str(self.config.get("tts_dtype", "float32"))
        seed = int(self.config.get("tts_seed", 20261012))
        temperature = float(self.config.get("tts_temperature", .7))
        max_tokens = int(self.config.get("tts_max_new_tokens", 768))
        if dtype not in ("float32", "float16") or not 0 < temperature <= 2 or not 32 <= max_tokens <= 4096:
            raise ValueError("Invalid bounded TTS settings")
        settings = {"model_id": provenance["repo_id"], "model_revision": provenance["revision"],
                    "model_files": provenance["files"], "dtype": dtype, "attention": "sdpa",
                    "device": "cuda:0", "do_sample": True, "temperature": temperature,
                    "top_p": .9, "top_k": 50, "repetition_penalty": 1.05,
                    "max_new_tokens": max_tokens, "implementation_sha256": sha256(driver_path)}
        request = self.folder / "audio/tts/requests" / (fingerprint(lines)[:24] + ".json")
        write_json(request, lines)
        expected = {}
        for line in driver.parse_input(request, voices):
            line_seed = (seed + int(hashlib.sha256(line["id"].encode()).hexdigest()[:8], 16)) % (2**31)
            source = {"line": line, "settings": settings, "seed": line_seed}
            source_sha = hashlib.sha256(driver.canonical(source).encode("utf-8")).hexdigest()
            key = fingerprint({"source_sha256": source_sha, "model_state": model_state, "schema": 2})
            if explicit_profile(self.project):
                key = fingerprint({"source": key, "production_profile": profile_signature(self.project)})
            expected[line["id"]] = {"source": source, "source_sha256": source_sha, "key": key,
                                    "model_state": model_state}
        return expected

    def _speech_receipt(self, path, expected):
        """A receipt is reusable only for this exact source and the saved WAV bytes."""
        path = Path(path)
        meta_path = path.with_suffix(".json")
        if not path.is_file() or not meta_path.is_file():
            return None
        try:
            meta = read_json(meta_path)
            line = expected["source"]["line"]
            if (meta.get("source_sha256") != expected["source_sha256"] or
                    meta.get("source") != expected["source"] or
                    meta.get("text") != line["text"] or meta.get("role") != line["role"]):
                return None
            quality = meta.get("quality", {})
            if (quality.get("wav_sha256") != sha256(path) or
                    quality.get("sample_rate") != 24000 or quality.get("channels") != 1 or
                    not float(quality.get("duration_seconds", 0)) >= .1):
                return None
            return {**media_receipt(path), "id": line["id"], "text": line["text"], "role": line["role"],
                    "quality": quality, "source_signature": expected["key"],
                    "source_sha256": expected["source_sha256"], "source": expected["source"],
                    "model_state": expected["model_state"],
                    "metadata_path": str(meta_path.relative_to(ROOT)),
                    **{key:meta[key] for key in ("execution_host","gpu_service_job_id") if key in meta}}
        except (ValueError, KeyError, TypeError, OSError):
            return None

    def _speech_index(self, expected, record=None):
        index = self.folder / "audio/tts/lines" / expected["key"] / "selected.json"
        if record is not None:
            write_json(index, record)
            return record
        if index.exists():
            value = read_json(index)
            if value.get("source_signature") == expected["key"]:
                path = self.receipt_path(value)
                return self._speech_receipt(path, expected)
        return None

    def _speech_attempt_ledger(self, expected, receipt=None):
        """The retry cap follows a line's exact input even when the pending batch changes."""
        path = self.folder / "audio/tts/lines" / expected["key"] / "attempt_receipts.json"
        values = read_json(path) if path.exists() else []
        if receipt is not None:
            reference = str(Path(receipt).relative_to(ROOT))
            if reference not in values:
                values.append(reference)
                write_json(path, values)
        return [read_json(within(ROOT, value)) for value in values]

    def _check_speech_attempts(self, lines, expected, records, maximum):
        blocked = []
        for line in lines:
            ident = line["id"]
            if ident in records:
                continue
            attempts = self._speech_attempt_ledger(expected[ident])
            if any(attempt.get("state") == "running" for attempt in attempts):
                raise ProductionNeedsDecision("A previous speech process has no terminal receipt",
                                              "unresolved_voice_submission", {"line_id": ident})
            if len(attempts) >= maximum or any(attempt.get("oom") for attempt in attempts):
                blocked.append({"id": ident, "attempt_count": len(attempts),
                                "oom": any(attempt.get("oom") for attempt in attempts)})
        if blocked:
            raise ProductionNeedsDecision("The bounded retry allowance for these exact speech inputs is exhausted",
                                          "voice_failed", {"blocked_lines": blocked, "completed_lines": list(records),
                                                           "oom": any(line["oom"] for line in blocked)})

    def _speech_batch(self, lines, voices, expected):
        if self.config.get("gpu_service") and self.config.get("audio_connection", {}).get("provider", "legacy") == "legacy":
            return self._remote_speech_batch(lines,voices,expected)
        signature = fingerprint([{"id": line["id"], "key": expected[line["id"]]["key"]} for line in lines])
        folder = self.folder / "audio/tts/batches" / signature[:24]
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "input.json", lines)
        write_json(folder / "voices.json", voices)
        write_json(folder / "expected_sources.json", {line["id"]: expected[line["id"]] for line in lines})
        attempts_file = folder / "attempts.json"
        attempts = read_json(attempts_file) if attempts_file.exists() else []

        def collect():
            records = {}
            for line in lines:
                record = self._speech_receipt(folder / (line["id"] + ".wav"), expected[line["id"]])
                if record:
                    records[line["id"]] = self._speech_index(expected[line["id"]], record)
            return records

        records = collect()
        if len(records) == len(lines):
            return records
        if any(attempt.get("state") == "running" for attempt in attempts):
            raise ProductionNeedsDecision("A prior speech process has no terminal receipt; completed lines were recovered",
                                          "unresolved_voice_submission",
                                          {"batch": str(folder.relative_to(ROOT)), "completed_lines": list(records)})
        maximum = int(self.config.get("tts_max_attempts", 2))
        if not 1 <= maximum <= 2:
            raise ValueError("tts_max_attempts must be one or two")
        last = attempts[-1] if attempts else {}
        self._check_speech_attempts(lines, expected, records, maximum)
        while len(attempts) < maximum and not last.get("oom"):
            self._check_speech_attempts(lines, expected, records, maximum)
            self.boundary(gpu=True)
            attempt = {"attempt": len(attempts) + 1, "state": "running", "started_at": time.time(),
                       "source_signature": signature, "line_ids": [line["id"] for line in lines],
                       "source_sha256": {line["id"]: expected[line["id"]]["source_sha256"] for line in lines},
                       "completed_before": list(records),
                       "pending_line_ids": [line["id"] for line in lines if line["id"] not in records]}
            log_path = folder / f"attempt_{attempt['attempt']:02d}.log"
            attempt_path = folder / f"attempt_{attempt['attempt']:02d}.json"
            attempt["log"] = str(log_path.relative_to(ROOT))
            before = self.budget.snapshot()
            pending_error = None
            from .scheduler import Scheduler
            audio_scheduler = self.scheduler
            if self.config.get("audio_connection", {}).get("provider") == "qwen_tts_local" and self.config.get("gpu_service"):
                audio_scheduler = Scheduler({k:v for k,v in self.config.items() if k != "gpu_service"}, self.budget)
            with audio_scheduler.audio_lease(self.control) as card:
                attempt["physical_gpu"] = card
                env = os.environ.copy()
                env.update(CUDA_VISIBLE_DEVICES=str(card), HF_HUB_OFFLINE="1",
                           TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
                settings = expected[lines[0]["id"]]["source"]["settings"]
                argv = [str(self.config.get("audio_connection", {}).get("python_path", ROOT / "envs/tts/bin/python")), str(ROOT / "audio/tts.py"),
                        "--model", str(self.config.get("audio_connection", {}).get("model_dir", ROOT / "models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice")),
                        "--input", str(folder / "input.json"), "--voices", str(folder / "voices.json"),
                        "--output-dir", str(folder), "--device", "cuda:0",
                        "--dtype", settings["dtype"], "--seed", str(self.config.get("tts_seed", 20261012)),
                        "--temperature", str(settings["temperature"]),
                        "--max-new-tokens", str(settings["max_new_tokens"])]
                attempt["argv"] = argv
                attempts.append(attempt)
                write_json(attempts_file, attempts)
                write_json(attempt_path, attempt)
                for line in lines:
                    if line["id"] in attempt["pending_line_ids"]:
                        self._speech_attempt_ledger(expected[line["id"]], attempt_path)
                self.production.setdefault("voice_attempts", []).append(str(attempt_path.relative_to(ROOT)))
                self.emit("voice_rendering")
                try:
                    with log_path.open("w", encoding="utf-8") as log:
                        process = subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                                 timeout=self.budget.timeout(3600, 1))
                    attempt["exit_code"] = process.returncode
                except (subprocess.TimeoutExpired, OSError, ProductionPaused) as exc:
                    pending_error = exc
                    attempt["error"] = type(exc).__name__ + ": " + str(exc)
                    attempt["state"] = "timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "failed"
            # The process has returned (or was killed/reaped by subprocess.run) and its lease is released.
            log_text = log_path.read_text(encoding="utf-8", errors="replace")[-6000:] if log_path.exists() else ""
            attempt["oom"] = bool(re.search(r"out of memory|cublas_status_alloc_failed|cuda.*memory allocation",
                                           log_text, re.IGNORECASE))
            attempt["error_tail"] = log_text[-2500:] if attempt.get("exit_code", 1) else ""
            records = collect()
            attempt.update(completed_lines=list(records), finished_at=time.time(),
                           gpu_seconds_delta=round(self.budget.snapshot()["gpu_seconds"] - before["gpu_seconds"], 3))
            if len(records) == len(lines):
                attempt["state"] = "completed" if not attempt.get("exit_code") else "completed_with_process_error"
            elif attempt.get("state") == "running":
                attempt["state"] = "failed"
            write_json(attempts_file, attempts)
            write_json(attempt_path, attempt)
            self.emit("voice_review")
            if len(records) == len(lines):
                return records
            if isinstance(pending_error, ProductionPaused):
                raise pending_error
            if isinstance(pending_error, subprocess.TimeoutExpired):
                self.budget.check(require_gpu=True)
                raise ProductionNeedsDecision("Speech generation reached its bounded process timeout", "voice_failed",
                                              {"batch": str(folder.relative_to(ROOT)), "attempt": attempt})
            if pending_error is not None or attempt.get("exit_code") == 0:
                raise ProductionNeedsDecision("Speech outputs could not be verified against the installed model inputs",
                                              "voice_failed", {"batch": str(folder.relative_to(ROOT)), "attempt": attempt})
            last = attempt
        raise ProductionNeedsDecision("Local speech generation failed; completed lines and errors are preserved",
                                      "voice_failed", {"batch": str(folder.relative_to(ROOT)), "attempts": attempts,
                                                       "completed_lines": list(records), "oom": bool(last.get("oom"))})

    def _remote_voice_subset(self,lines,voices):
        """Only active dialogue roles belong to the remote speech contract.

        The bundled voice map also contains legacy Chinese aliases. Those are
        useful locally but must not become unused server request role bindings.
        """
        roles={line["role"] for line in lines}
        if not roles <= voices.keys():
            raise ValueError("Every remote dialogue role requires a configured voice")
        return {role:copy.deepcopy(voices[role]) for role in sorted(roles)}

    def _remote_speech_options(self):
        return {"tts_dtype":self.config.get("tts_dtype","float32"),
                "tts_seed":self.config.get("tts_seed",20261012),
                "tts_temperature":self.config.get("tts_temperature",.7),
                "tts_max_new_tokens":self.config.get("tts_max_new_tokens",768)}

    def _remote_speech_batch(self,lines,voices,expected):
        from .gpu_client import GPUClient
        signature=fingerprint([{"id":line["id"],"key":expected[line["id"]]["key"]} for line in lines])
        folder=self.folder / "audio/tts/batches" / signature[:24]
        folder.mkdir(parents=True,exist_ok=True)
        for line in lines:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}",line["id"]):
                raise ValueError("Invalid remote speech line identifier")
        def collect():
            records={}
            for line in lines:
                record=self._speech_receipt(folder / (line["id"]+".wav"),expected[line["id"]])
                if record:
                    records[line["id"]]=self._speech_index(expected[line["id"]],record)
            return records
        records=collect()
        if len(records)==len(lines):
            return records
        client=GPUClient(self.config["gpu_service"])
        workload={"kind":"tts","lines":lines,"voices":self._remote_voice_subset(lines,voices),"options":self._remote_speech_options()}
        if self.project.get("production_profile") is not None:
            workload["project_profile"]=self.project["production_profile"]
        maximum=int(self.config.get("tts_max_attempts",2))
        if not 1 <= maximum <= 2:
            raise ValueError("tts_max_attempts must be one or two")
        attempts_path=folder / "attempts.json"
        attempts=read_json(attempts_path) if attempts_path.exists() else []
        while True:
            if attempts and attempts[-1]["state"] in {"running","paused"}:
                attempt=attempts[-1]
            else:
                if len(attempts)>=maximum:
                    raise ProductionNeedsDecision("Remote speech exhausted its bounded retry allowance","voice_failed")
                attempt={"attempt":len(attempts)+1,"state":"running","started_at":time.time(),
                         "line_ids":[line["id"] for line in lines],"execution_host":client.execution_host}
                attempts.append(attempt)
            number=attempt["attempt"]
            attempt_path=folder / f"attempt_{number:02d}.json"
            job_folder=folder / f"remote_attempt_{number:02d}"
            write_json(attempts_path,attempts)
            write_json(attempt_path,attempt)
            reference=str(attempt_path.relative_to(ROOT))
            if reference not in self.production.setdefault("voice_attempts",[]):
                self.production["voice_attempts"].append(reference)
            self.emit("voice_rendering")
            try:
                answer=client.run(workload,job_folder,self.budget,self.control,
                    lambda event:self.emit("voice_rendering",remote_inference=event),self.budget.timeout(3600))
                result=answer.get("result",{})
                if result.get("expected")!={line["id"]:expected[line["id"]] for line in lines}:
                    raise ProductionNeedsDecision("Speech source changed between planning and inference","remote_gpu_source_changed")
                remote_records=result.get("records",{})
                artifacts={item["index"]:item for item in result.get("artifacts",[])}
                if set(remote_records)!={line["id"] for line in lines}:
                    raise ProductionNeedsDecision("Remote speech returned mismatched line receipts","remote_gpu_receipt_invalid")
                for line in lines:
                    record=remote_records[line["id"]]
                    try:
                        wave=artifacts[record["wave_artifact_index"]]
                        meta=artifacts[record["metadata_artifact_index"]]
                    except (KeyError,TypeError):
                        raise ProductionNeedsDecision("Remote speech artifact mapping is invalid","remote_gpu_receipt_invalid")
                    local_wave=client.download(answer["id"],wave,folder/(line["id"]+".wav"))
                    raw_meta=client.download(answer["id"],meta,job_folder/"artifacts"/(str(meta["index"])+".json"))
                    metadata=read_json(raw_meta)
                    metadata.update(wav=str(local_wave.resolve()),gpu_service_job_id=answer["id"],
                                    execution_host=result["receipt"].get("execution_host",client.execution_host))
                    write_json(local_wave.with_suffix(".json"),metadata)
                records=collect()
                if len(records)!=len(lines):
                    raise ProductionNeedsDecision("Downloaded speech failed per-line source verification","remote_gpu_receipt_invalid")
                attempt.update(state="completed",finished_at=time.time(),completed_lines=list(records),
                               gpu_service_job_id=answer["id"],gpu_seconds_delta=result["receipt"]["gpu_seconds"])
                write_json(attempts_path,attempts)
                write_json(attempt_path,attempt)
                self.emit("voice_review")
                return records
            except (ProductionPaused,ProductionNeedsDecision):
                # A transport interruption or unresolved remote process is not
                # permission to submit a replacement inference attempt.
                attempt["state"]="paused"
                write_json(attempts_path,attempts)
                write_json(attempt_path,attempt)
                self.emit("voice_review")
                raise
            except RuntimeError:
                attempt.update(state="failed",finished_at=time.time())
                write_json(attempts_path,attempts)
                write_json(attempt_path,attempt)
                self.emit("voice_review")
                # Only a proven server terminal failure authorizes the next
                # bounded attempt; unknown local errors preserve this job.
                terminal=job_folder / "remote_result.json"
                if not terminal.exists() or read_json(terminal).get("status")!="failed":
                    raise

    def _voice(self):
        self.emit("voice")
        lines = [copy.deepcopy(line) for shot in self.shots for line in shot.get("dialogue", [])]
        if not lines:
            self.assets["audio"] = {"lines": [], "speech": "no_dialogue_in_script"}
            self.emit()
            return {}
        voices = read_json(ROOT / "audio/voices.json")
        voices.update(self.episode.get("voices") or {})
        voices.update(self.config.get("audio_connection", {}).get("voices", {}))
        if explicit_profile(self.project):
            voices = voices_for(self.project, voices)
        else:
            for role in self.cast:
                voices.setdefault(role, {"speaker": "Serena" if role == "female" else "Uncle_Fu", "language": "Chinese"})
        expected = self._speech_sources(lines, voices)
        previous = {line["id"]: line for line in self.assets.get("audio", {}).get("lines", [])}
        records, missing = {}, []
        for line in lines:
            ident = line["id"]
            record = self._speech_index(expected[ident])
            if record is None and ident in previous:
                # Migrate a legacy batch receipt only when its original driver source is still exact.
                record = self._speech_receipt(self.receipt_path(previous[ident]), expected[ident])
                if record:
                    self._speech_index(expected[ident], record)
            if record:
                records[ident] = record
            else:
                missing.append(line)
        if missing:
            records.update(self._speech_batch(missing, voices, expected))
        self.assets["audio"] = {"source_signature": fingerprint([expected[line["id"]]["key"] for line in lines]),
                                "lines": [records[line["id"]] for line in lines],
                                "model": "Wkp Qwen3-TTS CustomVoice" if self.config.get("gpu_service") else "local Qwen3-TTS CustomVoice", "cache": "verified_per_line_source_v2"}
        if explicit_profile(self.project):
            self.assets["audio"]["production_profile_signature"] = profile_signature(self.project)
        self.emit("voice_complete")
        return {ident: self.receipt_path(record) for ident, record in records.items()}

    def _speech_timing(self, shot, durations):
        lines = shot.get("dialogue", [])
        maximum = float(self.config.get("max_speech_tempo", 1.25))
        if not 1 <= maximum <= 2:
            raise ValueError("max_speech_tempo must be between one and two")
        gap, tail = .13, .25
        end = float(shot["seconds"]) - tail
        first_start = float(lines[0].get("start", .2)) if lines else .2
        starts = [first_start if index == 0 else float(line.get("start", 0))
                  for index, line in enumerate(lines)]
        if any(not math.isfinite(start) or not 0 <= start < end for start in starts):
            raise ProductionNeedsDecision("A planned dialogue onset is outside its shot", "dialogue_timing",
                                          {"shot_id": shot["id"], "shot_seconds": shot["seconds"], "starts": starts})
        available = max(0, end - first_start - max(0, len(lines) - 1) * gap)
        required = 1.0
        for index, start in enumerate(starts):
            slot = end - start - (len(lines) - index - 1) * gap
            if slot <= 0:
                raise ProductionNeedsDecision("Planned dialogue onsets leave no playable speech slot", "dialogue_timing",
                                              {"shot_id": shot["id"], "line_id": lines[index]["id"]})
            required = max(required, sum(durations[index:]) / slot)
        selected_rate = min(maximum, required)
        fits = required <= maximum + 1e-7
        reduction = min(.9, maximum / max(required, 1) * .9)
        cursor = first_start
        measured = []
        for index, line in enumerate(lines):
            cursor = max(cursor, starts[index])
            cursor += durations[index] / selected_rate
            measured.append({"id": line["id"], "role": line["role"], "text": line["text"],
                             "emotion": line.get("emotion", ""), "start_seconds": starts[index],
                             "duration_seconds": round(durations[index], 6),
                             "max_seconds": round(durations[index] * reduction, 6),
                             "suggested_max_characters": max(1, math.floor(spoken_length(line["text"]) * reduction))})
            if index + 1 < len(lines):
                cursor += gap
        return {"schema": "autonomous_factory.dialogue_measurement.v1", "shot_id": shot["id"],
                "shot_seconds": shot["seconds"], "speech_seconds": round(sum(durations), 6),
                "available_seconds": round(available, 6), "required_tempo": round(required, 6),
                "max_speech_tempo": maximum, "selected_tempo": selected_rate,
                "max_total_speech_seconds": round(available * maximum, 6),
                "gap_seconds": gap, "tail_seconds": tail, "first_start_seconds": first_start,
                "playback_end_seconds": round(cursor, 6), "fits": fits, "lines": measured}

    def _persist_dialogue(self):
        lookup = {shot["id"]: shot for shot in self.shots}
        for raw in self.episode.get("shots", []):
            if raw["id"] in lookup:
                raw["dialogue"] = copy.deepcopy(lookup[raw["id"]].get("dialogue", []))
        path = self.folder / "episode.json"
        artifact = read_json(path) if path.exists() else copy.deepcopy(self.episode)
        for raw in artifact.get("shots", []):
            if raw["id"] in lookup:
                raw["dialogue"] = copy.deepcopy(lookup[raw["id"]].get("dialogue", []))
        write_json(self.folder / "revisions" / ("episode_" + fingerprint(artifact)[:16] + ".json"), artifact)
        write_json(path, artifact)
        self.emit("dialogue_repaired", episode=copy.deepcopy(self.episode), script=copy.deepcopy(self.episode))

    def _fit_dialogue(self, mapping):
        maximum = int(self.config.get("dialogue_repair_attempts", 2))
        if not 0 <= maximum <= 2:
            raise ValueError("dialogue_repair_attempts must be between zero and two")
        for shot in self.shots:
            if not shot.get("dialogue"):
                continue
            while True:
                self.boundary()
                durations = [len(self._read_audio(mapping[line["id"]])) / SR for line in shot["dialogue"]]
                measurement = self._speech_timing(shot, durations)
                for line in measurement["lines"]:
                    line["sha256"] = sha256(mapping[line["id"]])
                repairs = shot.setdefault("dialogue_repairs", [])
                measurement.update(repair_attempt=len(repairs) + 1, max_repair_attempts=maximum)
                shot["audio_timing"] = measurement
                self.emit("dialogue_timing_review")
                if measurement["fits"]:
                    break
                if not self.dialogue_rewriter or len(repairs) >= maximum:
                    raise ProductionNeedsDecision("Measured speech needs shorter approved wording to fit its shot",
                                                  "dialogue_timing", measurement)
                if self.budget.snapshot()["active_gpu_leases"]:
                    raise RuntimeError("Dialogue rewriting must not overlap an owned GPU generation lease")
                receipt = {"attempt": len(repairs) + 1, "state": "rewriting", "started_at": time.time(),
                           "measurement": measurement, "original_dialogue": copy.deepcopy(shot["dialogue"])}
                repairs.append(receipt)
                audit_path = self.folder / "audio/dialogue_repairs" / shot["id"] / f"attempt_{len(repairs):02d}.json"
                write_json(audit_path, receipt)
                self.emit("dialogue_rewriting")
                try:
                    result = self.dialogue_rewriter(copy.deepcopy(self.project), copy.deepcopy(shot),
                                                    copy.deepcopy(measurement))
                    if not isinstance(result, dict) or not isinstance(result.get("line_updates"), list) or not result["line_updates"]:
                        raise ValueError("A dialogue rewrite must contain nonempty line_updates")
                    lines = {line["id"]: line for line in shot["dialogue"]}
                    changes, seen = [], set()
                    for update in result["line_updates"]:
                        if not isinstance(update, dict) or set(update) - {"id", "text", "emotion"}:
                            raise ValueError("Only existing dialogue text and emotion may change")
                        ident, text = update.get("id"), update.get("text")
                        if ident not in lines or ident in seen or not isinstance(text, str):
                            raise ValueError("A rewrite must identify each affected original line exactly once")
                        seen.add(ident)
                        text = text.strip()
                        old = lines[ident]
                        if not 0 < spoken_length(text) < spoken_length(old["text"]) or len(text) > 500:
                            raise ValueError("Rewritten dialogue must have strictly fewer spoken characters")
                        if re.findall(r"\d+(?:[.:/]\d+)*", text) != re.findall(r"\d+(?:[.:/]\d+)*", old["text"]):
                            raise ValueError("A timing rewrite must preserve numeric facts")
                        if not isinstance(update.get("emotion", old.get("emotion", "")), str):
                            raise ValueError("Dialogue emotion must remain a string")
                        changes.append({**old, "text": text,
                                        **({"emotion": update["emotion"]} if "emotion" in update else {})})
                    for change in changes:
                        lines[change["id"]].update(change)
                    receipt.update(state="applied", finished_at=time.time(),
                                   line_updates=result["line_updates"], reason=str(result.get("reason", "")),
                                   meaning_check="delegated_to_dialogue_rewriter; ids_roles_numbers_guarded")
                    write_json(audit_path, receipt)
                    self._persist_dialogue()
                except ProductionPaused:
                    receipt.update(state="paused", finished_at=time.time())
                    write_json(audit_path, receipt)
                    self.emit()
                    raise
                except Exception as exc:
                    receipt.update(state="failed", finished_at=time.time(), error=type(exc).__name__ + ": " + str(exc))
                    write_json(audit_path, receipt)
                    self.emit("dialogue_rewrite_failed")
                    if len(repairs) >= maximum:
                        raise ProductionNeedsDecision("Automatic dialogue compression did not produce a valid local repair",
                                                      "dialogue_rewrite_failed",
                                                      {"shot_id": shot["id"], "measurement": measurement,
                                                       "attempts": repairs}) from exc
                    continue
                # Only changed source digests miss the line cache; all other WAV files stay identical.
                mapping = self._voice()
        return mapping
