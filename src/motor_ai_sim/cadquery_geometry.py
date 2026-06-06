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
from math import sin, cos, tan, radians, degrees, pi, acos, atan2
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


def _round_corners_vertex(poly, r, n_arc=6, ang_min_deg=8.0, ang_max_deg=168.0):
    """Round the SHARP corners of a Shapely (Multi)Polygon by inserting a tangent
    arc of radius r at each corner — a true vertex fillet.  Unlike a morphological
    open/close it does NOT erode or dilate edges, so it can never break thin
    inter-magnet bridges: the fillet length is clamped to ≤45 % of each adjacent
    edge, so r auto-reduces on thin features.  Rounds both convex and concave
    corners.  Returns a (Multi)Polygon (topology preserved)."""
    import numpy as _np
    import math as _m
    from shapely.geometry import Polygon as _SP, MultiPolygon as _SMP

    a_lo, a_hi = _m.radians(ang_min_deg), _m.radians(ang_max_deg)

    def _ring(coords):
        pts = [_np.asarray(c, float) for c in coords]
        if len(pts) > 1 and _np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        n = len(pts)
        if n < 3:
            return coords
        out = []
        for i in range(n):
            A, V, B = pts[i - 1], pts[i], pts[(i + 1) % n]
            dA, dB = A - V, B - V
            la, lb = float(_np.hypot(*dA)), float(_np.hypot(*dB))
            if la < 1e-9 or lb < 1e-9:
                out.append(tuple(V)); continue
            uA, uB = dA / la, dB / lb
            ang = _m.acos(max(-1.0, min(1.0, float(_np.dot(uA, uB)))))   # interior angle
            if ang < a_lo or ang > a_hi:
                out.append(tuple(V)); continue                          # ~straight → skip
            half = ang / 2.0
            tl = min(r / _m.tan(half), 0.45 * la, 0.45 * lb)
            if tl < 1e-6:
                out.append(tuple(V)); continue
            reff = tl * _m.tan(half)
            Pa, Pb = V + uA * tl, V + uB * tl
            bis = uA + uB; bl = float(_np.hypot(*bis))
            if bl < 1e-9:
                out.append(tuple(V)); continue
            bis /= bl
            C = V + bis * (reff / _m.sin(half))                         # arc centre on the bisector
            a0 = _m.atan2(Pa[1] - C[1], Pa[0] - C[0])
            a1 = _m.atan2(Pb[1] - C[1], Pb[0] - C[0])
            d = a1 - a0
            while d > _m.pi:  d -= 2 * _m.pi
            while d < -_m.pi: d += 2 * _m.pi
            out.extend((C[0] + reff * _m.cos(a0 + d * k / n_arc),
                        C[1] + reff * _m.sin(a0 + d * k / n_arc)) for k in range(n_arc + 1))
        return out

    def _one(p):
        q = _SP(_ring(list(p.exterior.coords)),
                [_ring(list(h.coords)) for h in p.interiors])
        return q if q.is_valid else q.buffer(0)

    if poly.geom_type == "MultiPolygon":
        return _SMP([_one(g) for g in poly.geoms])
    return _one(poly)


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

        # 1b. Sliding-band air rings — first-class components with material Air.
        # in_band  rotates with the rotor (rotor_outer..mid_radius)
        # out_band stays with the stator (mid_radius..stator_inner)
        try:
            self.parts['in_band']  = self._create_in_band(cq)
            self.parts['out_band'] = self._create_out_band(cq)
        except Exception as e:
            print(f"Failed to build in_band / out_band: {e}")
        
        # 2. Magnets and Rotor Core with Cavities
        magnets_list = self._create_magnets(cq)
        rotor_solid = self._create_rotor(cq)
        
        for i, magnet in enumerate(magnets_list):
            if magnet is not None:
                rotor_solid = rotor_solid.cut(magnet) # Cut hole in rotor
                self.parts[f'magnet_{i}'] = magnet    # Keep magnet separate

        # NOTE on rotor_fill_r: the rotor corners are rounded in the 2D physics
        # geometry (get_2d_polygons / get_2d_mesh_data, per-edge clamped vertex
        # fillet).  A CadQuery BRep fillet on THIS 3D solid can't take the full
        # radius near the ~1.2 mm bridges (StdFail_NotDone) and is slow, so the
        # /api/geometry/mesh route instead swaps rotor_core for the extruded
        # filleted 2D mesh (get_extruded_mesh_data) — exact radius, fast,
        # identical to the physics.  The solid here stays sharp on purpose.
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

    def _create_in_band(self, cq) -> Any:
        """Inner air domain: full DISK r=0..mid_r minus rotor+magnets+shaft.

        Sliding-band FEM rotates this rigidly with the rotor.  Material
        is Air; the disk captures every internal air pocket (shaft bore,
        inter-magnet wedges, inner half of the air gap).
        """
        p = self.parameters
        rotor_or = p['rotor_outer_radius']
        inner_r  = p['stator_inner_radius']
        length   = p['stator_width']
        mid_r    = 0.5 * (rotor_or + inner_r)
        return (
            cq.Workplane("XY")
            .circle(mid_r)
            .extrude(length)
        )

    def _create_out_band(self, cq) -> Any:
        """Outer air domain: ANNULUS mid_r..r_outer minus stator+coils.

        The outer edge (r_outer) is the far-field Dirichlet boundary for
        the magnetic vector potential A_z.  Stationary in the lab frame.
        """
        p = self.parameters
        outer_r  = p['stator_outer_radius']
        rotor_or = p['rotor_outer_radius']
        inner_r  = p['stator_inner_radius']
        length   = p['stator_width']
        mid_r    = 0.5 * (rotor_or + inner_r)
        # Default outer_air_factor=1.3 if not set
        r_outer  = float(p.get('outer_air_factor', 1.3)) * outer_r
        return (
            cq.Workplane("XY")
            .circle(r_outer)
            .circle(mid_r)
            .extrude(length)
        )
    
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

        # Rectangular cut above each magnet — matches 3D _create_rotor cut_up:
        #   rect(rec_w, -mag_h).translate((-rec_w/2, rotor_outer_r)).rotate(angle)
        mag_angle_up_hole = pole_angle_r * mag_fu * magnet_hole / 2  # radians
        rec_w = 2 * rotor_or * sin(mag_angle_up_hole)
        rect_local = [
            (-rec_w / 2, rotor_or),
            ( rec_w / 2, rotor_or),
            ( rec_w / 2, rotor_or - mag_h),
            (-rec_w / 2, rotor_or - mag_h),
        ]

        rotor_outer_pts = _circle(rotor_or)
        rotor_inner_pts = _circle(rotor_ir)
        # Build the rotor body as a clean annulus first, then subtract
        # holes via shapely difference.  The previous approach passed
        # raw hole coordinate lists to SPoly(); when the rect_local
        # rectangle's top corners landed at r=sqrt((rec_w/2)^2 +
        # rotor_or^2) > rotor_or (i.e. just outside the rotor disk), the
        # hole crossed the exterior boundary, produced an invalid
        # polygon, and buffer(0) repaired it by dropping the bridges
        # between adjacent magnets.  Doing a proper shapely difference
        # avoids that.
        rotor_disk = SPoly(rotor_outer_pts, [rotor_inner_pts])
        if not rotor_disk.is_valid:
            rotor_disk = rotor_disk.buffer(0)

        mag_rot_polys = []
        hole_polys    = []
        for i in range(num_poles):
            a   = i * pole_angle_r
            pts = [_rot(x, y, a) for x, y in mag_local]
            mp  = _build_mag_poly(pts, mag_fill_r)
            if not mp.is_valid:
                mp = mp.buffer(0)

            rect_pts  = [_rot(x, y, a) for x, y in rect_local]
            rect_poly = SPoly(rect_pts)
            if not rect_poly.is_valid:
                rect_poly = rect_poly.buffer(0)

            hole = mp.union(rect_poly)
            if not hole.is_valid:
                hole = hole.buffer(0)
            hole_polys.append(hole)
            mag_rot_polys.append(mp)

        # One difference call with the union of all holes — preserves
        # bridges between adjacent magnets and clips any sliver that
        # would stick past rotor_or.
        rotor_poly = rotor_disk.difference(unary_union(hole_polys))
        if not rotor_poly.is_valid:
            rotor_poly = rotor_poly.buffer(0)
        # Round the rotor pole-tip sharp corners (rotor_fill_r) — same guarded
        # vertex fillet as get_2d_polygons, so the Mesh tab shows the rounded
        # rotor too.  Keep only if valid, >=85% area, same piece count (else
        # keep sharp — never break the thin inter-magnet bridges).
        _rfr = float(self.parameters.get('rotor_fill_r', 0.0) or 0.0)
        if _rfr > 1e-4:
            def _npoly(g): return len(g.geoms) if g.geom_type == 'MultiPolygon' else (0 if g.is_empty else 1)
            try:
                _f = _round_corners_vertex(rotor_poly, _rfr)
                if not _f.is_valid: _f = _f.buffer(0)
                if (_f.is_valid and not _f.is_empty
                        and _f.area >= 0.85 * rotor_poly.area
                        and _npoly(_f) == _npoly(rotor_poly)):
                    rotor_poly = _f
            except Exception as _e:
                print(f"rotor_fill_r (mesh) failed ({_e}) -- keeping sharp rotor")
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

        # ── Stator corner rounding (matches 3D _OuterRingSelector / _InnerRingSelector) ──
        # Apply _fillet_corner only to vertices near outer_r (concave slot-wall corners)
        # and near inner_r (convex tooth-tip corners), matching the 3D fillet selectors.
        fillet_r  = p.get('stator_fillet_r',  2.5)
        fillet_r1 = p.get('stator_fillet_r1', 0.9)

        def _fillet_coords(coords, target_r, r_tol, fillet_radius, min_angle_deg=20.0):
            """Round only SHARP corners near target_r in a coordinate ring."""
            n = len(coords)
            new_coords = []
            for i in range(n):
                p_prev   = coords[(i - 1) % n]
                p_corner = coords[i]
                p_next   = coords[(i + 1) % n]
                rc = (p_corner[0]**2 + p_corner[1]**2) ** 0.5
                if abs(rc - target_r) < r_tol:
                    dA = np.array(p_prev) - np.array(p_corner); la = np.linalg.norm(dA)
                    dB = np.array(p_next) - np.array(p_corner); lb = np.linalg.norm(dB)
                    if la > 1e-9 and lb > 1e-9:
                        cos_t = float(np.clip(np.dot(dA/la, dB/lb), -1.0, 1.0))
                        angle_deg = degrees(acos(cos_t))
                        if angle_deg < (180.0 - min_angle_deg):
                            arc = _fillet_corner(p_prev, p_corner, p_next, fillet_radius)
                            new_coords.extend(arc)
                            continue
                new_coords.append(p_corner)
            return new_coords

        def _fillet_ring_corners(poly, target_r, r_tol, fillet_radius, min_angle_deg=20.0):
            """Round sharp corners near target_r on BOTH exterior and interior rings."""
            if not hasattr(poly, 'exterior'):
                return poly
            new_ext  = _fillet_coords(list(poly.exterior.coords[:-1]),
                                      target_r, r_tol, fillet_radius, min_angle_deg)
            new_ints = [_fillet_coords(list(h.coords[:-1]),
                                       target_r, r_tol, fillet_radius, min_angle_deg)
                        for h in poly.interiors]
            result = SPoly(new_ext, new_ints)
            if not result.is_valid:
                result = result.buffer(0)
            return result

        if fillet_r > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _fillet_ring_corners(stator_poly, outer_r, 1.5, fillet_r)
        if fillet_r1 > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _fillet_ring_corners(stator_poly, inner_r, 1.0, fillet_r1)

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

        # ── 6. SLIDING-BAND AIR DOMAINS ───────────────────────────────────
        # in_band  = full DISK r=0..mid_r  MINUS rotor + magnets + shaft.
        #            Captures every air pocket inside the rotor region.
        # out_band = ANNULUS mid_r..r_outer MINUS stator + coils.
        #            Outer boundary is where the Dirichlet BC will be set.
        Z_IN_BAND  = -0.05  # behind everything so they don't occlude
        Z_OUT_BAND = -0.05
        mid_r   = 0.5 * (rotor_or + inner_r)
        r_outer = float(p.get('outer_air_factor', 1.3)) * outer_r
        try:
            in_band_poly  = SPoly(_circle(mid_r))
            out_band_poly = SPoly(_circle(r_outer), [_circle(mid_r)])

            # Subtract rotor solids from in_band.  CRITICAL: subtract the
            # SAME filleted magnet polygons (mag_rot_polys) that are
            # rendered and that were used to carve the rotor holes.  An
            # earlier version subtracted the UNFILLETED hexagon
            # (mag_local) here, which is larger than the rendered
            # filleted magnet — that mismatch left a thin uncovered band
            # between the filleted magnet's rounded top and the
            # unfilleted hexagon's flat top (inside the cut_up width),
            # rendering as a black notch above each magnet.  Using
            # mag_rot_polys keeps in_band's lower boundary exactly on the
            # magnet's rendered top edge.
            shaft_solid = SPoly(_circle(rotor_ir), [_circle(shaft_r)])
            try:
                in_band_poly = in_band_poly.difference(
                    unary_union([shaft_solid, rotor_poly] + list(mag_rot_polys)))
                if not in_band_poly.is_valid:
                    in_band_poly = in_band_poly.buffer(0)
            except Exception:
                pass

            # Subtract stator iron from out_band.  (Coils sit inside slot
            # cutouts that are already part of the stator's exterior — no
            # extra cut needed.)
            try:
                out_band_poly = out_band_poly.difference(stator_poly)
                if not out_band_poly.is_valid:
                    out_band_poly = out_band_poly.buffer(0)
            except Exception:
                pass

            r_in  = _tri(in_band_poly,  z=Z_IN_BAND)
            r_out = _tri(out_band_poly, z=Z_OUT_BAND)
            if r_in:  result['in_band']  = r_in
            if r_out: result['out_band'] = r_out
        except Exception as e:
            print(f"[2d] Failed to build in_band/out_band: {e}")

        return result

    def get_2d_polygons(self, rotor_angle_deg: float = 0.0) -> Dict[str, Any]:
        """Return raw Shapely polygon objects for each motor domain.

        Same geometry as get_2d_mesh_data() but returns Shapely objects
        (not triangulated meshes).  Used for 2D field-map domain classification.

        Coordinates are in mm (same as get_2d_mesh_data).

        Parameters
        ----------
        rotor_angle_deg : float
            Rotor rotation angle in degrees (rotates magnets + rotor).

        Returns
        -------
        Dict with keys:
            'stator'   : Shapely Polygon/MultiPolygon  (stator steel with slots)
            'magnets'  : list of (Shapely Polygon, polarity:int +1/-1)
            'rotor'    : Shapely Polygon               (rotor back-iron)
            'shaft'    : Shapely Polygon               (shaft ring)
            'coils'    : list of Shapely Polygon       (one per coil side, in mm)
            'air_gap'  : Shapely Polygon               (air gap ring)
        """
        from math import pi, sin, cos, tan, radians, degrees, acos, atan2, sqrt
        try:
            from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly
            from shapely.ops import unary_union
        except ImportError:
            raise ImportError("shapely required — pip install shapely")

        import numpy as np
        p = self.parameters

        outer_r   = p['stator_outer_radius']
        inner_r   = p['stator_inner_radius']
        rotor_or  = p['rotor_outer_radius']
        rotor_ir  = p['rotor_inner_radius']
        shaft_r   = p['shaft_inner_radius']

        num_slots   = int(p['num_slots'])
        core_h      = p['core_thickness']
        tooth_w     = p['tooth_width']
        cut_w       = p.get('cut_width', 2.0)
        wire_w      = p['wire_width']
        ins_w       = p['insulation_thickness']
        wire_dx     = p['wire_spacing_x']
        wire_dy     = p['wire_spacing_y']
        wire_h      = p['wire_height']
        num_wires   = int(p['num_wires_per_slot'])

        num_poles   = int(p['num_poles'])
        mag_h       = p['magnet_height']
        rotor_hh    = p['rotor_house_height']
        mag_fd      = p['magnet_fill_down']
        mag_fu      = p['magnet_fill_up']
        mag_up_gap  = p['magnet_up_gap']
        mag_down_h  = p['magnet_down_height']
        mag_fill_r  = p['magnet_fill_radius']
        magnet_r    = rotor_ir + rotor_hh
        magnet_hole = p['rotor_hole']
        fillet_r    = p.get('stator_fillet_r', 2.5)
        fillet_r1   = p.get('stator_fillet_r1', 0.9)

        # ── Zero-position alignment ──────────────────────────────────────
        # At rotor_angle_deg = 0 the rotor IRON TOOTH between mag[6] (N)
        # and mag[7] (S) — i.e. an effective N-pole of the rotor in the
        # SPOKE-PM topology — sits at math 90° (+Y axis), aligned with
        # the first stator tooth (also at math 90°).  This is the
        # convention shown in the user's Ansys reference image:  rotor
        # d-axis pole at +Y, magnets distributed in the upper arc of the
        # rotor.  Cadquery's native magnet origin is the +Y axis; we add
        # a small −(90° − half_pole_pitch) shift so the 7 magnets of the
        # 1/4 sector all fit INSIDE the wedge with centers at math 6.43°
        # through 83.57°.
        _pole_pitch_deg  = 360.0 / num_poles
        ZERO_OFFSET_DEG  = -(90.0 - _pole_pitch_deg * 0.5)
        theta_r = radians(rotor_angle_deg + ZERO_OFFSET_DEG)

        def _circle(r, n=256):
            return [(r*cos(2*pi*i/n), r*sin(2*pi*i/n)) for i in range(n)]

        def _rot(x, y, a):
            c, s = cos(a), sin(a)
            return x*c - y*s, x*s + y*c

        def _fillet_corner(p_prev, p_corner, p_next, r, n_arc=12):
            V  = np.array(p_corner, float)
            dA = np.array(p_prev, float) - V; dA /= max(np.linalg.norm(dA), 1e-9)
            dB = np.array(p_next, float) - V; dB /= max(np.linalg.norm(dB), 1e-9)
            cos_t = float(np.clip(np.dot(dA, dB), -1.0, 1.0))
            half_a = acos(cos_t) / 2
            if sin(half_a) < 1e-9:
                return [tuple(p_corner)]
            tan_len = r / tan(half_a)
            bis = dA + dB; bis /= np.linalg.norm(bis)
            center = V + (r / sin(half_a)) * bis
            T1 = V + tan_len * dA; T2 = V + tan_len * dB
            a1 = atan2(T1[1]-center[1], T1[0]-center[0])
            a2 = atan2(T2[1]-center[1], T2[0]-center[0])
            da = a2 - a1
            if da >  pi: da -= 2*pi
            elif da < -pi: da += 2*pi
            return [(float(center[0] + r*cos(a1 + da*k/n_arc)),
                     float(center[1] + r*sin(a1 + da*k/n_arc)))
                    for k in range(n_arc + 1)]

        # ── Magnet local polygon ──────────────────────────────────────────────
        pole_angle_r = 2*pi / num_poles
        angle_down   = pole_angle_r * mag_fd / 2
        angle_up_m   = pole_angle_r * mag_fu / 2
        mp1 = ( magnet_r*sin(angle_down),               magnet_r*cos(angle_down))
        mp2 = ((magnet_r+mag_down_h)*sin(angle_down),  (magnet_r+mag_down_h)*cos(angle_down))
        mp3 = ((rotor_or-mag_up_gap)*sin(angle_up_m),  (rotor_or-mag_up_gap)*cos(angle_up_m))
        mp4 = (-(rotor_or-mag_up_gap)*sin(angle_up_m), (rotor_or-mag_up_gap)*cos(angle_up_m))
        mp5 = (-(magnet_r+mag_down_h)*sin(angle_down), (magnet_r+mag_down_h)*cos(angle_down))
        mp6 = (-magnet_r*sin(angle_down),               magnet_r*cos(angle_down))
        mag_local = [mp1, mp2, mp3, mp4, mp5, mp6]

        def _build_mag_poly(pts, fr):
            if fr <= 0: return SPoly(pts)
            try:
                new_pts = (pts[:2]
                           + _fillet_corner(pts[1], pts[2], pts[3], fr)
                           + _fillet_corner(pts[2], pts[3], pts[4], fr)
                           + pts[4:])
                return SPoly(new_pts)
            except Exception:
                return SPoly(pts)

        # Rectangular cut above each magnet
        mag_angle_up_hole = pole_angle_r * mag_fu * magnet_hole / 2
        rec_w = 2 * rotor_or * sin(mag_angle_up_hole)
        rect_local = [(-rec_w/2, rotor_or), (rec_w/2, rotor_or),
                      (rec_w/2, rotor_or-mag_h), (-rec_w/2, rotor_or-mag_h)]

        mag_polys = []   # (poly, polarity)
        hole_polys = []  # list of shapely (magnet + cut_up) polygons

        for i in range(num_poles):
            a   = i * pole_angle_r + theta_r
            pts = [_rot(x, y, a) for x, y in mag_local]
            mp  = _build_mag_poly(pts, mag_fill_r)
            if not mp.is_valid: mp = mp.buffer(0)

            rect_pts = [_rot(x, y, a) for x, y in rect_local]
            rect_poly = SPoly(rect_pts)
            if not rect_poly.is_valid: rect_poly = rect_poly.buffer(0)
            hole = mp.union(rect_poly)
            if not hole.is_valid: hole = hole.buffer(0)
            hole_polys.append(hole)
            polarity = +1 if i % 2 == 0 else -1
            mag_polys.append((mp, polarity))

        # Rotor = annulus rotor_or..rotor_ir minus union(all holes).
        # Using shapely difference avoids the invalid-polygon problem
        # caused by passing raw hole coords whose top corners lie at
        # r = sqrt((rec_w/2)^2 + rotor_or^2) > rotor_or — these
        # apparent-but-tiny excursions outside the rotor disk used to
        # cross the exterior boundary, invalidate the polygon, and let
        # buffer(0) silently drop the rotor-iron bridges between
        # adjacent magnets.
        rotor_disk = SPoly(_circle(rotor_or), [_circle(rotor_ir)])
        if not rotor_disk.is_valid: rotor_disk = rotor_disk.buffer(0)
        rotor_poly = rotor_disk.difference(unary_union(hole_polys))
        if not rotor_poly.is_valid: rotor_poly = rotor_poly.buffer(0)

        # ── Round the rotor pole-tip sharp corners (rotor_fill_r) ─────────────
        # Sharp iron corners at the air-gap surface concentrate flux → feed
        # cogging/ripple + iron loss.  Use a VERTEX fillet (tangent arc per corner)
        # — it rounds corners WITHOUT eroding edges, so the thin inter-magnet
        # bridges (rotor_house ~1.2 mm) survive (the fillet auto-clamps to the
        # local feature size).  GUARDED: keep only if valid, ≥85 % area, same #
        # pieces — else keep the sharp rotor.  ("не ломай геометрию")
        _rfr = float(p.get('rotor_fill_r', 0.0) or 0.0)
        if _rfr > 1e-4:
            def _npoly(g):
                return len(g.geoms) if g.geom_type == 'MultiPolygon' else (0 if g.is_empty else 1)
            try:
                _f = _round_corners_vertex(rotor_poly, _rfr)
                if not _f.is_valid:
                    _f = _f.buffer(0)
                if (_f.is_valid and not _f.is_empty
                        and _f.area >= 0.85 * rotor_poly.area
                        and _npoly(_f) == _npoly(rotor_poly)):
                    rotor_poly = _f
                else:
                    print(f"rotor_fill_r={_rfr:.2f} rejected: valid={_f.is_valid} empty={_f.is_empty} "
                          f"area={100*_f.area/max(rotor_poly.area,1e-9):.1f}% "
                          f"pieces {_npoly(rotor_poly)}->{_npoly(_f)} -- keeping sharp rotor")
            except Exception as _e:
                print(f"rotor_fill_r failed ({_e}) -- keeping sharp rotor")

        # Shaft
        shaft_poly = SPoly(_circle(rotor_ir), [_circle(shaft_r)])
        if not shaft_poly.is_valid: shaft_poly = shaft_poly.buffer(0)

        # Air gap ring
        airgap_poly = SPoly(_circle(inner_r), [_circle(rotor_or)])
        if not airgap_poly.is_valid: airgap_poly = airgap_poly.buffer(0)

        # ── Stator with real slot cutouts ────────────────────────────────────
        half_slots     = num_slots // 2
        slot_angle_deg = 360.0 / half_slots
        cut_x  = tooth_w/2 + ins_w*2 + wire_w + wire_dx*2 + p.get('tooth2_width', 4.5)
        fill_r2 = ((inner_r + cut_w) * sin(radians(slot_angle_deg/2)) - cut_x) \
                  / (1 - sin(radians(slot_angle_deg/2)))
        rr  = inner_r + cut_w + fill_r2
        ext = outer_r * 2

        p1s = (cut_x,  ext)
        p2s = (cut_x,  rr * cos(radians(slot_angle_deg/2)))
        p3s = (cut_x + fill_r2, rr * cos(radians(slot_angle_deg/2)))
        p4s = (ext * tan(radians(slot_angle_deg/2)), ext)

        stator_poly_base = SPoly(_circle(outer_r), [_circle(inner_r)])
        cutters = []
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            trap_p = SPoly([_rot(*p1s,a), _rot(*p2s,a), _rot(*p3s,a), _rot(*p4s,a)])
            cx, cy = _rot(p3s[0], p3s[1], a)
            circ_p = SPoly([(cx + fill_r2*cos(2*pi*k/32), cy + fill_r2*sin(2*pi*k/32))
                             for k in range(32)])
            m_p = trap_p.union(circ_p)
            cutters.append(m_p if m_p.is_valid else m_p.buffer(0))

            mp1n=(-p1s[0],p1s[1]); mp2n=(-p2s[0],p2s[1])
            mp3n=(-p3s[0],p3s[1]); mp4n=(-p4s[0],p4s[1])
            trap_n = SPoly([_rot(*mp1n,a), _rot(*mp2n,a), _rot(*mp3n,a), _rot(*mp4n,a)])
            cxn,cyn = _rot(-p3s[0],p3s[1],a)
            circ_n = SPoly([(cxn + fill_r2*cos(2*pi*k/32), cyn + fill_r2*sin(2*pi*k/32))
                             for k in range(32)])
            m_n = trap_n.union(circ_n)
            cutters.append(m_n if m_n.is_valid else m_n.buffer(0))

            slot_w_c = wire_w + ins_w*2 + wire_dx
            slot_h_c = p['slot_height']
            slot_x   = tooth_w / 2
            slot_y   = outer_r - core_h
            rx0, ry0 = slot_x, slot_y
            cutters.append(SPoly([_rot(*pt, a) for pt in
                [(rx0,ry0),(rx0+slot_w_c,ry0),(rx0+slot_w_c,ry0-slot_h_c*2),(rx0,ry0-slot_h_c*2)]]))
            cutters.append(SPoly([_rot(*pt, a) for pt in
                [(-rx0,ry0),(-rx0-slot_w_c,ry0),(-rx0-slot_w_c,ry0-slot_h_c*2),(-rx0,ry0-slot_h_c*2)]]))

        tool = unary_union(cutters)
        stator_poly = stator_poly_base.difference(tool)
        if isinstance(stator_poly, SMPoly):
            parts = [g for g in stator_poly.geoms if g.area > 0.1]
            stator_poly = parts[0] if len(parts)==1 else SMPoly(parts)
        if not stator_poly.is_valid: stator_poly = stator_poly.buffer(0)

        # ── Stator slot-corner fillets — same radii as 3D CadQuery + Geometry tab ──
        # The 3D model applies `.fillet(stator_fillet_r)` to outer-ring edges and
        # `.fillet(stator_fillet_r1)` to inner-ring edges via _OuterRingSelector /
        # _InnerRingSelector.  We mirror that on the 2-D polygon here so the
        # Mesh tab (which only sees this dict) renders the SAME corners.
        import numpy as _np
        from math import acos as _acos, degrees as _degrees

        def _fillet_coords_2d(coords, target_r, r_tol, fr, min_angle_deg=20.0):
            n = len(coords)
            new_coords = []
            for i in range(n):
                p_prev   = coords[(i - 1) % n]
                p_corner = coords[i]
                p_next   = coords[(i + 1) % n]
                rc = (p_corner[0]**2 + p_corner[1]**2) ** 0.5
                if abs(rc - target_r) < r_tol:
                    dA = _np.array(p_prev) - _np.array(p_corner)
                    dB = _np.array(p_next) - _np.array(p_corner)
                    la = _np.linalg.norm(dA); lb = _np.linalg.norm(dB)
                    if la > 1e-9 and lb > 1e-9:
                        cos_t = float(_np.clip(_np.dot(dA/la, dB/lb), -1.0, 1.0))
                        angle_deg = _degrees(_acos(cos_t))
                        if angle_deg < (180.0 - min_angle_deg):
                            arc = _fillet_corner(p_prev, p_corner, p_next, fr)
                            new_coords.extend(arc)
                            continue
                new_coords.append(p_corner)
            return new_coords

        def _fillet_ring_corners_2d(poly, target_r, r_tol, fr, min_angle_deg=20.0):
            if not hasattr(poly, 'exterior'):
                return poly
            new_ext  = _fillet_coords_2d(list(poly.exterior.coords[:-1]),
                                          target_r, r_tol, fr, min_angle_deg)
            new_ints = [_fillet_coords_2d(list(h.coords[:-1]),
                                           target_r, r_tol, fr, min_angle_deg)
                        for h in poly.interiors]
            result = SPoly(new_ext, new_ints)
            if not result.is_valid:
                result = result.buffer(0)
            return result

        fillet_r  = p.get('stator_fillet_r',  2.5)
        fillet_r1 = p.get('stator_fillet_r1', 0.9)
        if fillet_r > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _fillet_ring_corners_2d(stator_poly, outer_r, 1.5, fillet_r)
        if fillet_r1 > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _fillet_ring_corners_2d(stator_poly, inner_r, 1.0, fillet_r1)

        # ── Coils (winding rectangles in slots) ──────────────────────────────
        # The cadquery iteration places wires on BOTH sides of each central
        # tooth (positive-x = "right slot", negative-x = "left slot"), so
        # we emit TWO separate polygons per iteration — one per physical
        # slot — labelled with the matching winding-layout slot index.
        # This makes per-coil J_z assignment straightforward downstream.
        right_x  = tooth_w/2 + ins_w + wire_dx/2
        slot_y_c = outer_r - core_h
        top_y_c  = slot_y_c - ins_w - wire_dy/2
        coil_polys = []
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            wires_pos: list = []
            wires_neg: list = []
            for step in range(num_wires):
                cy = top_y_c - step*(wire_h + wire_dy)
                # right (positive x) side
                local_p = [(right_x, cy), (right_x + wire_w, cy),
                            (right_x + wire_w, cy - wire_h), (right_x, cy - wire_h)]
                wp_p = SPoly([_rot(*pt, a) for pt in local_p])
                if wp_p.is_valid and wp_p.area > 0:
                    wires_pos.append(wp_p)
                # left (negative x) side
                sx_n = -(right_x + wire_w)
                local_n = [(sx_n, cy), (sx_n + wire_w, cy),
                            (sx_n + wire_w, cy - wire_h), (sx_n, cy - wire_h)]
                wp_n = SPoly([_rot(*pt, a) for pt in local_n])
                if wp_n.is_valid and wp_n.area > 0:
                    wires_neg.append(wp_n)
            if wires_pos:
                coil_polys.append(unary_union(wires_pos))   # +x slot
            if wires_neg:
                coil_polys.append(unary_union(wires_neg))   # -x slot

        # ── Sliding-band air domains: in_band + out_band ──────────────────
        # in_band  = full DISK r=0..mid_r  MINUS rotor + magnets + shaft.
        #            Captures every bit of air inside the moving (rotor)
        #            region — including the shaft bore, inter-magnet pockets,
        #            and the inner half of the air gap.  Rotates rigidly
        #            with the rotor in the sliding-band transient solver.
        #
        # out_band = ANNULUS mid_r..r_outer MINUS stator + coils.
        #            r_outer = outer_air_factor × stator_outer_radius is the
        #            far-field boundary where the Dirichlet BC A_z = 0
        #            (magnetic potential clamp) will be applied.  Captures
        #            slot-opening air, outer ambient air and the outer half
        #            of the air gap — stationary in the lab frame.
        #
        # The shared circle r=mid_r is the slip surface.
        mid_r   = 0.5 * (rotor_or + inner_r)
        # Outer boundary for the FE domain — pulled in from motor_config
        # if available; defaults to 1.3× the stator OD.
        r_outer = float(p.get('outer_air_factor', 1.3)) * outer_r

        in_band_poly  = SPoly(_circle(mid_r))                # full disk to mid_r
        out_band_poly = SPoly(_circle(r_outer), [_circle(mid_r)])  # annulus mid_r..r_outer
        if not in_band_poly.is_valid:  in_band_poly  = in_band_poly.buffer(0)
        if not out_band_poly.is_valid: out_band_poly = out_band_poly.buffer(0)

        # Subtract rotor solids from in_band (shaft + rotor + every magnet).
        try:
            rotor_solids = [rotor_poly, shaft_poly] + [mp for mp, _pol in mag_polys]
            in_band_poly = in_band_poly.difference(unary_union(rotor_solids))
            if not in_band_poly.is_valid: in_band_poly = in_band_poly.buffer(0)
        except Exception:
            pass

        # Subtract stator + coils from out_band.
        try:
            stator_solids = [stator_poly] + list(coil_polys)
            out_band_poly = out_band_poly.difference(unary_union(stator_solids))
            if not out_band_poly.is_valid: out_band_poly = out_band_poly.buffer(0)
        except Exception:
            pass

        return {
            'stator':   stator_poly,      # Shapely Polygon in mm
            'magnets':  mag_polys,        # list of (Polygon, polarity)
            'rotor':    rotor_poly,       # Shapely Polygon in mm
            'shaft':    shaft_poly,       # Shapely Polygon in mm
            'air_gap':  airgap_poly,      # Shapely Polygon in mm — kept for back-compat
            'in_band':  in_band_poly,     # Air disk r=0..mid_r minus rotor bodies (rotates)
            'out_band': out_band_poly,    # Air annulus mid_r..r_outer minus stator (stationary)
            'mid_r_mm': mid_r,            # slip-surface radius (mm)
            'r_outer_boundary_mm': r_outer,  # outer Dirichlet BC radius (mm)
            'coils':    coil_polys,       # list of Shapely Polygon in mm
        }

    def get_extruded_mesh_data(self, depth: float = None) -> Dict[str, Dict]:
        """
        Extrude flat 2D cross-section meshes into 3D solid meshes.

        Takes the output of get_2d_mesh_data() and for each component:
          1. Duplicates vertices at z=0 (top) and z=-depth (bottom)
          2. Keeps top faces with original winding
          3. Adds bottom faces with reversed winding
          4. Finds boundary edges and builds side-wall quads

        No CadQuery / OCCT required — pure NumPy.

        Parameters
        ----------
        depth : float, optional
            Axial extrusion depth in mm.  Defaults to motor_length parameter
            or 30 mm if not set.

        Returns
        -------
        Dict mapping component name → same mesh dict format as get_2d_mesh_data /
        get_all_mesh_data, with z spanning [0, -depth].
        """
        import numpy as np

        if depth is None:
            depth = float(self.parameters.get('motor_length', 30.0))

        flat = self.get_2d_mesh_data()
        extruded: Dict[str, Dict] = {}

        for name, comp in flat.items():
            verts_2d = np.array(comp['vertices'], dtype=float)  # (N, 3) z≈0
            faces_2d = np.array(comp['faces'],    dtype=int)    # (M, 3)
            N = len(verts_2d)

            # ── top & bottom vertices ────────────────────────────────────────
            top_v    = verts_2d.copy()
            top_v[:, 2] = 0.0
            bot_v    = verts_2d.copy()
            bot_v[:, 2] = -depth

            vertices = np.vstack([top_v, bot_v])  # (2N, 3)

            # ── top faces (original winding) ─────────────────────────────────
            top_f = faces_2d.copy()

            # ── bottom faces (reversed winding so normals point down) ────────
            bot_f = faces_2d[:, ::-1] + N

            # ── side walls (vectorised) ──────────────────────────────────────
            # Build directed edge array: for each face [a,b,c] → edges a→b, b→c, c→a
            M = len(faces_2d)
            # directed_edges shape (3M, 2): each row is [from, to]
            directed = np.concatenate([
                faces_2d[:, [0, 1]],
                faces_2d[:, [1, 2]],
                faces_2d[:, [2, 0]],
            ], axis=0)  # (3M, 2)

            # Canonical (sorted) edge for counting duplicates
            canonical = np.sort(directed, axis=1)  # (3M, 2)
            # Encode as a single int64 for fast uniqueness check (N < 2**31)
            MAX_IDX = N + 1
            codes   = canonical[:, 0].astype(np.int64) * MAX_IDX + canonical[:, 1].astype(np.int64)
            unique_codes, counts = np.unique(codes, return_counts=True)
            boundary_codes = unique_codes[counts == 1]
            boundary_set   = set(boundary_codes.tolist())

            # Among directed edges, keep those whose canonical code is a boundary
            dir_codes  = directed[:, 0].astype(np.int64) * MAX_IDX + directed[:, 1].astype(np.int64)
            can_codes  = np.sort(directed, axis=1)
            can_codes2 = can_codes[:, 0].astype(np.int64) * MAX_IDX + can_codes[:, 1].astype(np.int64)
            mask       = np.isin(can_codes2, list(boundary_set))
            boundary_directed = directed[mask]  # each row [a, b] in correct CCW winding

            if len(boundary_directed):
                a_col = boundary_directed[:, 0]
                b_col = boundary_directed[:, 1]
                # For a CCW-wound boundary edge a→b (solid to the left), the outward
                # normal of the side-wall quad must point to the RIGHT of a→b.
                # Cross-product analysis shows (a, b+N, b) and (a, a+N, b+N) give
                # normals = depth*(dy, -dx, 0) which is 90° CW from (dx,dy) = outward.
                tri1 = np.stack([a_col,        b_col + N,    b_col      ], axis=1)
                tri2 = np.stack([a_col,        a_col + N,    b_col + N  ], axis=1)
                side_f = np.vstack([tri1, tri2])
            else:
                side_f = np.empty((0, 3), dtype=int)

            all_faces = np.vstack([top_f, bot_f, side_f])

            extruded[name] = {
                'vertices':     vertices.tolist(),
                'faces':        all_faces.tolist(),
                'vertex_count': len(vertices),
                'face_count':   len(all_faces),
            }

        return extruded

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
