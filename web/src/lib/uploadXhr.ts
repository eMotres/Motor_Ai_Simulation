/** Multipart upload through XMLHttpRequest, the only browser API that reports the bytes SENT (fetch does not).
 *  Auth, answers and the refusal / idle notices follow the fetch wrapper (lib/apiAuth.ts); credentials stay as fetch's default
 *  (same-origin cookies only: the API is called with a Bearer header, never with cookies). */
// apiAuth is loaded when a file is sent: it reads `window` at import time, and the API modules are also imported by node tests
const auth = () => import('./apiAuth');

export interface XhrAnswer { status: number; body: unknown }

export interface XhrUploadOptions {
  /** bytes of the request body sent so far, and the body's total (a little above the file: form fields and boundaries) */
  onProgress?: (loaded: number, total: number) => void;
  signal?: AbortSignal;
}

export class UploadAborted extends Error { constructor() { super('aborted'); this.name = 'UploadAborted'; } }
export class UploadNetworkError extends Error { constructor() { super('Network error'); this.name = 'UploadNetworkError'; } }

/** POST `form` to `url`; resolves with the status and the parsed JSON (null when the answer is not JSON) for ANY HTTP status,
 *  so the caller maps errors exactly as it does for fetch.  Rejects with UploadAborted / UploadNetworkError. */
export async function xhrPost(url: string, form: FormData, o: XhrUploadOptions = {}): Promise<XhrAnswer> {
  const { authTokenForUrl, noteApiAnswer } = await auth();
  const token = await authTokenForUrl(url);
  if (o.signal?.aborted) throw new UploadAborted();
  return new Promise<XhrAnswer>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) o.onProgress?.(e.loaded, e.total); };
    const onAbort = () => xhr.abort();
    o.signal?.addEventListener('abort', onAbort);
    const done = () => o.signal?.removeEventListener('abort', onAbort);
    xhr.onerror = () => { done(); reject(new UploadNetworkError()); };
    xhr.ontimeout = xhr.onerror;
    xhr.onabort = () => { done(); reject(new UploadAborted()); };
    xhr.onload = () => {
      done();
      let body: unknown = null;
      try { body = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch { body = null; }
      void noteApiAnswer(url, token, xhr.status, xhr.getResponseHeader('x-auth-reason'), body);
      resolve({ status: xhr.status, body });
    };
    xhr.send(form);
  });
}
