"""FastAPI server for motor geometry API.

This module provides REST API endpoints to access motor geometry
from motor_geometry.py, enabling the web frontend to fetch geometry
from a single source of truth.

Usage:
    # Start the server
    uvicorn motor_ai_sim.api:app --reload --port 8000
    
    # Or run directly
    python -m motor_ai_sim.api

Endpoints:
    GET /api/geometry - Get current geometry parameters
    PUT /api/geometry - Update geometry parameters
    GET /api/geometry/summary - Get geometry summary
    GET /api/materials - Get material assignments
    GET /api/config - Get full configuration
"""

from pathlib import Path
from typing import Dict, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from motor_ai_sim.config import (
    get_config,
    get_geometry_params,
    get_material_assignments,
    clear_config_cache,
)
from motor_ai_sim.geometry.motor_geometry import MotorGeometryParams, MotorGeometry2D, HAS_MODULUS

# Import pipeline components
try:
    from motor_ai_sim.cadquery_geometry import CadQueryMotor, CadQueryCache
    from motor_ai_sim.modulus_bridge import ModulusBridge, GeometryCache
    HAS_PIPELINE = True
except ImportError:
    HAS_PIPELINE = False
    print("Warning: Pipeline components not available")


# Pydantic models for API
class GeometryParamsModel(BaseModel):
    """Geometry parameters for API serialization."""
    # Stator parameters
    stator_diameter: float
    slot_height: float
    core_thickness: float
    num_seg: int
    num_slots_per_segment: int
    num_poles_per_segment: int
    stator_width: float
    air_gap: float
    
    # Slot details
    tooth_width: float
    insulation_thickness: float
    wire_width: float
    wire_height: float
    wire_spacing_x: float
    wire_spacing_y: float
    num_wires_per_slot: int
    slot_hs: float
    
    # Rotor parameters
    magnet_height: float
    rotor_house_height: float
    
    # Derived parameters
    stator_outer_radius: float
    stator_inner_radius: float
    rotor_outer_radius: float
    rotor_inner_radius: float
    num_slots: int
    num_poles: int
    angle_slot: float
    angle_pole: float
    slot_width: float
    
    class Config:
        from_attributes = True


class GeometryUpdateModel(BaseModel):
    """Model for geometry parameter updates."""
    stator_diameter: Optional[float] = None
    slot_height: Optional[float] = None
    core_thickness: Optional[float] = None
    num_seg: Optional[int] = None
    num_slots_per_segment: Optional[int] = None
    num_poles_per_segment: Optional[int] = None
    stator_width: Optional[float] = None
    air_gap: Optional[float] = None
    tooth_width: Optional[float] = None
    insulation_thickness: Optional[float] = None
    wire_width: Optional[float] = None
    wire_height: Optional[float] = None
    wire_spacing_x: Optional[float] = None
    wire_spacing_y: Optional[float] = None
    num_wires_per_slot: Optional[int] = None
    slot_hs: Optional[float] = None
    magnet_height: Optional[float] = None
    rotor_house_height: Optional[float] = None


# Create FastAPI app
app = FastAPI(
    title="Motor Geometry API",
    description="REST API for electric motor geometry parameters",
    version="0.1.0",
)

# Add CORS middleware for web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:5174",  # Vite dev server (new port)
        "http://localhost:3000",  # Alternative dev port
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def params_to_dict(params: MotorGeometryParams) -> Dict:
    """Convert MotorGeometryParams to dictionary for JSON serialization.
    
    Uses the dynamic to_dict() method - automatically includes ALL parameters
    from motor_config.yaml, no need to manually list them.
    """
    return params.to_dict()


# In-memory storage for runtime geometry changes
_current_geometry: Optional[MotorGeometryParams] = None


def get_current_geometry(reload: bool = False) -> MotorGeometryParams:
    """Get current geometry parameters (from memory or config file).
    
    Args:
        reload: If True, always reload from YAML file.
    """
    global _current_geometry
    if _current_geometry is None or reload:
        _current_geometry = get_geometry_params(reload=reload)
    return _current_geometry


def update_current_geometry(**kwargs) -> MotorGeometryParams:
    """Update current geometry parameters.
    
    Uses dynamic approach - updates any parameter from motor_config.yaml.
    """
    global _current_geometry
    current = get_current_geometry()
    
    # Get current params as dict and apply updates dynamically
    update_dict = current.to_dict()
    for key, value in kwargs.items():
        if value is not None:
            update_dict[key] = value
    
    # Reconstruct with derived params
    geometry_config = {k: v for k, v in update_dict.items() 
                       if not k.startswith('_') and k not in [
                           'stator_outer_radius', 'stator_inner_radius', 'rotor_outer_radius',
                           'rotor_inner_radius', 'num_slots', 'num_poles', 'angle_slot', 'angle_pole',
                           'slot_pitch', 'pole_pitch', 'slot_width', 'stator_slot_radius',
                           'rotor_core_radius', 'shaft_radius'
                       ]}
    
    _current_geometry = MotorGeometryParams(geometry_config, {})
    return _current_geometry


