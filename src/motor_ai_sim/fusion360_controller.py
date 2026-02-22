# Fusion 360 Parametric Controller
"""
Fusion 360 Headless Parametric Controller for Motor Geometry

This script provides:
1. Parameter Injection: Read JSON config and update Fusion 360 UserParameters
2. Automated Rebuild: Trigger model compute to update B-Rep geometry
3. Multi-Body STL Export: Export each component as STL
4. CLI Mode: Can be triggered via command line

Usage:
    python fusion360_controller.py --params params.json --output_dir ./stl_output
    
    # Or with direct parameters:
    python fusion360_controller.py --stator_outer_radius 100 --num_slots 36 --num_poles 12
"""

import json
import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Optional, List
import numpy as np

# Try to import Fusion 360 API
try:
    import adsk
    import adsk.fusion
    import adsk.core
    FUSION360_AVAILABLE = True
except ImportError:
    FUSION360_AVAILABLE = False
    print("Fusion 360 API not available. Using fallback geometry mode.")


class Fusion360Controller:
    """Controller for Fusion 360 parametric geometry."""
    
    def __init__(self):
        self.app = None
        self.design = None
        self.root_comp = None
        self.parameters = {}
        
    def connect(self) -> bool:
        """Connect to running Fusion 360 instance."""
        if not FUSION360_AVAILABLE:
            return False
            
        try:
            self.app = adsk.core.Application.get()
            self.design = self.app.activeProduct
            self.root_comp = self.design.rootComponent
            return True
        except Exception as e:
            print(f"Failed to connect to Fusion 360: {e}")
            return False
    
    def load_parameters(self, params: Dict) -> None:
        """Load parameters from dictionary to Fusion 360."""
        if not self.connect():
            self.parameters = params
            return
            
        # Get user parameters from Fusion 360
        user_params = self.design.userParameters
        
        # Map our params to Fusion 360 parameter names
        param_mapping = {
            'stator_outer_radius': 'StatorOuterDiameter',
            'stator_inner_radius': 'StatorInnerDiameter', 
            'stator_width': 'StatorWidth',
            'slot_height': 'SlotHeight',
            'slot_width': 'SlotWidth',
            'num_slots': 'NumSlots',
            'num_poles': 'NumPoles',
            'rotor_outer_radius': 'RotorOuterDiameter',
            'shaft_radius': 'ShaftDiameter',
            'magnet_height': 'MagnetHeight',
            'air_gap': 'AirGap',
        }
        
        for our_name, fusion_name in param_mapping.items():
            if our_name in params:
                try:
                    param = user_params.itemByName(fusion_name)
                    if param:
                        param.expression = str(params[our_name])
                        self.parameters[our_name] = params[our_name]
                except:
                    pass
        
        # Trigger rebuild
        self.rebuild()
        
    def rebuild(self) -> None:
        """Trigger model rebuild."""
        if self.app:
            self.app.executeTrigger(
                adsk.core.CommandTriggerCommand.id, 
                ''
            )
    
    def export_stl(self, output_dir: str, components: Optional[List[str]] = None) -> Dict[str, str]:
        """
        Export components as STL files.
        
        Args:
            output_dir: Directory to save STL files
            components: List of component names to export (default: all)
            
        Returns:
            Dictionary mapping component name to STL file path
        """
        if components is None:
            components = ['stator_core', 'rotor_core', 'magnets', 'coils', 'shaft']
            
        os.makedirs(output_dir, exist_ok=True)
        
        if not self.connect():
            # Fallback: generate STLs from current geometry
            return self._generate_fallback_stls(output_dir, components)
        
        export_manager = self.app.exportManager
        
        stl_files = {}
        
        for comp_name in components:
            stl_path = os.path.join(output_dir, f"{comp_name}.stl")
            
            try:
                # Find the component
                occs = self.root_comp.occurrences
                target_occ = None
                
                for occ in occs:
                    if comp_name.lower() in occ.name.lower():
                        target_occ = occ
                        break
                
                if target_occ:
                    stl_options = export_manager.createSTLExportOptions(
                        target_occ, stl_path
                    )
                    stl_options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
                    export_manager.execute(stl_options)
                    stl_files[comp_name] = stl_path
                    print(f"Exported {comp_name} to {stl_path}")
                else:
                    print(f"Component {comp_name} not found in Fusion 360")
                    
            except Exception as e:
                print(f"Error exporting {comp_name}: {e}")
        
        return stl_files
    
    def _generate_fallback_stls(self, output_dir: str, components: List[str]) -> Dict[str, str]:
        """Generate STL files from fallback geometry when Fusion 360 is not available."""
        try:
            import trimesh
            import trimesh.creation
        except ImportError:
            print("Warning: trimesh not available for STL generation")
            return {}
        
        os.makedirs(output_dir, exist_ok=True)
        stl_files = {}
        
        # Get parameters for geometry generation
        params = self.parameters
        
        for comp_name in components:
            stl_path = os.path.join(output_dir, f"{comp_name}.stl")
            
            try:
                mesh = self._create_component_mesh(comp_name, params)
                if mesh is not None:
                    mesh.export(stl_path)
                    stl_files[comp_name] = stl_path
                    print(f"Generated fallback STL for {comp_name}")
            except Exception as e:
                print(f"Error generating {comp_name}: {e}")
        
        return stl_files
    
    def _create_component_mesh(self, comp_name: str, params: Dict):
        """Create mesh for a component using trimesh."""
        import trimesh
        import trimesh.creation
        
        # Default values
        outer_r = params.get('stator_outer_radius', 100)
        inner_r = params.get('stator_inner_radius', 70)
        width = params.get('stator_width', 50)
        num_slots = params.get('num_slots', 36)
        num_poles = params.get('num_poles', 12)
        shaft_r = params.get('shaft_radius', 15)
        
        if comp_name == 'stator_core':
            # Create stator with slots
            mesh = self._create_stator_mesh(outer_r, inner_r, width, num_slots)
        elif comp_name == 'rotor_core':
            # Create rotor disc
            rotor_r = params.get('rotor_outer_radius', outer_r - params.get('magnet_height', 10))
            mesh = trimesh.creation.annulus(
                r_min=shaft_r, 
                r_max=rotor_r, 
                height=width
            )
        elif comp_name == 'shaft':
            mesh = trimesh.creation.cylinder(
                radius=shaft_r, 
                height=width * 1.2
            )
        elif comp_name == 'magnets':
            mesh = self._create_magnets_mesh(
                params.get('rotor_outer_radius', 60), 
                shaft_r, 
                width, 
                num_poles
            )
        elif comp_name == 'coils':
            mesh = self._create_coils_mesh(
                outer_r, inner_r, width, num_slots
            )
        else:
            mesh = None
            
        return mesh
    
    def _create_stator_mesh(self, outer_r: float, inner_r: float, width: float, num_slots: int):
        """Create stator with slots using trimesh."""
        import trimesh
        import numpy as np
        
        # Create outer circle
        outer_circle = trimesh.creation.circle(inner_r, num_points=64)
        outer_circle.apply_translation([outer_r, 0])
        
        # This is simplified - real stator would need proper boolean operations
        # For now, create a simple annulus
        mesh = trimesh.creation.annulus(
            r_min=inner_r + 5,  # Approximate slot depth
            r_max=outer_r, 
            height=width
        )
        
        return mesh
    
    def _create_magnets_mesh(self, rotor_r: float, shaft_r: float, width: float, num_poles: int):
        """Create magnet segments."""
        import trimesh
        import numpy as np
        
        # Create magnet segments
        meshes = []
        pole_angle = 2 * np.pi / num_poles
        magnet_angle = pole_angle * 0.8  # 80% fill
        
        for i in range(num_poles):
            angle = i * pole_angle
            segment = trimesh.creation.circle(
                (rotor_r - shaft_r) / 2,
                num_points=16
            )
            # Position the segment
            segment.apply_translation([shaft_r + (rotor_r - shaft_r)/2, 0])
            segment.rotate_z(angle)
            
            # Extrude to 3D
            prism = trimesh.creation.extrude_polygon(segment, height=width)
            meshes.append(prism)
        
        # Combine all magnets
        if meshes:
            return trimesh.util.concatenate(meshes)
        return None
    
    def _create_coils_mesh(self, outer_r: float, inner_r: float, width: float, num_slots: int):
        """Create coil geometry."""
        import trimesh
        import numpy as np
        
        # Simplified coil representation
        slot_depth = 10
        slot_width = (2 * np.pi * inner_r) / num_slots * 0.8
        
        meshes = []
        for i in range(num_slots):
            angle = i * (2 * np.pi / num_slots)
            
            # Create rectangular coil in slot
            box = trimesh.creation.box(extents=[slot_width, slot_depth, width])
            
            # Position in slot
            r = inner_r + slot_depth/2
            box.apply_translation([r * np.cos(angle), r * np.sin(angle), 0])
            box.rotate_z(angle + np.pi/2)
            
            meshes.append(box)
        
        if meshes:
            return trimesh.util.concatenate(meshes)
        return None


