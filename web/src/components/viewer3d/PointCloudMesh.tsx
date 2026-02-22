import React, { useMemo, useEffect } from 'react';
import * as THREE from 'three';
import { useMotorStore } from '../../stores/motorStore';

// Material colors for point cloud
const MATERIAL_COLORS: Record<string, string> = {
  steel: '#4a5568',      // Grey
  copper: '#b45309',      // Copper/amber
  permanent_magnet: '#ef4444',  // Red
  air: '#93c5fd',        // Light blue
};

interface PointCloudRegion {
  points: number[][];
  material: string;
  count: number;
}

interface PointCloudData {
  n_points: number;
  has_modulus: boolean;
  regions: Record<string, PointCloudRegion>;
}

/**
 * Point cloud mesh that renders geometry from Python API
 * Shows what the AI sees during PINN training
 */
export const PointCloudMesh: React.FC = () => {
  const { viewMode, pointCloudData, fetchPointCloudFromApi, connectedToApi } = useMotorStore();
  
  // Fetch point cloud data when viewMode changes to pointcloud or hybrid
  useEffect(() => {
    if ((viewMode === 'pointcloud' || viewMode === 'hybrid') && connectedToApi && !pointCloudData) {
      fetchPointCloudFromApi(10000);
    }
  }, [viewMode, connectedToApi, pointCloudData, fetchPointCloudFromApi]);
  
  // Only render when in pointcloud or hybrid mode and data is available
  if (viewMode === 'solid' || !pointCloudData) {
    return null;
  }
  
  const regions = pointCloudData.regions as Record<string, PointCloudRegion>;
  
  return (
    <group>
      {Object.entries(regions).map(([regionName, data]) => (
        <PointCloudRegion 
          key={regionName} 
          name={regionName} 
          data={data} 
        />
      ))}
    </group>
  );
};

interface PointCloudRegionProps {
  name: string;
  data: PointCloudRegion;
}

const PointCloudRegion: React.FC<PointCloudRegionProps> = ({ name, data }) => {
  const geometry = useMemo(() => {
    if (!data.points || data.points.length === 0) {
      return null;
    }
    
    // Flatten points to position array
    const positions: number[] = [];
    for (const point of data.points) {
      // Each point is [x, y, z]
      positions.push(point[0], point[1], point[2]);
    }
    
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      'position', 
      new THREE.Float32BufferAttribute(positions, 3)
    );
    
    return geometry;
  }, [data.points]);
  
  if (!geometry) {
    return null;
  }
  
  const color = MATERIAL_COLORS[data.material] || '#ffffff';
  
  return (
    <points geometry={geometry}>
      <pointsMaterial 
        color={color} 
        size={1.5} 
        sizeAttenuation={true}
        transparent={true}
        opacity={0.8}
      />
    </points>
  );
};

export default PointCloudMesh;
