/* Small DOM reconciler: progress refreshes retain live media nodes in place. */
const key = node => node.nodeType === 1 ? node.getAttribute('data-dom-key') || node.id || (node.tagName === 'VIDEO' ? 'video:' + node.getAttribute('src') : '') : '';
const playing = node => node.nodeType === 1 && (node.tagName === 'VIDEO' ? !node.paused && !node.ended : [...node.querySelectorAll('video')].some(video => !video.paused && !video.ended));
const compatible = (left, right) => left && left.nodeType === right.nodeType && (left.nodeType !== 1 || left.tagName === right.tagName) && key(left) === key(right);

function patchNode(current, desired) {
  if (current.nodeType === 3 || current.nodeType === 8) {
    if (current.nodeValue !== desired.nodeValue) current.nodeValue = desired.nodeValue;
    return;
  }
  if (current.nodeType !== 1) return;
  for (const attribute of [...current.attributes]) if (!desired.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
  for (const attribute of desired.attributes) if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
  // Do not call load(), replace source children, detach or reset playback state.
  if (current.tagName === 'VIDEO') return;
  patchChildren(current, desired);
  if (current.tagName === 'SELECT') {
    const value = desired.value;
    for (const option of current.querySelectorAll('option')) option.selected = option.value === value;
  }
  if (['INPUT', 'TEXTAREA'].includes(current.tagName)) {
    if (current.value !== desired.value) current.value = desired.value;
    if (current.tagName === 'INPUT') current.checked = desired.checked;
  }
}
function patchChildren(parent, desiredParent) {
  const original = [...parent.childNodes], used = new Set();
  let cursor = parent.firstChild;
  for (const desired of [...desiredParent.childNodes]) {
    let current = compatible(cursor, desired) && !used.has(cursor) ? cursor : null;
    if (!current && key(desired)) current = original.find(node => !used.has(node) && compatible(node, desired));
    if (!current) {
      current = desired.cloneNode(true);
      parent.insertBefore(current, cursor);
    } else {
      if (current !== cursor) {
        if (typeof parent.moveBefore === 'function' && current.isConnected) parent.moveBefore(current, cursor);
        else if (!playing(current)) parent.insertBefore(current, cursor);
        // Older browsers retain an actively playing subtree in place rather
        // than detach it. Its siblings and all progress fields still update.
      }
      patchNode(current, desired);
    }
    used.add(current);
    cursor = current.nextSibling;
  }
  for (const node of original) if (!used.has(node) && node.parentNode === parent) node.remove();
}
export function patchWorkspace(root, html) {
  const template = root.ownerDocument.createElement('template');
  template.innerHTML = html;
  patchChildren(root, template.content);
}
