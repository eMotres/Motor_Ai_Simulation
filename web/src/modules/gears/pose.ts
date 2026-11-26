// Client-side pose: the formula of docs/gear_module/CORE_API_AND_LIMITATIONS.md ("Pose"), from the placement data the assembly
// answer already carries.  rotation = reference + speed_ratio * angle; a planet orbits its carrier centre at
// orbital_angle_ref + carrier_ratio * angle.  The pose operation stays the reference: web tests compare this against its fixture.
import type { AssemblyResult, PoseMember } from './types';

const rad = (deg: number) => (deg * Math.PI) / 180;

export function poseAt(assembly: AssemblyResult, inputAngleDeg: number): Record<string, PoseMember> {
  const out: Record<string, PoseMember> = {};
  for (const p of assembly.members) {
    const rotation = p.reference_angle_deg + p.speed_ratio_to_input * inputAngleDeg;
    let centre = p.center_mm;
    if (p.orbital_radius_mm > 0) {
      const a = rad(p.orbital_angle_ref_deg + p.carrier_speed_ratio_to_input * inputAngleDeg);
      centre = [p.carrier_center_mm[0] + p.orbital_radius_mm * Math.cos(a), p.carrier_center_mm[1] + p.orbital_radius_mm * Math.sin(a), p.center_mm[2]];
    }
    out[p.name] = { name: p.name, member_id: p.member_id, role: p.role, center_mm: centre, rotation_deg: rotation };
  }
  return out;
}
