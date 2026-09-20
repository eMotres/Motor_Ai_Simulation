// Geometry tab Help button — opens the static "which knob is which"
// picture(s) in a new window, with tabs: "Sector" (one enlarged sector,
// every schema key) and "Radii" (every diameter/radius, plus a magnified
// callout of the air-gap zone).
//
// These are STATIC assets (web/public/help/geometry_parameters*.{svg,png},
// built once by `scripts/geometry_help_sheet.py`, one pair of pictures for
// every model — not a per-machine backend render), so unlike the
// report/datasheet downloads (`lib/exportDownload.ts`) there is no auth
// header to attach and no fetch to await: the browser can load them directly.
//
// `geometryHelpUrls` is exported separately from `openGeometryHelpWindow` so
// the URL-building logic (the only part with no DOM dependency) is testable
// under plain Node — see web/src/lib/__tests__/geometryHelpWindow.test.mjs.

export interface GeometryHelpTab {
  id: string;
  label: string;
  svg: string;
  png: string;
}

/** Both tabs' asset URLs, relative to `origin` (defaults to the page's own —
 *  passed explicitly so this stays testable without a real `window`). */
export function geometryHelpTabs(origin: string): GeometryHelpTab[] {
  const base = origin.replace(/\/$/, '');
  return [
    { id: 'sector', label: 'Sector',
      svg: `${base}/help/geometry_parameters.svg`,
      png: `${base}/help/geometry_parameters.png` },
    { id: 'radii', label: 'Radii',
      svg: `${base}/help/geometry_parameters_radii.svg`,
      png: `${base}/help/geometry_parameters_radii.png` },
  ];
}

// Kept for anything still calling the old single-picture shape.
export interface GeometryHelpUrls {
  svg: string;
  png: string;
}
export function geometryHelpUrls(origin: string): GeometryHelpUrls {
  const [sector] = geometryHelpTabs(origin);
  return { svg: sector.svg, png: sector.png };
}

function wrapperHtml(tabs: GeometryHelpTab[]): string {
  // Minimal viewer: wheel to zoom, drag to pan, tabs to switch picture, plus
  // explicit download links for both formats of whichever tab is showing
  // (the browser's own "Save As" on the image works too, this just makes it
  // a one click).
  const esc = (s: string) => s.replace(/"/g, '&quot;');
  const tabsJson = esc(JSON.stringify(tabs));
  const tabButtons = tabs
    .map((t, i) => `<button class="tabbtn" data-i="${i}"${i === 0 ? ' aria-current="true"' : ''}>${esc(t.label)}</button>`)
    .join('');
  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Geometry parameters — Help</title>
<style>
  html,body{margin:0;height:100%;background:#fff;overflow:hidden;
    font-family:-apple-system,Segoe UI,Roboto,sans-serif;}
  #bar{position:fixed;top:0;left:0;right:0;height:44px;display:flex;
    align-items:center;gap:14px;padding:0 14px;background:#f6f6f8;
    border-bottom:1px solid #ddd;font-size:13px;color:#333;z-index:2;}
  #bar a{color:#4a3f80;text-decoration:none;}
  #bar a:hover{text-decoration:underline;}
  .tabbtn{border:1px solid #ccc;background:#fff;border-radius:6px;
    padding:5px 12px;font-size:13px;cursor:pointer;color:#333;}
  .tabbtn[aria-current="true"]{background:#4a3f80;border-color:#4a3f80;color:#fff;}
  #stage{position:absolute;top:44px;left:0;right:0;bottom:0;overflow:hidden;
    cursor:grab;display:flex;align-items:center;justify-content:center;}
  #stage.grabbing{cursor:grabbing;}
  #pic{max-width:none;transform-origin:0 0;user-select:none;
    -webkit-user-drag:none;}
</style></head>
<body>
  <div id="bar">
    <strong>Geometry parameters</strong>
    <span id="tabbar">${tabButtons}</span>
    <span>scroll/pinch to zoom, drag to pan</span>
    <span style="flex:1"></span>
    <a id="dl-svg" href="#" download>Download SVG</a>
    <a id="dl-png" href="#" download>Download PNG</a>
  </div>
  <div id="stage">
    <img id="pic" alt="Geometry parameters">
  </div>
  <script>
    (function () {
      var TABS = ${tabsJson};
      var stage = document.getElementById('stage');
      var pic = document.getElementById('pic');
      var dlSvg = document.getElementById('dl-svg');
      var dlPng = document.getElementById('dl-png');
      var scale = 1, tx = 0, ty = 0, dragging = false, lastX = 0, lastY = 0;
      function apply() {
        pic.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
      }
      function showTab(i) {
        var t = TABS[i];
        pic.src = t.svg;
        dlSvg.href = t.svg; dlPng.href = t.png;
        scale = 1; tx = 0; ty = 0; apply();
        var btns = document.querySelectorAll('.tabbtn');
        for (var k = 0; k < btns.length; k++) {
          btns[k].setAttribute('aria-current', String(k === i));
        }
      }
      var btns = document.querySelectorAll('.tabbtn');
      for (var j = 0; j < btns.length; j++) {
        btns[j].addEventListener('click', function (e) {
          showTab(parseInt(e.currentTarget.getAttribute('data-i'), 10));
        });
      }
      showTab(0);
      stage.addEventListener('wheel', function (e) {
        e.preventDefault();
        var factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        scale = Math.min(12, Math.max(0.2, scale * factor));
        apply();
      }, { passive: false });
      stage.addEventListener('mousedown', function (e) {
        dragging = true; lastX = e.clientX; lastY = e.clientY;
        stage.classList.add('grabbing');
      });
      window.addEventListener('mouseup', function () {
        dragging = false; stage.classList.remove('grabbing');
      });
      window.addEventListener('mousemove', function (e) {
        if (!dragging) return;
        tx += e.clientX - lastX; ty += e.clientY - lastY;
        lastX = e.clientX; lastY = e.clientY;
        apply();
      });
    })();
  </script>
</body></html>`;
}

/**
 * Open the geometry Help picture(s) in a new window, with tabs to switch
 * between the sector view and the radii view.
 *
 * Returns null on success, or a short reason the caller can show (e.g. a
 * popup blocker refused the window) — same contract as
 * `exportDownload.downloadExport`.
 */
export function openGeometryHelpWindow(): string | null {
  try {
    const tabs = geometryHelpTabs(window.location.origin);
    const win = window.open('', '_blank', 'noopener,width=1500,height=950');
    if (!win) {
      return 'Popup blocked — allow popups for this site to open the geometry help picture.';
    }
    win.document.open();
    win.document.write(wrapperHtml(tabs));
    win.document.close();
    return null;
  } catch (e: any) {
    return e?.message ?? String(e);
  }
}