@app.get("/")
def root():
    """Root endpoint with API information."""
    return {
        "name": "Motor Geometry API",
        "version": "0.1.0",
        "endpoints": [
            "/api/geometry",
            "/api/geometry/summary",
            "/api/materials",
            "/api/config",
        ]
    }


@app.get("/api/geometry")
def get_geometry():
    """Get current geometry parameters.
    
    Returns all geometry parameters including derived values.
    Always reloads from YAML to ensure fresh values.
    """
    try:
        # Always reload from YAML to get fresh values
        params = get_current_geometry(reload=True)
        return params_to_dict(params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/geometry")
def update_geometry(update: GeometryUpdateModel):
    """Update geometry parameters.
    
    Only provided parameters will be updated; others remain unchanged.
    """
    try:
        params = update_current_geometry(**update.model_dump())
        return params_to_dict(params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/geometry/reset")
def reset_geometry():
    """Reset geometry to default values from config file."""
    global _current_geometry
    try:
        clear_config_cache()
        _current_geometry = get_geometry_params(reload=True)
        return params_to_dict(_current_geometry)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/geometry/summary")
def get_geometry_summary():
    """Get geometry summary with key dimensions."""
    params = get_current_geometry()
    return {
        "stator_outer_radius": params.stator_outer_radius,
        "stator_inner_radius": params.stator_inner_radius,
        "rotor_outer_radius": params.rotor_outer_radius,
        "rotor_inner_radius": params.rotor_inner_radius,
        "air_gap": params.air_gap,
        "num_slots": params.num_slots,
        "num_poles": params.num_poles,
        "shaft_radius": params.shaft_radius,
    }


@app.get("/api/materials")
def get_materials():
    """Get material assignments for motor regions."""
    try:
        return get_material_assignments()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
def get_full_config():
    """Get full configuration including geometry, materials, and mesh settings."""
    try:
        config = get_config()
        return {
            "geometry": params_to_dict(get_current_geometry()),
            "materials": get_material_assignments(),
            "mesh": {
                "n_radial": config.get("mesh", {}).get("n_radial", 10),
                "n_angular": config.get("mesh", {}).get("n_angular", 64),
                "n_angular_slots": config.get("mesh", {}).get("n_angular_slots", 8),
            },
            "simulation": {
                "max_current": config.get("simulation", {}).get("max_current", 10.0),
                "frequency": config.get("simulation", {}).get("frequency", 50.0),
                "rpm": config.get("simulation", {}).get("rpm", 2000),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": "2024-01-01"}


@app.get("/api/geometry/schema")
def get_geometry_schema():
    """Get parameter schema for dynamic form generation.
    
    Returns metadata for all geometry parameters including:
    - label: Human-readable label
    - unit: Unit of measurement
    - type: Parameter type (float, int)
    - min/max: Valid range
    - step: Step size for UI controls
    - group: Group for UI organization
    - description: Help text
    
    This endpoint reads from motor_config.yaml, making it the
    single source of truth for parameter definitions.
    """
    try:
        # Always reload config to pick up YAML changes
        config = get_config(reload=True)
        
        # Get schema from config
        schema = config.get("geometry_schema", {})
        groups = config.get("parameter_groups", {})
        
        # Convert to list format for frontend
        parameters = []
        for name, meta in schema.items():
            param_info = {
                "name": name,
                "label": meta.get("label", name.replace("_", " ").title()),
                "unit": meta.get("unit", ""),
                "type": meta.get("type", "float"),
                "min": meta.get("min", 0),
                "max": meta.get("max", 1000),
                "step": meta.get("step", 0.1),
                "group": meta.get("group", "other"),
                "description": meta.get("description", ""),
            }
            parameters.append(param_info)
        
        # Convert groups to list format
        group_list = []
        for group_id, group_meta in groups.items():
            group_list.append({
                "id": group_id,
                "label": group_meta.get("label", group_id.title()),
                "order": group_meta.get("order", 99),
            })
        
        # Sort groups by order
        group_list.sort(key=lambda g: g["order"])
        
        return {
            "parameters": parameters,
            "groups": group_list,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/geometry/mesh")
def get_geometry_mesh():
    """Get mesh data for web visualization.
    
    Returns vertices and faces for each motor component.
    This allows the web to render the exact geometry from motor_geometry.py.
    """
    try:
        params = get_current_geometry()
        
        # Generate mesh data for each component
        meshes = {}
        
        # 1. Stator Core (annulus with slots)
        meshes['stator_core'] = _generate_stator_mesh(params)
        
        # 2. Rotor Core (annulus)
        meshes['rotor_core'] = _generate_rotor_mesh(params)
        
        # 3. Shaft (cylinder)
        meshes['shaft'] = _generate_shaft_mesh(params)
        
        # 4. Magnets
        meshes['magnets'] = _generate_magnets_mesh(params)
        
        # 5. Coils/Windings (copper in slots)
        meshes['coils'] = _generate_coils_mesh(params)
        
        # 6. Air gap (for reference)
        meshes['air_gap'] = _generate_airgap_mesh(params)
        
        return meshes
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/geometry/pointcloud")
def get_geometry_pointcloud(n_points: int = 20000):
    """Get point cloud data sampled from the motor geometry.
    
    This endpoint samples interior points from each geometry region,
    which is what the AI sees during PINN training. This allows
    visualizing exactly what the neural network is learning.
    
    If NVIDIA Modulus is available, it uses the exact CSG geometry.
    Otherwise, it generates synthetic points based on the geometry parameters.
    
    Args:
        n_points: Number of points to sample per region (default 20000)
    
    Returns:
        Dictionary with point cloud data for each region, including
        material type metadata for coloring.
    """
    try:
        params = get_current_geometry(reload=True)
        
        # Try to use Modulus if available
        if HAS_MODULUS:
            motor = MotorGeometry2D(params)
            geometries = motor.get_modulus_geometries()
            
            # Sample points from each region
            pointcloud_data = {}
            regions_to_sample = {
                'stator_core': 'steel',
                'rotor_core': 'steel',
                'coils': 'copper',
                'magnets': 'permanent_magnet',
                'shaft': 'steel',
                'air_gap': 'air',
            }
            
            for region_name, material_type in regions_to_sample.items():
                if region_name in geometries:
                    try:
                        samples = geometries[region_name].sample_interior(n_points)
                        if hasattr(samples, 'numpy'):
                            samples = samples.numpy()
                        if samples.shape[0] == 3:
                            points = samples.T.tolist()
                        else:
                            points = samples.tolist()
                        if len(points) > 0 and len(points[0]) == 2:
                            points_3d = [[x, y, 0.0] for x, y in points]
                        else:
                            points_3d = points
                        pointcloud_data[region_name] = {
                            'points': points_3d,
                            'material': material_type,
                            'count': len(points_3d)
                        }
                    except Exception as e:
                        pointcloud_data[region_name] = {
                            'points': [],
                            'material': material_type,
                            'count': 0,
                            'error': str(e)
                        }
        else:
            # Fallback: Generate synthetic point cloud from parameters
            pointcloud_data = _generate_synthetic_pointcloud(params, n_points)
        
        return {
            'n_points': n_points,
            'has_modulus': HAS_MODULUS,
            'regions': pointcloud_data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _generate_synthetic_pointcloud(params: MotorGeometryParams, n_points: int) -> Dict:
    """Generate synthetic point cloud from geometry parameters (fallback when Modulus unavailable).
    
    This creates approximate point distributions based on the motor geometry parameters.
    """
    import numpy as np
    
    pointcloud = {}
    
    # Material colors for reference
    materials = {
        'stator_core': 'steel',
        'rotor_core': 'steel',
        'coils': 'copper',
        'magnets': 'permanent_magnet',
        'shaft': 'steel',
        'air_gap': 'air',
    }
    
    # Stator core: annulus between inner and outer radius
    outer_r = params.stator_outer_radius
    inner_r = params.stator_inner_radius
    n_stator = int(n_points * 0.4)  # 40% of points in stator
    
    # Generate points in annulus
    r = np.sqrt(np.random.uniform(inner_r**2, outer_r**2, n_stator))
    theta = np.random.uniform(0, 2 * np.pi, n_stator)
    stator_points = [[r[i] * np.cos(theta[i]), r[i] * np.sin(theta[i]), 0.0] 
                    for i in range(n_stator)]
    pointcloud['stator_core'] = {
        'points': stator_points,
        'material': materials['stator_core'],
        'count': n_stator
    }
    
    # Rotor core: annulus between shaft and magnet radius
    shaft_r = getattr(params, 'shaft_radius', 20)  # Default if not available
    magnet_thickness = getattr(params, 'magnet_height', 5)  # Default thickness
    magnet_r = params.rotor_outer_radius - magnet_thickness
    n_rotor = int(n_points * 0.25)  # 25% of points in rotor
    
    r = np.sqrt(np.random.uniform(shaft_r**2, magnet_r**2, n_rotor))
    theta = np.random.uniform(0, 2 * np.pi, n_rotor)
    rotor_points = [[r[i] * np.cos(theta[i]), r[i] * np.sin(theta[i]), 0.0] 
                   for i in range(n_rotor)]
    pointcloud['rotor_core'] = {
        'points': rotor_points,
        'material': materials['rotor_core'],
        'count': n_rotor
    }
    
    # Shaft: small circle at center
    n_shaft = int(n_points * 0.05)  # 5% of points in shaft
    r = np.sqrt(np.random.uniform(0, shaft_r**2, n_shaft))
    theta = np.random.uniform(0, 2 * np.pi, n_shaft)
    shaft_points = [[r[i] * np.cos(theta[i]), r[i] * np.sin(theta[i]), 0.0] 
                   for i in range(n_shaft)]
    pointcloud['shaft'] = {
        'points': shaft_points,
        'material': materials['shaft'],
        'count': n_shaft
    }
    
    # Magnets: arc sectors on rotor surface
    n_magnets = int(n_points * 0.15)  # 15% of points in magnets
    num_poles = params.num_poles
    magnet_inner = magnet_r
    magnet_outer = params.rotor_outer_radius
    
    # Use magnet_height if available, otherwise estimate from rotor dimensions
    magnet_thickness = getattr(params, 'magnet_height', magnet_outer - magnet_inner)
    if magnet_thickness <= 0:
        magnet_thickness = magnet_outer - magnet_inner
    
    magnet_inner = magnet_outer - magnet_thickness
    
    r = np.sqrt(np.random.uniform(magnet_inner**2, magnet_outer**2, n_magnets))
    theta = np.random.uniform(0, 2 * np.pi, n_magnets)
    magnet_points = [[r[i] * np.cos(theta[i]), r[i] * np.sin(theta[i]), 0.0] 
                     for i in range(n_magnets)]
    pointcloud['magnets'] = {
        'points': magnet_points,
        'material': materials['magnets'],
        'count': n_magnets
    }
    
    # Coils: in slots between inner radius and slot bottom
    n_coils = int(n_points * 0.15)  # 15% of points in coils
    slot_inner = inner_r
    slot_outer = outer_r - params.core_thickness
    
    r = np.sqrt(np.random.uniform(slot_inner**2, slot_outer**2, n_coils))
    theta = np.random.uniform(0, 2 * np.pi, n_coils)
    coil_points = [[r[i] * np.cos(theta[i]), r[i] * np.sin(theta[i]), 0.0] 
                   for i in range(n_coils)]
    pointcloud['coils'] = {
        'points': coil_points,
        'material': materials['coils'],
        'count': n_coils
    }
    
    return pointcloud


def _generate_stator_mesh(params: MotorGeometryParams) -> Dict:
    """Generate stator mesh with slots.
    
    Creates a 2D cross-section with proper triangulation and extrudes to 3D.
    Returns vertices and faces for Three.js rendering.
    """
    import numpy as np
    
    vertices = []
    faces = []
    
    outer_r = params.stator_outer_radius
    inner_r = params.stator_inner_radius
    slot_height = params.slot_height
    slot_width = params.slot_width
    num_slots = params.num_slots
    stator_width = params.stator_width
    
    # Create vertices for outer circle (bottom and top)
    n_outer = 64
    outer_bottom_start = 0
    for i in range(n_outer):
        angle = 2 * np.pi * i / n_outer
        vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), -stator_width/2])
    
    outer_top_start = n_outer
    for i in range(n_outer):
        angle = 2 * np.pi * i / n_outer
        vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), stator_width/2])
    
    # Create outer circle faces (side walls)
    for i in range(n_outer):
        next_i = (i + 1) % n_outer
        faces.append([outer_bottom_start + i, outer_top_start + i, outer_top_start + next_i])
        faces.append([outer_bottom_start + i, outer_top_start + next_i, outer_bottom_start + next_i])
    
    # Create inner circle with slots (bottom and top)
    inner_r_max = inner_r + slot_height
    slot_angle = 2 * np.pi / num_slots
    
    # Simplified inner circle (without detailed slot geometry)
    n_inner = num_slots * 4
    inner_bottom_start = len(vertices)
    for i in range(n_inner):
        angle = 2 * np.pi * i / n_inner
        r = inner_r + slot_height * 0.3  # Approximate slot depth
        vertices.append([r * np.cos(angle), r * np.sin(angle), -stator_width/2])
    
    inner_top_start = len(vertices)
    for i in range(n_inner):
        angle = 2 * np.pi * i / n_inner
        r = inner_r + slot_height * 0.3
        vertices.append([r * np.cos(angle), r * np.sin(angle), stator_width/2])
    
    # Connect inner circle faces
    for i in range(n_inner):
        next_i = (i + 1) % n_inner
        faces.append([inner_bottom_start + next_i, inner_bottom_start + i, inner_top_start + i])
        faces.append([inner_bottom_start + next_i, inner_top_start + i, inner_top_start + next_i])
    
    # Add top cap
    center_top = len(vertices)
    vertices.append([0, 0, stator_width/2])
    for i in range(n_outer):
        next_i = (i + 1) % n_outer
        faces.append([center_top, outer_top_start + i, outer_top_start + next_i])
    
    # Add bottom cap
    center_bottom = len(vertices)
    vertices.append([0, 0, -stator_width/2])
    for i in range(n_outer):
        next_i = (i + 1) % n_outer
        faces.append([center_bottom, outer_bottom_start + next_i, outer_bottom_start + i])
    
    return {
        "vertices": vertices,
        "faces": faces,
    }


