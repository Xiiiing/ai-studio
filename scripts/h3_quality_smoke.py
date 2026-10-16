#!/usr/bin/env python3
"""Generate one bounded, isolated H3 shot and retain measured technical evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factory.gpu_service import validate_graph
from factory.quality import inspect_video
from factory.scheduler import Scheduler, Budget
from factory.video_providers import normalize_video_profile
from studio.core import ROOT, write_json, sha256
from studio.graphs import h3_video
from studio.h3 import frame_count, graph_recipe

HUMAN_PROMPT = (
    'Cinematic live-action medium shot. The adult man in the dark blue shirt sits at the cafe table '
    'beside the window, keeping the same face, hairstyle, clothing and white ceramic cup as the first frame. '
    'Over five seconds he looks calmly towards the window, blinks naturally, then turns his gaze slightly '
    'toward the camera with a subtle warm smile. His hands remain resting around the cup on the table, '
    'without lifting it. Static camera, continuous single take, subtle natural breathing, soft warm daylight, '
    'realistic skin texture, shallow depth of field. No cuts, no added people, no text or subtitles.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Public local-quality configuration fragment')
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='Independent directory under this checkout runtime/')
    parser.add_argument('--gpu-cards', type=int, nargs='+', required=True)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--seed', type=int, default=20261016)
    parser.add_argument('--max-wall-seconds', type=float, default=4200)
    parser.add_argument('--max-gpu-seconds', type=float, default=8400)
    parser.add_argument('--prompt-file', type=Path, help='Optional plain text shot prompt')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'runtime').resolve()):
        parser.error('Test output must be under this checkout runtime directory')
    if len(set(args.gpu_cards)) != len(args.gpu_cards) or len(args.gpu_cards) < 2 or any(not 0 <= c < 32 for c in args.gpu_cards):
        parser.error('Select at least two distinct GPU indices')
    config = json.loads(args.config.read_text())
    profile = normalize_video_profile(config['video_profile'])
    if profile['provider'] != 'comfyui_h3':
        parser.error('This isolated test requires a local H3 profile')
    geometry = config['production_geometry']
    prompt = args.prompt_file.read_text() if args.prompt_file else HUMAN_PROMPT
    budget = Budget({'max_wall_seconds': args.max_wall_seconds, 'max_gpu_seconds': args.max_gpu_seconds})
    scheduler = Scheduler({'gpu_cards': args.gpu_cards, 'gpu_wait_seconds': 60,
                           'h3_reserve_vram_gib': config.get('h3_reserve_vram_gib', 12)}, budget)
    started = time.monotonic()
    # A unique tag keeps independent test output, process logs and submission state together.
    tag = 'quality-' + output.name
    with scheduler.worker(tag, h3=True) as worker:
        first_frame = worker.copy_input(args.image)
        graph = h3_video(prompt, args.seed, first_frame, geometry['video_width'], geometry['video_height'],
                         args.seconds, prefix='local_quality/human', profile=profile)
        validate_graph(graph, {first_frame}, 'video')
        video = worker.execute(graph, output, 'video', timeout=budget.timeout(args.max_wall_seconds, gpu_count=2))
        technical = inspect_video(video, expected_seconds=frame_count(args.seconds) / 24,
                                  expected_size=(geometry['video_width'], geometry['video_height']))
        evidence = {'recipe': graph_recipe(graph), 'prompt': prompt, 'seed': args.seed,
                    'reference_sha256': sha256(args.image), 'video_sha256': sha256(video),
                    'physical_gpus': worker.cards, 'technical': technical,
                    'production_services_changed': False}
    evidence.update(budget=budget.snapshot(), elapsed_seconds=round(time.monotonic() - started, 3))
    write_json(output / 'validation.json', evidence)
    print(json.dumps(evidence, ensure_ascii=False))
    if technical['status'] != 'pass':
        raise SystemExit('Generated shot failed technical checks; evidence retained')


if __name__ == '__main__':
    main()