def load_params_from_file(filepath: str) -> Dict:
    """Load parameters from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description='Fusion 360 Parametric Controller for Motor Geometry'
    )
    
    # Parameter inputs
    parser.add_argument('--params', type=str, help='JSON file with parameters')
    parser.add_argument('--output_dir', type=str, default='./stl_output', 
                       help='Output directory for STL files')
    parser.add_argument('--components', type=str, nargs='+',
                       default=['stator_core', 'rotor_core', 'magnets', 'coils', 'shaft'],
                       help='Components to export')
    
    # Direct parameter overrides
    parser.add_argument('--stator_outer_radius', type=float)
    parser.add_argument('--stator_inner_radius', type=float)
    parser.add_argument('--stator_width', type=float)
    parser.add_argument('--slot_height', type=float)
    parser.add_argument('--slot_width', type=float)
    parser.add_argument('--num_slots', type=int)
    parser.add_argument('--num_poles', type=int)
    parser.add_argument('--rotor_outer_radius', type=float)
    parser.add_argument('--shaft_radius', type=float)
    parser.add_argument('--magnet_height', type=float)
    parser.add_argument('--air_gap', type=float)
    
    args = parser.parse_args()
    
    # Load parameters
    params = {}
    
    # From file
    if args.params:
        params = load_params_from_file(args.params)
    
    # Override with command line arguments
    for key, value in vars(args).items():
        if value is not None and key not in ['params', 'output_dir', 'components']:
            params[key] = value
    
    print(f"Using parameters: {params}")
    
    # Create controller
    controller = Fusion360Controller()
    
    # Load parameters
    controller.load_parameters(params)
    
    # Export STL files
    stl_files = controller.export_stl(args.output_dir, args.components)
    
    print(f"\nGenerated STL files:")
    for name, path in stl_files.items():
        print(f"  {name}: {path}")
    
    return stl_files


if __name__ == '__main__':
    main()
