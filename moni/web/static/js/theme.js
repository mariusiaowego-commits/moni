// theme.js — Theme engine for moni frontend (v1.0.30)
// CSS variables, dark mode, localStorage, import merge with DEFAULT.

const THEME_STORAGE_KEY = 'moni-themes';
const THEME_ACTIVE_KEY = 'moni-active-theme';
const THEME_DARK_KEY = 'moni-dark-mode';

/** Full default palette — import merge fills missing keys from here. */
const DEFAULT_THEME = {
  name: '中国传统色 · 纯衣',
  isDefault: true,
  light: {
    '--radius': '0.375rem',
    '--background': 'oklch(0.963 0.013 97.4)',
    '--foreground': 'oklch(0.218 0.000 89.9)',
    '--card': 'oklch(0.996 0.007 97.3)',
    '--card-fg': 'oklch(0.218 0.000 89.9)',
    '--muted': 'oklch(0.928 0.034 84.6)',
    '--muted-fg': 'oklch(0.486 0.040 87.0)',
    '--accent': 'oklch(0.819 0.109 302.2)',
    '--accent-fg': 'oklch(0.218 0.000 89.9)',
    '--destructive': 'oklch(0.542 0.208 19.5)',
    '--destructive-fg': 'oklch(0.963 0.013 97.4)',
    '--border': 'oklch(0.848 0.030 75.6)',
    '--input': 'oklch(0.848 0.030 75.6)',
    '--ring': 'oklch(0.963 0.013 97.4)',
    '--success': 'oklch(0.48 0.12 150)',
    '--success-fg': 'oklch(0.99 0.01 150)',
    '--success-muted': 'oklch(0.92 0.05 150)',
    '--success-muted-fg': 'oklch(0.32 0.08 150)',
    '--warning': 'oklch(0.72 0.14 75)',
    '--warning-fg': 'oklch(0.25 0.06 75)',
    '--warning-muted': 'oklch(0.94 0.06 85)',
    '--warning-muted-fg': 'oklch(0.40 0.10 70)',
    '--info': 'oklch(0.55 0.12 250)',
    '--info-fg': 'oklch(0.99 0.01 250)',
    '--info-muted': 'oklch(0.93 0.04 250)',
    '--info-muted-fg': 'oklch(0.35 0.10 250)',
    '--chart-1': 'oklch(0.55 0.14 250)',
    '--chart-2': 'oklch(0.853 0.045 187.6)',
    '--chart-3': 'oklch(0.894 0.031 191.5)',
    '--chart-4': 'oklch(0.832 0.029 322.2)',
    '--chart-5': 'oklch(0.805 0.071 326.2)',
    '--stage-bg': 'oklch(0.945 0.020 90)',
    '--stage-overlay': 'oklch(0.963 0.013 97.4 / 0.92)',
    '--shadow': '0 1px 3px oklch(0.218 0.000 89.9 / 0.06)',
    '--shadow-lg': '0 4px 12px oklch(0.218 0.000 89.9 / 0.08)',
  },
  dark: {
    '--radius': '0.375rem',
    '--background': 'oklch(0.170 0.004 164.5)',
    '--foreground': 'oklch(0.928 0.034 84.6)',
    '--card': 'oklch(0.218 0.000 89.9)',
    '--card-fg': 'oklch(0.928 0.034 84.6)',
    '--muted': 'oklch(0.257 0.030 61.5)',
    '--muted-fg': 'oklch(0.633 0.000 89.9)',
    '--accent': 'oklch(0.819 0.109 302.2)',
    '--accent-fg': 'oklch(0.170 0.004 164.5)',
    '--destructive': 'oklch(0.542 0.208 19.5)',
    '--destructive-fg': 'oklch(0.928 0.034 84.6)',
    '--border': 'oklch(0.337 0.007 84.6)',
    '--input': 'oklch(0.337 0.007 84.6)',
    '--ring': 'oklch(0.967 0.015 90.2)',
    '--success': 'oklch(0.70 0.14 150)',
    '--success-fg': 'oklch(0.15 0.03 150)',
    '--success-muted': 'oklch(0.28 0.05 150)',
    '--success-muted-fg': 'oklch(0.85 0.08 150)',
    '--warning': 'oklch(0.78 0.12 80)',
    '--warning-fg': 'oklch(0.18 0.04 80)',
    '--warning-muted': 'oklch(0.30 0.05 80)',
    '--warning-muted-fg': 'oklch(0.88 0.06 85)',
    '--info': 'oklch(0.72 0.12 250)',
    '--info-fg': 'oklch(0.15 0.04 250)',
    '--info-muted': 'oklch(0.28 0.05 250)',
    '--info-muted-fg': 'oklch(0.85 0.06 250)',
    '--chart-1': 'oklch(0.70 0.14 250)',
    '--chart-2': 'oklch(0.75 0.08 187)',
    '--chart-3': 'oklch(0.78 0.06 191)',
    '--chart-4': 'oklch(0.75 0.08 322)',
    '--chart-5': 'oklch(0.72 0.10 326)',
    '--stage-bg': 'oklch(0.22 0.012 90)',
    '--stage-overlay': 'oklch(0.18 0.01 90 / 0.92)',
    '--shadow': '0 1px 3px oklch(0 0 0 / 0.2)',
    '--shadow-lg': '0 4px 12px oklch(0 0 0 / 0.3)',
  },
};

