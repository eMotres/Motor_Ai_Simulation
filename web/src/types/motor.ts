/**
 * Motor geometry parameter schema (from API)
 */
export interface ParameterSchema {
  name: string;
  label: string;
  unit: string;
  type: 'float' | 'int';
  min: number;
  max: number;
  step: number;
  group: string;
  description: string;
}

/**
 * Parameter group schema (from API)
 */
export interface ParameterGroup {
  id: string;
  label: string;
  order: number;
}

/**
 * Geometry schema response from API
 */
export interface GeometrySchemaResponse {
  parameters: ParameterSchema[];
  groups: ParameterGroup[];
}

/**
 * Motor geometry parameters (dynamic, from API)
 * Using Record for flexibility - actual parameters defined in YAML
 */
export type MotorGeometryParams = Record<string, number>;

/**
 * Material assignment for motor regions
 */
export interface MaterialAssignments {
  stator_core: string;
  slot: string;
  air_gap: string;
  rotor_core: string;
  magnet: string;
  shaft: string;
}

/**
 * Mesh generation settings
 */
export interface MeshSettings {
  n_radial: number;
  n_angular: number;
  n_angular_slots: number;
}

/**
 * Material properties
 */
export interface Material {
  name: string;
  mu_r: number;
  sigma: number;
  B_sat?: number;
  Br?: number;
  Hc?: number;
  density: number;
  color: [number, number, number];
}

/**
 * Material category
 */
export interface MaterialCategory {
  name: string;
  materials: string[];
}

/**
 * Complete motor configuration
 */
export interface MotorConfig {
  geometry: MotorGeometryParams;
  materials: MaterialAssignments;
  mesh: MeshSettings;
}

/**
 * Default geometry parameters (empty - will be populated from API)
 */
export const defaultGeometryParams: MotorGeometryParams = {};

/**
 * Default material assignments
 */
export const defaultMaterialAssignments: MaterialAssignments = {
  stator_core: 'm27_silicon_steel',
  slot: 'copper',
  air_gap: 'air',
  rotor_core: 'm27_silicon_steel',
  magnet: 'ndfeb_n42',
  shaft: 'carbon_steel',
};

/**
 * Default mesh settings
 */
export const defaultMeshSettings: MeshSettings = {
  n_radial: 10,
  n_angular: 64,
  n_angular_slots: 8,
};

/**
 * Compute derived geometry parameters
 * Note: Derived params are now computed on the Python side
 */
export function computeDerivedParams(params: MotorGeometryParams): MotorGeometryParams {
  // Just return params - derived params computed by Python API
  return { ...params };
}
