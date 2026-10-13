"""Bounded deterministic temporal sampling with timestamps, not three-frame QA."""
from __future__ import annotations

import math
from pathlib import Path

import av

POLICY = "uniform_endpoints_and_cut_neighbors.v1"


def sample_times(duration, count=8, cuts=()):
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 180:
        raise ValueError("Evaluation media duration must be in (0,180] seconds")
    if type(count) is not int or not 4 <= count <= 16:
        raise ValueError("Temporal sampling count must be an integer in 4..16")
    end = max(0.0, duration - min(1 / 24, duration / 10))
    points = [end * index / (count - 1) for index in range(count)]
    for value in cuts:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= duration:
            raise ValueError("Invalid cut timestamp")
        points.extend([max(0.0, value - 1 / 24), min(end, value + 1 / 24)])
    # Both endpoint and uniform coverage are preserved. The caller explicitly
    # sees truncation if it supplied more cut boundaries than the bounded cap.
    points = sorted(set(round(point, 6) for point in points))
    return points if len(points) <= 48 else [points[round(i * (len(points) - 1) / 47)] for i in range(48)]


def sample_video(path, count=8, cuts=()):
    path = Path(path)
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("No video stream to sample")
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else float(container.duration or 0) / av.time_base
        fps = float(stream.average_rate or 24)
        times = sample_times(duration, count, cuts)
        samples, next_index, last, decoded = [], 0, None, 0
        for index, frame in enumerate(container.decode(stream)):
            timestamp = float(frame.time) if frame.time is not None else index / fps
            if not math.isfinite(timestamp) or timestamp > 181 or index > 12000:
                raise ValueError("Decoded media exceeds evaluation bounds")
            decoded += 1
            last = (index, timestamp, frame)
            if next_index < len(times) and timestamp + 1e-5 >= times[next_index]:
                image = frame.to_image()
                image.thumbnail((512, 768))
                samples.append({"frame_index": index, "timestamp": round(timestamp, 6), "image": image})
                while next_index < len(times) and timestamp + 1e-5 >= times[next_index]:
                    next_index += 1
        if last and (not samples or samples[-1]["frame_index"] != last[0]):
            image = last[2].to_image()
            image.thumbnail((512, 768))
            if len(samples) >= len(times):
                samples[-1] = {"frame_index": last[0], "timestamp": round(last[1], 6), "image": image}
            else:
                samples.append({"frame_index": last[0], "timestamp": round(last[1], 6), "image": image})
    if not samples:
        raise ValueError("No decodable video samples")
    metadata = {"policy": POLICY, "requested_samples": count, "sampled_frames": len(samples),
                "timestamps": [row["timestamp"] for row in samples], "decoded_frames": decoded,
                "duration_seconds": duration, "cut_boundaries": list(cuts),
                "coverage": "sampled_timestamps_not_exhaustive_motion_or_semantic_validation"}
    return samples, metadata