def _generate_rotor_mesh(params: MotorGeometryParams) -> Dict:
    """Generate rotor core mesh."""
    import numpy as np
    
    outer_r = params.rotor_core_radius
    inner_r = params.shaft_radius
    stator_width = params.stator_width
    
    # Simple annulus
    n_angular = 32
    
    vertices = []
    # Bottom layer
    for i in range(n_angular):
        angle = 2 * np.pi * i / n_angular
        vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), -stator_width/2])
    for i in range(n_angular):
        angle = 2 * np.pi * i / n_angular
        vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), -stator_width/2])
    
    # Top layer
    for i in range(n_angular):
        angle = 2 * np.pi * i / n_angular
        vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), stator_width/2])
    for i in range(n_angular):
        angle = 2 * np.pi * i / n_angular
        vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), stator_width/2])
    
    return {
        "vertices": vertices,
        "outer_radius": outer_r,
        "inner_radius": inner_r,
        "stator_width": stator_width,
    }


def _generate_rotor_mesh(params: MotorGeometryParams) -> Dict:
    """Generate rotor core mesh with proper triangulation."""
    import numpy as np
    
    outer_r = params.rotor_outer_radius
    inner_r = params.shaft_radius + 5  # Hole for shaft
    rotor_width = params.stator_width
    
    vertices = []
    faces = []
    
    # Outer circle (bottom and top)
    n_outer = 48
    outer_bottom_start = 0
    for i in range(n_outer):
        angle = 2 * np.pi * i / n_outer
        vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), -rotor_width/2])
    
    outer_top_start = n_outer
    for i in range(n_outer):
        angle = 2 * np.pi * i / n_outer
        vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), rotor_width/2])
    
    # Inner circle (bottom and top)  
    n_inner = 16
    inner_bottom_start = len(vertices)
    for i in range(n_inner):
        angle = 2 * np.pi * i / n_inner
        vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), -rotor_width/2])
    
    inner_top_start = len(vertices)
    for i in range(n_inner):
        angle = 2 * np.pi * i / n_inner
        vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), rotor_width/2])
    
    # Side walls
    for i in range(n_outer):
        next_i = (i + 1) % n_outer
        faces.append([outer_bottom_start + i, outer_top_start + i, outer_top_start + next_i])
        faces.append([outer_bottom_start + i, outer_top_start + next_i, outer_bottom_start + next_i])
    
    # Inner wall
    for i in range(n_inner):
        next_i = (i + 1) % n_inner
        faces.append([inner_bottom_start + i, inner_bottom_start + next_i, inner_top_start + next_i])
        faces.append([inner_bottom_start + i, inner_top_start + next_i, inner_top_start + i])
    
    # Top cap
    center_top = len(vertices)
    vertices.append([0, 0, rotor_width/2])
    for i in range(n_outer):
        next_i = (i + 1) % n_outer
        faces.append([center_top, outer_top_start + i, outer_top_start + next_i])
    
    # Bottom cap  
    center_bottom = len(vertices)
    vertices.append([0, 0, -rotor_width/2])
    for i in range(n_outer):
        next_i = (i + 1) % n_outer
        faces.append([center_bottom, outer_bottom_start + next_i, outer_bottom_start + i])
    
    return {
        "vertices": vertices,
        "faces": faces,
    }


