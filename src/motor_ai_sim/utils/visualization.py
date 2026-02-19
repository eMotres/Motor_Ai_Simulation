"""Visualization utilities for electric motor geometry.

This module provides functions to visualize:
- Motor cross-section with material colors
- Individual regions
- Material property distributions
"""

from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon

from motor_ai_sim.geometry import MotorGeometryParams


def visualize_motor(
    meshes: Dict[str, Dict],
    title: str = "Electric Motor Cross-Section",
    figsize: Tuple[float, float] = (12, 10),
    show_materials: bool = True,
    show_labels: bool = True,
    save_path: Optional[str] = None,
) -> plt.Figure:
    """Visualize the complete motor cross-section.

    Args:
        meshes: Dictionary of region meshes from MotorMeshGenerator
        title: Plot title
        figsize: Figure size (width, height)
        show_materials: Whether to show material legend
        show_labels: Whether to show region labels
        save_path: Path to save figure (optional)

    Returns:
        Matplotlib Figure object
    """
    fig, ax = plt.subplots(1, 1, figsize=figsize)

    # Color map for regions
    legend_patches = []

    for region_name, mesh_data in meshes.items():
        points = mesh_data["points"].cpu().numpy()
        cells = mesh_data["cells"].cpu().numpy()

        # Get color from point_data
        if "point_data" in mesh_data and "color" in mesh_data["point_data"]:
            color = mesh_data["point_data"]["color"]
        else:
            color = (0.5, 0.5, 0.5)  # Default gray

        # Plot triangles
        for cell in cells:
            triangle = points[cell]
            poly = Polygon(triangle, closed=True)
            ax.add_patch(plt.Polygon(triangle, facecolor=color, edgecolor='none', alpha=0.8))

        # Add to legend
        material_name = mesh_data["point_data"].get("material_name", "Unknown")
        legend_patches.append(mpatches.Patch(color=color, label=f"{region_name} ({material_name})"))

    # Set equal aspect ratio
    ax.set_aspect('equal')

    # Get bounds
    all_points = np.vstack([m["points"].cpu().numpy() for m in meshes.values()])
    margin = 0.01
    ax.set_xlim(all_points[:, 0].min() - margin, all_points[:, 0].max() + margin)
    ax.set_ylim(all_points[:, 1].min() - margin, all_points[:, 1].max() + margin)

    # Labels
    ax.set_xlabel('X [m]')
    ax.set_ylabel('Y [m]')
    ax.set_title(title)

    # Grid
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color='k', linewidth=0.5, alpha=0.3)
    ax.axvline(x=0, color='k', linewidth=0.5, alpha=0.3)

    # Legend
    if show_materials and legend_patches:
        ax.legend(handles=legend_patches, loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=8)

    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')

    return fig


def visualize_region(
    mesh_data: Dict,
    title: Optional[str] = None,
    figsize: Tuple[float, float] = (8, 8),
    color_by: str = "mu_r",
    cmap: str = "viridis",
) -> plt.Figure:
    """Visualize a single region with property coloring.

    Args:
        mesh_data: Single region mesh dictionary
        title: Plot title (defaults to region name)
        figsize: Figure size
        color_by: Property to color by ('mu_r', 'sigma', 'material_id')
        cmap: Matplotlib colormap name

    Returns:
        Matplotlib Figure object
    """
    fig, ax = plt.subplots(1, 1, figsize=figsize)

    points = mesh_data["points"].cpu().numpy()
    cells = mesh_data["cells"].cpu().numpy()
    point_data = mesh_data.get("point_data", {})

    if title is None:
        title = mesh_data.get("region", "Region")

    # Get property values for coloring
    if color_by in point_data:
        values = point_data[color_by]
        if hasattr(values, 'cpu'):
            values = values.cpu().numpy()
        # Use first value for all points in each cell
        facecolors = values[cells[:, 0]]
    else:
        facecolors = np.ones(len(cells))

    # Create tripcolor plot
    trip = ax.tripcolor(
        points[:, 0],
        points[:, 1],
        cells,
        facecolors=facecolors,
        cmap=cmap,
        edgecolors='none',
        alpha=0.9,
    )

    plt.colorbar(trip, ax=ax, label=color_by)

    ax.set_aspect('equal')
    ax.set_xlabel('X [m]')
    ax.set_ylabel('Y [m]')
    ax.set_title(title)
    ax.grid(True, alpha=0.3)

    return fig


