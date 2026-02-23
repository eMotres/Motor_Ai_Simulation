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
import math

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
            'air_gap': 2.0,
            'wire_width': 2.0,
            'wire_height': 1.5,
            'wire_spacing_x': 0.5,
            'wire_spacing_y': 0.5,
            'insulation_thickness': 0.3,
            'slot_hs': 0.2,  # slot opening height ratio
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
        """Build all motor components."""
        if not _import_cadquery():
            raise RuntimeError("CadQuery is not available")
        
        import cadquery as cq
        from cadquery import exporters
        
        p = self.parameters
        
        # Create stator
        self.parts['stator_core'] = self._create_stator(cq)
        self.parts['rotor_core'] = self._create_rotor(cq)
        self.parts['shaft'] = self._create_shaft(cq)
        
        # Create magnets
        magnets = self._create_magnets(cq)
        for i, magnet in enumerate(magnets):
            self.parts[f'magnet_{i}'] = magnet
            
        # Create coils
        coils = self._create_coils(cq)
        for i, coil in enumerate(coils):
            self.parts[f'coil_{i}'] = coil
            
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

        slot_angle = 360.0 / half_slots #tooth_width_rad = math.radians(tooth_width_deg)
        
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
            )
            # Translate to correct position
            slot = slot.translate((slot_x, slot_y, 0))
            
            # Create negative side slot
            slot_neg = (
                cq.Workplane("XY")
                .rect(-slot_w, -slot_h, centered=(False, False))
                .extrude(stator_w + 1)
            )
            # Translate to correct position
            slot_neg = slot_neg.translate((-slot_x, slot_y, 0))
            
            # Rotate both slots to position
            slot = slot.rotate((0, 0, 0), (0, 0, 1), angle)
            slot_neg = slot_neg.rotate((0, 0, 0), (0, 0, 1), angle)
                       
            # Cut both slots from stator
            stator = stator.cut(slot)
            stator = stator.cut(slot_neg)
        
        return stator
    
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
    
    def _create_shaft(self, cq) -> Any:
        """Create motor shaft."""
        p = self.parameters
        
        shaft_r = p['shaft_radius']
        length = p['stator_width'] * 1.2
        
        shaft = (
            cq.Workplane("XY")
            .circle(shaft_r)
            .extrude(length)
        )
        
        return shaft
    
    def _create_magnets(self, cq) -> List[Any]:
        """Create rotor magnets."""
        p = self.parameters
        
        rotor_outer_r = p['rotor_outer_radius']
        magnet_width = p['magnet_width']
        magnet_height = p['magnet_height']
        num_poles = int(p['num_poles'])
        width = p['stator_width']
        
        magnets = []
        pole_angle = 360.0 / num_poles
        
        for i in range(num_poles):
            angle = i * pole_angle
            magnet_r = rotor_outer_r - magnet_height / 2
            
            # Create magnet at origin then rotate/translate
            magnet = (
                cq.Workplane("XY")
                .rect(magnet_height, magnet_width)
                .extrude(width)
            )
            # Position at correct radius and angle
            magnet = magnet.translate((magnet_r, 0, 0))
            magnet = magnet.rotate((0, 0, 0), (0, 0, 1), angle)
            magnets.append(magnet)
            
        return magnets
    
    def _create_coils(self, cq) -> List[Any]:
        """Create coils wound in stator slots - individual coils for each slot."""
        import math
        p = self.parameters
        
        outer_r = p['stator_outer_radius']
        inner_r = p['stator_inner_radius']
        core_h = p['core_thickness']
        slot_hs = p['slot_hs']
        slot_height = p['slot_height']
        stator_w = p['stator_width']
        num_slots = int(p['num_slots'])
        tooth_width = p['tooth_width']
        wire_w = p['wire_width']
        ins_w = p['insulation_thickness']
        wire_d_y = p['wire_spacing_y']
        wire_d_x = p['wire_spacing_x']
        coil_w = wire_w
        coil_h = slot_height * (1 - slot_hs) - ins_w *2 - wire_d_y # Use slot_height directly for cut depth
        coil_x = tooth_width / 2 + ins_w + wire_d_x / 2
        coil_y = outer_r - core_h -ins_w -wire_d_y / 2 
        half_slots = num_slots // 2

        slot_angle = 360.0 / half_slots #tooth_width_rad = math.radians(tooth_width_deg)
        
        # Create individual coils for each slot
        coils = []
        
        for i in range(half_slots):
            angle = i * slot_angle
            
            # Create coil using rotate/translate approach
            coil = (
                cq.Workplane("XY")
                .rect(coil_w, -coil_h, centered=(False, False))
                .extrude(stator_w )
            )
            
            # Translate to position and rotate
            coil = coil.translate((coil_x, coil_y, 0))
            coil = coil.rotate((0, 0, 0), (0, 0, 1), angle)
            
            coils.append(coil)
        
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