def _generate_shaft_mesh(params: MotorGeometryParams) -> Dict:
    """Generate shaft mesh with proper triangulation."""
    import numpy as np
    
    radius = params.shaft_radius
    stator_width = params.stator_width
    n_angular = 16
    
    vertices = []
    faces = []
    
    # Bottom layer
    bottom_start = 0
    for i in range(n_angular):
        angle = 2 * np.pi * i / n_angular
        vertices.append([radius * np.cos(angle), radius * np.sin(angle), -stator_width/2])
    
    # Top layer
    top_start = n_angular
    for i in range(n_angular):
        angle = 2 * np.pi * i / n_angular
        vertices.append([radius * np.cos(angle), radius * np.sin(angle), stator_width/2])
    
    # Side faces
    for i in range(n_angular):
        next_i = (i + 1) % n_angular
        faces.append([bottom_start + i, top_start + i, top_start + next_i])
        faces.append([bottom_start + i, top_start + next_i, bottom_start + next_i])
    
    # Top cap
    center_top = len(vertices)
    vertices.append([0, 0, stator_width/2])
    for i in range(n_angular):
        next_i = (i + 1) % n_angular
        faces.append([center_top, top_start + i, top_start + next_i])
    
    # Bottom cap
    center_bottom = len(vertices)
    vertices.append([0, 0, -stator_width/2])
    for i in range(n_angular):
        next_i = (i + 1) % n_angular
        faces.append([center_bottom, bottom_start + next_i, bottom_start + i])
    
    return {
        "vertices": vertices,
        "faces": faces,
    }


