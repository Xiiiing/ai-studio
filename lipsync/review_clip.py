#!/usr/bin/env python3
"""CPU-only evidence extraction for a real Wan clip and an unadopted lip-sync candidate."""
import argparse, json, math, os, re, subprocess, time
from pathlib import Path
from runtime import ROOT, LIPSYNC, MODELS, configure_environment, media_info, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default="the-last-stop")
    parser.add_argument("--shot", required=True)
    parser.add_argument("--take", default="lipsync_quality_01")
    parser.add_argument("--source-take", default="quality_01")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,60}", args.episode):
        parser.error("Invalid episode")
    if not re.fullmatch(r"S[0-9]{2,3}", args.shot):
        parser.error("Invalid shot")
    for value in [args.take, args.source_take]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,60}", value):
            parser.error("Invalid take")
    ffmpeg = configure_environment()
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import cv2
    import numpy as np
    from scipy.signal import correlate, correlation_lags
    from PIL import Image, ImageDraw, ImageOps
    import onnxruntime as ort
    from insightface.model_zoo.model_zoo import ModelRouter

    folder = ROOT / "production" / args.episode
    definition = json.loads((folder / "episode.json").read_text())
    shot = next(s for s in definition["shots"] if s["id"] == args.shot)
    target_frames = round(shot["seconds"] * 24)
    source_dir = folder / "renders" / args.shot / args.source_take
    candidate_dir = folder / "renders" / args.shot / args.take
    source = source_dir / "video.mp4"
    candidate = candidate_dir / "video.mp4"
    audio = folder / "audio/lipsync" / (args.shot + ".wav")
    source_record = json.loads((source_dir / "result.json").read_text())
    candidate_record = json.loads((candidate_dir / "result.json").read_text())
    selected_before = json.loads((folder / "renders" / args.shot / "selected.json").read_text())
    source_sha, candidate_sha, audio_sha = sha256(source), sha256(candidate), sha256(audio)
    if source_record["sha256"] != source_sha or source_record.get("origin") != "new_local_inference":
        raise RuntimeError("Source is not the recorded original local-generation take")
    if candidate_record["sha256"] != candidate_sha or candidate_record.get("origin") != "local_lipsync":
        raise RuntimeError("Candidate does not match its successful lip-sync result")
    if candidate_record["input_video"]["sha256"] != source_sha or candidate_record["input_audio"]["sha256"] != audio_sha:
        raise RuntimeError("Candidate uses different video or audio")
    review_dir = LIPSYNC / "reviews" / (args.shot + "_" + args.take)
    review_dir.mkdir(parents=True, exist_ok=True)
    report_path = review_dir / "review.json"
    if report_path.exists():
        existing = json.loads(report_path.read_text())
        if existing.get("source_sha256") == source_sha and existing.get("candidate_sha256") == candidate_sha and existing.get("technical_passed"):
            print(json.dumps({"state": "review_evidence_reused", "report": str(report_path)}), flush=True)
            return
        raise RuntimeError("Different or incomplete review exists; inspect it before reusing this review path")

    source_info, candidate_info = media_info(source), media_info(candidate)
    if candidate_info["frames"] != target_frames or abs(candidate_info["fps"] - 24) > 0.001:
        raise RuntimeError("Candidate does not match the timeline")
    if (source_info["width"], source_info["height"]) != (candidate_info["width"], candidate_info["height"]):
        raise RuntimeError("Unexpected source/candidate geometry change")
    for label, path in [("source", source), ("candidate", candidate)]:
        cmd = [str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0"]
        if label == "candidate":
            cmd += ["-map", "0:a:0"]
        cmd += ["-f", "null", "-"]
        check = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        (review_dir / (label + "_full_decode.log")).write_text(check.stdout + check.stderr)
        if check.returncode:
            raise RuntimeError(label + " full decode failed")

    indices = list(range(0, target_frames, 6))
    second_indices = [round(t * 24) for t in range(math.ceil(shot["seconds"])) if t < shot["seconds"]]
    needed = set(indices + second_indices + [target_frames - 1])
    def read_all(path):
        cap = cv2.VideoCapture(str(path))
        frames, count, previous, changes, dark = {}, 0, None, [], []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            tiny = cv2.resize(frame, (180,320)).astype(np.float32)
            if previous is not None:
                changes.append(float(np.abs(tiny-previous).mean()))
            previous = tiny
            if float(tiny.max()) < 3:
                dark.append(count)
            if count in needed:
                frames[count] = frame
            count += 1
        cap.release()
        return frames, {"decoded_frames":count, "mean_adjacent_frame_absolute_difference":float(np.mean(changes)) if changes else 0, "max_adjacent_frame_absolute_difference":float(np.max(changes)) if changes else 0, "near_black_frame_indices":dark}
    source_frames, source_stats = read_all(source)
    candidate_frames, candidate_stats = read_all(candidate)
    if source_stats["decoded_frames"] < target_frames or candidate_stats["decoded_frames"] != target_frames:
        raise RuntimeError("Frame decoder did not reach required length")
    if candidate_stats["near_black_frame_indices"]:
        raise RuntimeError("Candidate contains an almost entirely black frame")

    def pcm(path):
        raw = subprocess.check_output([str(ffmpeg), "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], timeout=60)
        return np.frombuffer(raw, dtype="<f4").astype(np.float64)
    reference_audio, rendered_audio = pcm(audio), pcm(candidate)
    n = min(len(reference_audio),len(rendered_audio))
    first, second = reference_audio[:n], rendered_audio[:n]
    values = correlate(second, first, mode="full", method="fft")
    lags = correlation_lags(n,n,mode="full")
    nearby = np.flatnonzero(np.abs(lags)<=1600)
    peak = nearby[np.argmax(values[nearby])]
    lag = float(lags[peak]/16000)
    correlation = float(values[peak]/np.sqrt(np.sum(first**2)*np.sum(second**2)))
    audio_check = {"scope":"output audio track against the aligned source WAV; not a lip-motion synchronization score", "lag_seconds":lag, "correlation":correlation, "decoded_audio_seconds":len(rendered_audio)/16000}
    if abs(lag)>.03 or correlation<.95 or abs(len(rendered_audio)/16000-shot["seconds"])>.04:
        raise RuntimeError("Output speech track alignment check failed: "+str(audio_check))

    detector_file = MODELS / "checkpoints/auxiliary/models/buffalo_l/det_10g.onnx"
    manifest = json.loads((MODELS / "manifest.json").read_text())
    expected = next(x for x in manifest["files"] if x["path"].endswith("/det_10g.onnx"))
    if sha256(detector_file) != expected["sha256"]:
        raise RuntimeError("Review detector checksum mismatch")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    opts.inter_op_num_threads = 1
    detector = ModelRouter(str(detector_file)).get_model(providers=["CPUExecutionProvider"], sess_options=opts)
    detector.prepare(ctx_id=-1, input_size=(640,640), det_thresh=.5)
    if detector.session.get_providers() != ["CPUExecutionProvider"]:
        raise RuntimeError("Review face detector is not CPU-only")
    def detect(frame):
        boxes, landmarks = detector.detect(frame,max_num=0,metric="default")
        if boxes is None or not len(boxes):
            return None, None, 0
        choice = int(np.argmax((boxes[:,2]-boxes[:,0])*(boxes[:,3]-boxes[:,1])))
        return boxes[choice].tolist(), landmarks[choice].tolist() if landmarks is not None else None, len(boxes)
    def rgb(frame):
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    def square_box(box, scale):
        cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
        side=max(box[2]-box[0],box[3]-box[1])*scale
        return tuple(round(v) for v in [cx-side/2,cy-side/2,cx+side/2,cy+side/2])
    def tile(pic, size):
        return ImageOps.pad(pic.convert("RGB"),size,color="#141920",method=Image.Resampling.LANCZOS)
    rows = math.ceil(len(indices)/6)
    face_input = Image.new("RGB",(1320,244*rows),"#141920")
    face_output = Image.new("RGB",face_input.size,"#141920")
    mouth_input = Image.new("RGB",(1320,194*rows),"#141920")
    mouth_output = Image.new("RGB",mouth_input.size,"#141920")
    detectors, missing = [], []
    for k,index in enumerate(indices):
        before,after=source_frames[index],candidate_frames[index]
        box,kps,count=detect(before)
        after_box,_,after_count=detect(after)
        detectors.append({"frame":index,"seconds":index/24,"source_face_count":count,"candidate_face_count":after_count,"source_bbox":box,"candidate_bbox":after_box})
        if box is None:
            missing.append(index)
            box=[0,0,source_info["width"],source_info["height"],0]
        facial=square_box(box,1.15)
        if kps is not None:
            corners=np.asarray(kps[3:5]); center=corners.mean(axis=0)
            w=max(float(np.linalg.norm(corners[0]-corners[1]))*2.4,(box[2]-box[0])*.46)
            mouth=tuple(round(v) for v in [center[0]-w/2,center[1]-w*.36,center[0]+w/2,center[1]+w*.40])
        else:
            mouth=(box[0],box[1]+(box[3]-box[1])*.55,box[2],box[3])
        for sheet,frame,crop,height in [(face_input,before,facial,220),(face_output,after,facial,220),(mouth_input,before,mouth,170),(mouth_output,after,mouth,170)]:
            x=(k%6)*220; y=(k//6)*(height+24)
            sheet.paste(tile(rgb(frame).crop(crop),(220,height)),(x,y+24))
            ImageDraw.Draw(sheet).text((x+6,y+6),"%.2f s | frame %d"%(index/24,index),fill="white")
    for name,sheet in [("face_input_timeline.jpg",face_input),("face_candidate_timeline.jpg",face_output),("mouth_input_timeline.jpg",mouth_input),("mouth_candidate_timeline.jpg",mouth_output)]:
        sheet.save(review_dir/name,quality=96)
    comparisons=[]
    contact=Image.new("RGB",(1104,522*math.ceil(len(second_indices)/2)),"#141920")
    for k,index in enumerate(second_indices):
        left,right=rgb(source_frames[index]),rgb(candidate_frames[index])
        left_path=review_dir/("input_%ds.png"%k)
        right_path=review_dir/("candidate_%ds.png"%k)
        left.save(left_path);right.save(right_path)
        pair=Image.new("RGB",(left.width+right.width,left.height))
        pair.paste(left,(0,0));pair.paste(right,(left.width,0))
        pair_path=review_dir/("compare_%ds.png"%k)
        pair.save(pair_path)
        x=(k%2)*552;y=(k//2)*522
        contact.paste(tile(pair,(552,492)),(x,y+30))
        ImageDraw.Draw(contact).text((x+8,y+8),"%d s | original Wan / lip-sync candidate"%k,fill="white")
        comparisons.append({"seconds":index/24,"path":str(pair_path),"sha256":sha256(pair_path)})
    contact.save(review_dir/"contact_sheet.jpg",quality=96)
    selected_after=json.loads((folder/"renders"/args.shot/"selected.json").read_text())
    if sha256(source)!=source_sha or sha256(candidate)!=candidate_sha or sha256(audio)!=audio_sha:
        raise RuntimeError("A review input changed during inspection")
    report={"kind":"formal_wan_lipsync_candidate_review","episode":args.episode,"shot":args.shot,"dialogue":shot.get("dialogue",[]),
            "state":"technical_checks_passed_visual_review_pending","source":str(source),"source_sha256":source_sha,
            "source_receipt":source_record,"candidate":str(candidate),"candidate_sha256":candidate_sha,
            "candidate_receipt":candidate_record,"audio":str(audio),"audio_sha256":audio_sha,
            "source_media":source_info,"candidate_media":candidate_info,"source_motion_statistics":source_stats,
            "candidate_motion_statistics":candidate_stats,"audio_track_alignment":audio_check,"full_decode":"passed",
            "technical_passed":True,"perceptual_review_required":True,"lip_sync_quality_verified":False,
            "source_is_generated_video":True,"static_benchmark":False,"selection_changed_by_review":False,
            "selection_unchanged_during_review":selected_before==selected_after,"currently_selected_candidate":selected_after.get("sha256")==candidate_sha,
            "face_review_detector":{"file":str(detector_file),"sha256":expected["sha256"],"providers":detector.session.get_providers(),"pretrained_weight_policy":"non-commercial research purposes only"},
            "sampled_face_detections":detectors,"source_face_detection_missing_frames":missing,"comparisons":comparisons,
            "contact_sheet":str(review_dir/"contact_sheet.jpg"),"face_input_timeline":str(review_dir/"face_input_timeline.jpg"),
            "face_candidate_timeline":str(review_dir/"face_candidate_timeline.jpg"),"mouth_input_timeline":str(review_dir/"mouth_input_timeline.jpg"),
            "mouth_candidate_timeline":str(review_dir/"mouth_candidate_timeline.jpg"),"reviewed_at":time.time(),"gpu_used_for_review":False}
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"shot":args.shot,"technical_passed":True,"report":str(report_path),"contact_sheet":report["contact_sheet"],"audio_track_alignment":audio_check,"source_face_detection_missing_frames":missing,"currently_selected_candidate":report["currently_selected_candidate"],"lip_sync_quality_verified":False}),flush=True)

if __name__=="__main__":
    main()
