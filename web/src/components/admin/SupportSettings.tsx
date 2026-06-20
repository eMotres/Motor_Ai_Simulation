/**
 * SupportSettings — configure the AI support assistant from the Admin page.
 *
 * Choose the provider (Auto / Google Gemini / Anthropic Claude), the model per
 * provider, and the API key per provider. Keys are WRITE-ONLY: they're sent to
 * the backend and stored server-side (Firestore); the UI only ever shows a
 * masked hint, never the key. Leave a key field blank to keep the current one.
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

const PANEL = { bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1.5, p: 2 } as const;
const LABEL = { fontSize: 10, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' } as const;
const PROVIDER_COLOR: Record<string, string> = { gemini: '#60a5fa', anthropic: '#a78bfa', none: '#64748b' };

const ProviderBlock: React.FC<{
  name: 'gemini' | 'anthropic'; title: string; info: ProviderInfo; active: boolean;
  model: string; onModel: (v: string) => void; keyVal: string; onKey: (v: string) => void;
}> = ({ name, title, info, active, model, onModel, keyVal, onKey }) => (
  <Box sx={{ flex: '1 1 300px', minWidth: 280, bgcolor: '#060d17', border: '1px solid #1e293b', borderRadius: 1, p: 1.5 }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
      <Typography sx={{ fontSize: 13, fontWeight: 700, color: PROVIDER_COLOR[name] }}>{title}</Typography>
      {active && <Chip label="active" size="small" sx={{ height: 17, fontSize: 9, bgcolor: '#052e16', color: '#4ade80' }} />}
      <Box sx={{ flex: 1 }} />
      <Typography sx={{ fontSize: 10.5, color: info.configured ? '#4ade80' : '#f87171' }}>
        {info.configured ? `key set · ${info.hint} (${info.keySource})` : 'no key'}
      </Typography>
    </Box>
    <Typography sx={LABEL}>Model</Typography>
    <TextField value={model} onChange={(e) => onModel(e.target.value)} size="small" fullWidth sx={{ mb: 1.25, mt: 0.25 }}
      inputProps={{ style: { fontSize: 12.5 } }} />
    <Typography sx={LABEL}>API key {info.configured ? '(leave blank to keep)' : ''}</Typography>
    <TextField value={keyVal} onChange={(e) => onKey(e.target.value)} size="small" fullWidth type="password"
      placeholder={info.configured ? '•••••••• (unchanged)' : 'paste key…'} autoComplete="off"
      sx={{ mt: 0.25 }} inputProps={{ style: { fontSize: 12.5 } }} />
  </Box>
);

const SupportSettings: React.FC<{ cfg: SupportCfg; onSaved: () => void }> = ({ cfg, onSaved }) => {
  const [provider, setProvider] = useState(cfg.providerOverride || 'auto');
  const [gModel, setGModel] = useState(cfg.gemini.model);
  const [aModel, setAModel] = useState(cfg.anthropic.model);
  const [gKey, setGKey] = useState('');
  const [aKey, setAKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const envOnly = cfg.store === 'env-only';

  const save = async () => {
    setSaving(true); setMsg(null);
    const body: Record<string, unknown> = { provider, gemini_model: gModel, anthropic_model: aModel };
    if (gKey.trim()) body.gemini_key = gKey.trim();
    if (aKey.trim()) body.anthropic_key = aKey.trim();
    try {
      const r = await fetch(`${API}/api/admin/support`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      }).then((res) => res.json());
      if (r.ok) { setMsg({ ok: true, text: 'Saved ✓' }); setGKey(''); setAKey(''); onSaved(); }
      else setMsg({ ok: false, text: r.error || 'Save failed' });
    } catch {
      setMsg({ ok: false, text: 'Save failed' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Paper sx={{ ...PANEL, mt: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: '#e2e8f0' }}>AI support assistant</Typography>
        <Chip label={`active: ${cfg.provider}`} size="small"
          sx={{ height: 20, fontSize: 10, fontWeight: 700, bgcolor: '#0e1a2f', color: PROVIDER_COLOR[cfg.provider] ?? '#64748b' }} />
        <Box sx={{ flex: 1 }} />
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography sx={LABEL}>Provider</Typography>
          <Select value={provider} onChange={(e) => setProvider(e.target.value)} size="small" variant="standard" disableUnderline
            sx={{ fontSize: 12.5, fontWeight: 700, color: '#e2e8f0', bgcolor: '#060d17', px: 1, borderRadius: 0.5, '& svg': { color: '#475569' } }}>
            <MenuItem value="auto" sx={{ fontSize: 12.5 }}>Auto</MenuItem>
            <MenuItem value="gemini" sx={{ fontSize: 12.5 }}>Google Gemini</MenuItem>
            <MenuItem value="anthropic" sx={{ fontSize: 12.5 }}>Anthropic Claude</MenuItem>
          </Select>
        </Box>
      </Box>

      <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', mb: 1.5 }}>
        <ProviderBlock name="gemini" title="Google Gemini" info={cfg.gemini} active={cfg.provider === 'gemini'}
          model={gModel} onModel={setGModel} keyVal={gKey} onKey={setGKey} />
        <ProviderBlock name="anthropic" title="Anthropic Claude" info={cfg.anthropic} active={cfg.provider === 'anthropic'}
          model={aModel} onModel={setAModel} keyVal={aKey} onKey={setAKey} />
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Button variant="contained" size="small" onClick={() => void save()} disabled={saving || envOnly}
          sx={{ textTransform: 'none' }}>
          {saving ? <CircularProgress size={16} /> : 'Save settings'}
        </Button>
        {msg && <Typography sx={{ fontSize: 12, color: msg.ok ? '#4ade80' : '#f87171' }}>{msg.text}</Typography>}
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 10, color: '#64748b' }}>store: {cfg.store}</Typography>
      </Box>

      <Typography sx={{ fontSize: 10.5, color: '#64748b', mt: 1.25, lineHeight: 1.5 }}>
        Keys are stored on the server and never shown again — only a masked hint.
        {envOnly
          ? ' This server has no settings store (Firebase Admin SDK not configured), so saving is disabled here — set keys via Cloud Run env vars. '
          : ' ⚠️ Make sure your Firestore rules deny client reads on the /config collection (see firestore.rules). '}
        Provider “Auto” uses whichever key is set (Gemini preferred).
      </Typography>
    </Paper>
  );
};

export default SupportSettings;