def _generate_magnets_mesh(params: MotorGeometryParams) -> Dict:
    """Generate magnets mesh with proper triangulation."""
    import numpy as np
    
    inner_r = params.rotor_core_radius
    outer_r = params.rotor_outer_radius
    num_poles = params.num_poles
    stator_width = params.stator_width
    
    all_vertices = []
    all_faces = []
    
    pole_angle = 2 * np.pi / num_poles
    
    for i in range(num_poles):
        center_angle = i * pole_angle
        half_angle = pole_angle * 0.4  # 80% fill
        
        # Create a single magnet as a properly triangulated mesh
        vertices = []
        faces = []
        
        n_arc = 8
        
        # Outer arc bottom
        for j in range(n_arc):
            angle = center_angle - half_angle + 2 * half_angle * j / (n_arc - 1)
            vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), -stator_width/2])
        
        # Outer arc top
        outer_top_start = len(vertices)
        for j in range(n_arc):
            angle = center_angle - half_angle + 2 * half_angle * j / (n_arc - 1)
            vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), stator_width/2])
        
        # Inner arc bottom
        inner_bottom_start = len(vertices)
        for j in range(n_arc):
            angle = center_angle + half_angle - 2 * half_angle * j / (n_arc - 1)
            vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), -stator_width/2])
        
        # Inner arc top
        inner_top_start = len(vertices)
        for j in range(n_arc):
            angle = center_angle + half_angle - 2 * half_angle * j / (n_arc - 1)
            vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), stator_width/2])
        
        # Side faces - outer
        for j in range(n_arc - 1):
            faces.append([j, outer_top_start + j, outer_top_start + j + 1])
            faces.append([j, outer_top_start + j + 1, j + 1])
        
        # Side faces - inner
        for j in range(n_arc - 1):
            idx = inner_bottom_start + j
            faces.append([idx, idx + 1, inner_top_start + j + 1])
            faces.append([idx, inner_top_start + j + 1, inner_top_start + j])
        
        # End caps
        # Bottom
        center_bottom = len(vertices)
        vertices.append([(inner_r + outer_r)/2 * np.cos(center_angle - half_angle), 
                        (inner_r + outer_r)/2 * np.sin(center_angle - half_angle), 
                        -stator_width/2])
        for j in range(n_arc - 1):
            faces.append([center_bottom, j, j + 1])
        
        center_top = len(vertices)
        vertices.append([(inner_r + outer_r)/2 * np.cos(center_angle - half_angle), 
                        (inner_r + outer_r)/2 * np.sin(center_angle - half_angle), 
                        stator_width/2])
        for j in range(n_arc - 1):
            faces.append([center_top, outer_top_start + j + 1, outer_top_start + j])
        
        # Offset all vertices/faces by current count
        offset = len(all_vertices)
        for v in vertices:
            all_vertices.append(v)
        for f in faces:
            all_faces.append([f[0] + offset, f[1] + offset, f[2] + offset])
    
    return {
        "vertices": all_vertices,
        "faces": all_faces,
    }


