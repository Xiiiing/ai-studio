import { test, expect } from '@playwright/test';

const id = '012345abcdef';
test.beforeEach(async ({ request, page }) => {
  await request.post('/__test/reset');
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
});
async function openTeam(page) {
  await page.goto('/');
  await page.locator('#project-list [data-id="' + id + '"]').click();
  await page.locator('.tabbar [data-tab="team"]').click();
  await expect(page.locator('[aria-label="团队指挥台"]')).toBeVisible();
}

test('team cockpit shows bounded evidence, manual final review and responsive layouts', async ({ page, request }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await openTeam(page);
  await expect(page.locator('[data-team-task]')).toHaveCount(6);
  await expect(page.locator('[data-team-task="production"]')).toContainText('执行已返回');
  await expect(page.locator('.team-cockpit')).toContainText('节点返回 ≠ 审核通过');
  await expect(page.locator('.supervisor-panel')).toContainText('终审建议已记录');
  await expect(page.locator('.supervisor-panel')).toContainText('不代表已观看成片');
  await expect(page.locator('.delivery-readiness')).toContainText('平台审核未核验');
  await page.locator('[data-flow-detail="delivery-materials"] summary').click();
  await expect(page.locator('.handoff-files a')).toHaveCount(1);
  await page.screenshot({ path: 'runtime/browser-report/team-cockpit-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('[data-team-task="research"]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: 'runtime/browser-report/team-cockpit-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await request.post('/__test/failure', { data: { route: '/api/factory/projects/' + id + '/flow' } });
  await page.locator('[data-action="refresh"]').click();
  await expect(page.locator('.team-cockpit')).toContainText('旧交付链接已停用');
  await expect(page.locator('.handoff-files a')).toHaveCount(0);
  const calls = await (await request.get('/__test/calls')).json();
  expect(calls.filter(call => call.method === 'POST' && call.path.startsWith('/api/'))).toEqual([]);
  expect(errors).toEqual([]);
});

test('saved optional producer binding enables explicit delegated creation, not final approval', async ({ page, request }) => {
  await page.goto('/');
  await page.locator('[data-action="new-project"]').first().click();
  await expect(page.locator('#project-dialog option[value="delegated"]')).toBeDisabled();
  await page.locator('#project-dialog [data-action="close-dialog"]').first().click();
  await page.locator('.main-nav [data-action="models"]').click();
  await expect(page.locator('#role-executive_producer')).toHaveValue('');
  await page.locator('#role-executive_producer').selectOption('writer');
  await page.locator('[data-model-action="save"]').click();
  await expect(page.locator('[data-model-action="save"]')).toBeDisabled();
  await page.locator('[data-action="new-project"]').first().click();
  await expect(page.locator('#project-dialog option[value="delegated"]')).toBeEnabled();
  await page.locator('#project-dialog [name="team_mode"]').selectOption('delegated');
  await page.locator('#project-dialog [name="brief"]').fill('一位成年修表师与成年邻居合作揭开旧时钟的秘密，误会解除，结局温暖。');
  await expect(page.locator('#project-dialog')).toContainText('文本请求可能按所选模型计费');
  await page.screenshot({ path: 'runtime/browser-report/team-delegated-create.png', fullPage: true });
  await page.locator('#project-dialog button[type="submit"]').click();
  await expect(page.locator('#project-dialog')).not.toBeVisible();
  await expect(page.locator('form[data-form="final"]')).toBeVisible();
  const calls = await (await request.get('/__test/calls')).json();
  const create = calls.find(call => call.path === '/api/factory/projects' && call.method === 'POST');
  expect(create?.body?.team_mode).toBe('delegated');
  expect(calls.filter(call => call.method === 'POST' && /decisions|cloud|generate|\/test$/.test(call.path))).toEqual([]);
});
