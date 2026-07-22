import { createTheme, type Theme, type PaletteMode } from '@mui/material';

/* Design tokens shared with the aerostator.com (eMotres) site.
   Light values mirror eMotres/src/app/globals.css; dark values are the
   app's original palette.  Keep in sync with the CSS vars in index.css. */
const TOKENS = {
  light: {
    brand: '#2563eb',
    brandDark: '#1d4ed8',
    appBg: '#f7f6f3',
    panel: '#ffffff',
    line: '#e2e0dc',
    text0: '#0d0d0d',
    text2: '#44443f',
  },
  dark: {
    brand: '#3b82f6',
    brandDark: '#2563eb',
    // NOTE: real hex, NOT var(--...) — MUI computes alpha/contrast from these.
    appBg: '#0f172a',
    panel: '#1e293b',
    line: '#334155',
    text0: '#f1f5f9',
    text2: '#94a3b8',
  },
} as const;

export type AppMode = PaletteMode;

const MODE_KEY = 'app.themeMode';

export function loadThemeMode(): AppMode {
  try {
    const v = localStorage.getItem(MODE_KEY);
    if (v === 'light' || v === 'dark') return v;
  } catch { /* default */ }
  return 'light';
}

export function saveThemeMode(mode: AppMode) {
  try { localStorage.setItem(MODE_KEY, mode); } catch { /* non-fatal */ }
  // CSS vars in index.css key off this attribute — flip it with the theme.
  document.documentElement.dataset.theme = mode;
}

export function buildAppTheme(mode: AppMode): Theme {
  const t = TOKENS[mode];
  return createTheme({
    palette: {
      mode,
      primary: { main: t.brand, dark: t.brandDark },
      secondary: { main: '#10b981' },
      background: { default: t.appBg, paper: t.panel },
      divider: t.line,
      ...(mode === 'light'
        ? {
            text: { primary: t.text0, secondary: t.text2 },
          }
        : {}),
    },
    typography: {
      fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    },
    shape: { borderRadius: 8 },
    components: {
      MuiTextField: { defaultProps: { variant: 'outlined', size: 'small' } },
      MuiSlider: { styleOverrides: { root: { color: t.brand } } },
      MuiAppBar: {
        styleOverrides: {
          root: {
            backgroundColor: t.panel,
            color: t.text0,
            borderBottom: `1px solid ${t.line}`,
            boxShadow: 'none',
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: mode === 'light'
            ? { border: `1px solid ${t.line}`, boxShadow: 'none' }
            : {},
        },
      },
    },
  });
}