/** Deep-merge palette: user keys win; missing keys filled from DEFAULT. */
function mergeThemeVars(mode, partial) {
  const base = { ...(DEFAULT_THEME[mode] || {}) };
  const src = partial && typeof partial === 'object' ? partial : {};
  for (const [k, v] of Object.entries(src)) {
    if (v != null && String(v).trim() !== '') base[k] = String(v).trim();
  }
  return base;
}

function normalizeThemeRecord(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const name = String(raw.name || '').trim() || '自定义主题';
  return {
    name,
    isDefault: !!raw.isDefault,
    light: mergeThemeVars('light', raw.light || {}),
    dark: mergeThemeVars('dark', raw.dark || {}),
  };
}

// ── Parse CSS variables from pasted globals.css ──
function parseGlobalsCSS(cssText) {
  const result = { light: {}, dark: {} };
  const rootMatch = cssText.match(/:root\s*\{([^}]*)\}/);
  if (rootMatch) {
    const vars = rootMatch[1].match(/--[\w-]+\s*:[^;]+/g) || [];
    for (const v of vars) {
      const idx = v.indexOf(':');
      const prop = v.slice(0, idx).trim();
      const val = v.slice(idx + 1).trim();
      if (prop && val) result.light[prop] = val;
    }
  }
  const darkMatch = cssText.match(/\.dark\s*\{([^}]*)\}/);
  if (darkMatch) {
    const vars = darkMatch[1].match(/--[\w-]+\s*:[^;]+/g) || [];
    for (const v of vars) {
      const idx = v.indexOf(':');
      const prop = v.slice(0, idx).trim();
      const val = v.slice(idx + 1).trim();
      if (prop && val) result.dark[prop] = val;
    }
  }
  // v1.0.30: do NOT copy light→dark wholesale; mergeThemeVars fills dark from DEFAULT
  return result;
}

