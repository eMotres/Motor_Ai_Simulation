# Synchronizing Modulus CSG Geometry with 3D Web Visualization

## Problem Statement
The current 3D interface displays generic shapes instead of the actual motor geometry defined in `MotorGeometry2D`. The stator slots are missing, and the air gap is not visually accurate. We need to bridge the gap between the Modulus CSG (SDF) logic and the Frontend Mesh rendering.

---

## Architecture Overview

```mermaid
graph TB
    subgraph Backend
        A[MotorGeometry2D] --> B[get_modulus_geometries]
        B --> C[Dictionary of CSG Objects]
        C --> D[sample_interior n_points]
        C --> E[Marching Cubes SDF]
    end
    
    subgraph API
        F[/api/geometry/mesh] --> G[Parametric Mesh Generation]
        H[/api/geometry/pointcloud] --> I[Modulus Point Cloud]
        J[/api/geometry/sdf-mesh] --> K[Marching Cubes Mesh]
    end
    
    subgraph Frontend
        L[MotorScene] --> M[Point Cloud Component]
        L --> N[Solid Mesh Component]
        L --> O[Magnetization Cones]
    end
    
    C --> I
    C --> K
    
    I --> M
    K --> N
```

---

## Implementation Plan

### 1. Point Cloud Streaming (Modulus View)

**Requirement**: Add a "Modulus View" toggle that shows what the AI actually sees during training.

**Backend Changes**:
- Add new endpoint: `GET /api/geometry/pointcloud`
- Sample interior points using `geometries["region_name"].sample_interior(n_points=20000)`
- Return JSON with material type metadata for each region

**Frontend Changes**:
- Add `viewMode` state to motorStore: `'solid' | 'pointcloud' | 'hybrid'`
- Add toggle button in UI
- Create `PointCloudMesh` component using Three.js `Points`

**API Response Format**:
```json
{
  "stator_core": {
    "points": [[x, y, z], ...],
    "material": "steel"
  },
  "rotor_core": {
    "points": [[x, y, z], ...],
    "material": "steel"
  },
  "coils": {
    "points": [[x, y, z], ...],
    "material": "copper"
  },
  "magnets": {
    "points": [[x, y, z], ...],
    "material": "permanent_magnet"
  }
}
```

---

### 2. Real-time Mesh Generation (SDF to Mesh)

**Requirement**: Show solid geometry with accurate slots.

**Option A: Marching Cubes (Recommended)**
- Use `skimage.measure.marching_cubes` to convert SDF to mesh
- Higher quality, but computationally expensive

**Option B: CSG Mirroring**
- Replicate CSG operations in browser using Three.js CSG
- Faster but requires syncing logic

**Decision**: Use Option A (Marching Cubes) for accuracy, with caching.

**Backend Changes**:
- Add new endpoint: `GET /api/geometry/sdf-mesh`
- Implement SDF evaluation on a grid
- Apply marching cubes algorithm

---

### 3. Magnetization Vector Alignment

**Requirement**: Show arrows/cones for magnetization direction that update dynamically.

**Backend Changes**:
- Add endpoint: `GET /api/geometry/magnetization-vectors`
- Return position and direction for each magnet pole
- Include `num_poles` parameter for dynamic calculation

**Frontend Changes**:
- Add `MagnetizationVectors` component
- Use Three.js `ConeGeometry` or `ArrowHelper`
- Color: North = Red, South = Blue

---

## Detailed Todo List

### Phase 1: Point Cloud Streaming

- [ ] **1.1** Add `viewMode` state to motorStore (`solid`, `pointcloud`, `hybrid`)
- [ ] **1.2** Create `useViewMode` hook in motorStore
- [ ] **1.3** Add backend endpoint `/api/geometry/pointcloud`
- [ ] **1.4** Implement point cloud sampling for all regions
- [ ] **1.5** Add material metadata to point cloud response
- [ ] **1.6** Create `PointCloudMesh` component in frontend
- [ ] **1.7** Add toggle button in UI (near grid/axes controls)
- [ ] **1.8** Handle point cloud updates when geometry parameters change

### Phase 2: Real-time Mesh Generation

- [ ] **2.1** Add backend endpoint `/api/geometry/sdf-mesh`
- [ ] **2.2** Implement SDF evaluation on regular grid
- [ ] **2.3** Integrate marching cubes (skimage or custom)
- [ ] **2.4** Add caching for mesh generation results
- [ ] **2.5** Update frontend to use SDF mesh when available

### Phase 3: Magnetization Vectors

- [ ] **3.1** Add backend endpoint `/api/geometry/magnetization-vectors`
- [ ] **3.2** Calculate magnetization directions from `num_poles`
- [ ] **3.3** Create `MagnetizationVectors` component
- [ ] **3.4** Add dynamic color (red/blue) based on polarity
- [ ] **3.5** Update when `num_poles` or `magnet_thickness` changes

---

## Files to Modify

### Backend (Python)
- `src/motor_ai_sim/api.py` - Add new endpoints
- `src/motor_ai_sim/geometry/motor_geometry.py` - Add sampling method

### Frontend (TypeScript/React)
- `web/src/stores/motorStore.ts` - Add viewMode state
- `web/src/components/viewer3d/MotorScene.tsx` - Add view mode toggle
- `web/src/components/viewer3d/PointCloudMesh.tsx` - New component
- `web/src/components/viewer3d/MagnetizationVectors.tsx` - New component
- `web/src/components/viewer3d/SdfMesh.tsx` - New component (optional)

---

## Dependencies

### Backend
- `scikit-image` - For marching cubes algorithm (if not already installed)
- `trimesh` - For mesh processing (optional)

### Frontend
- Already using Three.js via @react-three/fiber
- No new dependencies needed

---

## Success Criteria

1. **Point Cloud View**: User can toggle to see 20,000+ points sampled from the SDF, colored by material type
2. **Mesh Accuracy**: The solid mesh matches the exact CSG geometry with correct slot shapes
3. **Magnetization Vectors**: Cones show correct polarity and update when parameters change
4. **Performance**: Point cloud loads in < 2 seconds, mesh generation in < 5 seconds

---

## Next Steps

1. **Approve this plan** or request modifications
2. **Switch to Code mode** to implement Phase 1 (Point Cloud Streaming)
3. **Test incrementally** after each subtask
4. **Proceed to Phase 2** once Point Cloud is working
5. **Finish with Phase 3** for magnetization vectors
