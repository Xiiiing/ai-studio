import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';

const id = 'dd0011223344';
function fixture() {
  return {id, record_kind:'drama', title:'车站的约定 · 整剧演示', revision:4, episode_count:3,
    duration_seconds:60, format:'animation', status:'ready', plan_version:1, plans:[], batches:[],
    episodes:[1,2,3].map(n=>({id:`ee001122334${n}`, episode_number:n, title:`第${n}集 · 旧信`,
      status:'draft', progress:0, plan_version:1, outline:{synopsis:'林在车站发现旧信，并沿着信件寻找多年以前的约定。'}}))};
}

for (const width of [1440,390]) test(`whole drama selection and batch controls at ${width}`, async ({page})=>{
  await page.setViewportSize({width,height:1000});
  const p=fixture(), calls=[];
  await page.route(`**/api/factory/projects/${id}/workspace`, r=>r.fulfill({json:{project:{id, title:p.title,record_kind:'drama'},revision:p.revision,operator:{},flow:{},workflow:{}}}));
  await page.route('**/api/factory/dramas/**', async route=>{
    if(route.request().method()==='POST') {
      const body=route.request().postDataJSON(); calls.push(body); p.revision++;
      if(route.request().url().endsWith('/generate')) {
        p.episodes.forEach(e=>{if(body.episode_numbers.includes(e.episode_number))e.status='queued';});
        p.batches=[{id:'bb0011223344',episode_numbers:body.episode_numbers,plan_version:1,members:p.episodes.filter(e=>e.status==='queued').map(e=>({number:e.episode_number,status:e.status}))}];
      }
    }
    return route.fulfill({json:p});
  });
  await page.goto('/#project='+id);
  await expect(page.getByRole('heading',{name:p.title})).toBeVisible();
  await page.getByRole('checkbox',{name:'选择第1集'}).check();
  await page.getByRole('checkbox',{name:'选择第3集'}).check();
  await page.getByRole('button',{name:'生成所选 2 集'}).click();
  await expect.poll(()=>calls.length).toBe(1);
  expect(calls[0].episode_numbers).toEqual([1,3]);
  expect(calls[0].expected_revision).toBe(4);
  await page.getByRole('button',{name:'生成批次',exact:true}).click();
  await expect(page.getByText('第1、3集 · 设定v1')).toBeVisible();
  await page.getByRole('button',{name:'暂停批次',exact:true}).click();
  await expect.poll(()=>calls.length).toBe(2);
  expect(calls[1].action).toBe('pause');
  expect(calls[1].batch_id).toBe('bb0011223344');
  await page.getByRole('button',{name:'剧集',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await fs.mkdir('docs/images/drama-010',{recursive:true});
  await page.screenshot({path:`docs/images/drama-010/episodes-${width}.png`,fullPage:true});
});

test('new project imports a UTF-8 script before planning',async({page})=>{
  let submitted;
  await page.route('**/api/factory/dramas',async r=>{
    submitted=r.request().postDataJSON();
    await r.fulfill({status:201,json:{id,record_kind:'drama',revision:1}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'新建制作',exact:true}).first().click();
  const form=page.getByRole('dialog');
  await form.getByLabel('剧名',{exact:true}).fill('导入剧本演示');
  await form.getByLabel('创作起点').selectOption('script');
  await form.getByLabel('导入剧本').setInputFiles({name:'剧本.md',mimeType:'text/markdown',buffer:Buffer.from('第一集：两位成年人在车站重逢，并找到一封旧信。','utf8')});
  await expect(form.getByLabel('剧本正文')).toContainText('第一集');
  await form.getByRole('button',{name:'建立整剧项目'}).click();
  await expect.poll(()=>submitted?.source_name).toBe('剧本.md');
  expect(submitted.source_text).toContain('车站重逢');
});
