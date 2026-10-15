import { h } from './format.js';

const PATHS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4m-4 6h4m10-6h4m-4 6h4"/>',
  inbox: '<path d="m4 4-2 12v4h20v-4L20 4Z"/><path d="M2 14h6l2 3h4l2-3h6"/>',
  flow: '<rect x="2" y="2" width="7" height="6" rx="1.5"/><rect x="15" y="16" width="7" height="6" rx="1.5"/><path d="M5.5 8v9a2 2 0 0 0 2 2H15M9 5h8a2 2 0 0 1 2 2v9"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  alert: '<path d="M12 3 2 21h20L12 3Z"/><path d="M12 9v5m0 3v.2"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2m3-11a3 3 0 0 0 0-6m3 17v-2a6 6 0 0 0-3-5"/>',
  play: '<path d="m8 5 11 7-11 7Z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6a8 8 0 0 1 13.5 3M4.4 15A8 8 0 0 0 18 18"/>',
  file: '<path d="M14 2H5v20h14V7Zm0 0v6h5M8 13h8m-8 4h6"/>',
  settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
};
export function icon(name, size = 18) { return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (PATHS[name] || PATHS.file) + '</svg>'; }
export const roleName = name => ({ executive_producer: '总制片', producer: '出品人', researcher: '研究员', creator: '故事策划', story_editor: '故事统筹', story_critic: '故事审稿', screenwriter: '编剧', storyboard_director: '分镜导演', independent_critic: '独立审稿', visual_designer: '美术指导', prompt_style_compiler: '视觉提示词', image_generator: '图像制作', video_generator: '视频制作', visual_critic: '画面质检', speech: '角色配音', dialogue_editor: '对白编辑', editor: '后期剪辑', edit_director: '剪辑导演', orchestrator: '执行器' }[name] || name || '未指定');
const LABELS = { pending: '待执行', running: '执行中', succeeded: '已完成本步', failed: '执行失败', blocked: '需要处理', waiting: '等待资源', paused: '已暂停', interrupted: '执行中断', superseded: '旧版本', human: '等待你决定', cancelled: '已取消' };
export function statePill(status) { const key = Object.hasOwn(LABELS, status) ? status : 'pending'; return '<span class="state-pill state-' + key + '"><i></i>' + LABELS[key] + '</span>'; }
export function sectionTitle(title, detail = '', action = '') { return '<div class="console-section-title"><div><h2>' + h(title) + '</h2>' + (detail ? '<p>' + h(detail) + '</p>' : '') + '</div>' + action + '</div>'; }
export function emptyState(title, detail, action = '') { return '<section class="console-empty">' + icon('film', 30) + '<h3>' + h(title) + '</h3><p>' + h(detail) + '</p>' + action + '</section>'; }
export function viewHeading(kicker, title, detail = '', action = '') { return '<header class="console-heading"><div><div class="eyebrow">' + h(kicker) + '</div><h1>' + h(title) + '</h1>' + (detail ? '<p>' + h(detail) + '</p>' : '') + '</div><div class="heading-actions">' + action + '</div></header>'; }
export const openProject = (id, label = '进入项目', tab = 'overview') => '<button class="button small" data-action="open-project-tab" data-id="' + h(id) + '" data-tab="' + h(tab) + '">' + h(label) + icon('arrow', 14) + '</button>';
