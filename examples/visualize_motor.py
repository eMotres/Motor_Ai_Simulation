#!/usr/bin/env python
"""Example: Visualize electric motor geometry.

This example demonstrates:
1. Loading motor configuration from YAML file
2. Generating mesh with material properties
3. Visualizing the motor cross-section
4. Displaying material properties

Usage:
    python visualize_motor.py
"""

import sys
from pathlib import Path

# Add src to path for development
src_path = Path(__file__).parent.parent / "src"
if src_path.exists():
    sys.path.insert(0, str(src_path))

import matplotlib.pyplot as plt

from motor_ai_sim import (
    get_geometry_params,
    get_mesh_params,
    get_material_assignments,
    MotorMeshGenerator,
    MaterialRegistry,
)
from motor_ai_sim.utils import (
    visualize_motor,
    visualize_materials_comparison,
    create_motor_diagram,
)


def main():
    """Main function to run the visualization example."""
    print("=" * 60)
    print("Electric Motor AI Simulator - Visualization Example")
    print("=" * 60)

    # 1. Load motor geometry parameters from config
    # All dimensions are in millimeters [mm], angles in degrees [deg]
    print("\n1. Loading motor configuration from config/motor_config.yaml...")
    params = get_geometry_params()

    print(f"   Stator diameter:      {params.stator_diameter:.1f} mm")
    print(f"   Stator outer radius:  {params.stator_outer_radius:.1f} mm")
    print(f"   Stator inner radius:  {params.stator_inner_radius:.1f} mm")
    print(f"   Rotor outer radius:   {params.rotor_outer_radius:.1f} mm")
    print(f"   Rotor inner radius:   {params.rotor_inner_radius:.1f} mm")
    print(f"   Air gap:              {params.air_gap:.2f} mm")
    print(f"   Number of slots:      {params.num_slots}")
    print(f"   Number of poles:      {params.num_poles}")
    print(f"   Slot angle:           {params.angle_slot:.2f} deg")
    print(f"   Pole angle:           {params.angle_pole:.2f} deg")

    # 2. Get mesh parameters from config
    mesh_params = get_mesh_params()
    print(f"\n   Mesh params: n_radial={mesh_params['n_radial']}, n_angular={mesh_params['n_angular']}")

    # 3. Get material assignments from config
    material_assignments = get_material_assignments()
    print(f"   Material assignments: {list(material_assignments.keys())}")

    # 4. Create mesh generator
    print("\n2. Creating mesh generator...")
    generator = MotorMeshGenerator(params)

    # Print configuration summary
    generator.print_summary()

    # 5. Generate mesh
    print("\n3. Generating mesh...")
    meshes = generator.generate(
        n_radial=mesh_params['n_radial'],
        n_angular=mesh_params['n_angular'],
        n_angular_slots=mesh_params['n_angular_slots'],
    )

    print(f"   Generated {len(meshes)} regions:")
    for region_name, mesh_data in meshes.items():
        n_points = mesh_data["points"].shape[0]
        n_cells = mesh_data["cells"].shape[0] if mesh_data["cells"].numel() > 0 else 0
        material = mesh_data["point_data"]["material_name"]
        print(f"   - {region_name}: {n_points} points, {n_cells} cells, material: {material}")

    # 6. List available materials
    print("\n4. Available materials in registry:")
    categories = MaterialRegistry.get_by_category()
    for category, materials in categories.items():
        print(f"\n   {category.replace('_', ' ').title()}:")
        for mat_name in materials:
            mat = MaterialRegistry.get(mat_name)
            print(f"     - {mat.name}: mu_r={mat.mu_r:.1f}, sigma={mat.sigma:.2e} S/m")

    # 7. Create visualizations
    print("\n5. Creating visualizations...")

    # Figure 1: Motor schematic
    fig1 = create_motor_diagram(params)
    fig1.savefig("motor_schematic.png", dpi=150, bbox_inches='tight')
    print("   Saved: motor_schematic.png")

    # Figure 2: Full motor cross-section
    fig2 = visualize_motor(meshes, title="Electric Motor Cross-Section")
    fig2.savefig("motor_cross_section.png", dpi=150, bbox_inches='tight')
    print("   Saved: motor_cross_section.png")

    # Figure 3: Material properties comparison
    fig3 = visualize_materials_comparison(meshes)
    fig3.savefig("motor_materials.png", dpi=150, bbox_inches='tight')
    print("   Saved: motor_materials.png")

    # 8. Show plots
    print("\n6. Displaying plots (close window to exit)...")
    plt.show()

    print("\nDone!")


if __name__ == "__main__":
    main()
