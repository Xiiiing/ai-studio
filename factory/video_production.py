"""Cloud video execution inside the existing shot/QA loop, without local GPU leases."""
from __future__ import annotations

import copy
import os
import time

from studio.core import media_receipt, read_json, sha256, write_json
from .minimax_video import VideoFailed, VideoPending, generate_video
from .providers import redact_secrets
from .quality import inspect_image
from .scheduler import ProductionNeedsDecision, ProductionPaused
from . import cloud_budget
from .remote_tasks import ProductionDeferred, phase_for
from studio.graphs import flux_image


class VideoProductionMixin:
    def _remote_video(self, target):
        return target["kind"] == "video" and self.video_connection.get("provider") == "minimax"

    def _guard_remote_submissions(self, target):
        for path in target["folder"].glob("take_*/submission.json"):
            attempt=path.parent/"attempt.json"
            if attempt.exists() and read_json(attempt).get("source_signature") != target["signature"] and not (path.parent/"result.json").exists():
                state=read_json(path)
                history=read_json(path.parent/"history.json") if (path.parent/"history.json").exists() else {}
                if state.get("state") not in {"failed", "completed"} and history.get("status",{}).get("status_str") != "error" and not (path.parent/"submission_error.json").exists():
                    raise ProductionNeedsDecision("旧 ComfyUI 请求尚未确认完成，不能用新配置绕过恢复", "unresolved_submission")
        if self.config.get("gpu_service"):
            from .gpu_client import guard_remote_submissions
            guard_remote_submissions(self.config, target["folder"], self.budget, self.control,
                                     event_callback=lambda event: self.emit("remote_gpu", remote_gpu_execution=event),
                                     current_signature=target["signature"])
        if target["kind"] != "video":
            return
        for path in sorted(target["folder"].glob("take_*/remote_task.json")):
            task = read_json(path)
            # A changed role/model must not hide a task that might still be charging.
            if task.get("status") not in ("failed", "rejected", "downloaded") and task.get("source_signature") != target["signature"]:
                raise ProductionNeedsDecision(
                    "An earlier video task is unresolved and uses different inputs or model settings",
                    "unresolved_video_submission", {"task_id": task.get("task_id"),
                                                    "receipt": str(path.relative_to(self.folder))})

    def _remote_first_frame(self, target, record):
        frame = target["references"][0]
        if not record.get("repair", {}).get("regenerate_keyframe"):
            return frame
        if record.get("repair_keyframe"):
            return self.receipt_path(record["repair_keyframe"])
        from .team_control import is_locked
        if is_locked(self.ident, "image", target["shot"]["id"]):
            raise ProductionNeedsDecision("视频返修需要更换已锁定首帧，请先解锁或调整返修要求", "team_locked_keyframe",
                                          {"shot_id": target["shot"]["id"], "task_id": "image:" + target["shot"]["id"]})
        if self.config.get("image_connection", {}).get("provider", "legacy") != "legacy":
            refs=list(target["references"]) + [self.receipt_path(v) for v in self.assets.get("characters", {}).values()]
            repair=self._target("repair_"+target["key"],target["folder"]/record["take"]/"repair_keyframe",
                                record["repair"].get("prompt_prefix", "") + "\n" + self.style_prompt + " " + target["shot"]["prompt"],refs[:4])
            self._render_targets([repair], "keyframe_repair")
            record["repair_keyframe"]=repair["selected"]
            self._save_attempt(target,record)
            return self.receipt_path(record["repair_keyframe"])
        # A diagnosed action/identity issue may require a new first frame. This is
        # the only local GPU lease in the cloud route; remote polling holds none.
        with self.scheduler.worker(self.slug + "_repair_keyframe", self.control) as worker:
            refs = list(target["references"])
            refs += [self.receipt_path(value) for value in self.assets.get("characters", {}).values()]
            names = [worker.copy_input(path) for path in refs[:4]]
            prompt = ((record["repair"].get("prompt_prefix") or
                       record["repair"].get("prompt_suffix", "")) + "\n" +
                      self.style_prompt + " " + target["shot"]["prompt"] +
                      " Preserve each corresponding character identity and wardrobe.")
            graph = flux_image(prompt, record["seed"], *self.image_size, references=names,
                               prefix=self.slug + "/repair/" + target["key"])
            frame = worker.execute(graph, target["folder"] / record["take"] / "repair_keyframe",
                                   "image", timeout=self.budget.timeout(1800, 1))
            qa = inspect_image(frame, self.image_size)
            if qa["status"] != "pass":
                raise ProductionNeedsDecision("Replacement first frame failed technical QA",
                                              "video_repair_keyframe")
            record["repair_keyframe"] = media_receipt(frame, technical_qa=qa)
            self._save_attempt(target, record)
        return frame

    def _prepare_cloud_targets(self, targets):
        prepared = []
        fresh = []
        for target in targets:
            self.boundary()
            record = target.pop("resume_attempt", None) or self._new_attempt(target)
            frame = self._remote_first_frame(target, record)
            line = cloud_budget.request_line(self.ident, target["key"], record, sha256(frame),
                                             self.video_connection, target["seconds"])
            journal = target["folder"] / record["take"] / "remote_task.json"
            if journal.exists():
                cloud_budget.reconcile_task(self.ident, line, read_json(journal), self.video_connection)
            else:
                fresh.append(line)
            prepared.append((target, record, frame, line))
        batch = cloud_budget.ensure_batch(self.project, self.video_connection, fresh) if fresh else None
        return prepared, batch

    def _render_remote_targets(self, targets, phase):
        ready = []
        prepared, batch = self._prepare_cloud_targets(targets)
        for target, record, frame, line in prepared:
            self._team_admit_target(target)
            self._team_begin_target(target, record)
            self.boundary()
            shot = target.get("shot")
            if shot is not None:
                shot["status"] = "rendering" if record["attempt"] == 1 else "retrying"
            self.emit(phase)
            started = time.monotonic()
            folder = target["folder"] / record["take"]
            def event(task):
                cloud_budget.reconcile_task(self.ident, line, task, self.video_connection)
                record["remote_task"] = task
                self._save_attempt(target, record)
                self.emit(phase_for(task))
            try:
                def authorize(request):
                    cloud_budget.validate_live_profile(self.video_connection)
                    return cloud_budget.reserve(self.ident, batch["id"], line, self.video_connection, request)
                raw, task = generate_video(self.video_connection, record["prompt"], frame, folder,
                                           source_signature=target["signature"], check=self.boundary,
                                           timeout=self.budget.timeout(self.video_connection["max_wait_seconds"]),
                                           event=event, before_submit=authorize,
                                           step=bool(self.config.get("remote_step", False)))
                # Keep the original artifact and adapt fps/size/duration to the
                # existing timeline. Failed conversion resumes this same download.
                width, height = self.video_size
                temporary = folder / "normalized.part.mp4"
                target_path = folder / "video.mp4"
                self.ffmpeg(["-y", "-i", raw, "-vf",
                             f"fps=24,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
                             "-frames:v", round(target["seconds"] * 24), "-an",
                             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", temporary],
                            limit=300)
                os.replace(temporary, target_path)
                receipt = media_receipt(target_path, provider="minimax",
                                        profile=self.video_connection["profile"], model=task["model"],
                                        task_id=task["task_id"], file_id=task["file_id"],
                                        cloud_intent_id=line["intent_id"], authorization=task.get("authorization"),
                                        provider_receipt=str((folder / "remote_task.json").relative_to(self.folder.parent.parent)),
                                        original=media_receipt(raw), expected_seconds=target["seconds"],
                                        generation_kind="cloud_diffusion_video")
                write_json(folder / "result.json", receipt)
                record.update(state="generated", output=receipt,
                              elapsed_seconds=round(time.monotonic() - started, 3))
                ready.append((target, record, receipt))
            except VideoFailed as exc:
                record.update(state="failed", error=str(exc),
                              qa={"status": "fail", "issues": [{"code": "generation", "detail": str(exc)}]})
                if shot is not None:
                    shot["status"] = "retrying"
            except VideoPending as exc:
                raise ProductionNeedsDecision(str(exc), exc.code, exc.details) from None
            except (ProductionPaused, ProductionNeedsDecision, ProductionDeferred):
                raise
            except Exception as exc:
                record["error"] = redact_secrets(str(exc), self.video_connection)[:1000]
                # A local/configuration failure is not proof that a paid task failed.
                # Preserve this attempt and its source for resume, including no-ID intent.
                raise ProductionNeedsDecision(
                    "Video processing needs attention; the existing task and download are preserved",
                    "video_processing_pending", {"error": record["error"],
                                                 "receipt": str((folder / "remote_task.json").relative_to(self.folder))}) from None
            finally:
                self._save_attempt(target, record)
                self.emit(phase)
            self._team_after_target(target)
        return ready
