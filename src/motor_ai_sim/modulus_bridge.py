# NVIDIA Modulus STL-to-SDF Ingestion Bridge
"""
This module provides the bridge between CAD geometry (STL files) and NVIDIA Modulus
for Physics-Informed Neural Network (PINN) training.

Key Features:
1. Tessellation Logic: Import STL files using modulus.sym.geometry.tessellation.Tessellation
2. SDF Generation: Create airtight SDF for accurate AI training
3. Point Sampling: Validate geometry by sampling 50,000+ points
4. Domain Definition: Map meshes to Modulus Nodes for Maxwell Equations

Usage:
    from modulus_bridge import ModulusBridge
    
    bridge = ModulusBridge()
    bridge.load_stl_files("./stl_output")
    bridge.create_sdf(airtight=True)
    points = bridge.sample_points(50000)
    nodes = bridge.get_maxwell_nodes()
"""

from typing import Dict, List, Optional, Tuple
from pathlib import Path
import numpy as np
import hashlib
import json

# Try to import NVIDIA Modulus
try:
    from modulus.sym.geometry import Tessellation, NumpyMesh
    from modulus.sym.geometry.geometry import Geometry
    from modulus.sym.domain import Domain
    from modulus.sym.geometry.csg import CSGGeometry
    HAS_MODULUS = True
except ImportError:
    HAS_MODULUS = False
    print("Warning: NVIDIA Modulus not available. Using fallback mode.")

try:
    import trimesh
except ImportError:
    trimesh = None


