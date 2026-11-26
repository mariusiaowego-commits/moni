/* v1.0.84 — hover 浮层挂到 body，修 tooltip 被表格 overflow 裁掉。
 *
 * 页面里的 tooltip 都是 absolute 定位在单元格内部，父级 .table-container
 * 有 overflow: hidden，所以浮层一出表格边界就被切掉。这里改成事件委托：
 * 悬停/聚焦时把浮层内容克隆到 body 下的 #moni-float（position: fixed），
 * 再按锚点位置贴合视口摆位；页面内原浮层由 CSS 隐藏。
 */
(function () {
  const SOURCES = [
    ['.tag-overflow-pill', '.popover-card'],
    ['.direction-usable', '.popover-card'],
    ['.tag-chip-wrap', '.tip'],
    ['.bv-chip-wrap', '.tip'],
    ['.title-cell', '.title-tip'],
    ['.tags-cell', '.tags-overflow'],
    ['.article-card-ctx', '.tip'],
    ['.feat-cell', '.feat-tip'],
  ];
  const GAP = 6;
  const EDGE = 8;

  let layer = null;
  let anchor = null;
  let parked = null;   // { node, parent, next } 被搬到浮层里的真实节点

  function ensureLayer() {
    if (layer) return layer;
    layer = document.createElement('div');
    layer.id = 'moni-float';
    document.body.appendChild(layer);
    document.body.classList.add('has-float-layer');
    return layer;
  }

  function hide() {
    anchor = null;
    if (parked) {
      const { node, parent, next } = parked;
      parked = null;
      if (parent.isConnected) parent.insertBefore(node, next && next.isConnected ? next : null);
    }
    if (!layer) return;
    layer.classList.remove('is-open');
  }

  function show(host, tip) {
    if (parked) hide();
    const el = ensureLayer();
    // 直接搬真实节点：clone 会丢掉 Alpine 已渲染的文本节点
    parked = { node: tip, parent: tip.parentNode, next: tip.nextSibling };
    el.appendChild(tip);
    el.classList.add('is-open');

    const a = host.getBoundingClientRect();
    const box = el.getBoundingClientRect();
    const below = a.bottom + GAP;
    const top = below + box.height > window.innerHeight - EDGE
      ? Math.max(EDGE, a.top - box.height - GAP)
      : below;
    const left = Math.min(
      Math.max(EDGE, a.left),
      Math.max(EDGE, window.innerWidth - box.width - EDGE),
    );
    el.style.top = `${Math.round(top)}px`;
    el.style.left = `${Math.round(left)}px`;
    anchor = host;
  }

  function resolve(node) {
    if (!node || !node.closest) return null;
    for (const [hostSel, tipSel] of SOURCES) {
      const host = node.closest(hostSel);
      if (!host) continue;
      const tip = host.querySelector(tipSel);
      if (tip && tip.innerHTML.trim()) return [host, tip];
    }
    return null;
  }

  document.addEventListener('mouseover', (event) => {
    const found = resolve(event.target);
    if (!found) {
      if (anchor && !anchor.contains(event.target)) hide();
      return;
    }
    if (found[0] === anchor) return;
    show(found[0], found[1]);
  });

  document.addEventListener('mouseout', (event) => {
    if (!anchor) return;
    const next = event.relatedTarget;
    if (next && anchor.contains(next)) return;
    hide();
  });

  document.addEventListener('focusin', (event) => {
    const found = resolve(event.target);
    if (found) show(found[0], found[1]);
  });
  document.addEventListener('focusout', hide);
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
})();
