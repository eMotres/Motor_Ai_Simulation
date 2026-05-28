/**
 * Hook: fetches the full materials library from the backend once and caches it.
 */
import { useEffect, useState } from 'react';

// ─── Types (mirror Python dataclasses) ───────────────────────────────────────

export interface BHPoint { 0: number; 1: number }   // [H or B, B or P]

export interface SteelData {
  description: string;
  form: string;
  sigma: number;
  density: number;
  stacking_factor: number;
  core_loss_model: string;
  core_loss_kh: number;
  core_loss_kc: number;
  core_loss_ke: number;
  core_loss_curve_unit: string;
  bh_curve: [number, number][];
  core_loss_curves?: Record<string, [number, number][]>;
}

export interface MagnetData {
  description: string;
  Br: number;
  Hc: number;
  mu_rec: number;
  sigma: number;
  density: number;
  energy_product_kj_m3: number;
  bh_curve: [number, number][];
}

export interface ConductorData {
  description: string;
  sigma: number;
  resistivity: number;
  density: number;
  thermal_conductivity: number | null;
  specific_heat: number | null;
  thermal_alpha: number | null;
  wire_width_mm: number | null;
  wire_height_mm: number | null;
}

export interface MaterialsLibrary {
  steel: Record<string, SteelData>;
  magnet: Record<string, MagnetData>;
  conductor: Record<string, ConductorData>;
}

export type MaterialCategory = 'steel' | 'magnet' | 'conductor';

export interface SelectedMaterial {
  category: MaterialCategory;
  name: string;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useMaterialsLibrary() {
  const [library, setLibrary] = useState<MaterialsLibrary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch('http://localhost:8000/api/materials/library')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => { setLibrary(data); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, []);

  return { library, loading, error };
}
