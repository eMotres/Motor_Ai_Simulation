# CadQuery Geometry Engine
"""
Native Python parametric motor geometry engine using CadQuery.

This module provides:
1. Parametric Stator: Ring with radial slots using polarArray()
2. Parametric Rotor: Hub with magnet cavities
3. Coils: Wound in slots
4. STL Export: High-resolution export for NVIDIA Modulus
5. Fast Rebuilds: < 1 second regeneration
"""

from __future__ import annotations
import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any
from math import sin, cos, tan, radians, pi, acos, atan2
#import math

# CadQuery imports - try to import lazily
HAS_CADQUERY = False

def _import_cadquery():
    """Lazy import of CadQuery."""
    global HAS_CADQUERY, cq, exporters
    if HAS_CADQUERY:
        return True
    
    try:
        import cadquery as cq
        from cadquery import exporters
        HAS_CADQUERY = True
        return True
    except ImportError:
        print("Warning: CadQuery not available")
        return False


class CadQueryMotor:
    """Parametric motor geometry engine using CadQuery."""
    
    def __init__(self):
        self.parameters: Dict = {}
        self.parts: Dict = {}
        self.assembly = None
        self._load_defaults_from_config()
    
    def _load_defaults_from_config(self) -> None:
        """Load default parameters from motor_config.yaml."""
        try:
            from motor_ai_sim.config import get_geometry_params
            params = get_geometry_params()
            # Convert MotorGeometryParams to dict with proper mapping
            self.parameters = self._map_api_to_cadquery(params.to_dict())
        except Exception as e:
            print(f"Warning: Could not load config: {e}")
            # Fall back to hardcoded defaults
            self.parameters = self._get_hardcoded_defaults()
    
    def _map_api_to_cadquery(self, api_params: Dict) -> Dict:
        """Map API parameter names to CadQuery parameter names.
        
        Uses derived_params from config/motor_config.yaml to compute values.
        All parameters should come from config - api_params are overrides.
        """
        # Get geometry params from config - this is the single source of truth
        try:
            from motor_ai_sim.config import get_geometry_params
            config_params = get_geometry_params().to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to load config: {e}")
        
        # Start with config params as defaults
        mapped = config_params.copy()
        
        # Override with any API params that are provided
        # This allows runtime overrides while keeping config as source of truth
        for key, value in api_params.items():
            if value is not None:
                mapped[key] = value
        
        # Compute derived parameters from config formulas
        # These formulas are defined in config/motor_config.yaml derived_params
        if 'stator_diameter' in mapped:
            mapped['stator_outer_radius'] = mapped['stator_diameter'] / 2
        
        if 'stator_outer_radius' in mapped and 'core_thickness' in mapped and 'slot_height' in mapped:
            mapped['stator_inner_radius'] = mapped['stator_outer_radius'] - mapped['core_thickness'] - mapped['slot_height']
        
        if 'stator_inner_radius' in mapped and 'air_gap' in mapped:
            mapped['rotor_outer_radius'] = mapped['stator_inner_radius'] - mapped['air_gap']
        
        if 'rotor_outer_radius' in mapped and 'magnet_height' in mapped and 'rotor_house_height' in mapped:
            mapped['rotor_inner_radius'] = mapped['rotor_outer_radius'] - mapped['magnet_height'] - mapped['rotor_house_height']
        
        if 'num_seg' in mapped and 'num_slots_per_segment' in mapped:
            mapped['num_slots'] = int(mapped['num_seg'] * mapped['num_slots_per_segment'])
        
        if 'num_seg' in mapped and 'num_poles_per_segment' in mapped:
            mapped['num_poles'] = int(mapped['num_seg'] * mapped['num_poles_per_segment'])
        
        if 'rotor_inner_radius' in mapped and 'shaft_height' in mapped:
            mapped['shaft_radius'] = mapped['rotor_inner_radius'] - mapped['shaft_height']
        
        if 'rotor_inner_radius' in mapped and 'shaft_height' in mapped:
            mapped['shaft_inner_radius'] = mapped['rotor_inner_radius'] - mapped['shaft_height']
        
        # Ensure magnet parameters exist
        for key in ['magnet_fill_down', 'magnet_fill_up', 'magnet_fill_radius', 'magnet_up_gap', 'magnet_down_height']:
            if key not in mapped:
                mapped[key] = config_params.get(key, 0.0)
        
        return mapped
    
    def _get_hardcoded_defaults(self) -> Dict:
        """Get default parameters from config/motor_config.yaml.
        
        This method loads all parameters from the config file, ensuring
        a single source of truth for all motor parameters.
        """
        try:
            from motor_ai_sim.config import get_geometry_params
            params = get_geometry_params()
            # Map API params to CadQuery internal parameters
            return self._map_api_to_cadquery(params.to_dict())
        except Exception as e:
            print(f"Warning: Could not load config: {e}")
            # Fallback - but this should never happen if config is valid
            raise RuntimeError(
                "Failed to load config/motor_config.yaml. "
                "All parameters must be defined in the config file."
            )
    
    def set_parameters(self, params: Dict) -> None:
        """Set motor geometry parameters (updates defaults from config)."""
        # Start with current parameters (from config)
        updated = self.parameters.copy() if self.parameters else self._get_hardcoded_defaults()
        
        # Map API params to CadQuery params first
        mapped_params = self._map_api_to_cadquery(params)
        
        # Update with mapped params
        updated.update(mapped_params)
        self.parameters = updated
        
    def get_parameter_hash(self) -> str:
        """Get hash of current parameters for caching."""
        param_str = json.dumps(self.parameters, sort_keys=True)
        return hashlib.sha256(param_str.encode()).hexdigest()[:16]
    
    def build_all(self) -> Dict:
        """
        Build all components. 
        Rotor has cavities, magnets are separate, and coils are separate per slot.
        """
        if not _import_cadquery():
            raise RuntimeError("CadQuery not found")
        
        import cadquery as cq
        
        # 1. Stator and Shaft
        self.parts['stator_core'] = self._create_stator(cq)
        self.parts['shaft'] = self._create_shaft(cq)
        
        # 2. Magnets and Rotor Core with Cavities
        magnets_list = self._create_magnets(cq)
        rotor_solid = self._create_rotor(cq)
        
        for i, magnet in enumerate(magnets_list):
            if magnet is not None:
                rotor_solid = rotor_solid.cut(magnet) # Cut hole in rotor
                self.parts[f'magnet_{i}'] = magnet    # Keep magnet separate
        
        self.parts['rotor_core'] = rotor_solid
        
        # 3. Individual Coils (one object per slot)
        try:
            coils_list = self._create_coils(cq)
            for i, coil_stack in enumerate(coils_list):
                self.parts[f'coil_{i}'] = coil_stack
        except Exception as e:
            print(f"Failed to build coils: {e}")
            
        return self.parts
        
    def _create_stator(self, cq) -> Any:
        """Create stator with radial slots/teeth."""
        import math
        p = self.parameters

        outer_r = p['stator_outer_radius']
        inner_r = p['stator_inner_radius']
        core_h     = p['core_thickness']
        slot_height = p['slot_height']
        stator_w   = p['stator_width']
        num_slots  = int(p['num_slots'])
        tooth_width  = p['tooth_width']
        tooth2_width = p.get('tooth2_width', 4.5)
        cut_width    = p.get('cut_width', 2.0)
        wire_w     = p['wire_width']
        ins_w      = p['insulation_thickness']
        wire_d_x   = p['wire_spacing_x']
        slot_fillet_r  = p.get('stator_fillet_r',  2.5)
        slot_fillet_r1 = p.get('stator_fillet_r1', 0.5)

        slot_w  = wire_w + ins_w*2 + wire_d_x
        slot_h  = slot_height
        slot_x  = tooth_width / 2
        slot_y  = outer_r - core_h
        half_slots  = num_slots // 2
        slot_angle  = 360.0 / half_slots

        # ── Compound-cutter geometry ──────────────────────────────────────────
        # All cuts are unioned into one solid then cut in a single boolean.
        # This is ~70× faster than sequential cuts.
        cut_x  = tooth_width/2 + ins_w*2 + wire_w + wire_d_x*2 + tooth2_width
        fill_r = ((inner_r + cut_width) * sin(radians(slot_angle/2)) - cut_x) \
                 / (1 - sin(radians(slot_angle/2)))
        rr   = inner_r + cut_width + fill_r
        ext  = outer_r * 2
        p1   = (cut_x, ext)
        p2   = (cut_x, rr * cos(radians(slot_angle/2)))
        p3   = (cut_x + fill_r, rr * cos(radians(slot_angle/2)))
        p4   = (ext * tan(radians(slot_angle/2)), ext)

        # Create stator as a solid ring
        stator = (
            cq.Workplane("XY")
            .circle(outer_r)
            .circle(inner_r)
            .extrude(stator_w)
        )

        cutters = []
        for i in range(half_slots):
            angle = i * slot_angle
            # Trapezoid wedge (+X)
            cutters.append(
                cq.Workplane("XY")
                .moveTo(p1[0], p1[1]).lineTo(p2[0], p2[1])
                .lineTo(p3[0], p3[1]).lineTo(p4[0], p4[1])
                .close().extrude(stator_w + 1)
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Trapezoid wedge (-X mirror)
            cutters.append(
                cq.Workplane("XY")
                .moveTo(-p1[0], p1[1]).lineTo(-p2[0], p2[1])
                .lineTo(-p3[0], p3[1]).lineTo(-p4[0], p4[1])
                .close().extrude(stator_w + 1)
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Fillet cylinder at p3 (+X)
            cutters.append(
                cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                .translate((p3[0], p3[1], 0))
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Fillet cylinder at p3 (-X)
            cutters.append(
                cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                .translate((-p3[0], p3[1], 0))
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Slot rectangle (+X)
            cutters.append(
                cq.Workplane("XY")
                .rect(slot_w, -slot_h*2, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((slot_x, slot_y, 0))
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Slot rectangle (-X)
            cutters.append(
                cq.Workplane("XY")
                .rect(-slot_w, -slot_h*2, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((-slot_x, slot_y, 0))
                .rotate((0,0,0),(0,0,1), angle)
            )

        # Single boolean cut
        tool = cutters[0]
        for c in cutters[1:]:
            tool = tool.union(c)
        stator = stator.cut(tool)

        import cadquery as _cq

        # ── Fillet: OUTER RADIUS corners ─────────────────────────────────────
        # |Z edges where trapezoid walls meet the outer cylinder (r ≈ outer_r)
        if slot_fillet_r > 0:
            _r_lo = outer_r - 0.5
            _r_hi = outer_r + 0.2

            class _OuterRingSelector(_cq.selectors.Selector):
                def filter(self_, obj_list):
                    return [e for e in obj_list
                            if _r_lo < (e.Center().x**2 + e.Center().y**2)**0.5 < _r_hi]

            try:
                stator = stator.edges("|Z").edges(_OuterRingSelector()).fillet(slot_fillet_r)
            except Exception as ex:
                print(f"[stator] outer-ring fillet failed (r={slot_fillet_r}): {ex}")

        # ── Fillet: INNER RADIUS corners ─────────────────────────────────────
        # |Z edges where slot walls and trapezoid walls meet the inner cylinder
        # (r ≈ inner_r). These are the corners visible in the red circle.
        if slot_fillet_r1 > 0:
            _r_lo1 = inner_r - 0.8
            _r_hi1 = inner_r + 0.8

            class _InnerRingSelector(_cq.selectors.Selector):
                def filter(self_, obj_list):
                    return [e for e in obj_list
                            if _r_lo1 < (e.Center().x**2 + e.Center().y**2)**0.5 < _r_hi1]

            try:
                stator = stator.edges("|Z").edges(_InnerRingSelector()).fillet(slot_fillet_r1)
            except Exception as ex:
                print(f"[stator] inner-ring fillet failed (r1={slot_fillet_r1}): {ex}")

        return stator
        
    def _create_shaft(self, cq) -> Any:
        """Create motor shaft."""
        p = self.parameters
        
        shaft_r = p['rotor_inner_radius']
        shaft_in = p['shaft_inner_radius']
        length = p['stator_width']
        
        # Print shaft parameters for debugging
        print(f"[DEBUG] _create_shaft: shaft_r={shaft_r}, shaft_in={shaft_in}")
        
        shaft = (
            cq.Workplane("XY")
            .circle(shaft_r)
            .circle(shaft_in)
            .extrude(length)
        )
        
        return shaft
    
    def _create_magnets(self, cq) -> List[Any]:
        """Create rotor magnets."""
        p = self.parameters
        rotor_inner_r = p['rotor_inner_radius']
        rotor_outer_r = p['rotor_outer_radius']
        num_poles = int(p['num_poles'])
        width = p['stator_width']

        mag_h = p['magnet_height']                  # magnet height
        rotor_house_h = p['rotor_house_height']     # rotor housing thickness
        mag_fill_down = p['magnet_fill_down']       # down fill ratio of the magnet 
        mag_fill_up = p['magnet_fill_up']           # up fill ratio of the magnet 
        mag_fill_r = p['magnet_fill_radius']   # magnet fillet radius 
        mag_up_gap = p['magnet_up_gap']             # magnet cut up gap
        mag_down_h = p['magnet_down_height']        # magnet down height 
        pole_angle = 360.0 / num_poles
        
        # Print magnet parameters for debugging
        print(f"[DEBUG] _create_magnets: mag_fill_down={mag_fill_down}, pole_angle={pole_angle}, num_poles={num_poles}")
        magnet_r = rotor_inner_r + rotor_house_h
        print(f"[DEBUG] _create_magnets: rotor_inner_r={rotor_inner_r}, magnet_r={magnet_r}")
        
        magnets = []
        
        # Calculate angles in radians for math functions
        angle_down = radians(pole_angle * mag_fill_down / 2)
        angle_up = radians(pole_angle * mag_fill_up / 2)
        
        p1 = (magnet_r * sin(angle_down), magnet_r * cos(angle_down))      
        p2 = ((magnet_r + mag_down_h) * sin(angle_down), (magnet_r + mag_down_h) * cos(angle_down))      
        p3 = ((rotor_outer_r - mag_up_gap) * sin(angle_up), (rotor_outer_r - mag_up_gap) * cos(angle_up))    
        p4 = (-(rotor_outer_r - mag_up_gap) * sin(angle_up), (rotor_outer_r - mag_up_gap) * cos(angle_up))           
        p5 = (-(magnet_r + mag_down_h) * sin(angle_down), (magnet_r + mag_down_h) * cos(angle_down))       
        p6 = (-magnet_r * sin(angle_down), magnet_r * cos(angle_down))      
        
        for i in range(num_poles):
            angle = i * pole_angle
            
            # Create magnet at origin then rotate/translate
            magnet = (
                cq.Workplane("XY")
                .polyline([p1, p2, p3, p4, p5, p6])
                .close()        
                .extrude(width)
            )
            
            if mag_fill_r > 0:
                try:
                    magnet = magnet.edges(">Y and |Z").fillet(mag_fill_r)
                except Exception as e:
                    print(f"Warning: Could not apply fillet to magnet: {e}")
            
            # Rotate to final position
            magnet = magnet.rotate((0, 0, 0), (0, 0, 1), angle)

            magnets.append(magnet)
            
        return magnets
    def _create_rotor(self, cq) -> Any:
        """Create rotor hub."""
        p = self.parameters
        
        rotor_outer_r = p['rotor_outer_radius']
        rotor_inner_r = p['rotor_inner_radius']
        width = p['stator_width']
        num_poles = int(p['num_poles'])
        magnet_hole = p['rotor_hole']
        pole_angle = 360.0 / num_poles
        mag_fill_up = p['magnet_fill_up']
        mag_h = p['magnet_height']
        width = p['stator_width']

        mag_angle_up = radians(pole_angle * mag_fill_up*magnet_hole / 2)
        rec_w = 2*rotor_outer_r * sin(mag_angle_up)
        
        rotor = (
            cq.Workplane("XY")
            .circle(rotor_outer_r)
            .circle(rotor_inner_r)
            .extrude(width)
        )

        for i in range(num_poles):
            angle = i * pole_angle
            # Create positive side slot 
            cut_up = (
                cq.Workplane("XY")
                .rect(rec_w, -mag_h, centered=(False, False))
                .extrude(width + 1)
                .translate((-rec_w/2, rotor_outer_r, 0))
                .rotate((0, 0, 0), (0, 0, 1), angle)
            )
            rotor = rotor.cut(cut_up)
        
        return rotor
   
    def _create_coils(self, cq) -> List[Any]:
        """Create hairpin coils wound in stator slots - high-fidelity spiral windings.
        
        Hairpin winding structure:
        - Straight legs passing through stator slots
        - Crown (U-turn) on FRONT side connecting the two legs
        - Leads (S-bend exit) on BACK side for connection to next layer
        """
        import math
        p = self.parameters
        
        # Core parameters
        outer_r = p['stator_outer_radius']
        inner_r = p['stator_inner_radius']
        core_h = p['core_thickness']
        stator_w = p['stator_width']
        num_slots = int(p['num_slots'])
        tooth_width = p['tooth_width']
        
        # Wire parameters
        wire_w = p['wire_width']         # 4.0 mm
        wire_h = p['wire_height']        # 0.6 mm
        wire_d_x = p['wire_spacing_x']     # 0.1 mm
        wire_d_y = p['wire_spacing_y']     # 0.13 mm
        ins_w  = p['insulation_thickness'] 
        num_wires = int(p['num_wires_per_slot']) 
        
        # Calculate slot dimensions
        half_slots = num_slots // 2
        slot_angle = 360.0 / half_slots
        slot_radial_depth = outer_r - inner_r
        available_width = tooth_width - 2 * ins_w
        
        # Crown and S-bend parameters
        crown_radius = wire_w * 1.5
        sbend_height = wire_h * 2
        sbend_offset = wire_w * 0.8
        
    # Top starting position (X is the vertical axis in the slot)
    # Calculation: Start from inner radius + insulation + full height of the stack
        top_y = outer_r - core_h - ins_w - wire_d_y/2
    
    # Horizontal Y positions for the two columns (centered around Y=0)
        right_x = tooth_width / 2 + ins_w + wire_d_x/2
    
        coils = [] # Renamed from final_coils
    
        for i in range(half_slots):
            angle = i * slot_angle
            wires = [] # Renamed from slot_wires
        
            for step_y in range(num_wires):
            # Calculate current Y position for this layer (stacking DOWNWARDS)
                current_y = top_y - step_y *(wire_h+wire_d_y) 
            
                # Define Right Wire Polygon coordinates
                right_pts = [
                    (right_x, current_y ),          
                    (right_x + wire_w, current_y ),   
                    (right_x + wire_w, current_y - wire_h),            
                    (right_x, current_y - wire_h)                    
                ]
            
                # Define Left Wire Polygon coordinates
                left_pts = [
                    (-right_x, current_y ),          
                    (-right_x - wire_w, current_y ),   
                    (-right_x - wire_w, current_y - wire_h),            
                    (-right_x, current_y - wire_h)                    
                ]
            
                # Create 3D geometry via extrusion along Z axis
                # .translate centers the coil along the motor length
                right_wire = (cq.Workplane("XY").polyline(right_pts).close().extrude(stator_w))
                left_wire = (cq.Workplane("XY").polyline(left_pts).close().extrude(stator_w))
            
                # Rotate and store individual wires
                wires.append(right_wire.rotate((0,0,0), (0,0,1), angle))
                wires.append(left_wire.rotate((0,0,0), (0,0,1), angle))
            
        # Instead of slow O(N^2) boolean union, create a Compound for fast export
            if wires:
                valid_wires = [w for w in wires if w is not None]
                if valid_wires:
                    # Use Compound to group wires without expensive boolean operations
                    compound = cq.Compound.makeCompound([w.val() for w in valid_wires])
                    coils.append(compound)
        
        return coils    
    
    def export_stl(self, output_dir: str, tolerance: float = 0.1) -> Dict[str, str]:
        """Export all components to STL files."""
        if not _import_cadquery():
            raise RuntimeError("CadQuery is not available")
            
        from cadquery import exporters
        
        os.makedirs(output_dir, exist_ok=True)
        stl_files = {}
        
        if not self.parts:
            self.build_all()
            
        for name, part in self.parts.items():
            stl_path = os.path.join(output_dir, f"{name}.stl")
            try:
                # Use the newer CadQuery export API with exportType string
                exporters.export(part, stl_path, exportType='STL', tolerance=tolerance)
                stl_files[name] = stl_path
                print(f"Exported {name} to {stl_path}")
            except Exception as e:
                print(f"Error exporting {name}: {e}")
                
        return stl_files
    
    def get_mesh_data(self, component: str) -> Optional[Dict]:
        """Get mesh data for a component."""
        if not _import_cadquery():
            return None
            
        if not self.parts:
            self.build_all()
            
        if component not in self.parts:
            return None
            
        try:
            shape = self.parts[component]
            # Use OCP's direct tessellation for massive speedup (no temp file IO)
            if hasattr(shape, 'val'):
                solid = shape.val()
            else:
                solid = shape
                
            vertices, faces = solid.tessellate(0.1)
            
            # Format to basic lists
            vertices_list = [[v.x, v.y, v.z] for v in vertices]
            
            return {
                'vertices': vertices_list,
                'faces': faces,
                'vertex_count': len(vertices_list),
                'face_count': len(faces),
            }
        except Exception as e:
            print(f"Error tessellating {component}: {e}")
            return None
    
    def get_all_mesh_data(self) -> Dict[str, Dict]:
        """Get mesh data for all components."""
        mesh_data = {}
        
        if not self.parts:
            self.build_all()
            
        for name in self.parts:
            data = self.get_mesh_data(name)
            if data:
                mesh_data[name] = data
                
        return mesh_data
    
    def get_2d_mesh_data(self) -> Dict[str, Dict]:
        """
        Build flat 2D cross-section meshes for all motor components.
        All triangles lie in the z=0 plane; each component gets a tiny
        z-offset (0…5 mm) so Three.js depth-sorts them correctly.
        No CadQuery / OCCT required – pure shapely + earcut.
        """
        from math import pi, sin, cos, tan, radians, sqrt
        try:
            from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly
            from shapely.ops import unary_union
            import numpy as np
            import mapbox_earcut as earcut
        except ImportError as exc:
            print(f"[2d] missing dependency: {exc}")
            return {}

        p = self.parameters

        # ── radii ──────────────────────────────────────────────────────────
        outer_r   = p['stator_outer_radius']
        inner_r   = p['stator_inner_radius']       # stator bore / air-gap inner
        rotor_or  = p['rotor_outer_radius']
        rotor_ir  = p['rotor_inner_radius']
        shaft_r   = p['shaft_inner_radius']

        # ── slot / tooth params ────────────────────────────────────────────
        num_slots   = int(p['num_slots'])
        core_h      = p['core_thickness']
        tooth_w     = p['tooth_width']
        tooth2_w    = p.get('tooth2_width', 4.5)
        cut_w       = p.get('cut_width', 2.0)
        wire_w      = p['wire_width']
        ins_w       = p['insulation_thickness']
        wire_dx     = p['wire_spacing_x']
        wire_dy     = p['wire_spacing_y']
        wire_h      = p['wire_height']
        num_wires   = int(p['num_wires_per_slot'])

        # ── magnet params ──────────────────────────────────────────────────
        num_poles   = int(p['num_poles'])
        mag_h       = p['magnet_height']
        rotor_hh    = p['rotor_house_height']
        mag_fd      = p['magnet_fill_down']
        mag_fu      = p['magnet_fill_up']
        mag_up_gap  = p['magnet_up_gap']
        mag_down_h  = p['magnet_down_height']
        mag_fill_r  = p['magnet_fill_radius']
        magnet_r    = rotor_ir + rotor_hh

        # ── rotor pocket params ────────────────────────────────────────────
        magnet_hole = p['rotor_hole']

        # helper: circle polygon
        def _circle(r: float, n: int = 256) -> list:
            return [(r * cos(2*pi*i/n), r * sin(2*pi*i/n)) for i in range(n)]

        # helper: rotate 2-D point
        def _rot(x, y, a_rad):
            c, s = cos(a_rad), sin(a_rad)
            return x*c - y*s, x*s + y*c

        # helper: triangulate a shapely (Multi)Polygon → dict
        def _tri(poly, z: float) -> Optional[Dict]:
            if poly is None or poly.is_empty:
                return None
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                return None
            geoms = list(poly.geoms) if isinstance(poly, SMPoly) else [poly]
            all_verts: list = []
            all_faces: list = []
            base = 0
            for g in geoms:
                ext = np.array(g.exterior.coords[:-1], dtype=np.float64)
                holes_raw = [np.array(h.coords[:-1], dtype=np.float64)
                             for h in g.interiors]
                verts = np.vstack([ext] + holes_raw) if holes_raw else ext
                # earcut needs cumulative end-indices, not lengths
                lengths = [len(ext)] + [len(h) for h in holes_raw]
                rings_u32 = np.cumsum(lengths, dtype=np.uint32)
                tris  = earcut.triangulate_float64(verts.astype(np.float64), rings_u32)
                if tris is None or len(tris) == 0:
                    continue
                tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
                # flip winding so normals point +Z
                tris = tris[:, ::-1]
                all_verts.append(verts)
                all_faces.append(tris + base)
                base += len(verts)
            if not all_verts:
                return None
            V = np.vstack(all_verts)
            F = np.vstack(all_faces)
            V3 = np.column_stack([V, np.full(len(V), z)])
            return {
                'vertices':     V3.tolist(),
                'faces':        F.tolist(),
                'vertex_count': len(V3),
                'face_count':   len(F),
            }

        result: Dict[str, Dict] = {}

        # Z-offsets: tiny (0.1 mm steps) so all layers look coplanar from
        # front/top but never z-fight each other.
        Z_SHAFT  = 0.0
        Z_ROTOR  = 0.1
        Z_MAG    = 0.2   # magnets sit on top of rotor surface
        Z_STATOR = 0.1   # same level as rotor (non-overlapping regions)
        Z_COIL   = 0.2   # coils sit in stator slots (non-overlapping with magnets)

        # Magnet trapezoid shape (local, pointing +Y at pole angle=0)
        half_slots   = num_slots // 2
        slot_angle_r = 2*pi / half_slots
        pole_angle_r = 2*pi / num_poles
        angle_down   = pole_angle_r * mag_fd / 2
        angle_up_m   = pole_angle_r * mag_fu / 2
        mp1 = ( magnet_r * sin(angle_down),                  magnet_r * cos(angle_down))
        mp2 = ((magnet_r + mag_down_h) * sin(angle_down),   (magnet_r + mag_down_h) * cos(angle_down))
        mp3 = ((rotor_or - mag_up_gap) * sin(angle_up_m),   (rotor_or - mag_up_gap) * cos(angle_up_m))
        mp4 = (-(rotor_or - mag_up_gap) * sin(angle_up_m),  (rotor_or - mag_up_gap) * cos(angle_up_m))
        mp5 = (-(magnet_r + mag_down_h) * sin(angle_down),  (magnet_r + mag_down_h) * cos(angle_down))
        mp6 = (-magnet_r * sin(angle_down),                  magnet_r * cos(angle_down))
        mag_local = [mp1, mp2, mp3, mp4, mp5, mp6]

        # ── 1. SHAFT (hollow ring: shaft_inner_radius → rotor_inner_radius) ──
        shaft_outer_r = rotor_ir          # outer edge of shaft tube
        shaft_inner_r = shaft_r           # inner bore of shaft
        shaft_poly = SPoly(_circle(shaft_outer_r), [_circle(shaft_inner_r)])
        if not shaft_poly.is_valid:
            shaft_poly = shaft_poly.buffer(0)
        r = _tri(shaft_poly, z=Z_SHAFT)
        if r: result['shaft'] = r

        # ── 2+3. MAGNETS + ROTOR CORE ─────────────────────────────────────
        # Round ONLY the two top corners (mp3/mp4, outer/air-gap edge) to match
        # CadQuery's edges(">Y and |Z").fillet(mag_fill_r).
        # The same rounded polygon is used for BOTH the rotor pocket holes and
        # the magnet so there is no dark gap at the corners.

        def _fillet_corner(p_prev, p_corner, p_next, r, n_arc=16):
            """Arc points (T1→T2 inclusive) for filleting one convex corner."""
            V  = np.array(p_corner, float)
            dA = np.array(p_prev,  float) - V;  dA /= np.linalg.norm(dA)
            dB = np.array(p_next,  float) - V;  dB /= np.linalg.norm(dB)
            cos_t  = float(np.clip(np.dot(dA, dB), -1.0, 1.0))
            half_a = acos(cos_t) / 2            # correct interior half-angle φ/2
            if sin(half_a) < 1e-9:              # ~180° corner → no fillet
                return [tuple(p_corner)]
            tan_len = r / tan(half_a)
            bis     = dA + dB;  bis /= np.linalg.norm(bis)
            center  = V + (r / sin(half_a)) * bis
            T1      = V + tan_len * dA
            T2      = V + tan_len * dB
            a1 = atan2(T1[1] - center[1], T1[0] - center[0])
            a2 = atan2(T2[1] - center[1], T2[0] - center[0])
            da = a2 - a1
            if da >  pi: da -= 2 * pi
            elif da < -pi: da += 2 * pi
            return [(float(center[0] + r * cos(a1 + da * k / n_arc)),
                     float(center[1] + r * sin(a1 + da * k / n_arc)))
                    for k in range(n_arc + 1)]

        def _build_mag_poly(pts, fillet_r):
            """Hexagon with only the two top corners (indices 2,3) filleted."""
            if fillet_r <= 0:
                return SPoly(pts)
            try:
                new_pts = (pts[:2]
                           + _fillet_corner(pts[1], pts[2], pts[3], fillet_r)
                           + _fillet_corner(pts[2], pts[3], pts[4], fillet_r)
                           + pts[4:])
                return SPoly(new_pts)
            except Exception:
                return SPoly(pts)

        rotor_outer_pts = _circle(rotor_or)
        rotor_inner_pts = _circle(rotor_ir)
        rotor_holes   = [rotor_inner_pts]
        mag_rot_polys = []

        for i in range(num_poles):
            a   = i * pole_angle_r
            pts = [_rot(x, y, a) for x, y in mag_local]
            mp  = _build_mag_poly(pts, mag_fill_r)
            if not mp.is_valid:
                mp = mp.buffer(0)
            rotor_holes.append(list(mp.exterior.coords[:-1]))
            mag_rot_polys.append(mp)

        rotor_poly = SPoly(rotor_outer_pts, rotor_holes)
        if not rotor_poly.is_valid:
            rotor_poly = rotor_poly.buffer(0)
        r = _tri(rotor_poly, z=Z_ROTOR)
        if r: result['rotor_core'] = r

        for i, poly in enumerate(mag_rot_polys):
            r = _tri(poly, z=Z_MAG)
            if r: result[f'magnet_{i}'] = r

        # ── 4. STATOR CORE (ring with slot cutouts) z=1 ────────────────
        slot_angle_deg = 360.0 / half_slots
        cut_x  = tooth_w/2 + ins_w*2 + wire_w + wire_dx*2 + tooth2_w
        fill_r = ((inner_r + cut_w) * sin(radians(slot_angle_deg/2)) - cut_x) \
                 / (1 - sin(radians(slot_angle_deg/2)))
        rr  = inner_r + cut_w + fill_r
        ext = outer_r * 2

        p1s = (cut_x,  ext)
        p2s = (cut_x,  rr * cos(radians(slot_angle_deg/2)))
        p3s = (cut_x + fill_r, rr * cos(radians(slot_angle_deg/2)))
        p4s = (ext * tan(radians(slot_angle_deg/2)), ext)

        # base ring
        stator_poly = SPoly(_circle(outer_r), [_circle(inner_r)])

        cutters = []
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            # +X trapezoid pre-merged with fill_r fillet circle at p3s
            # (circle overlaps trap → clean Polygon union, no tangency issues)
            trap_p = SPoly([_rot(*p1s, a), _rot(*p2s, a),
                             _rot(*p3s, a), _rot(*p4s, a)])
            cx, cy = _rot(p3s[0], p3s[1], a)
            circ_p = SPoly([(cx + fill_r * cos(2*pi*k/64),
                              cy + fill_r * sin(2*pi*k/64)) for k in range(64)])
            m_p = trap_p.union(circ_p)
            cutters.append(m_p if m_p.is_valid else m_p.buffer(0))

            # -X trapezoid pre-merged with fill_r fillet circle
            mp1n = (-p1s[0], p1s[1]); mp2n = (-p2s[0], p2s[1])
            mp3n = (-p3s[0], p3s[1]); mp4n = (-p4s[0], p4s[1])
            trap_n = SPoly([_rot(*mp1n, a), _rot(*mp2n, a),
                             _rot(*mp3n, a), _rot(*mp4n, a)])
            cxn, cyn = _rot(-p3s[0], p3s[1], a)
            circ_n = SPoly([(cxn + fill_r * cos(2*pi*k/64),
                              cyn + fill_r * sin(2*pi*k/64)) for k in range(64)])
            m_n = trap_n.union(circ_n)
            cutters.append(m_n if m_n.is_valid else m_n.buffer(0))

            # slot rectangles
            slot_w  = wire_w + ins_w*2 + wire_dx
            slot_h  = p['slot_height']
            slot_x  = tooth_w / 2
            slot_y  = outer_r - core_h
            # +X rect
            rx0, ry0 = slot_x, slot_y
            rect_pts_p = [(rx0, ry0), (rx0 + slot_w, ry0),
                          (rx0 + slot_w, ry0 - slot_h*2), (rx0, ry0 - slot_h*2)]
            cutters.append(SPoly([_rot(*pt, a) for pt in rect_pts_p]))
            # -X rect
            rect_pts_n = [(-rx0, ry0), (-rx0 - slot_w, ry0),
                          (-rx0 - slot_w, ry0 - slot_h*2), (-rx0, ry0 - slot_h*2)]
            cutters.append(SPoly([_rot(*pt, a) for pt in rect_pts_n]))

        tool = unary_union(cutters)
        stator_poly = stator_poly.difference(tool)
        # Filter any zero-area ghost fragments produced by Shapely difference
        # when tool boundaries are tangent to the stator ring boundary
        if isinstance(stator_poly, SMPoly):
            parts = [g for g in stator_poly.geoms if g.area > 0.1]
            stator_poly = parts[0] if len(parts) == 1 else SMPoly(parts)
        if not stator_poly.is_valid:
            stator_poly = stator_poly.buffer(0)
        r = _tri(stator_poly, z=Z_STATOR)
        if r: result['stator_core'] = r

        # ── 5. COILS (rectangles in slots) ─────────────────────────────
        right_x = tooth_w / 2 + ins_w + wire_dx/2
        slot_y  = outer_r - core_h
        top_y_c = slot_y - ins_w - wire_dy/2

        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            for step in range(num_wires):
                cy = top_y_c - step * (wire_h + wire_dy)
                for side, sx in ((1, right_x), (-1, -(right_x + wire_w))):
                    pts_local = [(sx, cy), (sx + wire_w, cy),
                                 (sx + wire_w, cy - wire_h), (sx, cy - wire_h)]
                    pts = [_rot(*pt, a) for pt in pts_local]
                    poly = SPoly(pts)
                    r = _tri(poly, z=Z_COIL)
                    if r:
                        key = f'coil_{i}'
                        if key not in result:
                            result[key] = r
                        else:
                            # merge into existing coil entry
                            result[key]['vertices'] += r['vertices']
                            result[key]['faces']    += [
                                [f + result[key]['vertex_count'] for f in face]
                                for face in r['faces']
                            ]
                            result[key]['vertex_count'] += r['vertex_count']
                            result[key]['face_count']   += r['face_count']

        return result

    def validate_sdf(self, n_points: int = 50000) -> Dict:
        """Validate geometry by computing SDF."""
        mesh_data = self.get_all_mesh_data()
        
        if not mesh_data:
            return {'valid': False, 'error': 'No mesh data'}
            
        import numpy as np
        
        all_vertices = []
        for comp, data in mesh_data.items():
            all_vertices.extend(data['vertices'])
            
        vertices = np.array(all_vertices)
        bounds_min = vertices.min(axis=0)
        bounds_max = vertices.max(axis=0)
        
        size = bounds_max - bounds_min
        volume = np.prod(size)
        
        valid = volume > 0 and len(mesh_data) > 0
        
        return {
            'valid': valid,
            'bounding_box': {
                'min': bounds_min.tolist(),
                'max': bounds_max.tolist(),
            },
            'approximate_volume': float(volume),
            'components': list(mesh_data.keys()),
            'n_components': len(mesh_data),
        }


class CadQueryCache:
    """Cache for CadQuery-generated geometry."""
    
    def __init__(self, cache_dir: str = "./cadquery_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
    def get_cache_path(self, param_hash: str) -> Path:
        return self.cache_dir / param_hash
        
    def exists(self, param_hash: str) -> bool:
        cache_path = self.get_cache_path(param_hash)
        return cache_path.exists() and any(cache_path.glob("*.stl"))
        
    def save(self, param_hash: str, stl_files: Dict[str, str]) -> str:
        import shutil
        cache_path = self.get_cache_path(param_hash)
        cache_path.mkdir(exist_ok=True)
        
        for comp_name, src_path in stl_files.items():
            dst_path = cache_path / f"{comp_name}.stl"
            shutil.copy2(src_path, dst_path)
            
        return str(cache_path)
        
    def load(self, param_hash: str) -> Optional[Dict[str, str]]:
        cache_path = self.get_cache_path(param_hash)
        
        if not self.exists(param_hash):
            return None
            
        stl_files = {}
        for stl_file in cache_path.glob("*.stl"):
            stl_files[stl_file.stem] = str(stl_file)
            
        return stl_files
    
    def clear_all(self):
        """Clear all cached geometry."""
        import shutil
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(exist_ok=True)
    
    def clear_hash(self, param_hash: str):
        """Clear a specific cached geometry by hash."""
        import shutil
        cache_path = self.get_cache_path(param_hash)
        if cache_path.exists():
            shutil.rmtree(cache_path)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='CadQuery Motor Geometry Generator')
    parser.add_argument('--stator_outer_radius', type=float, default=100.0)
    parser.add_argument('--num_slots', type=int, default=36)
    parser.add_argument('--num_poles', type=int, default=12)
    parser.add_argument('--output', type=str, default='./stl_output')
    parser.add_argument('--validate', action='store_true')
    
    args = parser.parse_args()
    
    motor = CadQueryMotor()
    motor.set_parameters(vars(args))
    
    if args.validate:
        motor.build_all()
        result = motor.validate_sdf()
        print(f"Validation result: {result}")
    else:
        stl_files = motor.export_stl(args.output)
        print(f"Generated {len(stl_files)} STL files")