def _generate_coils_mesh(params: MotorGeometryParams) -> Dict:
    """Generate coils/windings mesh with proper triangulation."""
    import numpy as np
    
    insulation = params.insulation_thickness
    coil_width = params.slot_width - 2 * insulation
    coil_height = params.slot_height - insulation
    
    outer_r = params.stator_outer_radius - params.core_thickness - insulation
    inner_r = outer_r - coil_height
    
    num_slots = params.num_slots
    stator_width = params.stator_width
    
    all_vertices = []
    all_faces = []
    
    slot_angle = 2 * np.pi / num_slots
    n_arc = 4
    
    for i in range(num_slots):
        center_angle = i * slot_angle
        half_angular_width = (coil_width / 2) / params.stator_outer_radius
        
        vertices = []
        faces = []
        
        # Outer arc bottom
        for j in range(n_arc):
            angle = center_angle - half_angular_width + 2 * half_angular_width * j / (n_arc - 1)
            vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), -stator_width/2])
        
        # Outer arc top
        outer_top_start = len(vertices)
        for j in range(n_arc):
            angle = center_angle - half_angular_width + 2 * half_angular_width * j / (n_arc - 1)
            vertices.append([outer_r * np.cos(angle), outer_r * np.sin(angle), stator_width/2])
        
        # Inner arc bottom
        inner_bottom_start = len(vertices)
        for j in range(n_arc):
            angle = center_angle + half_angular_width - 2 * half_angular_width * j / (n_arc - 1)
            vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), -stator_width/2])
        
        # Inner arc top
        inner_top_start = len(vertices)
        for j in range(n_arc):
            angle = center_angle + half_angular_width - 2 * half_angular_width * j / (n_arc - 1)
            vertices.append([inner_r * np.cos(angle), inner_r * np.sin(angle), stator_width/2])
        
        # Side faces - outer
        for j in range(n_arc - 1):
            faces.append([j, outer_top_start + j, outer_top_start + j + 1])
            faces.append([j, outer_top_start + j + 1, j + 1])
        
        # Side faces - inner
        for j in range(n_arc - 1):
            idx = inner_bottom_start + j
            faces.append([idx, idx + 1, inner_top_start + j + 1])
            faces.append([idx, inner_top_start + j + 1, inner_top_start + j])
        
        # End caps - bottom
        center_bottom = len(vertices)
        vertices.append([(inner_r + outer_r)/2 * np.cos(center_angle - half_angular_width), 
                        (inner_r + outer_r)/2 * np.sin(center_angle - half_angular_width), 
                        -stator_width/2])
        for j in range(n_arc - 1):
            faces.append([center_bottom, j, j + 1])
        
        # End caps - top
        center_top = len(vertices)
        vertices.append([(inner_r + outer_r)/2 * np.cos(center_angle - half_angular_width), 
                        (inner_r + outer_r)/2 * np.sin(center_angle - half_angular_width), 
                        stator_width/2])
        for j in range(n_arc - 1):
            faces.append([center_top, outer_top_start + j + 1, outer_top_start + j])
        
        # Offset and add to all
        offset = len(all_vertices)
        for v in vertices:
            all_vertices.append(v)
        for f in faces:
            all_faces.append([f[0] + offset, f[1] + offset, f[2] + offset])
    
    return {
        "vertices": all_vertices,
        "faces": all_faces,
    }


