"""A media-only retake preserves planning and invalidates observed evidence."""
import subprocess

from factory import llm, shot_continuity
from studio import core
from studio.media import FFMPEG
from tests.test_factory_shot_continuity_contract import actual_cut


def test_changed_actual_cut_is_reviewed_again_with_unchanged_approved_state(actual_cut, monkeypatch):
    project, delivery, colors = actual_cut
    shot = project['script']['shots'][1]
    planned = shot_continuity.request_context(project, shot)
    reviewed = []

    def review(_config, _role, _system, payload, *_args, **_kwargs):
        reviewed.append(payload)
        return {'status': 'pass', 'previous_observed': 'Synthetic sampled color before the cut.',
                'current_observed': 'Synthetic sampled color after the cut.', 'issues': []}

    monkeypatch.setattr(llm, '_request', review)
    original = shot_continuity.verify_delivery(project, delivery, {})
    assert len(reviewed) == 1
    project['script']['shots'][0]['render_revisions'] = {'image': 1, 'video': 1}
    assert shot_continuity.request_context(project, shot) == planned

    # The production renderer may keep the approved script and replace only the
    # media. Both the old evidence and the new evidence must bind actual pixels.
    path = core.ROOT / delivery['artifacts'][0]['path']
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'color=green:size=64x64:rate=24:duration=1',
                    '-f', 'lavfi', '-i', 'color=yellow:size=64x64:rate=24:duration=1',
                    '-filter_complex_threads', '1', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
                    '-map', '[v]', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(path)],
                   check=True, capture_output=True)
    changed_receipt = core.media_receipt(path)
    assert changed_receipt['sha256'] != delivery['artifacts'][0]['sha256']
    changed = shot_continuity.verify_delivery(project, {'artifacts': [changed_receipt]}, {})
    assert len(reviewed) == 2
    assert reviewed[0]['approved_transition'] == reviewed[1]['approved_transition'] == planned
    assert original['media_sha256'] != changed['media_sha256'] == changed_receipt['sha256']
    assert original['transitions'][0]['signature'] != changed['transitions'][0]['signature']
    assert original['transitions'][0]['identity']['context'] == changed['transitions'][0]['identity']['context']
    assert len(colors) == 4 and colors[0] != colors[2] and colors[1] != colors[3]
    assert shot_continuity.verify_delivery(project, {'artifacts': [changed_receipt]}, {}) == changed
    assert len(reviewed) == 2
