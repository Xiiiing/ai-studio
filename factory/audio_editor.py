"""Audio, procedural camera motion and immutable film exports.

AudioEditorMixin uses the production context's budget, checkpoint, shots, assets
and path helpers. GPU orchestration remains in production/scheduler.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw

from studio.core import ROOT, fingerprint, media_receipt, read_json, sha256, write_json
from studio.media import SR, chunks, font, piano, stamp, write_wav
from .quality import combine, inspect_video, semantic_review
from .scheduler import ProductionNeedsDecision
from .shot_workbench import plan_signature

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class AudioEditorMixin:
    def ffmpeg(self, args, limit=1800):
        self.boundary()
        run = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                              *map(str, args)], cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=self.budget.timeout(limit), text=True)
        if run.returncode:
            raise RuntimeError("FFmpeg failed: " + run.stderr[-2500:])
        return run

    def _camera_clip(self, shot):
        image = self.receipt_path(shot["image"])
        width, height = self.output_size
        frames = round(shot["seconds"] * 24)
        requested_motion = shot.get("camera_motion") or "slow_push"
        aliases = {"push_in": "slow_push", "pull_out": "slow_pull", "hold": "static"}
        motion = aliases.get(requested_motion, requested_motion)
        if motion not in ("slow_push", "slow_pull", "pan_left", "pan_right", "static"):
            raise ProductionNeedsDecision("The planned camera motion is unsupported", "camera_motion",
                                          {"shot": shot["id"], "motion": requested_motion})
        signature = fingerprint({"image": sha256(image), "seconds": shot["seconds"],
                                 "output": self.output_size, "motion": motion, "engine": "ffmpeg_zoompan_v1",
                                 **({"render_revision": shot["render_revisions"]["video"]}
                                    if shot.get("render_revisions", {}).get("video") else {})})
        pin = self.project.get("shot_selections", {}).get(shot["id"], {}).get("video")
        if pin:
            if pin["plan_signature"] != plan_signature(self.project, shot, "video") or pin["receipt"].get("source_signature") != signature:
                raise ProductionNeedsDecision("人工运镜选片参数已变化，请重新确认", "manual_selection_stale")
            self.receipt_path(pin["receipt"])
            return {**pin["receipt"], "producer_selection": {k: v for k, v in pin.items() if k != "receipt"}}
        folder = self.folder / "renders" / shot["id"] / ("camera_" + signature[:16])
        target = folder / "video.mp4"
        receipt_path = folder / "result.json"
        if receipt_path.exists():
            record = read_json(receipt_path)
            self.receipt_path(record)
            return record
        folder.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = folder / ("video_recovery_" + str(time.time_ns()) + ".mp4")
        progress = f"on/{max(1, frames - 1)}"
        zoom = "1.0" if motion == "static" else f"1.06-0.04*{progress}" if motion == "slow_pull" else f"1.01+0.045*{progress}"
        x = "iw/2-iw/zoom/2"
        if motion == "pan_left":
            zoom, x = "1.075", f"(iw-iw/zoom)*(0.8-0.6*{progress})"
        elif motion == "pan_right":
            zoom, x = "1.075", f"(iw-iw/zoom)*(0.2+0.6*{progress})"
        vf = (f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2},"
              f"zoompan=z='{zoom}':x='{x}':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps=24,setsar=1")
        started = time.monotonic()
        self.ffmpeg(["-n", "-loop", "1", "-framerate", "24", "-i", image,
                     "-vf", vf, "-frames:v", frames, "-an", "-c:v", "libx264", "-preset", "fast",
                     "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", target])
        technical = inspect_video(target, shot["seconds"], self.output_size)
        qa = combine(technical)
        if technical["status"] == "fail":
            write_json(folder / "quality_failed.json", qa)
            raise ProductionNeedsDecision("A procedural camera clip failed technical validation", "technical_video",
                                          {"shot": shot["id"], "qa": qa})
        record = media_receipt(target, source_signature=signature, origin="procedural_camera_motion",
                               motion=motion, requested_motion=requested_motion, diffusion_video=False, lip_sync=False,
                               source_image=shot["image"], qa=qa, plan_signature=plan_signature(self.project, shot, "video"),
                               take=folder.name, model="ffmpeg_zoompan_v1", shot_revision=shot.get("shot_revision", 1),
                               elapsed_seconds=round(time.monotonic() - started, 3))
        write_json(receipt_path, record)
        return record

    def _read_audio(self, path):
        run = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                              "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=self.budget.timeout(120), cwd=ROOT)
        if run.returncode:
            raise RuntimeError("Audio decode failed: " + run.stderr.decode(errors="replace")[-1200:])
        return np.frombuffer(run.stdout, dtype="<f4").copy()

    def _mix_audio(self, folder, mapping):
        self.emit("sound_edit")
        duration = sum(s["seconds"] for s in self.shots)
        n = round(duration * SR)
        voice = np.zeros((n, 2), dtype=np.float32)
        cues, edits, offset = [], [], 0.0
        speech_dir = folder / "dialogue_edit"
        speech_dir.mkdir(parents=True, exist_ok=True)
        for shot in self.shots:
            self.boundary()
            rows = [(line, self._read_audio(mapping[line["id"]])) for line in shot.get("dialogue", [])]
            measurement = self._speech_timing(shot, [len(samples) / SR for _, samples in rows])
            if not measurement["fits"]:
                raise ProductionNeedsDecision("Measured dialogue still exceeds the approved shot timing",
                                              "dialogue_timing", measurement)
            gap, first_start = measurement["gap_seconds"], measurement["first_start_seconds"]
            rate = measurement["selected_tempo"]
            cursor = offset + first_start
            shot_speech = np.zeros(round(shot["seconds"] * SR), dtype=np.float32)
            for line, raw in rows:
                cursor = max(cursor, offset + float(line.get("start", 0)))
                target = speech_dir / (line["id"] + ".wav")
                self.ffmpeg(["-y", "-i", mapping[line["id"]], "-af",
                             f"atempo={rate:.6f},loudnorm=I=-20:TP=-3:LRA=7",
                             "-ar", SR, "-ac", "1", target], 120)
                samples = self._read_audio(target)
                peak = float(np.max(np.abs(samples))) if len(samples) else 0
                if not len(samples) or not np.isfinite(samples).all() or peak < .0001:
                    raise ProductionNeedsDecision("A dialogue edit is empty or invalid", "voice_quality")
                samples *= min(1.0, .70 / max(peak, 1e-7))
                # Only a measured near-silent trailing tail may be trimmed.
                permitted = max(0, round((offset + shot["seconds"] - .1 - cursor) * SR))
                if len(samples) > permitted + round(.1 * SR):
                    raise ProductionNeedsDecision("Speech editing exceeded the planned shot timing", "dialogue_timing")
                if len(samples) > permitted:
                    tail = samples[permitted:]
                    if np.max(np.abs(tail), initial=0) > .003:
                        raise ProductionNeedsDecision("The final spoken syllable would be cut by the timeline", "dialogue_timing")
                    samples = samples[:permitted]
                start = round(cursor * SR)
                stop = start + len(samples)
                voice[start:stop, 0] += samples
                voice[start:stop, 1] += samples
                local_start = round((cursor - offset) * SR)
                shot_speech[local_start:local_start + len(samples)] += samples
                pieces = chunks(line["text"], 16)
                weights = [max(1, len(re.sub(r"[\W_]", "", p))) for p in pieces]
                line_start, total = cursor, sum(weights)
                for text, weight in zip(pieces, weights):
                    end = cursor + len(samples) / SR * weight / total
                    cues.append({"start": cursor, "end": end, "text": text,
                                 "line": line["id"], "shot": shot["id"]})
                    cursor = end
                edits.append({"id": line["id"], "shot": shot["id"], "start": line_start,
                              "seconds": len(samples) / SR, "tempo": rate,
                              "source": media_receipt(mapping[line["id"]]),
                              "processed": media_receipt(target)})
                cursor += gap
            if shot.get("lip_sync") and self.mode != "motion_comic":
                target = folder / "lipsync_audio" / (shot["id"] + ".wav")
                write_wav(target, shot_speech)
                shot["lipsync_audio"] = media_receipt(target)
            offset += shot["seconds"]
        music = np.zeros((n, 2), dtype=np.float32)
        gain = float((self.project.get("edit_plan") or {}).get("music_gain", self.config.get("music_gain", .12)))
        if not 0 <= gain <= .3:
            raise ValueError("Music gain must be between 0 and 0.3")
        for proportion, hz in ((0, 146.83), (.18, 174.61), (.38, 220), (.60, 196), (.78, 293.66), (.9, 349.23)):
            note = piano(hz) * gain * 4
            start = round(duration * proportion * SR)
            end = min(n, start + len(note))
            music[start:end, 0] += note[:end-start]
            music[start:end, 1] += note[:end-start] * .94
        # Duck the original procedural score under actual speech.
        active = np.max(np.abs(voice), axis=1) > .006
        music[active] *= .35
        t = np.arange(n, dtype=np.float32) / SR
        music *= np.minimum(1, np.maximum(0, duration - t))[:, None]
        mix = voice + music
        peak = float(np.max(np.abs(mix))) if n else 0
        mix *= min(1.0, .9 / max(peak, 1e-8))
        for name, samples in (("dialogue", voice), ("music", music), ("premaster", mix)):
            write_wav(folder / (name + ".wav"), samples)
        final = folder / "soundtrack.wav"
        self.ffmpeg(["-y", "-i", folder / "premaster.wav",
                     "-af", f"loudnorm=I=-16:TP=-1.5:LRA=9,apad,atrim=duration={duration:.9f}",
                     "-ar", SR, "-ac", "2", final])
        write_json(folder / "audio_edit.json", {"dialogue": edits, "music_gain": gain,
                                               "music_source": "original_procedural_sparse_piano",
                                               "subtitle_timing": "proportional_text_segments_within_measured_speech_not_forced_alignment"})
        return final, cues

    def _subtitles(self, folder, cues, duration):
        srt = folder / "subtitles.zh.srt"
        srt.write_text("\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}"
                                  for i, c in enumerate(cues, 1)) + "\n", encoding="utf-8")
        width, height = self.output_size
        size = max(20, round(width * .052))
        header = ("[Script Info]\nScriptType: v4.00+\nPlayResX: " + str(width) +
                  "\nPlayResY: " + str(height) + "\nWrapStyle: 0\n\n[V4+ Styles]\n"
                  "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                  "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                  "Alignment, MarginL, MarginR, MarginV, Encoding\n"
                  f"Style: Dialogue,Noto Sans CJK SC,{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H50000000,0,0,0,0,100,100,0,0,1,2,0.5,2,42,42,{round(height*.11)},1\n"
                  f"Style: Disclosure,Noto Sans CJK SC,{max(14,round(size*.5))},&H40FFFFFF,&H00FFFFFF,&H80101010,&H80000000,0,0,0,0,100,100,0,0,1,1,0,7,28,28,28,1\n\n"
                  "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        def escape(text):
            return str(text).replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", r"\N")
        events = "\n".join(f"Dialogue: 0,{stamp(c['start'],True)},{stamp(c['end'],True)},Dialogue,,0,0,0,,{escape(c['text'])}" for c in cues)
        events += f"\nDialogue: 1,0:00:00.00,{stamp(duration,True)},Disclosure,,0,0,0,,AI 生成 · 剧情虚构\n"
        ass = folder / "subtitles.zh.ass"
        ass.write_text(header + events, encoding="utf-8")
        return srt, ass

    def _lip_sync(self, folder):
        selected = [s for s in self.shots if s.get("lip_sync")]
        if not selected or self.mode == "motion_comic":
            return
        for shot in selected:
            self.boundary()
            source = self.receipt_path(shot["video"])
            audio = self.receipt_path(shot["lipsync_audio"])
            signature = fingerprint({"video": sha256(source), "audio": sha256(audio),
                                     "seconds": shot["seconds"], "backend": "LatentSync-1.6"})
            review_signature = fingerprint({"source": signature, "policy": self.review_policy,
                                            "shot": {key: shot.get(key) for key in
                                                     ("id", "seconds", "dialogue", "motion_prompt", "continuity")}})
            target_dir = self.folder / "renders" / shot["id"] / ("lipsync_" + signature[:16])
            target_dir.mkdir(parents=True, exist_ok=True)
            receipt_file = target_dir / "result.json"
            if receipt_file.exists():
                record = read_json(receipt_file)
                target = self.receipt_path(record)
            else:
                target = target_dir / "video.mp4"
                if target.exists():
                    raise ProductionNeedsDecision("A lip-sync output has no proven receipt", "unresolved_submission")
                # Cached media can be re-reviewed with no generator lease or GPU memory gate.
                from studio.comfy import gpu_snapshot
                snapshot = gpu_snapshot([5])
                if snapshot[5]["free_mib"] < 21500 or snapshot[5]["utilization"] >= 10:
                    raise ProductionNeedsDecision("The installed lip-sync adapter requires GPU 5 with 21500 MiB free",
                                                  "lip_sync_gpu_gate",
                                                  {"snapshot": snapshot, "existing_services_preserved": True})
                self.boundary(gpu=True)
                argv = [str(ROOT / "envs/lipsync/bin/python"), str(ROOT / "lipsync/sync.py"),
                        "--video", str(source), "--audio", str(audio), "--output", str(target),
                        "--duration", str(shot["seconds"]), "--fps", "24", "--steps", "20",
                        "--guidance", "1.5", "--seed", "1247"]
                with self.budget.gpu_operation():
                    result = subprocess.run(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, timeout=self.budget.timeout(7200, 1))
                (target_dir / "stdout.log").write_text(result.stdout, encoding="utf-8")
                (target_dir / "stderr.log").write_text(result.stderr, encoding="utf-8")
                if result.returncode:
                    raise ProductionNeedsDecision("Local lip-sync failed", "lip_sync_failed",
                                                  {"shot": shot["id"], "log": str((target_dir / "stderr.log").relative_to(ROOT))})
                raw = json.loads(result.stdout.strip().splitlines()[-1])
                if raw.get("state") != "completed" or sha256(target) != raw.get("output_sha256"):
                    raise ProductionNeedsDecision("Lip-sync completion could not be verified", "lip_sync_failed")
                technical = inspect_video(target, shot["seconds"], self.video_size)
                record = media_receipt(target, source_signature=signature, origin="local_lipsync",
                                       input_video=shot["video"], input_audio=shot["lipsync_audio"],
                                       qa=combine(technical, semantic_required=self.semantic_enabled))
                write_json(receipt_file, record)
            technical = record.get("qa", {}).get("technical", {})
            if technical.get("status") != "pass":
                raise ProductionNeedsDecision("Lip-sync output failed technical validation",
                                              "lip_sync_quality", {"qa": record.get("qa")})
            semantic = record.get("qa", {}).get("semantic")
            current = record.get("review_signature") == review_signature
            if self.semantic_enabled and (not current or (semantic or {}).get("status") not in ("pass", "fail")):
                for number in range(1, self.semantic_max_attempts + 1):
                    self.boundary()
                    started = time.monotonic()
                    semantic = semantic_review(self.evaluator, self.project, shot, target, [source])
                    record.setdefault("semantic_attempts", []).append({
                        "at": time.time(), "invocation_attempt": number, "review_signature": review_signature,
                        "elapsed_seconds": round(time.monotonic() - started, 3), "assessment": semantic})
                    record.update(qa=combine(technical, semantic, semantic_required=True),
                                  review_signature=review_signature,
                                  state="semantic_pending" if semantic.get("status") == "unavailable" else "reviewed")
                    write_json(receipt_file, record)
                    if semantic.get("status") in ("pass", "fail"):
                        break
            qa = combine(technical, semantic, semantic_required=self.semantic_enabled)
            record["qa"] = qa
            write_json(receipt_file, record)
            if qa["status"] == "fail":
                raise ProductionNeedsDecision("Lip-sync output requires local repair", "lip_sync_quality", {"qa": qa})
            if not qa["eligible_for_selection"]:
                shot.update(lip_sync_status="semantic_pending", lip_sync_candidate=record, status="reviewing")
                self.emit("lip_sync_review")
                raise ProductionNeedsDecision("The existing lip-sync output is awaiting semantic review",
                                              "semantic_unavailable",
                                              {"shot": shot["id"], "stage": "lip_sync",
                                               "resume_action": "review_existing_assets_only"})
            shot.update(video=record, video_path=record["path"], video_url=self.media_url(self.receipt_path(record)),
                        lip_sync_status="generated_requires_final_review", qa=qa, status="passed")
            self.emit("lip_sync")

    def _delivery(self, mapping):
        self.emit("edit")
        revision = int(self.project.get("production_revision", 1))
        if revision < 1:
            revision = 1
        signature = fingerprint({"shots": [{"id": s["id"], "seconds": s["seconds"],
                                            "video": s["video"]["sha256"],
                                            "dialogue": s.get("dialogue", []), "lip_sync": bool(s.get("lip_sync")),
                                            "semantic_source": s.get("qa", {}).get("semantic", {})} for s in self.shots],
                                 "audio": self.assets.get("audio", {}),
                                 "format": self.mode, "output": self.output_size,
                                 "revision": revision, "edit_plan": self.project.get("edit_plan"),
                                 "edit_feedback": self.project.get("edit_feedback"),
                                 "music_gain": self.config.get("music_gain", .12), "editor_version": 2,
                                 "semantic_required": self.semantic_enabled,
                                 "semantic_policy": self.review_policy if self.semantic_enabled else None})
        release = self.folder / "release" / f"v{revision}-{signature[:12]}"
        manifest_file = release / "manifest.json"
        if manifest_file.exists():
            cached = read_json(manifest_file)
            if cached.get("source_signature") == signature:
                for artifact in cached["artifacts"]:
                    self.receipt_path(artifact)
                self.emit("delivery_ready", delivery=cached)
                return cached
        # A prior delivered file is never replaced, including an incomplete prior export.
        if release.exists() and any(release.glob("*.mp4")):
            release = release.with_name(release.name + "-retry-" + str(time.time_ns()))
        release.mkdir(parents=True, exist_ok=True)
        mix, cues = self._mix_audio(release, mapping)
        self._lip_sync(release)
        self._assert_semantic_gate()
        duration = sum(s["seconds"] for s in self.shots)
        srt, ass = self._subtitles(release, cues, duration)
        segments, sources = [], []
        width, height = self.output_size
        for shot in self.shots:
            self.boundary()
            source = self.receipt_path(shot["video"])
            clip = release / ("segment_" + shot["id"] + ".mp4")
            self.ffmpeg(["-n", "-i", source, "-an", "-t", shot["seconds"],
                         "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=24",
                         "-frames:v", round(shot["seconds"] * 24),
                         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", clip])
            segments.append(clip)
            sources.append({"shot": shot["id"], "video": shot["video"], "image": shot["image"],
                            "seconds": shot["seconds"], "segment": media_receipt(clip)})
        listing = release / "concat.txt"
        listing.write_text("\n".join("file '" + str(p) + "'" for p in segments) + "\n", encoding="utf-8")
        clean = release / "episode_clean.mp4"
        self.ffmpeg(["-n", "-f", "concat", "-safe", "0", "-i", listing, "-i", mix,
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                     "-b:a", "192k", "-ar", SR, "-ac", "2", "-t", duration,
                     "-movflags", "+faststart", clean])
        final = release / "episode.mp4"
        self.ffmpeg(["-n", "-i", clean, "-vf", "ass=" + str(ass) + ":fontsdir=" + str(ROOT / "assets/fonts"),
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-c:a", "copy", "-movflags", "+faststart", final])
        self.emit("final_technical_review")
        technical = {"clean": inspect_video(clean, duration, self.output_size, require_audio=True),
                     "subtitled": inspect_video(final, duration, self.output_size, require_audio=True)}
        write_json(release / "technical_qa.json", technical)
        if any(value["status"] != "pass" for value in technical.values()):
            raise ProductionNeedsDecision("An exported film failed its final technical gate", "delivery_quality",
                                          {"checks": technical})
        first = Image.open(self.receipt_path(self.shots[0]["image"])).convert("RGB")
        first = first.resize((width, height))
        overlay = Image.new("RGBA", first.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((0, int(height * .76), width, height), fill=(8, 15, 24, 205))
        title = str(self.episode.get("title") or self.project.get("title") or self.slug)
        draw.text((round(width * .07), round(height * .8)), title,
                  font=font(min(round(width * .08), round(width * .85 / max(1, len(title))))),
                  fill=(245, 240, 224, 255))
        draw.text((round(width * .07), round(height * .93)), "AI 生成 · 剧情虚构",
                  font=font(max(16, round(width * .026))), fill=(210, 212, 220, 255))
        cover = release / "cover.jpg"
        Image.alpha_composite(first.convert("RGBA"), overlay).convert("RGB").save(cover, quality=93)
        timeline = release / "timeline.json"
        write_json(timeline, {"fps": 24, "duration_seconds": duration, "shots": sources,
                              "audio": media_receipt(mix), "subtitles": cues,
                              "edit_feedback": self.project.get("edit_feedback"),
                              "edit_plan": self.project.get("edit_plan", {})})
        semantics = [s.get("qa", {}).get("semantic", {}) for s in self.shots]
        semantic_status = "pass" if semantics and all(s.get("status") == "pass" for s in semantics) else "review_required"
        artifacts = [media_receipt(p) for p in (final, clean, srt, ass, cover, mix, timeline,
                                               release / "technical_qa.json", release / "audio_edit.json")]
        delivery = {"schema": "autonomous_factory.delivery.v1", "title": title,
                    "project_id": self.ident, "slug": self.slug, "revision": revision,
                    "source_signature": signature, "created_at": time.time(),
                    "status": "awaiting_producer_final_review", "format": self.mode,
                    "path": str(final.relative_to(ROOT)), "url": self.media_url(final),
                    "video_url": self.media_url(final), "mp4_url": self.media_url(final),
                    "clean_url": self.media_url(clean), "subtitle_url": self.media_url(srt),
                    "cover_url": self.media_url(cover), "timeline_url": self.media_url(timeline),
                    "manifest_url": self.media_url(release / "manifest.json"),
                    "duration_seconds": duration, "fps": 24, "resolution": list(self.output_size),
                    "source_image_resolution": list(self.image_size),
                    "artifacts": artifacts, "sources": sources, "technical_qa": technical,
                    "semantic_status": semantic_status, "budget": self.budget.snapshot(),
                    "capabilities": self.capabilities(),
                    "limitations": {
                        "motion": "procedural_camera_motion; illustrated stills with camera moves, no generated character acting or lip sync"
                                  if self.mode == "motion_comic" else "local_diffusion_video; consult each shot's QA and lip-sync records",
                        "resolution": "Delivery dimensions are postproduction resampling/cropping of the recorded source dimensions.",
                        "semantic": "Sampled VLM review is separate from technical decode checks; final producer review is still required.",
                        "vbench": "VBench and DSPy optimization are not claimed by this renderer.",
                        "subtitles": "Timing follows measured speech spans with proportional text segmentation; no forced-alignment guarantee."}}
        write_json(release / "manifest.json", delivery)
        self.emit("delivery_ready", delivery=delivery)
        return delivery

