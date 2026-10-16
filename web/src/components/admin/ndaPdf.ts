// Admin: open a signed NDA PDF (the route needs the admin token, so a plain link cannot be used).
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export async function openNdaPdf(recordId: string): Promise<string | null> {
  try {
    const r = await fetch(`${API}/api/nda/${encodeURIComponent(recordId)}/pdf`);
    if (!r.ok) return String(r.status);
    const href = URL.createObjectURL(await r.blob());
    window.open(href, '_blank', 'noopener');
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
    return null;
  } catch (e) { return (e as Error).message; }
}