def visualize_materials_comparison(
    meshes: Dict[str, Dict],
    figsize: Tuple[float, float] = (14, 5),
) -> plt.Figure:
    """Visualize material property distributions.

    Args:
        meshes: Dictionary of region meshes
        figsize: Figure size

    Returns:
        Matplotlib Figure object
    """
    fig, axes = plt.subplots(1, 3, figsize=figsize)

    # Combine all meshes
    all_points = []
    all_cells = []
    all_mu_r = []
    all_sigma = []
    all_mat_id = []

    point_offset = 0
    for mesh_data in meshes.values():
        points = mesh_data["points"].cpu().numpy()
        cells = mesh_data["cells"].cpu().numpy()
        point_data = mesh_data["point_data"]

        n_points = len(points)
        all_points.append(points)
        # Only add cells if they exist (non-empty)
        if cells.size > 0:
            all_cells.append(cells + point_offset)
        point_offset += n_points

        all_mu_r.append(point_data["mu_r"].cpu().numpy())
        all_sigma.append(point_data["sigma"].cpu().numpy())
        all_mat_id.append(point_data["material_id"].cpu().numpy())

    points = np.vstack(all_points)
    # Filter out empty cell arrays before vstack
    non_empty_cells = [c for c in all_cells if c.size > 0]
    if non_empty_cells:
        cells = np.vstack(non_empty_cells)
    else:
        cells = np.array([]).reshape(0, 3)
    mu_r = np.concatenate(all_mu_r)
    sigma = np.concatenate(all_sigma)
    mat_id = np.concatenate(all_mat_id)

    # Plot mu_r
    ax = axes[0]
    trip = ax.tripcolor(points[:, 0], points[:, 1], cells,
                        facecolors=mu_r[cells[:, 0]],
                        cmap='plasma', edgecolors='none')
    plt.colorbar(trip, ax=ax, label='μr')
    ax.set_aspect('equal')
    ax.set_title('Relative Permeability')
    ax.set_xlabel('X [m]')
    ax.set_ylabel('Y [m]')

    # Plot sigma (log scale)
    ax = axes[1]
    sigma_plot = np.log10(sigma[cells[:, 0]] + 1e-10)  # Add small value to avoid log(0)
    trip = ax.tripcolor(points[:, 0], points[:, 1], cells,
                        facecolors=sigma_plot,
                        cmap='viridis', edgecolors='none')
    plt.colorbar(trip, ax=ax, label='log₁₀(σ) [S/m]')
    ax.set_aspect('equal')
    ax.set_title('Electrical Conductivity')
    ax.set_xlabel('X [m]')
    ax.set_ylabel('Y [m]')

    # Plot material ID
    ax = axes[2]
    trip = ax.tripcolor(points[:, 0], points[:, 1], cells,
                        facecolors=mat_id[cells[:, 0]],
                        cmap='tab10', edgecolors='none')
    plt.colorbar(trip, ax=ax, label='Material ID')
    ax.set_aspect('equal')
    ax.set_title('Material Regions')
    ax.set_xlabel('X [m]')
    ax.set_ylabel('Y [m]')

    plt.tight_layout()
    return fig