def _generate_airgap_mesh(params: MotorGeometryParams) -> Dict:
    """Generate air gap mesh."""
    return {
        "outer_radius": params.stator_inner_radius,
        "inner_radius": params.rotor_outer_radius,
        "gap": params.air_gap,
    }


def main():
    """Run the API server."""
    import uvicorn
    uvicorn.run(
        "motor_ai_sim.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()


# ===========================================
# PIPELINE ORCHESTRATOR ENDPOINTS (Task 4)
# ===========================================

# Global pipeline instances
_pipeline_cache: Dict = {}


@app.get("/api/pipeline/status")
def get_pipeline_status():
    """Get status of the pipeline components."""
    return {
        "fusion360_available": HAS_PIPELINE,
        "modulus_bridge_available": HAS_PIPELINE,
        "cache_enabled": True,
    }


@app.post("/api/pipeline/clear-cache")
def clear_pipeline_cache():
    """Clear the geometry cache and force regeneration."""
    try:
        cache = CadQueryCache()
        cache.clear_all()
        return {
            "status": "success",
            "message": "Cache cleared successfully"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }, 500


@app.post("/api/pipeline/generate")
def generate_geometry_pipeline(params: Dict):
    """Run the full pipeline: UI params → CadQuery → STL → Modulus SDF.
    
    This is the main orchestrator endpoint that:
    1. Takes geometry parameters from UI
    2. Generates STL files using CadQuery
    3. Creates SDF for AI training (via Modulus Bridge)
    4. Returns validation data
    
    Request body:
        Dictionary of geometry parameters from UI sliders
    
    Returns:
        Pipeline result with STL paths, SDF validation, and metadata
    """
    if not HAS_PIPELINE:
        raise HTTPException(
            status_code=503,
            detail="Pipeline components not available"
        )
    
    try:
        # 1. Create CadQueryMotor and set parameters from request
        motor = CadQueryMotor()
        
        # If params are provided, set them first
        if params and len(params) > 0:
            motor.set_parameters(params)
            print(f"Using params from request: {list(params.keys())[:5]}...")
        
        # 2. Compute parameter hash for caching (AFTER setting params)
        param_hash = motor.get_parameter_hash()
        
        # 3. Check cache
        cache = CadQueryCache()
        if cache.exists(param_hash):
            cached_stl = cache.load(param_hash)
            print(f"Using cached geometry: {param_hash}")
            stl_dir = cache.get_cache_path(param_hash)
        else:
            # 4. Generate geometry via CadQuery (params already set above)
            # Export STL
            import tempfile
            temp_dir = tempfile.mkdtemp()
            stl_files = motor.export_stl(temp_dir)
            
            # Save to cache
            stl_dir = cache.save(param_hash, stl_files)
            print(f"Generated new geometry: {param_hash}")
        
        # 4. Create Modulus SDF
        bridge = ModulusBridge()
        bridge.load_stl_files(str(stl_dir))
        
        # 5. Validate geometry
        validation = bridge.validate_geometry(n_points=50000)
        
        return {
            "status": "success",
            "param_hash": param_hash,
            "stl_directory": str(stl_dir),
            "validation": validation,
            "components": list(bridge.mesh_data.keys()),
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pipeline/stl/{component}")
def get_stl_mesh(component: str):
    """Get mesh data for a specific component from cached STL.
    
    Args:
        component: Component name (stator_core, rotor_core, etc.)
    
    Returns:
        Mesh vertices and faces for rendering
    """
    if not HAS_PIPELINE:
        raise HTTPException(
            status_code=503,
            detail="Pipeline not available"
        )
    
    try:
        # Use CadQueryCache for STL files
        from motor_ai_sim.cadquery_geometry import CadQueryCache
        cache = CadQueryCache()
        cache_dir = cache.cache_dir
        
        if not cache_dir.exists():
            raise HTTPException(status_code=404, detail="No cached geometry found. Run /api/pipeline/generate first.")
        
        # Find most recent cache
        caches = sorted(
            [d for d in cache_dir.iterdir() if d.is_dir()],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        if not caches:
            raise HTTPException(status_code=404, detail="No cached geometry found. Run /api/pipeline/generate first.")
        
        latest_cache = caches[0]
        stl_path = latest_cache / f"{component}.stl"
        
        if not stl_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Component {component} not found in cache. Available in cache."
            )
        
        # Load STL using trimesh
        import trimesh
        mesh = trimesh.load_mesh(str(stl_path))
        
        return {
            "component": component,
            "vertices": mesh.vertices.tolist(),
            "faces": mesh.faces.tolist(),
            "vertex_count": len(mesh.vertices),
            "face_count": len(mesh.faces),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pipeline/validate")
def validate_ai_geometry(n_points: int = 50000):
    """Validate that AI geometry matches CAD.
    
    Samples points from the SDF and returns validation data
    that can be used to check alignment with the mesh.
    
    Args:
        n_points: Number of points to sample (default 50000)
    
    Returns:
        Validation data including bounding box, volume, and component stats
    """
    if not HAS_PIPELINE:
        raise HTTPException(
            status_code=503,
            detail="Pipeline not available"
        )
    
    try:
        # Find latest cached geometry - use CadQueryCache same as generate
        from motor_ai_sim.cadquery_geometry import CadQueryCache
        cache = CadQueryCache()
        cache_dir = cache.cache_dir
        
        if not cache_dir.exists():
            raise HTTPException(status_code=404, detail="No cached geometry found")
        
        caches = sorted(
            [d for d in cache_dir.iterdir() if d.is_dir()],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        if not caches:
            raise HTTPException(status_code=404, detail="No cached geometry found")
        
        latest_cache = caches[0]
        
        # Validate
        bridge = ModulusBridge()
        bridge.load_stl_files(str(latest_cache))
        result = bridge.validate_geometry(n_points=n_points)
        
        return {
            "param_hash": latest_cache.name,
            "validation": result,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
