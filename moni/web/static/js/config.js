// moni/web/static/js/config.js
// 配置管理 UI: tags / sources / keyword filters CRUD
// 直接挂到 root Alpine app() 上, 通过 getApp() 访问
// 仿 crud.js 的 modal 模式

function getApp() {
  return document.querySelector('#app').__x.$data;
}

// ── Tags CRUD ───────────────────────────────────────────────────────────
function configLoadTags() {
  const app = getApp();
  fetch('/api/config/tags', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(data => { app.configTags = Array.isArray(data) ? data : []; })
    .catch(err => { console.error('Failed to load tags:', err); app.configTags = []; });
}

function configCreateTag() {
  openConfigModal('tag', null);
}
function configEditTag(tag) {
  openConfigModal('tag', tag);
}
function configDeleteTag(tag) {
  if (!confirm(`确定删除 tag "${tag.name}"? (会级联清除 source_tags 和 article_tags)`)) return;
  fetch(`/api/config/tags/${encodeURIComponent(tag.name)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
    .then(r => { if (!r.ok) throw new Error('Delete failed'); configLoadTags(); })
    .catch(err => alert('删除失败: ' + err.message));
}

function configSaveTag(body, isEdit, originalName) {
  const app = getApp();
  const url = isEdit
    ? `/api/config/tags/${encodeURIComponent(originalName)}`
    : '/api/config/tags';
  const method = isEdit ? 'PUT' : 'POST';
  return fetch(url, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
    return r.json();
  }).then(() => {
    configLoadTags();
    closeConfigModal();
  }).catch(err => alert('保存失败: ' + err.message));
}

// ── Sources CRUD ────────────────────────────────────────────────────────
function configLoadSources() {
  const app = getApp();
  fetch('/api/config/sources', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(data => { app.configSources = Array.isArray(data) ? data : []; })
    .catch(err => { console.error('Failed to load sources:', err); app.configSources = []; });
}

function configCreateSource() {
  openConfigModal('source', null);
}
function configEditSource(source) {
  openConfigModal('source', source);
}
function configDeleteSource(source) {
  if (!confirm(`确定删除 source "${source.name}"? (会级联清除 source_tags + filters)`)) return;
  fetch(`/api/config/sources/${encodeURIComponent(source.id)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
    .then(r => { if (!r.ok) throw new Error('Delete failed'); configLoadSources(); })
    .catch(err => alert('删除失败: ' + err.message));
}

function configSaveSource(body, isEdit) {
  const app = getApp();
  const url = isEdit
    ? `/api/config/sources/${encodeURIComponent(body.id)}`
    : '/api/config/sources';
  const method = isEdit ? 'PUT' : 'POST';
  return fetch(url, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
    return r.json();
  }).then(() => {
    configLoadSources();
    closeConfigModal();
  }).catch(err => alert('保存失败: ' + err.message));
}

function configEditSourceTags(source) {
  openConfigModal('sourceTags', source);
}

function configSaveSourceTags(sourceId, tagNames) {
  return fetch(`/api/config/sources/${encodeURIComponent(sourceId)}/tags`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(tagNames),
  }).then(r => {
    if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
    return r.json();
  }).then(() => {
    configLoadSources();
    closeConfigModal();
  }).catch(err => alert('保存 tags 失败: ' + err.message));
}

// ── Keyword Filters CRUD ────────────────────────────────────────────────
function configLoadFilters() {
  const app = getApp();
  const sourceId = app.configFilterSourceFilter || '';
  const url = sourceId
    ? `/api/config/filters?source_id=${encodeURIComponent(sourceId)}`
    : '/api/config/filters';
  fetch(url, { credentials: 'same-origin' })
    .then(r => r.json())
    .then(data => { app.configFilters = Array.isArray(data) ? data : []; })
    .catch(err => { console.error('Failed to load filters:', err); app.configFilters = []; });
}

function configCreateFilter() {
  openConfigModal('filter', null);
}
function configEditFilter(filter) {
  openConfigModal('filter', filter);
}
function configDeleteFilter(filter) {
  if (!confirm(`确定删除 filter id=${filter.id}?`)) return;
  fetch(`/api/config/filters/${filter.id}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
    .then(r => { if (!r.ok) throw new Error('Delete failed'); configLoadFilters(); })
    .catch(err => alert('删除失败: ' + err.message));
}

function configSaveFilter(body, isEdit) {
  const url = isEdit
    ? `/api/config/filters/${body.id}`
    : '/api/config/filters';
  const method = isEdit ? 'PUT' : 'POST';
  return fetch(url, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
    return r.json();
  }).then(() => {
    configLoadFilters();
    closeConfigModal();
  }).catch(err => alert('保存失败: ' + err.message));
}

// ── Modal helpers ───────────────────────────────────────────────────────
function openConfigModal(type, target) {
  const app = getApp();
  app.configModalType = type;       // 'tag' | 'source' | 'sourceTags' | 'filter'
  app.configModalTarget = target ? JSON.parse(JSON.stringify(target)) : {};
  // For edit: remember original (used by isEdit flag, e.g. tag originalName)
  if (target && target.name && type === 'tag') {
    app.configModalTargetOriginal = target.name;
  } else {
    app.configModalTargetOriginal = null;
  }
  // For new source/filter, set defaults
  if (type === 'source' && !target) {
    app.configModalTarget = {
      id: '', name: '', display_name: '', source_type: 'html', url: '',
      fetch_interval_min: 30, is_active: true, notes: '',
    };
  } else if (type === 'filter' && !target) {
    app.configModalTarget = {
      source_id: app.configSources[0]?.id || '',
      mode: 'blacklist', match_field: 'title+content',
      keywords: [], is_active: true, notes: '',
    };
  } else if (type === 'sourceTags' && target) {
    // Ensure tags is array of all currently-associated tag names
    app.configModalTarget = {
      id: target.id, name: target.name,
      tags: Array.isArray(target.tags) ? [...target.tags] : [],
    };
  }
  app.configModalOpen = true;
}

function closeConfigModal() {
  const app = getApp();
  app.configModalOpen = false;
  app.configModalType = null;
  app.configModalTarget = null;
  app.configModalTargetOriginal = null;
}

function toggleSourceTagInModal(tagName, checked) {
  const app = getApp();
  if (!app.configModalTarget.tags) app.configModalTarget.tags = [];
  if (checked) {
    if (!app.configModalTarget.tags.includes(tagName)) {
      app.configModalTarget.tags.push(tagName);
    }
  } else {
    app.configModalTarget.tags = app.configModalTarget.tags.filter(t => t !== tagName);
  }
}