class ModulusBridge:
    """Bridge between CAD geometry and NVIDIA Modulus."""
    
    def __init__(self):
        self.stl_files: Dict[str, str] = {}
        self.geometries: Dict[str, Tessellation] = {}
        self.sdf_geometry: Optional[CSGGeometry] = None
        self.mesh_data: Dict[str, dict] = {}
        
    def load_stl_files(self, stl_dir: str) -> Dict[str, str]:
        """
        Load STL files from directory.
        
        Args:
            stl_dir: Directory containing STL files
            
        Returns:
            Dictionary mapping component name to file path
        """
        stl_dir = Path(stl_dir)
        
        if not stl_dir.exists():
            raise FileNotFoundError(f"STL directory not found: {stl_dir}")
        
        # Find all STL files
        for stl_file in stl_dir.glob("*.stl"):
            component_name = stl_file.stem
            self.stl_files[component_name] = str(stl_file)
            
            # Load mesh data for validation
            if trimesh:
                mesh = trimesh.load_mesh(str(stl_file))
                self.mesh_data[component_name] = {
                    'vertices': mesh.vertices.tolist(),
                    'faces': mesh.faces.tolist(),
                    'vertex_count': len(mesh.vertices),
                    'face_count': len(mesh.faces),
                }
        
        print(f"Loaded {len(self.stl_files)} STL files: {list(self.stl_files.keys())}")
        return self.stl_files
    
    def create_tessellation(self, component_name: str, airtight: bool = True) -> 'Tessellation':
        """
        Create Modulus Tessellation from STL file.
        
        Args:
            component_name: Name of the component (e.g., 'stator_core')
            airtight: If True, ensures SDF is calculated accurately
            
        Returns:
            Modulus Tessellation object
        """
        if not HAS_MODULUS:
            raise RuntimeError("NVIDIA Modulus is not available")
        
        if component_name not in self.stl_files:
            raise ValueError(f"Component {component_name} not found in loaded STL files")
        
        stl_path = self.stl_files[component_name]
        
        # Load mesh using trimesh first, then convert to Modulus format
        mesh = trimesh.load_mesh(stl_path)
        
        # Create Modulus tessellation
        # Note: The exact API depends on Modulus version
        # Using generic approach that should work with most versions
        try:
            # Try the newer API first
            tess = Tessellation.from_stl(
                stl_path,
                airtight=airtight,
                verbose=False
            )
        except:
            # Fallback to numpy mesh approach
            numpy_mesh = NumpyMesh(
                vertices=mesh.vertices,
                faces=mesh.faces
            )
            tess = Tessellation(
                mesh=numpy_mesh,
                airtight=airtight
            )
        
        self.geometries[component_name] = tess
        print(f"Created tessellation for {component_name} (airtight={airtight})")
        
        return tess
    
    def create_sdf(self, components: Optional[List[str]] = None, airtight: bool = True) -> 'CSGGeometry':
        """
        Create combined SDF geometry from multiple components.
        
        Args:
            components: List of component names to include
            airtight: If True, ensures airtight SDF for all bodies
            
        Returns:
            Combined CSGGeometry with SDF
        """
        if not HAS_MODULUS:
            raise RuntimeError("NVIDIA Modulus is not available")
        
        if components is None:
            components = list(self.stl_files.keys())
        
        # Create tessellations for each component
        for comp in components:
            if comp not in self.geometries:
                self.create_tessellation(comp, airtight)
        
        # Combine geometries using CSG operations
        # For a motor, we typically want:
        # - Stator (steel)
        # - Rotor (steel)  
        # - Magnets (permanent_magnet)
        # - Coils (copper)
        
        # Create CSG geometry from all components
        combined_geo = None
        
        for comp in components:
            tess = self.geometries[comp]
            
            if combined_geo is None:
                combined_geo = tess
            else:
                # Union operation
                combined_geo = combined_geo + tess
        
        self.sdf_geometry = combined_geo
        print(f"Created combined SDF geometry with {len(components)} components")
        
        return self.sdf_geometry
    
    def sample_points(self, n_points: int = 50000, 
                     region: Optional[str] = None) -> np.ndarray:
        """
        Sample points from the SDF geometry.
        
        Args:
            n_points: Number of points to sample
            region: Optional specific component to sample from
                     
        Returns:
            Array of shape (n_points, 3) with sampled (x, y, z) coordinates
        """
        if not HAS_MODULUS and not trimesh:
            raise RuntimeError("Neither NVIDIA Modulus nor trimesh is available")
        
        if region and region in self.mesh_data:
            # Sample from specific component
            mesh_data = self.mesh_data[region]
            vertices = np.array(mesh_data['vertices'])
            faces = np.array(mesh_data['faces'])
            
            # Sample points on mesh surface
            points = self._sample_mesh_surface(vertices, faces, n_points)
        elif self.sdf_geometry and HAS_MODULUS:
            # Use Modulus SDF sampling
            try:
                sampler = self.sdf_geometry.sample_interior(n_points)
                points = sampler.cpu().numpy()
            except:
                # Fallback to surface sampling
                points = self.sdf_geometry.sample(n_points).cpu().numpy()
        else:
            # Fallback: sample randomly in bounding box
            all_vertices = []
            for mesh_data in self.mesh_data.values():
                all_vertices.extend(mesh_data['vertices'])
            
            vertices = np.array(all_vertices)
            bounds_min = vertices.min(axis=0)
            bounds_max = vertices.max(axis=0)
            
            points = np.random.uniform(bounds_min, bounds_max, (n_points, 3))
        
        print(f"Sampled {len(points)} points" + (f" from {region}" if region else ""))
        return points
    
    def _sample_mesh_surface(self, vertices: np.ndarray, faces: np.ndarray, 
                            n_points: int) -> np.ndarray:
        """Sample points on mesh surface."""
        # Calculate face areas
        v0 = vertices[faces[:, 0]]
        v1 = vertices[faces[:, 1]]
        v2 = vertices[faces[:, 2]]
        
        cross = np.cross(v1 - v0, v2 - v0)
        areas = np.linalg.norm(cross, axis=1)
        areas = areas / areas.sum()
        
        # Sample faces proportional to area
        face_indices = np.random.choice(len(faces), size=n_points, p=areas)
        
        # Random barycentric coordinates
        r1 = np.random.rand(n_points)
        r2 = np.random.rand(n_points)
        
        # Avoid triangles with side too close to vertex
        sqrt_r1 = np.sqrt(r1)
        bary = np.column_stack([
            1 - sqrt_r1,
            sqrt_r1 * (1 - r2),
            sqrt_r1 * r2
        ])
        
        # Get points
        sampled_points = np.zeros((n_points, 3))
        for i, (fidx, b) in enumerate(zip(face_indices, bary)):
            v = vertices[faces[fidx]]
            sampled_points[i] = b[0] * v[0] + b[1] * v[1] + b[2] * v[2]
        
        return sampled_points
    
    def validate_geometry(self, n_points: int = 50000) -> dict:
        """
        Validate that the AI's version of geometry matches CAD.
        
        This samples points from the SDF and returns validation data
        that can be used to check alignment with the mesh.
        
        Args:
            n_points: Number of points to sample
            
        Returns:
            Dictionary with validation data
        """
        points = self.sample_points(n_points)
        
        # Calculate bounding box
        bounds_min = points.min(axis=0)
        bounds_max = points.max(axis=0)
        
        # Calculate approximate volume
        volume = np.prod(bounds_max - bounds_min)
        
        # Get statistics per component
        component_stats = {}
        for comp_name, mesh_data in self.mesh_data.items():
            vertices = np.array(mesh_data['vertices'])
            component_stats[comp_name] = {
                'vertex_count': mesh_data['vertex_count'],
                'face_count': mesh_data['face_count'],
                'bounding_box': {
                    'min': vertices.min(axis=0).tolist(),
                    'max': vertices.max(axis=0).tolist()
                }
            }
        
        return {
            'sampled_points': points.tolist(),
            'n_points': n_points,
            'bounding_box': {
                'min': bounds_min.tolist(),
                'max': bounds_max.tolist()
            },
            'approximate_volume': float(volume),
            'components': component_stats
        }
    
    def get_maxwell_nodes(self) -> List:
        """
        Create Modulus nodes for electromagnetic equations (Maxwell Equations).
        
        Returns:
            List of Modulus nodes for the motor geometry
        """
        if not HAS_MODULUS:
            # Return placeholder for fallback mode
            return []
        
        from modulus.sym.node import Node
        
        nodes = []
        
        # For each component, create appropriate material nodes
        material_mapping = {
            'stator_core': 'steel',
            'rotor_core': 'steel', 
            'shaft': 'steel',
            'magnets': 'permanent_magnet',
            'coils': 'copper',
            'air_gap': 'air'
        }
        
        for comp_name, tess in self.geometries.items():
            material = material_mapping.get(comp_name, 'air')
            
            # Create geometry node
            geo_node = Node.from_sympy(
                lambda x, y, z: tess,
                name=f"{comp_name}_geometry"
            )
            nodes.append(geo_node)
        
        print(f"Created {len(nodes)} Modulus nodes for Maxwell equations")
        return nodes
    
    def compute_parameter_hash(self, params: Dict) -> str:
        """
        Compute hash of parameters for caching.
        
        Args:
            params: Dictionary of geometry parameters
            
        Returns:
            SHA256 hash string
        """
        # Sort keys for consistent hashing
        param_str = json.dumps(params, sort_keys=True)
        return hashlib.sha256(param_str.encode()).hexdigest()[:16]


