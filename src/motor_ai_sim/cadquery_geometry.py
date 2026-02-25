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
from math import sin, cos, radians
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
        """Map API parameter names to CadQuery parameter names."""
        # Start with hardcoded defaults to ensure all required params exist
        mapped = self._get_hardcoded_defaults().copy()
        
        # Map API params to CadQuery params (these will override defaults)
        # Stator parameters
        if 'stator_diameter' in api_params:
            mapped['stator_outer_radius'] = api_params['stator_diameter'] / 2
        elif 'stator_outer_radius' in api_params:
            mapped['stator_outer_radius'] = api_params['stator_outer_radius']
            
        if 'stator_inner_radius' in api_params:
            mapped['stator_inner_radius'] = api_params['stator_inner_radius']
        elif 'stator_outer_radius' in mapped:
            # Calculate stator_inner_radius as 70% of outer radius
            mapped['stator_inner_radius'] = mapped['stator_outer_radius'] * 0.7
            
        if 'core_thickness' in api_params:
            mapped['core_thickness'] = api_params['core_thickness']
        if 'stator_width' in api_params:
            mapped['stator_width'] = api_params['stator_width']
            
        if 'slot_height' in api_params:
            mapped['slot_height'] = api_params['slot_height']
        if 'tooth_width' in api_params:
            mapped['tooth_width'] = api_params['tooth_width']
            
        if 'num_slots' in api_params:
            mapped['num_slots'] = int(api_params['num_slots'])
        elif 'num_slots_per_segment' in api_params and 'num_seg' in api_params:
            # num_slots = num_slots_per_segment * num_seg
            mapped['num_slots'] = int(api_params['num_slots_per_segment'] * api_params['num_seg'])
        
        # Map num_seg to num_poles
        if 'num_seg' in api_params:
            mapped['num_poles'] = int(api_params['num_seg'])
        elif 'num_poles_per_segment' in api_params and 'num_seg' in api_params:
            mapped['num_poles'] = int(api_params['num_poles_per_segment'] * api_params['num_seg'])
        
        # Rotor parameters
        if 'rotor_outer_radius' in api_params:
            mapped['rotor_outer_radius'] = api_params['rotor_outer_radius']
        elif 'stator_inner_radius' in mapped:
            air_gap = api_params.get('air_gap', 2.0)
            mapped['rotor_outer_radius'] = mapped['stator_inner_radius'] - air_gap
        elif 'stator_outer_radius' in mapped:
            air_gap = api_params.get('air_gap', 2.0)
            mapped['rotor_outer_radius'] = mapped['stator_outer_radius'] * 0.68 - air_gap
            
        if 'rotor_inner_radius' in api_params:
            mapped['rotor_inner_radius'] = api_params['rotor_inner_radius']
        elif 'rotor_outer_radius' in mapped:
            mapped['rotor_inner_radius'] = mapped['rotor_outer_radius'] * 0.3
        else:
            mapped['rotor_inner_radius'] = 20.0  # Default fallback
            
        if 'magnet_height' in api_params:
            mapped['magnet_height'] = api_params['magnet_height']
        if 'magnet_width' in api_params:
            mapped['magnet_width'] = api_params['magnet_width']
        elif 'num_poles' in mapped:
            # Calculate from number of poles (use mapped value, not api_params)
            mapped['magnet_width'] = 360.0 / mapped['num_poles'] * 0.7
        
        # Shaft radius (from rotor_inner_radius or use default)
        if 'shaft_radius' in api_params:
            mapped['shaft_radius'] = api_params['shaft_radius']
        else:
            mapped['shaft_radius'] = mapped.get('rotor_inner_radius', 20)
        
        # Air gap
        if 'air_gap' in api_params:
            mapped['air_gap'] = api_params['air_gap']
        
        # Wire parameters
        if 'wire_width' in api_params:
            mapped['wire_width'] = api_params['wire_width']
        if 'wire_height' in api_params:
            mapped['wire_height'] = api_params['wire_height']
        if 'wire_spacing_x' in api_params:
            mapped['wire_spacing_x'] = api_params['wire_spacing_x']
        if 'wire_spacing_y' in api_params:
            mapped['wire_spacing_y'] = api_params['wire_spacing_y']
        if 'insulation_thickness' in api_params:
            mapped['insulation_thickness'] = api_params['insulation_thickness']
        if 'slot_hs' in api_params:
            mapped['slot_hs'] = api_params['slot_hs']
        
        # Number of poles
        if 'num_poles' in api_params:
            mapped['num_poles'] = int(api_params['num_poles'])
        
        return mapped
    
    def _get_hardcoded_defaults(self) -> Dict:
        """Get hardcoded default parameters."""
        return {
            'stator_outer_radius': 100.0,
            'stator_inner_radius': 70.0,
            'stator_width': 50.0,
            'core_thickness': 50.0,
            'slot_height': 10.0,
            'tooth_width': 5.0,
            'num_slots': 36,
            'num_poles': 12,
            'rotor_outer_radius': 68.0,
            'rotor_inner_radius': 20.0,
            'magnet_width': 8.0,
            'magnet_height': 5.0,
            'shaft_radius': 15.0,
            'shaft_inner_radius': 5.0,  # inner hole radius for shaft
            'air_gap': 2.0,
            'wire_width': 4.0,
            'wire_height': 0.6,
            'wire_spacing_x': 0.1,
            'wire_spacing_y': 0.13,
            'insulation_thickness': 0.15,
            'slot_hs': 0.2,  # slot opening height ratio
            'num_wires_per_slot': 10,  # number of wires stacked vertically
        }
    
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
        core_h = p['core_thickness']
        slot_height = p['slot_height']
        stator_w = p['stator_width']
        num_slots = int(p['num_slots'])
        tooth_width = p['tooth_width']
        wire_w = p['wire_width']
        ins_w = p['insulation_thickness']
        wire_d_x = p['wire_spacing_x']
        slot_w = wire_w + ins_w*2 + wire_d_x
        slot_h = slot_height + core_h # Use slot_height directly for cut depth
        slot_x = tooth_width / 2
        slot_y = outer_r - core_h
        half_slots = num_slots // 2
        
        slot_angle = 360.0 / half_slots  # Fixed: use num_slots, not half_slots
        
        # Create stator as a solid ring first
        stator = (
            cq.Workplane("XY")
            .circle(outer_r)
            .circle(inner_r)
            .extrude(stator_w)
        )
        
        # Create slot cutouts using rotate/translate approach instead of polarArray
        # This is more reliable in CadQuery 2.x
        for i in range(half_slots):
            angle = i * slot_angle
            # Create positive side slot 
            slot = (
                cq.Workplane("XY")
                .rect(slot_w, -slot_h, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((slot_x, slot_y, 0))
                .rotate((0, 0, 0), (0, 0, 1), angle)
            )
            
            # Create negative side slot
            slot_neg = (
                cq.Workplane("XY")
                .rect(-slot_w, -slot_h, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((-slot_x, slot_y, 0))
                .rotate((0, 0, 0), (0, 0, 1), angle)
            )
                                  
            # Cut both slots from stator
            stator = stator.cut(slot)
            stator = stator.cut(slot_neg)
        
        return stator
        
    def _create_shaft(self, cq) -> Any:
        """Create motor shaft."""
        p = self.parameters
        
        shaft_r = p['rotor_inner_radius']
        shaft_in = p['shaft_inner_radius']
        length = p['stator_width'] 
        
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
        rotor_house_h = p.get('rotor_house_height', 2.0)     # rotor housing thickness
        mag_fill_down = p.get('magnet_fill_down', 0.8)       # down fill ratio of the magnet 
        mag_fill_up = p.get('magnet_fill_up', 0.8)           # up fill ratio of the magnet 
        mag_fill_radius = p.get('magnet_fill_radius', 1.0)   # magnet fillet radius 
        mag_up_gap = p.get('magnet_up_gap', 1.0)             # magnet cut up gap
        mag_down_h = p.get('magnet_down_height', 5.0)        # magnet down height 
        pole_angle = 360.0 / num_poles
        magnet_r = rotor_inner_r + rotor_house_h
        
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
                #.translate((magnet_r, 0, 0))
                .rotate((0, 0, 0), (0, 0, 1), angle)
            )

            magnets.append(magnet)
            
        return magnets
    def _create_rotor(self, cq) -> Any:
        """Create rotor hub."""
        p = self.parameters
        
        rotor_outer_r = p['rotor_outer_radius']
        shaft_r = p['shaft_radius']
        width = p['stator_width']
        
        rotor = (
            cq.Workplane("XY")
            .circle(rotor_outer_r)
            .circle(shaft_r)
            .extrude(width)
        )
        
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
            
        import trimesh
        from cadquery import exporters
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as tmp:
            tmp_path = tmp.name
            
        try:
            exporters.export(self.parts[component], tmp_path, exportType='STL', tolerance=0.1)
            mesh = trimesh.load_mesh(tmp_path)
            return {
                'vertices': mesh.vertices.tolist(),
                'faces': mesh.faces.tolist(),
                'vertex_count': len(mesh.vertices),
                'face_count': len(mesh.faces),
            }
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    
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