def visualize_magnetization(
    meshes: Dict[str, Dict],
    figsize: Tuple[float, float] = (10, 10),
    scale: float = 0.002,
) -> plt.Figure:
    """Visualize magnetization vectors in permanent magnets.

    Args:
        meshes: Dictionary of region meshes
        figsize: Figure size
        scale: Arrow scale factor

    Returns:
        Matplotlib Figure object
    """
    fig, ax = plt.subplots(1, 1, figsize=figsize)

    # Plot all regions as background
    for region_name, mesh_data in meshes.items():
        points = mesh_data["points"].cpu().numpy()
        cells = mesh_data["cells"].cpu().numpy()
        color = mesh_data["point_data"].get("color", (0.5, 0.5, 0.5))

        for cell in cells:
            triangle = points[cell]
            ax.add_patch(plt.Polygon(triangle, facecolor=color, edgecolor='none', alpha=0.5))

    # Plot magnetization vectors
    for region_name, mesh_data in meshes.items():
        if "magnetization" in mesh_data["point_data"]:
            points = mesh_data["points"].cpu().numpy()
            magnetization = mesh_data["point_data"]["magnetization"].cpu().numpy()

            # Subsample for clarity
            step = max(1, len(points) // 50)
            sample_points = points[::step]
            sample_mag = magnetization[::step]

            # Plot arrows
            ax.quiver(
                sample_points[:, 0],
                sample_points[:, 1],
                sample_mag[:, 0],
                sample_mag[:, 1],
                color='red',
                scale=1/scale,
                scale_units='xy',
                width=0.001,
                headwidth=3,
                headlength=4,
            )

    ax.set_aspect('equal')
    ax.set_xlabel('X [m]')
    ax.set_ylabel('Y [m]')
    ax.set_title('Permanent Magnet Magnetization')
    ax.grid(True, alpha=0.3)

    return fig


def create_motor_diagram(
    params,
    figsize: Tuple[float, float] = (10, 10),
) -> plt.Figure:
    """Create a schematic diagram of the motor geometry.

    Args:
        params: MotorGeometryParams instance
        figsize: Figure size

    Returns:
        Matplotlib Figure object
    """
    fig, ax = plt.subplots(1, 1, figsize=figsize)

    # Draw stator
    stator_outer = plt.Circle((0, 0), params.stator_outer_radius,
                               fill=False, color='blue', linewidth=2, label='Stator outer')
    stator_inner = plt.Circle((0, 0), params.stator_inner_radius,
                               fill=False, color='blue', linewidth=2, linestyle='--', label='Stator bore')
    ax.add_patch(stator_outer)
    ax.add_patch(stator_inner)

    # Draw rotor
    rotor_outer = plt.Circle((0, 0), params.rotor_outer_radius,
                              fill=False, color='red', linewidth=2, label='Rotor outer')
    rotor_inner = plt.Circle((0, 0), params.rotor_inner_radius,
                              fill=False, color='red', linewidth=2, linestyle='--', label='Rotor inner')
    ax.add_patch(rotor_outer)
    ax.add_patch(rotor_inner)

    # Draw slots (simplified)
    for i in range(params.num_slots):
        angle = i * 2 * np.pi / params.num_slots
        x1 = params.stator_inner_radius * np.cos(angle)
        y1 = params.stator_inner_radius * np.sin(angle)
        x2 = params.stator_slot_radius * np.cos(angle)
        y2 = params.stator_slot_radius * np.sin(angle)
        ax.plot([x1, x2], [y1, y2], 'g-', linewidth=1, alpha=0.5)

    # Draw magnets (simplified)
    # Convert angle_pole from degrees to radians for internal calculations
    angle_pole_rad = MotorGeometryParams.deg_to_rad(params.angle_pole)
    for i in range(params.num_poles):
        angle_start = i * params.pole_pitch - angle_pole_rad / 2
        angle_end = angle_start + angle_pole_rad

        theta = np.linspace(angle_start, angle_end, 20)
        r_inner = params.rotor_core_radius
        r_outer = params.rotor_outer_radius

        x_inner = r_inner * np.cos(theta)
        y_inner = r_inner * np.sin(theta)
        x_outer = r_outer * np.cos(theta[::-1])
        y_outer = r_outer * np.sin(theta[::-1])

        x = np.concatenate([x_inner, x_outer])
        y = np.concatenate([y_inner, y_outer])

        color = 'red' if i % 2 == 0 else 'blue'
        ax.fill(x, y, color=color, alpha=0.3)

    # Annotations (air_gap is already in mm)
    ax.annotate(f'Air gap: {params.air_gap:.2f} mm',
                xy=(params.stator_inner_radius - params.air_gap/2, 0),
                xytext=(params.stator_inner_radius + 5, 5),
                fontsize=10,
                arrowprops=dict(arrowstyle='->', color='black'))

    # Set limits and labels (all dimensions in mm)
    margin = 5  # mm
    ax.set_xlim(-params.stator_outer_radius - margin, params.stator_outer_radius + margin)
    ax.set_ylim(-params.stator_outer_radius - margin, params.stator_outer_radius + margin)
    ax.set_aspect('equal')
    ax.set_xlabel('X [mm]')
    ax.set_ylabel('Y [mm]')
    ax.set_title(f'Motor Schematic\n{params.num_slots} slots, {params.num_poles} poles')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)

    return fig
