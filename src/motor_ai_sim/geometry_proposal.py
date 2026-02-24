"""
Geometry Refinement Module for Motor AI Simulation

This module provides advanced geometry refinement features inspired by CAD software:
- Micro-fillets on stator and coil edges
- Smooth transitions between components
- Chamfer options for specific edges

These refinements improve the visual quality to match professional CAD standards.
"""

import cadquery as cq
from typing import List, Tuple, Optional


class GeometryRefinement:
    """
    Provides geometry refinement operations for motor components.
    These operations add subtle details that improve visual quality.
    """
    
    def __init__(self, fillet_radius: float = 0.2, chamfer_radius: float = 0.1):
        """
        Initialize geometry refinement with specified radii.
        
        Args:
            fillet_radius: Radius for fillet operations (default 0.2mm)
            chamfer_radius: Radius for chamfer operations (default 0.1mm)
        """
        self.fillet_radius = fillet_radius
        self.chamfer_radius = chamfer_radius
    
    def add_stator_fillets(
        self, 
        stator: cq.Workplane, 
        edges: Optional[List[cq.Edge]] = None
    ) -> cq.Workplane:
        """
        Add micro-fillets to stator outer edges for smoother appearance.
        
        Args:
            stator: The stator workplane to refine
            edges: Optional specific edges to fillet (if None, uses outer edges)
            
        Returns:
            Refined stator workplane
        """
        if edges is None:
            # Get the outer cylindrical edges of the stator
            # These are typically the top and bottom edges of the outer cylinder
            try:
                # Try to fillet all edges first, then fall back to specific ones
                stator = stator.fillet(self.fillet_radius)
            except Exception:
                # If general fillet fails, try specific edge selection
                try:
                    # Select top and bottom edges of outer cylinder
                    stator = stator.fillet(self.fillet_radius, 
                        stator.edges().filterByGeometry('CYLINDER').vals())
                except Exception:
                    # If that also fails, use a smaller radius or skip
                    try:
                        stator = stator.fillet(self.fillet_radius * 0.5)
                    except Exception:
                        pass  # Skip filleting if not possible
        
        return stator
    
    def add_coil_fillets(
        self, 
        coils: cq.Workplane, 
        edges: Optional[List[cq.Edge]] = None
    ) -> cq.Workplane:
        """
        Add micro-fillets to coil outer edges for smoother appearance.
        
        Args:
            coils: The coil workplane to refine
            edges: Optional specific edges to fillet
            
        Returns:
            Refined coil workplane
        """
        if edges is None:
            try:
                # Apply smaller fillets to coils (they're more delicate)
                coils = coils.fillet(self.fillet_radius * 0.5)
            except Exception:
                try:
                    # Try with even smaller radius
                    coils = coils.fillet(self.fillet_radius * 0.25)
                except Exception:
                    pass  # Skip if not possible
        
        return coils
    
    def add_chamfer(
        self, 
        workplane: cq.Workplane, 
        edges: Optional[List[cq.Edge]] = None,
        radius: Optional[float] = None
    ) -> cq.Workplane:
        """
        Add chamfer to specified edges.
        
        Args:
            workplane: The workplane to chamfer
            edges: Optional specific edges to chamfer
            radius: Optional override for chamfer radius
            
        Returns:
            Refined workplane
        """
        if radius is None:
            radius = self.chamfer_radius
            
        if edges is None:
            try:
                workplane = workplane.chamfer(radius)
            except Exception:
                pass
        else:
            try:
                workplane = workplane.chamfer(radius, edges)
            except Exception:
                pass
                
        return workplane
    
    def smooth_transition(
        self, 
        part1: cq.Workplane, 
        part2: cq.Workplane,
        transition_type: str = 'fillet'
    ) -> Tuple[cq.Workplane, cq.Workplane]:
        """
        Create smooth transitions between two parts.
        
        Args:
            part1: First part workplane
            part2: Second part workplane
            transition_type: Type of transition ('fillet' or 'chamfer')
            
        Returns:
            Tuple of (refined_part1, refined_part2)
        """
        radius = self.fillet_radius if transition_type == 'fillet' else self.chamfer_radius
        
        try:
            if transition_type == 'fillet':
                part1 = part1.fillet(radius)
            else:
                part1 = part1.chamfer(radius)
        except Exception:
            pass
            
        return part1, part2