function detectThemeName(cssText) {
  const commentMatch = cssText.match(/\/\*\s*(?:Theme|主题)?[:\s]*([^*\/]{2,40})\s*\*\//i);
  if (commentMatch) {
    let name = commentMatch[1].replace(/[═─\s]+$/g, '').replace(/^[═─\s]+/g, '').trim();
    if (name.length >= 2 && name.length <= 30) return name;
  }
  const light = parseGlobalsCSS(cssText).light;
  if (light['--background']) {
    const hueMatch = light['--background'].match(/oklch\([\d.]+\s+[\d.]+\s+([\d.]+)/);
    if (hueMatch) {
      const hue = parseFloat(hueMatch[1]);
      if (hue >= 70 && hue <= 110) return '暖沙主题';
      if (hue >= 200 && hue <= 260) return '海蓝主题';
      if (hue >= 260 && hue <= 330) return '紫藤主题';
      if (hue >= 330 || hue <= 30) return '丹霞主题';
      if (hue >= 130 && hue <= 180) return '青竹主题';
      return 'oklch 主题';
    }
    const hexMatch = light['--background'].match(/#[0-9a-f]{6}/i);
    if (hexMatch) return '自定义 #' + hexMatch[0].slice(1, 4);
  }
  return '自定义主题 ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function themeMixin() {
  return {
    themeModalOpen: false,
    themeTab: 'apply',
    themeCssInput: '',
    themeImportStatus: '',
    activeTheme: localStorage.getItem(THEME_ACTIVE_KEY) || DEFAULT_THEME.name,
    isDarkMode: localStorage.getItem(THEME_DARK_KEY) === 'true',
    customThemes: (() => {
      try {
        const raw = JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) || '[]');
        return (Array.isArray(raw) ? raw : [])
          .map(normalizeThemeRecord)
          .filter(Boolean);
      } catch (_) {
        return [];
      }
    })(),
    toastVisible: false,
    toastMessage: '',
    _toastTimer: null,

    get themeList() {
      return [DEFAULT_THEME, ...this.customThemes];
    },

    openThemeManager() {
      this.themeModalOpen = true;
      this.themeTab = 'apply';
      this.themeCssInput = '';
      this.themeImportStatus = '';
    },
    closeThemeManager() {
      this.themeModalOpen = false;
    },

    applyTheme(name) {
      const all = [DEFAULT_THEME, ...this.customThemes];
      let theme = all.find(t => t.name === name) || DEFAULT_THEME;
      // ensure full palette even if stored theme is partial
      theme = {
        ...theme,
        light: mergeThemeVars('light', theme.light),
        dark: mergeThemeVars('dark', theme.dark),
      };
      const vars = this.isDarkMode ? theme.dark : theme.light;
      let css = ':root {\n';
      for (const [k, v] of Object.entries(vars)) {
        css += `  ${k}: ${v};\n`;
      }
      css += '}\n';
      // also set .dark block so components under .dark get dark vars when toggled
      if (this.isDarkMode) {
        css += '.dark {\n';
        for (const [k, v] of Object.entries(theme.dark)) {
          css += `  ${k}: ${v};\n`;
        }
        css += '}\n';
      }
      let styleEl = document.getElementById('theme-override');
      if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'theme-override';
        document.head.appendChild(styleEl);
      }
      styleEl.textContent = css;
      document.body.classList.toggle('dark', this.isDarkMode);
      this.activeTheme = name;
      localStorage.setItem(THEME_ACTIVE_KEY, name);
    },

    toggleDark() {
      this.isDarkMode = !this.isDarkMode;
      localStorage.setItem(THEME_DARK_KEY, this.isDarkMode);
      this.applyTheme(this.activeTheme);
    },

    importTheme() {
      const css = this.themeCssInput.trim();
      if (!css) { this.themeImportStatus = '⚠ 请粘贴 CSS 内容'; return; }
      const parsed = parseGlobalsCSS(css);
      if (Object.keys(parsed.light).length === 0) {
        this.themeImportStatus = '⚠ 未找到 :root { --xxx: ... } 变量';
        return;
      }
      const name = detectThemeName(css);
      const newTheme = normalizeThemeRecord({
        name,
        isDefault: false,
        light: parsed.light,
        dark: Object.keys(parsed.dark).length ? parsed.dark : {},
      });
      const existing = this.customThemes.findIndex(t => t.name === name);
      if (existing >= 0) {
        this.customThemes[existing] = newTheme;
      } else {
        this.customThemes.push(newTheme);
      }
      localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(this.customThemes));
      this.applyTheme(name);
      this.themeImportStatus = `✅ 「${name}」已保存并应用（缺省 token 已从默认补齐）`;
      this.themeCssInput = '';
      this.showToast(`主题「${name}」已导入`);
    },

    deleteTheme(name) {
      this.customThemes = this.customThemes.filter(t => t.name !== name);
      localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(this.customThemes));
      if (this.activeTheme === name) this.applyTheme(DEFAULT_THEME.name);
      this.showToast(`主题「${name}」已删除`);
    },

    showToast(msg) {
      this.toastMessage = msg;
      this.toastVisible = true;
      if (this._toastTimer) clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => { this.toastVisible = false; }, 2500);
    },

    initTheme() {
      this.applyTheme(this.activeTheme);
    },
  };
}
