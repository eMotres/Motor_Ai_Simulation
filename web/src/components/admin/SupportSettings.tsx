/**
 * SupportSettings — configure the AI support assistant from the Admin page.
 *
 * Guided flow:  1) pick the company (Google Gemini / Anthropic Claude)
 *               2) pick the model (loaded live from the provider, static fallback)
 *               3) enter the API key
 *
 * Keys are WRITE-ONLY: sent to the backend and stored server-side (Firestore);
 * the UI only ever shows a masked hint, never the key. Leave the key blank to
 * keep the current one.
 */
import React, { useState } from 'react';
import {
  Box, Paper, Typography, Chip, Select, MenuItem, TextField, Button, CircularProgress,
} from '@mui/material';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface ProviderInfo { model: string; configured: boolean; hint: string | null; keySource: string; }
export interface SupportCfg {
  provider: string; providerOverride?: string; model: string | null; configured: boolean;
  store: string; gemini: ProviderInfo; anthropic: ProviderInfo;
}
type Provider = 'gemini' | 'anthropic';
const TITLE: Record<Provider, string> = { gemini: 'Google Gemini', anthropic: 'Anthropic Claude' };
const COLOR: Record<string, string> = { gemini: '#60a5fa', anthropic: '#a78bfa', none: '#64748b' };

// Curated shortlist of CURRENT models (no retired ones — e.g. Gemini 1.5 is gone).
// The `*-latest` aliases always track Google's newest stable model.
const CURATED: Record<Provider, string[]> = {
  gemini: ['gemini-flash-latest', 'gemini-pro-latest', 'gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.0-flash'],
  anthropic: ['claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
};

const PANEL = { bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1.5, p: 2 } as const;
const STEP = { fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em', mb: 0.75 } as const;

const SupportSettings: React.FC<{ cfg: SupportCfg; onSaved: () => void }> = ({ cfg, onSaved }) => {
  const initial: Provider = (cfg.providerOverride === 'anthropic' || cfg.provider === 'anthropic') ? 'anthropic' : 'gemini';
  // If the saved model is a retired one (e.g. Gemini 1.5/1.0), start from a current default.
  const pickModel = (p: Provider) => (/1\.5|1\.0/.test(cfg[p].model) ? CURATED[p][0] : cfg[p].model);
  const [provider, setProvider] = useState<Provider>(initial);
  const [model, setModel] = useState<string>(pickModel(initial));
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const envOnly = cfg.store === 'env-only';
  const info = cfg[provider];

  const choose = (p: Provider) => { setProvider(p); setModel(pickModel(p)); setApiKey(''); setMsg(null); };

  // model dropdown: curated current models, with the saved model guaranteed present
  const opts = Array.from(new Set([model, ...CURATED[provider]].filter(Boolean)));

  const save = async () => {
    setSaving(true); setMsg(null);
    const body: Record<string, unknown> = {
      provider,
      [provider === 'gemini' ? 'gemini_model' : 'anthropic_model']: model,
    };
    if (apiKey.trim()) body[provider === 'gemini' ? 'gemini_key' : 'anthropic_key'] = apiKey.trim();
    try {
      const r = await fetch(`${API}/api/admin/support`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      }).then((res) => res.json());
      if (r.ok) { setMsg({ ok: true, text: 'Saved ✓' }); setApiKey(''); onSaved(); }
      else setMsg({ ok: false, text: r.error || 'Save failed' });
    } catch {
      setMsg({ ok: false, text: 'Save failed' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Paper sx={{ ...PANEL, mt: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: '#e2e8f0' }}>AI support assistant</Typography>
        <Chip label={`active: ${cfg.provider}`} size="small"
          sx={{ height: 20, fontSize: 10, fontWeight: 700, bgcolor: '#0e1a2f', color: COLOR[cfg.provider] ?? '#64748b' }} />
      </Box>

      <Box sx={{ maxWidth: 460 }}>
        {/* Step 1 — company */}
        <Typography sx={STEP}>1 · Company</Typography>
        <Select value={provider} onChange={(e) => choose(e.target.value as Provider)} size="small" fullWidth
          sx={{ mb: 2, fontSize: 13, bgcolor: '#060d17' }}>
          <MenuItem value="gemini" sx={{ fontSize: 13 }}>Google Gemini</MenuItem>
          <MenuItem value="anthropic" sx={{ fontSize: 13 }}>Anthropic Claude</MenuItem>
        </Select>

        {/* Step 2 — model */}
        <Typography sx={STEP}>2 · Model</Typography>
        <Select value={opts.includes(model) ? model : ''} onChange={(e) => setModel(e.target.value)} size="small" fullWidth
          displayEmpty sx={{ mb: 2, fontSize: 13, bgcolor: '#060d17' }}
          MenuProps={{ PaperProps: { sx: { maxHeight: 340, bgcolor: '#0b1424' } } }}>
          {opts.length === 0 && <MenuItem value="" disabled sx={{ fontSize: 13 }}>loading…</MenuItem>}
          {opts.map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 13 }}>{m}</MenuItem>)}
        </Select>

        {/* Step 3 — key */}
        <Typography sx={STEP}>3 · API key {TITLE[provider]}</Typography>
        <Typography sx={{ fontSize: 11, color: info.configured ? '#4ade80' : '#f87171', mb: 0.5 }}>
          {info.configured ? `current: ${info.hint} (${info.keySource}) — leave blank to keep` : 'no key set yet'}
        </Typography>
        <TextField value={apiKey} onChange={(e) => setApiKey(e.target.value)} size="small" fullWidth type="password"
          placeholder={info.configured ? '•••••••• (unchanged)' : 'paste your API key…'} autoComplete="off"
          inputProps={{ style: { fontSize: 13 } }} />

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 2 }}>
          <Button variant="contained" onClick={() => void save()} disabled={saving || !model}
            sx={{ textTransform: 'none' }}>
            {saving ? <CircularProgress size={18} /> : 'Save settings'}
          </Button>
          {msg && <Typography sx={{ fontSize: 12.5, color: msg.ok ? '#4ade80' : '#f87171' }}>{msg.text}</Typography>}
          <Box sx={{ flex: 1 }} />
          <Typography sx={{ fontSize: 10, color: '#64748b' }}>store: {cfg.store}</Typography>
        </Box>

        <Typography sx={{ fontSize: 10.5, color: '#64748b', mt: 1.5, lineHeight: 1.5 }}>
          Keys are stored on the server and never shown again — only a masked hint.
          {envOnly
            ? ' This server has no settings store (Firebase Admin SDK not configured), so saving is disabled here — set keys via Cloud Run env vars.'
            : ' ⚠️ Make sure your Firestore rules deny client reads on the /config collection (see firestore.rules).'}
        </Typography>
      </Box>
    </Paper>
  );
};

export default SupportSettings;
