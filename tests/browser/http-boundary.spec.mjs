import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { once } from 'node:events';

let child, endpoint, projectId;
test.beforeAll(async () => {
  child = spawn(process.env.FACTORY_TEST_PYTHON || 'python', ['scripts/browser_http_fixture.py'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { PATH: process.env.PATH, PYTHONPATH: process.env.PYTHONPATH || '', PYTHONUNBUFFERED: '1' }
  });
  let errors = '';
  child.stderr.on('data', data => { errors = (errors + data).slice(-4000); });
  const lines = createInterface({ input: child.stdout });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new Error('HTTP fixture did not start: ' + errors)); }, 12000);
    const failed = code => { clearTimeout(timer); reject(new Error('HTTP fixture exited ' + code + ': ' + errors)); };
    child.once('exit', failed);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    lines.on('line', line => {
      if (!line.startsWith('HTTP_BROWSER_FIXTURE_READY ')) return;
      clearTimeout(timer); child.off('exit', failed);
      resolve(JSON.parse(line.slice('HTTP_BROWSER_FIXTURE_READY '.length)));
    });
  });
  endpoint = 'http://127.0.0.1:' + ready.port;
  projectId = ready.project_id;
});
test.afterAll(async () => {
  if (child && child.exitCode === null) {
    const stopped = once(child, 'exit'); child.kill('SIGTERM'); await stopped;
  }
});
test.beforeEach(async ({ page }) => {
  await page.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    return ['127.0.0.1', 'localhost'].includes(host) ? route.continue() : route.abort('blockedbyclient');
  });
});

test('real Python boundary admits workbench and legacy on its actual loopback port', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  expect((await page.goto(endpoint + '/')).status()).toBe(200);
  await expect(page.locator('#project-list')).toContainText('HTTP 边界隔离测试');
  await page.locator('#project-list [data-id="' + projectId + '"]').click();
  await expect(page.locator('#workspace h1')).toHaveText('HTTP 边界隔离测试');
  await page.locator('.main-nav [data-action="models"]').click();
  await expect(page.locator('#workspace h1')).toHaveText('模型与连接');
  expect(errors).toEqual([]);
  expect((await page.goto(endpoint + '/legacy')).status()).toBe(200);
});

test('real Python media response supports browser decoding and range playback', async ({ page }) => {
  await page.goto(endpoint + '/');
  await page.evaluate(async url => {
    const video = document.createElement('video');
    video.id = 'http-media-smoke'; video.muted = true; video.controls = true;
    video.src = url; document.body.append(video); await video.play();
  }, endpoint + '/media/production/http-fixture/clip.mp4');
  await page.waitForFunction(() => document.querySelector('#http-media-smoke').currentTime > .2);
  expect(await page.locator('#http-media-smoke').evaluate(v => v.readyState >= 2 && !v.paused)).toBeTruthy();
});

test('cross-origin browser POST cannot reach the legacy production launcher', async ({ page }) => {
  await page.goto('/'); // Different loopback port: deliberately a different origin.
  const url = endpoint + '/api/start';
  const rejected = page.waitForResponse(response => response.url() === url);
  await page.evaluate(url => fetch(url, { method: 'POST', mode: 'no-cors', headers: { 'Content-Type': 'text/plain' }, body: '{}' }).catch(() => null), url);
  expect((await rejected).status()).toBe(403);
});
