// Download an export the backend renders — the datasheet (.xlsx) and the motor
// report (.docx by default, .pdf on `?format=pdf`) go through here.
//
// Both are plain GETs that answer with a file, and both need the same six lines
// of browser plumbing: read the blob, hand it to an <a download>, revoke the
// object URL afterwards.  One copy, so the two exports can never drift apart on
// error handling — and so a third one costs a call, not a paste.
//
// The Authorization header is NOT added here: `lib/apiAuth.installFetchAuth`
// already attaches it to every request aimed at the backend base.

/**
 * Fetch `url` and save the body as `filename`.
 *
 * Returns `null` on success, or a human-readable reason on failure — the
 * caller owns the message, because "Datasheet failed: …" and "Report failed: …"
 * are the user's words for two different buttons.
 */
export async function downloadExport(url: string,
                                     filename: string): Promise<string | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) {
      // The backend refuses by NAME (no duty saved, die not granted, build
      // failed) and that sentence is the whole value of the error — a bare
      // status code sends the user to the console.
      let msg = `${r.status}`;
      try {
        const j = await r.json();
        msg = typeof j?.detail === 'string' ? j.detail
          : (j?.detail?.error ?? JSON.stringify(j?.detail ?? j));
      } catch { /* not JSON — the status stands */ }
      return msg;
    }
    const blob = await r.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Long enough for the browser to have started writing the file; the URL is
    // dead weight after that and holds the whole blob in memory until revoked.
    setTimeout(() => URL.revokeObjectURL(href), 10_000);
    return null;
  } catch (e: any) {
    return e?.message ?? String(e);
  }
}
