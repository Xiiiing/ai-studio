import test from 'node:test';
import assert from 'node:assert/strict';
import { productionProfileFields, applyProductionProfile } from '../ui/factory/production-profile.js';
import { createForm } from '../ui/factory/views.js';

const catalog = { presets: { legacy15: { label: '经典规格' }, compact6: { label: '六镜短片' }, ensemble10: { label: '十镜短片' } } };

test('profile choices come from the backend catalog and keep the legacy default', () => {
  const html = createForm(catalog);
  assert.match(html, /name="production_preset"/);
  assert.match(html, /value="legacy15" selected/);
  assert.ok(html.includes('六镜短片') && html.includes('十镜短片') && html.includes('voice_id'));
  assert.ok(!productionProfileFields({ presets: { bad: { label: '<img onerror=alert(1)>' } } }).includes('<img onerror'));
});
test('new presets do not accidentally inherit the old 60 second field', () => {
  const payload = applyProductionProfile({ duration_seconds: 60, brief: 'fixture' }, { production_preset: 'compact6' }, catalog);
  assert.equal(payload.duration_seconds, undefined);
  assert.equal(payload.production_profile.preset, 'compact6');
  const legacy = applyProductionProfile({ duration_seconds: 75 }, {}, catalog);
  assert.equal(legacy.duration_seconds, 75);
  assert.equal(legacy.production_profile.preset, 'legacy15');
});
test('explicit unequal shot plan and stable identities are sent without gender inference', () => {
  const payload = applyProductionProfile({}, { production_preset: 'compact6', profile_shot_seconds: '3, 4,5,6,5,7',
    profile_characters: '[{"character_id":"person_A","voice_id":"deep_voice"}]' }, catalog);
  assert.equal(payload.production_profile.duration_seconds, 30);
  assert.equal(payload.production_profile.shot_count, 6);
  assert.deepEqual(payload.production_profile.characters, [{ character_id: 'person_A', voice_id: 'deep_voice' }]);
});
test('invalid profile drafts remain local and never submit an invented preset', () => {
  for (const values of [{ production_preset: 'unknown' }, { profile_characters: 'not-json' }, { profile_characters: '{}' }, { profile_shot_seconds: '5,NaN,6' }]) {
    assert.throws(() => applyProductionProfile({}, values, catalog));
  }
});
