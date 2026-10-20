// The viewer's one GLTFLoader set-up.  The server sends the 3-D models meshopt-compressed (cad/viewer_pack.py: gltfpack,
// EXT_meshopt_compression + KHR_mesh_quantization, float positions - same node tree as the canonical GLB, about 5x smaller); an
// older uncompressed GLB loads through the same loader unchanged.  The decoder is three's own module: the WebAssembly is inside the
// JS file, bundled by Vite - no CDN, no extra request.
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';

export function makeGltfLoader(): GLTFLoader {
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  return loader;
}