def refine_motor_geometry(
    stator: Optional[cq.Workplane] = None,
    coils: Optional[cq.Workplane] = None,
    rotor: Optional[cq.Workplane] = None,
    magnets: Optional[cq.Workplane] = None,
    fillet_radius: float = 0.2,
    enable_stator_fillets: bool = True,
    enable_coil_fillets: bool = True,
    enable_chamfers: bool = False
) -> dict:
    """
    Apply geometry refinement to motor components.
    
    Args:
        stator: Stator workplane (optional)
        coils: Coils workplane (optional)
        rotor: Rotor workplane (optional)
        magnets: Magnets workplane (optional)
        fillet_radius: Radius for fillet operations
        enable_stator_fillets: Whether to add fillets to stator
        enable_coil_fillets: Whether to add fillets to coils
        enable_chamfers: Whether to add chamfers
        
    Returns:
        Dictionary with refined components
    """
    refinement = GeometryRefinement(fillet_radius=fillet_radius)
    result = {}
    
    if stator is not None and enable_stator_fillets:
        result['stator'] = refinement.add_stator_fillets(stator)
    elif stator is not None:
        result['stator'] = stator
        
    if coils is not None and enable_coil_fillets:
        result['coils'] = refinement.add_coil_fillets(coils)
    elif coils is not None:
        result['coils'] = coils
        
    if rotor is not None:
        result['rotor'] = rotor
        
    if magnets is not None:
        result['magnets'] = magnets
    
    return result


# Preset configurations for different refinement styles
REFINEMENT_PRESETS = {
    'minimal': {
        'fillet_radius': 0.1,
        'enable_stator_fillets': True,
        'enable_coil_fillets': False,
        'enable_chamfers': False,
    },
    'standard': {
        'fillet_radius': 0.2,
        'enable_stator_fillets': True,
        'enable_coil_fillets': True,
        'enable_chamfers': False,
    },
    'smooth': {
        'fillet_radius': 0.3,
        'enable_stator_fillets': True,
        'enable_coil_fillets': True,
        'enable_chamfers': True,
    },
    'cad_like': {
        'fillet_radius': 0.4,
        'enable_stator_fillets': True,
        'enable_coil_fillets': True,
        'enable_chamfers': True,
    },
}


def apply_refinement_preset(
    preset_name: str,
    stator: Optional[cq.Workplane] = None,
    coils: Optional[cq.Workplane] = None,
    rotor: Optional[cq.Workplane] = None,
    magnets: Optional[cq.Workplane] = None
) -> dict:
    """
    Apply a predefined refinement preset.
    
    Args:
        preset_name: Name of preset ('minimal', 'standard', 'smooth', 'cad_like')
        stator: Stator workplane (optional)
        coils: Coils workplane (optional)
        rotor: Rotor workplane (optional)
        magnets: Magnets workplane (optional)
        
    Returns:
        Dictionary with refined components
        
    Raises:
        ValueError: If preset_name is not recognized
    """
    if preset_name not in REFINEMENT_PRESETS:
        raise ValueError(
            f"Unknown preset: {preset_name}. "
            f"Available presets: {list(REFINEMENT_PRESETS.keys())}"
        )
    
    preset = REFINEMENT_PRESETS[preset_name]
    return refine_motor_geometry(
        stator=stator,
        coils=coils,
        rotor=rotor,
        magnets=magnets,
        fillet_radius=preset['fillet_radius'],
        enable_stator_fillets=preset['enable_stator_fillets'],
        enable_coil_fillets=preset['enable_coil_fillets'],
        enable_chamfers=preset['enable_chamfers'],
    )
