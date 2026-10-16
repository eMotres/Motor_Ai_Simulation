import test from 'node:test';
import assert from 'node:assert/strict';
import i18next from 'i18next';
import resourcesToBackend from 'i18next-resources-to-backend';
import { loadNamespace } from '../loadNamespace.ts';

test('a rejected lazy locale import emits failedLoading and adds no empty bundle', async () => {
  const failure = new Error('missing locale chunk');
  const notified = [];
  const i18n = i18next.createInstance().use(resourcesToBackend((lng, ns) =>
    loadNamespace(() => Promise.reject(failure), (error) => notified.push(error))));
  const failedLoads = new Set();
  let finish;
  const failed = new Promise((resolve) => { finish = resolve; });
  i18n.on('failedLoading', (lng, ns, error) => {
    assert.equal(error, failure);
    failedLoads.add(`${lng}/${ns}`);
    if (failedLoads.has('en/geometry')) finish();
  });

  await i18n.init({ lng: 'en', fallbackLng: false, ns: ['geometry'], defaultNS: 'geometry' });
  let timeout;
  await Promise.race([failed, new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('loader timeout')), 3000); })]);
  clearTimeout(timeout);
  assert.deepEqual(notified, [failure]);
  assert.equal(i18n.getResourceBundle('en', 'geometry'), undefined);
  assert.equal(i18n.hasResourceBundle('en', 'geometry'), false);
});
