/**
 * Motor geometry parameters (all dimensions in mm, angles in degrees)
 */
export interface MotorGeometryParams {
  // Stator parameters
  stator_diameter: number;
  slot_height: number;
  core_thickness: number;
  num_seg: number;
  num_slots_per_segment: number;
  num_poles_per_segment: number;
  stator_width: number;
  air_gap: number;
  
  // Slot details
  tooth_width: number;
  insulation_thickness: number;
  wire_width: number;
  wire_height: number;
  wire_spacing_x: number;
  wire_spacing_y: number;
  num_wires_per_slot: number;
  
  // Rotor parameters
  magnet_height: number;
  rotor_house_height: number;
  
  // Derived parameters (computed)
  stator_outer_radius?: number;
  stator_inner_radius?: number;
  rotor_outer_radius?: number;
  rotor_inner_radius?: number;
  num_slots?: number;
  num_poles?: number;
  angle_slot?: number;
  angle_pole?: number;
}

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
 * Default geometry parameters
 */
export const defaultGeometryParams: MotorGeometryParams = {
  stator_diameter: 200.0,
  slot_height: 16.0,
  core_thickness: 3.8,
  num_seg: 6,
  num_slots_per_segment: 6,
  num_poles_per_segment: 7,
  stator_width: 30.0,
  air_gap: 0.65,
  tooth_width: 8.6,
  insulation_thickness: 0.15,
  wire_width: 4.0,
  wire_height: 0.6,
  wire_spacing_x: 0.1,
  wire_spacing_y: 0.13,
  num_wires_per_slot: 15,
  magnet_height: 13.8,
  rotor_house_height: 1.2,
};

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
 */
export function computeDerivedParams(params: MotorGeometryParams): MotorGeometryParams {
  const stator_outer_radius = params.stator_diameter / 2.0;
  const stator_inner_radius = stator_outer_radius - params.core_thickness - params.slot_height;
  const rotor_outer_radius = stator_outer_radius - params.core_thickness - params.slot_height - params.air_gap;
  const rotor_inner_radius = rotor_outer_radius - params.magnet_height - params.rotor_house_height;
  const num_slots = params.num_seg * params.num_slots_per_segment;
  const num_poles = params.num_seg * params.num_poles_per_segment;
  const angle_slot = 360.0 / num_slots;
  const angle_pole = 360.0 / num_poles;
  
  return {
    ...params,
    stator_outer_radius,
    stator_inner_radius,
    rotor_outer_radius,
    rotor_inner_radius,
    num_slots,
    num_poles,
    angle_slot,
    angle_pole,
  };
}
