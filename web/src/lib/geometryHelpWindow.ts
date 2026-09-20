// Geometry tab Help button — opens the static "which knob is which"
// picture in a new window.
//
// This is a STATIC asset (web/public/help/geometry_parameters.{svg,png},
// built once by `scripts/geometry_help_sheet.py`, one picture for every
// model — not a per-machine backend render), so unlike the report/datasheet
// downloads (`lib/exportDownload.ts`) there is no auth header to attach and
// no fetch to await: the browser can load it directly.
//
// `geometryHelpUrls` is exported separately from `openGeometryHelpWindow` so
// the URL-building logic (the only part with no DOM dependency) is testable
// under plain Node — see web/src/lib/__tests__/geometryHelpWindow.test.mjs.

export interface GeometryHelpUrls {
  svg: string;
  png: string;
}

/** The two asset URLs, relative to `origin` (defaults to the page's own —
 *  passed explicitly so this stays testable without a real `window`). */
export function geometryHelpUrls(origin: string): GeometryHelpUrls {
  const base = origin.replace(/\/$/, '');
  return {
    svg: `${base}/help/geometry_parameters.svg`,
    png: `${base}/help/geometry_parameters.png`,
  };
}

function wrapperHtml(urls: GeometryHelpUrls): string {
  // Minimal viewer: wheel to zoom, drag to pan, plus explicit download
  // links for both formats (the spec's ask — the browser's own "Save As"
  // on the image works too, this just makes it a one click).
  const esc = (s: string) => s.replace(/"/g, '&quot;');
  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Geometry parameters — Help</title>
<style>
  html,body{margin:0;height:100%;background:#fff;overflow:hidden;
    font-family:-apple-system,Segoe UI,Roboto,sans-serif;}
  #bar{position:fixed;top:0;left:0;right:0;height:40px;display:flex;
    align-items:center;gap:16px;padding:0 14px;background:#f6f6f8;
    border-bottom:1px solid #ddd;font-size:13px;color:#333;z-index:2;}
  #bar a{color:#4a3f80;text-decoration:none;}
  #bar a:hover{text-decoration:underline;}
  #stage{position:absolute;top:40px;left:0;right:0;bottom:0;overflow:hidden;
    cursor:grab;display:flex;align-items:center;justify-content:center;}
  #stage.grabbing{cursor:grabbing;}
  #pic{max-width:none;transform-origin:0 0;user-select:none;
    -webkit-user-drag:none;}
</style></head>
<body>
  <div id="bar">
    <strong>Geometry parameters</strong>
    <span>scroll/pinch to zoom, drag to pan</span>
    <span style="flex:1"></span>
    <a href="${esc(urls.svg)}" download>Download SVG</a>
    <a href="${esc(urls.png)}" download>Download PNG</a>
  </div>
  <div id="stage">
    <img id="pic" src="${esc(urls.svg)}" alt="Geometry parameters">
  </div>
  <script>
    (function () {
      var stage = document.getElementById('stage');
      var pic = document.getElementById('pic');
      var scale = 1, tx = 0, ty = 0, dragging = false, lastX = 0, lastY = 0;
      function apply() {
        pic.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
      }
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
 * Open the geometry Help picture in a new window.
 *
 * Returns null on success, or a short reason the caller can show (e.g. a
 * popup blocker refused the window) — same contract as
 * `exportDownload.downloadExport`.
 */
export function openGeometryHelpWindow(): string | null {
  try {
    const urls = geometryHelpUrls(window.location.origin);
    const win = window.open('', '_blank', 'noopener,width=1400,height=900');
    if (!win) {
      return 'Popup blocked — allow popups for this site to open the geometry help picture.';
    }
    win.document.open();
    win.document.write(wrapperHtml(urls));
    win.document.close();
    return null;
  } catch (e: any) {
    return e?.message ?? String(e);
  }
}
