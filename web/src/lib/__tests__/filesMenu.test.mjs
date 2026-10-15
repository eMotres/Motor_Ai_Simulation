// node --test - the Files menu items and permission rules (lib/filesMenu.ts), user / engineer / admin, with and without the NDA.
import test from 'node:test';
import assert from 'node:assert/strict';

let F = null;
try { F = await import('../filesMenu.ts'); } catch { /* old Node */ }
const t = F ? test : test.skip;

const base = { scope: 'full', connectedToApi: true, isAdmin: false, enforced: true, role: 'engineer', canDownload: false,
  modelBuilt: true, canWrite: false };
const by = (items) => Object.fromEntries(items.map((i) => [i.id, i]));

t('Active offers the EM parameter files: Fusion CSV and FreeCAD, in and out', () => {
  const m = F.filesMenu({ ...base, scope: 'active', isAdmin: true, role: 'admin' });
  assert.deepEqual(m.map((i) => i.id), ['fusionCsvEm', 'freecadEm', 'fusionCsvEmImport', 'freecadEmImport']);
  assert.deepEqual(m.map((i) => i.section), ['export', 'export', 'import', 'import']);
  assert.ok(m.every((i) => i.enabled));
});

t('Active imports belong to the admin (the server route is require_admin); exports stay open to the simulation UI', () => {
  const eng = by(F.filesMenu({ ...base, scope: 'active' }));
  assert.equal(eng.fusionCsvEm.enabled, true);
  assert.equal(eng.freecadEm.enabled, true);
  assert.equal(eng.fusionCsvEmImport.enabled, false);
  assert.equal(eng.fusionCsvEmImport.reason, 'adminOnly');
  const local = by(F.filesMenu({ ...base, scope: 'active', enforced: false }));          // local dev backend: no auth
  assert.equal(local.fusionCsvEmImport.enabled, true);
});

t('offline disables everything with the reason "offline"', () => {
  for (const scope of ['active', 'full']) {
    const m = F.filesMenu({ ...base, scope, connectedToApi: false, isAdmin: true, role: 'admin', canDownload: true, canWrite: true });
    assert.ok(m.every((i) => !i.enabled && i.reason === 'offline'), scope);
  }
});

t('Full: the construction files need the NDA; the FreeCAD file is not offered to a plain user at all', () => {
  const noNda = by(F.filesMenu(base));
  for (const id of ['step', 'bomCsv', 'bomXlsx', 'paramsCsv', 'fcstd']) {
    assert.equal(noNda[id].enabled, false, id);
    assert.equal(noNda[id].reason, 'nda', id);
  }
  const nda = by(F.filesMenu({ ...base, canDownload: true }));
  for (const id of ['step', 'bomCsv', 'bomXlsx', 'paramsCsv', 'fcstd']) assert.equal(nda[id].enabled, true, id);
  const user = by(F.filesMenu({ ...base, role: 'user', canDownload: true }));
  assert.equal(user.fcstd, undefined);
  assert.equal(user.fcstdImport, undefined);
  assert.equal(user.step.enabled, true);                                                      // the STEP is the user file
  assert.equal(F.mayHaveFreecadFile({ isAdmin: false, enforced: true, role: 'user' }), false);
  assert.equal(F.mayHaveFreecadFile({ isAdmin: true, enforced: true, role: 'admin' }), true);
  assert.equal(F.mayHaveFreecadFile({ isAdmin: false, enforced: false, role: 'anon' }), true);
});

t('Full: admins need no NDA; with no built model nothing downloads', () => {
  const adm = by(F.filesMenu({ ...base, isAdmin: true, role: 'admin', canDownload: true, canWrite: true }));
  assert.ok(Object.values(adm).every((i) => i.enabled));
  const none = by(F.filesMenu({ ...base, isAdmin: true, role: 'admin', canDownload: true, canWrite: true, modelBuilt: false }));
  assert.ok(Object.values(none).every((i) => !i.enabled && i.reason === 'noModel'));
});

t('Full imports follow the catalog-write rule', () => {
  const ro = by(F.filesMenu({ ...base, canDownload: true, canWrite: false }));
  assert.equal(ro.paramsImport.enabled, false);
  assert.equal(ro.paramsImport.reason, 'readOnly');
  const rw = by(F.filesMenu({ ...base, canDownload: false, canWrite: true }));
  assert.equal(rw.paramsImport.enabled, true);                                                // import needs no NDA, only write rights
  assert.equal(rw.step.enabled, false);
});

t('every Full download has a server path; the menu is sections of export then import', () => {
  const m = F.filesMenu({ ...base, canDownload: true, canWrite: true });
  for (const i of m.filter((x) => x.section === 'export')) assert.ok(F.FULL_DOWNLOADS[i.id], i.id);
  const firstImport = m.findIndex((i) => i.section === 'import');
  assert.ok(m.slice(0, firstImport).every((i) => i.section === 'export'));
  assert.ok(m.slice(firstImport).every((i) => i.section === 'import'));
  assert.ok(m.every((i) => i.labelKey === `files_${i.id}`));
  // a frameless kit: STEP and BOM are the kit's and say so; nothing else changes
  const kit = F.filesMenu({ ...base, canDownload: true, canWrite: true, kit: true });
  assert.deepEqual(kit.map((i) => i.id), m.map((i) => i.id));
  assert.deepEqual(kit.filter((i) => i.labelKey.endsWith('Kit')).map((i) => i.id), ['step', 'bomCsv', 'bomXlsx']);
  assert.equal(kit.find((i) => i.id === 'paramsCsv').labelKey, 'files_paramsCsv');
});
