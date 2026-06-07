import React, { useEffect, useState } from 'react';
import { Box, Typography, Chip, CircularProgress, Tooltip } from '@mui/material';
import GarageIcon from '@mui/icons-material/Garage';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface PresetSummary {
  id: string;
  name: string;
  description: string;
  metrics?: { T_avg_Nm?: number; ripple_pct?: number; gamma_deg?: number };
  order?: number;
}

/**
 * Motor "garage" — a library of ready-made motor designs.  Clicking a card
 * applies that preset (geometry + operating point) on the backend and reloads
 * the app so every tab switches to that motor.
 */
const MotorGarage: React.FC = () => {
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/presets`)
      .then((r) => r.json())
      .then((d) => setPresets(d.presets ?? []))
      .catch(() => setPresets([]))
      .finally(() => setLoading(false));
  }, []);

  const apply = async (id: string) => {
    setApplying(id);
    try {
      await fetch(`${API}/api/presets/${id}/apply`, { method: 'POST' });
      // Full reload: a preset changes geometry + operating point + caches.
      window.location.reload();
    } catch {
      setApplying(null);
    }
  };

  return (
    <Box sx={{ mb: 2, p: 1.25, border: '1px solid #1e293b', borderRadius: 1, bgcolor: '#0b1220' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
        <GarageIcon sx={{ fontSize: 16, color: '#60a5fa' }} />
        <Typography sx={{ fontSize: '0.7rem', fontWeight: 700, color: '#93c5fd',
          letterSpacing: '0.08em', textTransform: 'uppercase' }}>
          Motor Garage
        </Typography>
      </Box>

      {loading ? (
        <CircularProgress size={16} />
      ) : presets.length === 0 ? (
        <Typography sx={{ fontSize: '0.65rem', color: '#64748b' }}>No saved motors.</Typography>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          {presets.map((p) => {
            const m = p.metrics ?? {};
            return (
              <Tooltip key={p.id} title={p.description || ''} placement="right" arrow>
                <Box
                  onClick={() => !applying && apply(p.id)}
                  sx={{
                    p: 1, borderRadius: 1, cursor: 'pointer',
                    border: '1px solid #1e293b', bgcolor: '#111827',
                    transition: 'all 0.12s',
                    '&:hover': { borderColor: '#3b82f6', bgcolor: '#152033' },
                    opacity: applying && applying !== p.id ? 0.4 : 1,
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Typography sx={{ fontSize: '0.75rem', fontWeight: 600, color: '#e2e8f0' }}>
                      {p.name}
                    </Typography>
                    {applying === p.id && <CircularProgress size={12} />}
                  </Box>
                  <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
                    {m.T_avg_Nm != null && (
                      <Chip size="small" label={`T ${m.T_avg_Nm.toFixed(1)} N·m`}
                        sx={{ height: 18, fontSize: '0.6rem', bgcolor: '#14532d', color: '#86efac' }} />
                    )}
                    {m.ripple_pct != null && (
                      <Chip size="small" label={`ripple ${m.ripple_pct.toFixed(1)}%`}
                        sx={{ height: 18, fontSize: '0.6rem',
                          bgcolor: m.ripple_pct < 8 ? '#14532d' : '#7c2d12',
                          color: m.ripple_pct < 8 ? '#86efac' : '#fdba74' }} />
                    )}
                    {m.gamma_deg != null && m.gamma_deg !== 0 && (
                      <Chip size="small" label={`γ ${m.gamma_deg}°`}
                        sx={{ height: 18, fontSize: '0.6rem', bgcolor: '#1e3a5f', color: '#93c5fd' }} />
                    )}
                  </Box>
                </Box>
              </Tooltip>
            );
          })}
        </Box>
      )}
    </Box>
  );
};

export default MotorGarage;
