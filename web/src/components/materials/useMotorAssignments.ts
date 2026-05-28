/**
 * Hook: fetches and updates motor material assignments (motor_config.yaml).
 */
import { useState, useEffect, useCallback } from 'react';

export interface MotorAssignments {
  stator_core: string;
  slot: string;
  air_gap: string;
  rotor_core: string;
  magnet: string;
  shaft: string;
}

const API = 'http://localhost:8000/api/materials';

export function useMotorAssignments() {
  const [assignments, setAssignments] = useState<MotorAssignments | null>(null);
  const [loading, setLoading]         = useState(true);
  const [saving, setSaving]           = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    fetch(API, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => { setAssignments(data); setLoading(false); })
      .catch(e  => { setError(String(e));   setLoading(false); });
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const assign = useCallback((part: string, material: string) => {
    setSaving(true);
    fetch(API, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ part, material }),
    })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => { setAssignments(data.assignments); setSaving(false); })
      .catch(e  => { setError(String(e)); setSaving(false); });
  }, []);

  return { assignments, loading, saving, error, assign, refresh };
}
