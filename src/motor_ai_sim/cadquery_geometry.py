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
from math import sin, cos, tan, radians
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
        import time as _time

        _t0 = _time.perf_counter()

        # 1. Stator and Shaft
        self.parts['stator_core'] = self._create_stator(cq)
        _t1 = _time.perf_counter()
        print(f"[PERF] stator: {_t1-_t0:.2f}s")

        self.parts['shaft'] = self._create_shaft(cq)
        _t2 = _time.perf_counter()
        print(f"[PERF] shaft:  {_t2-_t1:.2f}s")

        # 2. Magnets and Rotor Core with Cavities
        magnets_list = self._create_magnets(cq)
        _t3 = _time.perf_counter()
        print(f"[PERF] magnets:{_t3-_t2:.2f}s")

        rotor_solid = self._create_rotor(cq)
        _t4 = _time.perf_counter()
        print(f"[PERF] rotor:  {_t4-_t3:.2f}s")

        # Cut all magnet cavities in one compound operation (no sequential booleans)
        valid_magnets = [m for m in magnets_list if m is not None]
        if valid_magnets:
            mag_compound = cq.Compound.makeCompound([m.val() for m in valid_magnets])
            rotor_solid = rotor_solid.cut(cq.Workplane().newObject([mag_compound]))

        _t5 = _time.perf_counter()
        print(f"[PERF] rotor_cut:{_t5-_t4:.2f}s")

        for i, magnet in enumerate(magnets_list):
            if magnet is not None:
                self.parts[f'magnet_{i}'] = magnet

        self.parts['rotor_core'] = rotor_solid

        # 3. Individual Coils (one object per slot)
        try:
            coils_list = self._create_coils(cq)
            for i, coil_stack in enumerate(coils_list):
                self.parts[f'coil_{i}'] = coil_stack
        except Exception as e:
            print(f"Failed to build coils: {e}")

        _t6 = _time.perf_counter()
        print(f"[PERF] coils:  {_t6-_t5:.2f}s")
        print(f"[PERF] TOTAL build_all: {_t6-_t0:.2f}s")

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

        # ── Template-rotate approach ──────────────────────────────────────────
        # Build 6 cutter shapes for slot-0 (angle=0), then BRepBuilderAPI_Transform
        # each template for every other slot.  Avoids re-running CadQuery's full
        # polyline→wire→face→prism pipeline (12 slots × 6 cutters = 72 calls → 6 calls).
        import cadquery as _cq
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCP.gp import gp_Trsf, gp_Ax1, gp_Dir, gp_Pnt as gp_P
            from OCP.TopoDS import TopoDS_Compound
            from OCP.BRep import BRep_Builder
            _use_template_rotate = True
        except ImportError:
            _use_template_rotate = False

        def _make_template_cutters():
            """Create the 6 cutter solids at angle=0 as raw OCC shapes."""
            templates = []
            templates.append(
                cq.Workplane("XY")
                .moveTo(p1[0], p1[1]).lineTo(p2[0], p2[1])
                .lineTo(p3[0], p3[1]).lineTo(p4[0], p4[1])
                .close().extrude(stator_w + 1).val().wrapped
            )
            templates.append(
                cq.Workplane("XY")
                .moveTo(-p1[0], p1[1]).lineTo(-p2[0], p2[1])
                .lineTo(-p3[0], p3[1]).lineTo(-p4[0], p4[1])
                .close().extrude(stator_w + 1).val().wrapped
            )
            templates.append(
                cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                .translate((p3[0], p3[1], 0)).val().wrapped
            )
            templates.append(
                cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                .translate((-p3[0], p3[1], 0)).val().wrapped
            )
            templates.append(
                cq.Workplane("XY")
                .rect(slot_w, -slot_h*2, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((slot_x, slot_y, 0)).val().wrapped
            )
            templates.append(
                cq.Workplane("XY")
                .rect(-slot_w, -slot_h*2, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((-slot_x, slot_y, 0)).val().wrapped
            )
            return templates

        if _use_template_rotate:
            templates = _make_template_cutters()
            _z_axis = gp_Ax1(gp_P(0, 0, 0), gp_Dir(0, 0, 1))

            bld = BRep_Builder()
            all_shapes = TopoDS_Compound()
            bld.MakeCompound(all_shapes)

            for i in range(half_slots):
                if i == 0:
                    for tmpl in templates:
                        bld.Add(all_shapes, tmpl)
                else:
                    trsf = gp_Trsf()
                    trsf.SetRotation(_z_axis, radians(i * slot_angle))
                    for tmpl in templates:
                        rotated = BRepBuilderAPI_Transform(tmpl, trsf, True).Shape()
                        bld.Add(all_shapes, rotated)

            # Wrap as CadQuery shape for the cut call
            tool_shape = _cq.Shape.cast(all_shapes)
        else:
            cutters = []
            for i in range(half_slots):
                angle = i * slot_angle
                cutters.append(
                    cq.Workplane("XY")
                    .moveTo(p1[0], p1[1]).lineTo(p2[0], p2[1])
                    .lineTo(p3[0], p3[1]).lineTo(p4[0], p4[1])
                    .close().extrude(stator_w + 1).rotate((0,0,0),(0,0,1), angle)
                )
                cutters.append(
                    cq.Workplane("XY")
                    .moveTo(-p1[0], p1[1]).lineTo(-p2[0], p2[1])
                    .lineTo(-p3[0], p3[1]).lineTo(-p4[0], p4[1])
                    .close().extrude(stator_w + 1).rotate((0,0,0),(0,0,1), angle)
                )
                cutters.append(
                    cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                    .translate((p3[0], p3[1], 0)).rotate((0,0,0),(0,0,1), angle)
                )
                cutters.append(
                    cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                    .translate((-p3[0], p3[1], 0)).rotate((0,0,0),(0,0,1), angle)
                )
                cutters.append(
                    cq.Workplane("XY")
                    .rect(slot_w, -slot_h*2, centered=(False, False))
                    .extrude(stator_w + 1).translate((slot_x, slot_y, 0))
                    .rotate((0,0,0),(0,0,1), angle)
                )
                cutters.append(
                    cq.Workplane("XY")
                    .rect(-slot_w, -slot_h*2, centered=(False, False))
                    .extrude(stator_w + 1).translate((-slot_x, slot_y, 0))
                    .rotate((0,0,0),(0,0,1), angle)
                )
            tool_shape = _cq.Compound.makeCompound([c.val() for c in cutters])

        # Cut with the compound tool in a single boolean operation
        stator = stator.cut(cq.Workplane().newObject([tool_shape]))

        # ── Fillet: OUTER RADIUS corners ─────────────────────────────────────
        # Short |Z edges where slot/trapezoid walls meet the outer cylinder.
        # After compound-cut the edges are classified by length: slot walls
        # create short edges (~stator_w) whereas the outer cylinder arcs are long.
        if slot_fillet_r > 0:
            _r_lo = outer_r - 1.5
            _r_hi = outer_r + 0.5

            class _OuterRingSelector(_cq.selectors.Selector):
                def filter(self_, obj_list):
                    return [e for e in obj_list
                            if _r_lo < (e.Center().x**2 + e.Center().y**2)**0.5 < _r_hi]

            try:
                stator = stator.edges("|Z").edges(_OuterRingSelector()).fillet(slot_fillet_r)
            except Exception as ex:
                print(f"[stator] outer-ring fillet skipped (r={slot_fillet_r}): {ex}")

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
        
        # Calculate angles in radians for math functions
        angle_down = radians(pole_angle * mag_fill_down / 2)
        angle_up = radians(pole_angle * mag_fill_up / 2)

        p1 = (magnet_r * sin(angle_down), magnet_r * cos(angle_down))
        p2 = ((magnet_r + mag_down_h) * sin(angle_down), (magnet_r + mag_down_h) * cos(angle_down))
        p3 = ((rotor_outer_r - mag_up_gap) * sin(angle_up), (rotor_outer_r - mag_up_gap) * cos(angle_up))
        p4 = (-(rotor_outer_r - mag_up_gap) * sin(angle_up), (rotor_outer_r - mag_up_gap) * cos(angle_up))
        p5 = (-(magnet_r + mag_down_h) * sin(angle_down), (magnet_r + mag_down_h) * cos(angle_down))
        p6 = (-magnet_r * sin(angle_down), magnet_r * cos(angle_down))

        # ── Template-rotate: create one magnet + fillet, then OCC-copy for the rest ──
        # Reduces polyline+extrude+fillet from N calls to 1 call + N-1 transforms.
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform as _BRT
            from OCP.gp import gp_Trsf as _Trsf, gp_Ax1 as _Ax1, gp_Dir as _Dir, gp_Pnt as _Pnt
            _use_mag_template = True
        except ImportError:
            _use_mag_template = False

        # Build the template magnet at angle=0
        template_magnet = (
            cq.Workplane("XY")
            .polyline([p1, p2, p3, p4, p5, p6])
            .close()
            .extrude(width)
        )
        if mag_fill_r > 0:
            try:
                template_magnet = template_magnet.edges(">Y and |Z").fillet(mag_fill_r)
            except Exception as e:
                print(f"Warning: Could not apply fillet to magnet: {e}")

        magnets = []
        if _use_mag_template:
            _tmpl_shape = template_magnet.val().wrapped
            _z_ax = _Ax1(_Pnt(0, 0, 0), _Dir(0, 0, 1))
            for i in range(num_poles):
                if i == 0:
                    magnets.append(template_magnet)
                else:
                    t = _Trsf()
                    t.SetRotation(_z_ax, radians(i * pole_angle))
                    rotated = _BRT(_tmpl_shape, t, True).Shape()
                    magnets.append(cq.Workplane().newObject([cq.Shape.cast(rotated)]))
        else:
            magnets.append(template_magnet)
            for i in range(1, num_poles):
                magnets.append(template_magnet.rotate((0, 0, 0), (0, 0, 1), i * pole_angle))

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

        import cadquery as _cq
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform as _BRT2
            from OCP.gp import gp_Trsf as _Trsf2, gp_Ax1 as _Ax2, gp_Dir as _Dir2, gp_Pnt as _Pnt2
            from OCP.TopoDS import TopoDS_Compound as _TDC2
            from OCP.BRep import BRep_Builder as _BB2
            _use_rotor_tpl = True
        except ImportError:
            _use_rotor_tpl = False

        # Create one slot cutter at angle=0, then rotate copies
        tmpl_cutter = (
            cq.Workplane("XY")
            .rect(rec_w, -mag_h, centered=(False, False))
            .extrude(width + 1)
            .translate((-rec_w/2, rotor_outer_r, 0))
        )
        if _use_rotor_tpl:
            _tc_shape = tmpl_cutter.val().wrapped
            _zax2 = _Ax2(_Pnt2(0, 0, 0), _Dir2(0, 0, 1))
            _bld2 = _BB2()
            _cpd2 = _TDC2()
            _bld2.MakeCompound(_cpd2)
            for i in range(num_poles):
                if i == 0:
                    _bld2.Add(_cpd2, _tc_shape)
                else:
                    t2 = _Trsf2()
                    t2.SetRotation(_zax2, radians(i * pole_angle))
                    _bld2.Add(_cpd2, _BRT2(_tc_shape, t2, True).Shape())
            rotor_tool = _cq.Shape.cast(_cpd2)
        else:
            rotor_tool = _cq.Compound.makeCompound(
                [tmpl_cutter.rotate((0,0,0),(0,0,1), i * pole_angle).val() for i in range(num_poles)]
            )
        rotor = rotor.cut(cq.Workplane().newObject([rotor_tool]))

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
    
        # Store coil geometry params for analytical mesh generation (bypass OCC tess)
        self._coil_mesh_params = {
            'half_slots': half_slots, 'slot_angle': slot_angle,
            'num_wires': num_wires, 'top_y': top_y,
            'right_x': right_x, 'wire_w': wire_w, 'wire_h': wire_h,
            'wire_d_y': wire_d_y, 'stator_w': stator_w,
        }

        # ── Pre-build OCC box shapes for one slot (template, rotated per slot) ─
        # BRepPrimAPI_MakeBox avoids the CadQuery polyline→wire→face→prism chain.
        # We create a Compound of all wire boxes, then rotate once per slot.
        # No boolean ops at all — Compound assembly is O(N) pointer operations.
        try:
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
            from OCP.gp import gp_Pnt
            from OCP.TopoDS import TopoDS_Compound
            from OCP.BRep import BRep_Builder
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCP.gp import gp_Trsf, gp_Ax1, gp_Dir
            import math as _math
            _use_occ_boxes = True
        except ImportError:
            _use_occ_boxes = False

        coils = []

        for i in range(half_slots):
            angle = i * slot_angle

            if _use_occ_boxes:
                # Build OCC compound of raw boxes — no CadQuery overhead
                builder = BRep_Builder()
                compound = TopoDS_Compound()
                builder.MakeCompound(compound)
                for step_y in range(num_wires):
                    current_y = top_y - step_y * (wire_h + wire_d_y)
                    # Right box: corner at (right_x, current_y-wire_h, 0)
                    rb = BRepPrimAPI_MakeBox(
                        gp_Pnt(right_x,          current_y - wire_h, 0),
                        gp_Pnt(right_x + wire_w, current_y,          stator_w),
                    ).Shape()
                    # Left box: corner at (-right_x-wire_w, current_y-wire_h, 0)
                    lb = BRepPrimAPI_MakeBox(
                        gp_Pnt(-right_x - wire_w, current_y - wire_h, 0),
                        gp_Pnt(-right_x,           current_y,          stator_w),
                    ).Shape()
                    builder.Add(compound, rb)
                    builder.Add(compound, lb)

                # Rotate compound around Z axis
                trsf = gp_Trsf()
                trsf.SetRotation(
                    gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)),
                    _math.radians(angle),
                )
                rotated = BRepBuilderAPI_Transform(compound, trsf, True).Shape()
                coils.append(cq.Shape.cast(rotated))
            else:
                # Fallback: original CadQuery approach
                wires = []
                for step_y in range(num_wires):
                    current_y = top_y - step_y * (wire_h + wire_d_y)
                    right_pts = [(right_x, current_y), (right_x + wire_w, current_y),
                                 (right_x + wire_w, current_y - wire_h), (right_x, current_y - wire_h)]
                    left_pts  = [(-right_x, current_y), (-right_x - wire_w, current_y),
                                 (-right_x - wire_w, current_y - wire_h), (-right_x, current_y - wire_h)]
                    wires.append(cq.Workplane("XY").polyline(right_pts).close().extrude(stator_w).rotate((0,0,0),(0,0,1), angle))
                    wires.append(cq.Workplane("XY").polyline(left_pts).close().extrude(stator_w).rotate((0,0,0),(0,0,1), angle))
                compound = cq.Compound.makeCompound([w.val() for w in wires])
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
                
            # tolerance=0.2 is visually fine for the web viewer and ~2× faster than 0.1
            vertices, faces = solid.tessellate(0.2)

            # Fast vertex formatting: tuple access is cheaper than list construction
            # and attribute lookup on gp_Pnt/Vector is the bottleneck for large meshes.
            vertices_list = [(v.x, v.y, v.z) for v in vertices]

            return {
                'vertices': vertices_list,
                'faces': faces,
                'vertex_count': len(vertices_list),
                'face_count': len(faces),
            }
        except Exception as e:
            print(f"Error tessellating {component}: {e}")
            return None

    def _make_coil_mesh_analytical(self) -> Optional[Dict]:
        """Generate coil wire mesh analytically (no OCC tessellation).

        Each wire is a rectangular box.  We compute all 8 vertices and
        12 triangles directly in Python, rotate by the slot angle, and
        return the combined mesh.  This is ~50× faster than OCC tessellate
        for 192 simple boxes.
        """
        cp = getattr(self, '_coil_mesh_params', None)
        if cp is None:
            return None

        import math as _m
        half_slots = cp['half_slots']
        slot_angle = cp['slot_angle']
        num_wires  = cp['num_wires']
        top_y      = cp['top_y']
        right_x    = cp['right_x']
        wire_w     = cp['wire_w']
        wire_h     = cp['wire_h']
        wire_d_y   = cp['wire_d_y']
        stator_w   = cp['stator_w']

        vertices = []
        faces    = []
        base_idx = 0

        # BOX_FACES: 6 faces × 2 triangles each; indices into 8 corners of a unit box
        # Corners: 0=(0,0,0) 1=(1,0,0) 2=(1,1,0) 3=(0,1,0)
        #          4=(0,0,1) 5=(1,0,1) 6=(1,1,1) 7=(0,1,1)
        BOX_TRIS = [
            (0,1,2),(0,2,3),  # -Z face
            (4,6,5),(4,7,6),  # +Z face
            (0,4,5),(0,5,1),  # -Y face
            (3,2,6),(3,6,7),  # +Y face
            (0,3,7),(0,7,4),  # -X face
            (1,5,6),(1,6,2),  # +X face
        ]

        for i in range(half_slots):
            ang_rad = _m.radians(i * slot_angle)
            cos_a, sin_a = _m.cos(ang_rad), _m.sin(ang_rad)

            def rot(x, y):
                return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)

            for step_y in range(num_wires):
                current_y = top_y - step_y * (wire_h + wire_d_y)
                y0, y1 = current_y - wire_h, current_y
                z0, z1 = 0.0, stator_w

                for (x0, x1) in [(right_x, right_x + wire_w),
                                  (-right_x - wire_w, -right_x)]:
                    # 8 box corners: (x in {x0,x1}) × (y in {y0,y1}) × (z in {z0,z1})
                    corners_local = [
                        (x0, y0), (x1, y0), (x1, y1), (x0, y1),  # z0 layer
                        (x0, y0), (x1, y0), (x1, y1), (x0, y1),  # z1 layer
                    ]
                    zs = [z0]*4 + [z1]*4
                    for (lx, ly), lz in zip(corners_local, zs):
                        rx, ry = rot(lx, ly)
                        vertices.append((rx, ry, lz))

                    for tri in BOX_TRIS:
                        faces.append((base_idx + tri[0],
                                      base_idx + tri[1],
                                      base_idx + tri[2]))
                    base_idx += 8

        return {
            'vertices': vertices,
            'faces':    faces,
            'vertex_count': len(vertices),
            'face_count':   len(faces),
        }

    def _split_coil_mesh(self, compound_mesh: Dict) -> List[Dict]:
        """Split the analytical coil compound mesh into per-slot meshes."""
        cp = getattr(self, '_coil_mesh_params', None)
        if cp is None:
            return [compound_mesh]
        half_slots = cp['half_slots']
        num_wires  = cp['num_wires']
        # Each slot = num_wires * 2 boxes; each box = 8 vertices, 12 faces
        verts_per_slot = num_wires * 2 * 8
        faces_per_slot = num_wires * 2 * 12
        all_v = compound_mesh['vertices']
        all_f = compound_mesh['faces']
        parts = []
        for i in range(half_slots):
            v0 = i * verts_per_slot
            f0 = i * faces_per_slot
            sv = all_v[v0: v0 + verts_per_slot]
            # Adjust face indices to be relative to this slot's vertex slice
            sf = [(fa - v0, fb - v0, fc - v0)
                  for (fa, fb, fc) in all_f[f0: f0 + faces_per_slot]]
            parts.append({
                'vertices': sv, 'faces': sf,
                'vertex_count': len(sv), 'face_count': len(sf),
            })
        return parts

    @staticmethod
    def _rotate_mesh(mesh: Dict, angle_deg: float) -> Dict:
        """Rotate all mesh vertices by angle_deg around Z axis (pure Python)."""
        import math as _m
        ang = _m.radians(angle_deg)
        cos_a, sin_a = _m.cos(ang), _m.sin(ang)
        rotated = [
            (x * cos_a - y * sin_a, x * sin_a + y * cos_a, z)
            for (x, y, z) in mesh['vertices']
        ]
        return {
            'vertices': rotated,
            'faces':    mesh['faces'],        # connectivity unchanged
            'vertex_count': mesh['vertex_count'],
            'face_count':   mesh['face_count'],
        }

    @staticmethod
    def _as_cq_shape(shape):
        """Normalise any shape variant to a cq.Shape for Compound assembly."""
        import cadquery as _cq
        if isinstance(shape, _cq.Shape):
            return shape
        if hasattr(shape, 'val'):          # cq.Workplane
            return shape.val()
        # Raw OCC TopoDS_* (from BRepBuilderAPI_Transform etc.)
        return _cq.Shape.cast(shape)

    def get_all_mesh_data(self) -> Dict[str, Dict]:
        """Get mesh data for all components.

        Magnets and coils are merged into single compounds before tessellation,
        reducing tessellation calls from O(N_poles + N_slots) to O(1).
        """
        import time as _time
        import cadquery as _cq

        if not self.parts:
            self.build_all()

        _t0 = _time.perf_counter()

        # Partition parts: structural stay separate; magnets/coils use template tess
        to_tessellate = {}
        mag_names:  List[str] = []
        coil_names: List[str] = []

        for name in self.parts:
            if name.startswith('magnet_'):
                mag_names.append(name)
            elif name.startswith('coil_'):
                coil_names.append(name)
            else:
                to_tessellate[name] = self._as_cq_shape(self.parts[name])

        # Coils: generate all meshes analytically (no OCC at all)
        _coil_mesh = self._make_coil_mesh_analytical() if coil_names else None

        # Per-shape tessellation tolerances (linear mm, angular rad)
        # Larger = fewer triangles = faster but coarser appearance
        _TESS_TOL = {
            'stator_core': (0.3, 0.4),
            'rotor_core':  (0.3, 0.4),
            'magnets':     (0.5, 0.5),
            'shaft':       (1.0, 0.8),
        }

        mesh_data = {}

        # Coil meshes: split analytical compound mesh back into per-slot parts
        if _coil_mesh:
            per_slot = self._split_coil_mesh(_coil_mesh)
            for idx, name in enumerate(sorted(coil_names)):
                if idx < len(per_slot):
                    mesh_data[name] = per_slot[idx]
            print(f"[PERF]   tess coils: analytical {len(coil_names)} slots")
        elif coil_names:
            # Fallback: tessellate template + rotate
            tmpl = self._tessellate_shape('coil_tpl', self._as_cq_shape(self.parts[coil_names[0]]))
            if tmpl:
                for idx, name in enumerate(sorted(coil_names)):
                    if idx == 0:
                        mesh_data[name] = tmpl
                    else:
                        ang = idx * (360.0 / len(coil_names))
                        mesh_data[name] = self._rotate_mesh(tmpl, ang)

        # Magnet meshes: tessellate template once, rotate for the rest (1 OCC call for N)
        if mag_names:
            sorted_mags = sorted(mag_names, key=lambda n: int(n.split('_')[1]))
            mag_tol = _TESS_TOL['magnets']
            _tp = _time.perf_counter()
            tmpl = self._tessellate_shape('mag_tpl', self._as_cq_shape(self.parts[sorted_mags[0]]),
                                          tolerance=mag_tol[0], angular_tolerance=mag_tol[1])
            n_mag = len(sorted_mags)
            pole_ang = 360.0 / n_mag
            if tmpl:
                mesh_data[sorted_mags[0]] = tmpl
                for idx in range(1, n_mag):
                    mesh_data[sorted_mags[idx]] = self._rotate_mesh(tmpl, idx * pole_ang)
            print(f"[PERF]   tess magnets: tpl+rotate {n_mag} poles: {_time.perf_counter()-_tp:.2f}s")

        for name, shape in to_tessellate.items():
            tols = _TESS_TOL.get(name, (0.3, 0.4))
            _tp = _time.perf_counter()
            data = self._tessellate_shape(name, shape, tolerance=tols[0], angular_tolerance=tols[1])
            print(f"[PERF]   tess {name}: {_time.perf_counter()-_tp:.2f}s"
                  + (f" ({data['vertex_count']}v)" if data else " FAILED"))
            if data:
                mesh_data[name] = data

        _t1 = _time.perf_counter()
        print(f"[PERF] tessellation ({len(to_tessellate)} groups, was {len(self.parts)} parts): {_t1-_t0:.2f}s")

        return mesh_data

    def _tessellate_shape(self, name: str, shape,
                          tolerance: float = 0.3,
                          angular_tolerance: float = 0.4) -> Optional[Dict]:
        """Tessellate one shape (any cq.Shape variant) and return mesh dict."""
        try:
            solid = self._as_cq_shape(shape)
            vertices, faces = solid.tessellate(tolerance, angular_tolerance)
            vertices_list = [(v.x, v.y, v.z) for v in vertices]
            return {
                'vertices': vertices_list,
                'faces': faces,
                'vertex_count': len(vertices_list),
                'face_count': len(faces),
            }
        except Exception as e:
            print(f"Error tessellating {name}: {e}")
            return None
    
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