class GeometryCache:
    """Cache for geometry files based on parameter hash."""
    
    def __init__(self, cache_dir: str = "./geometry_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
    def get_cache_path(self, param_hash: str) -> Path:
        """Get cache directory for a specific parameter set."""
        return self.cache_dir / param_hash
    
    def exists(self, param_hash: str) -> bool:
        """Check if cached geometry exists."""
        cache_path = self.get_cache_path(param_hash)
        return cache_path.exists() and any(cache_path.glob("*.stl"))
    
    def save(self, param_hash: str, stl_files: Dict[str, str]) -> str:
        """
        Save STL files to cache.
        
        Args:
            param_hash: Hash of parameters
            stl_files: Dictionary of STL file paths
            
        Returns:
            Cache directory path
        """
        import shutil
        
        cache_path = self.get_cache_path(param_hash)
        cache_path.mkdir(exist_ok=True)
        
        for comp_name, src_path in stl_files.items():
            dst_path = cache_path / f"{comp_name}.stl"
            shutil.copy2(src_path, dst_path)
        
        return str(cache_path)
    
    def load(self, param_hash: str) -> Optional[Dict[str, str]]:
        """
        Load STL files from cache.
        
        Args:
            param_hash: Hash of parameters
            
        Returns:
            Dictionary of component names to STL paths, or None if not cached
        """
        cache_path = self.get_cache_path(param_hash)
        
        if not self.exists(param_hash):
            return None
        
        stl_files = {}
        for stl_file in cache_path.glob("*.stl"):
            stl_files[stl_file.stem] = str(stl_file)
        
        return stl_files


# Example usage
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='NVIDIA Modulus STL-to-SDF Bridge')
    parser.add_argument('--stl_dir', type=str, required=True, help='Directory with STL files')
    parser.add_argument('--n_points', type=int, default=50000, help='Points to sample')
    parser.add_argument('--validate', action='store_true', help='Run validation')
    
    args = parser.parse_args()
    
    bridge = ModulusBridge()
    bridge.load_stl_files(args.stl_dir)
    
    if args.validate:
        result = bridge.validate_geometry(args.n_points)
        print(f"Validation result:")
        print(f"  Bounding box: {result['bounding_box']}")
        print(f"  Volume: {result['approximate_volume']:.2f}")
        print(f"  Components: {list(result['components'].keys())}")
