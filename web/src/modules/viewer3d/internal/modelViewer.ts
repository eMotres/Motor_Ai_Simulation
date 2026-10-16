// three.js viewer for the "3D model" tab: orbit / zoom, a section plane, an exploded view, part toggles, a point-to-point
// measure tool.  Plain three (not react-three-fiber): the panel needs an imperative handle and renders on demand.
//
// The GLB is in the CAD frame (mm, +Z = motor axis, +Z = propeller side); the camera is Z-up.  The GLB nodes are one
// group per part with `extras` {family, explode} (cad/fc_scripts/glb_writer.py); the viewer never shows more than the file
// contains - what a caller may see is decided on the server.
import {
  AmbientLight, Box3, BufferGeometry, Color, DataTexture, DirectionalLight, DoubleSide, Float32BufferAttribute, Group,
  HemisphereLight, Line, LineBasicMaterial, LinearMipmapLinearFilter, LinearFilter, Material, Mesh, MeshBasicMaterial,
  MeshStandardMaterial, Object3D, PerspectiveCamera, OrthographicCamera, Plane, Raycaster, RepeatWrapping, RGBAFormat, Scene, SphereGeometry,
  UnsignedByteType, Vector2, Vector3, WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import {
  distance, explodeShift, sectionPlane, sectionRange, type Axis, type Box, type PartGroup, type Vec3, type StandardView, standardViewDirection,
} from './model3d';
import {
  buildThreadNormalMap, normalScaleFor, parseThread, resolveAxis, threadLabel, threadUVs, type ThreadInfo,
} from './threadTexture';

const THREAD_MAP_SIZE = 128;

export interface ViewerState {
  hidden: ReadonlySet<string>;
  /** GLB node names hidden individually (a part switched off in THE part tree) */
  hiddenNodes?: ReadonlySet<string>;
  /** GLB node names to highlight (a part selected in THE part tree); the rest is dimmed.  Empty / absent = no highlight. */
  highlight?: ReadonlySet<string>;
  explode: number;             // 0..1
  section: { on: boolean; axis: Axis; position: number };
  measure: boolean;
  /** show part hints (the thread size under the pointer); the NDA view only */
  hints?: boolean;
}

export class ModelViewer {
  readonly renderer: WebGLRenderer;
  readonly scene = new Scene();
  camera: PerspectiveCamera | OrthographicCamera;
  controls: OrbitControls;
  projection: 'perspective' | 'orthographic' = 'orthographic';
  private root: Object3D | null = null;
  private groups: Array<{ node: Object3D; part: PartGroup; base: Vector3 }> = [];
  private plane = new Plane(new Vector3(0, 0, -1), 0);
  private ray = new Raycaster();
  private marks = new Group();
  private picks: Vector3[] = [];
  private frame = 0;
  private dirty = true;
  private ro: ResizeObserver;
  private disposed = false;
  private loadToken = 0;
  private threadMap: DataTexture | null = null;       // ONE shared normal map for every thread of the model
  private threadMeshes: Array<{ mesh: Mesh; info: ThreadInfo }> = [];
  private hints = false;
  private hoverText = '';
  private measurementSceneSignature = '';
  private down = new Vector2();
  box: Box = { min: [-1, -1, -1], max: [1, 1, 1] };
  motorLength = 12;
  onMeasure: (mm: number | null) => void = () => {};
  onCameraDirection: (direction: Vec3) => void = () => {};
  onCameraUp: (up: Vec3) => void = () => {};
  onSelect: (name: string | null) => void = () => {};
  onContextLost: () => void = () => {};

  private host: HTMLElement;

  constructor(host: HTMLElement) {
    this.host = host;
    this.renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'default' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.localClippingEnabled = true;
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = 'block';
    this.camera = new OrthographicCamera(-100, 100, 100, -100, 0.1, 5000);
    this.camera.up.set(0, 0, 1);
    this.controls = this.makeControls(this.camera);
    this.scene.add(new HemisphereLight(0xffffff, 0x445066, 1.15), new AmbientLight(0xffffff, 0.35));
    const key = new DirectionalLight(0xffffff, 1.6); key.position.set(60, -80, 90);
    const fill = new DirectionalLight(0xffffff, 0.6); fill.position.set(-70, 60, -40);
    this.scene.add(key, fill, this.marks);
    const el = this.renderer.domElement;
    el.addEventListener('pointerdown', (e) => this.down.set(e.clientX, e.clientY));
    el.addEventListener('pointerup', (e) => this.onPointerUp(e));
    el.addEventListener('pointermove', (e) => this.onPointerMove(e));
    el.addEventListener('webglcontextlost', (e) => { e.preventDefault(); this.onContextLost(); });
    el.addEventListener('webglcontextrestored', () => this.invalidate());
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();
    this.loop();
  }

  // ---------------------------------------------------------------- loading

  /** Parse a GLB and show it.  Returns the part groups in file order. */
  async load(buffer: ArrayBuffer): Promise<{ groups: PartGroup[]; box: Box }> {
    const token = ++this.loadToken;
    const gltf = await new GLTFLoader().parseAsync(buffer, '');
    if (token !== this.loadToken || this.disposed) {
      gltf.scene.traverse((o) => { const m = o as Mesh; if (m.isMesh) { m.geometry.dispose(); for (const mt of Array.isArray(m.material) ? m.material : [m.material]) mt.dispose(); } });
      return { groups: [], box: this.box };
    }
    const model = gltf.scene.getObjectByName('Model') ?? gltf.scene;
    const groups: PartGroup[] = [];
    for (const node of model.children) {
      const ex = (node.userData ?? {}) as { family?: string; explode?: number };
      groups.push({ name: node.name, family: ex.family ?? node.name, explode: Number(ex.explode ?? 0),
        children: node.children.map((c) => c.name), colour: this.colourOf(node) });
    }
    // setObject clears the previous scene only after parsing has completed, so
    // users keep seeing the last gated artifact while its replacement loads.
    this.setObject(gltf.scene, groups, model);
    gltf.scene.traverse((o) => {
      const mesh = o as Mesh;
      if (!mesh.isMesh) return;
      mesh.geometry.dispose();
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) material.dispose();
    });
    return { groups: this.groups.map((g) => g.part), box: this.box };
  }

  /** Install locally-built active geometry into this same scene engine. */
  setObject(root: Object3D, parts: readonly PartGroup[], groupRoot: Object3D = root): void {
    this.loadToken++;
    this.clear();
    const ownedRoot = root.clone(true);
    ownedRoot.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh) return;
      m.geometry = m.geometry.clone();
      m.material = Array.isArray(m.material) ? m.material.map((mt) => mt.clone()) : m.material.clone();
    });
    this.root = ownedRoot;
    this.scene.add(ownedRoot);
    const index = new Map<string, Object3D>();
    groupRoot = ownedRoot.getObjectByName(groupRoot.name) ?? ownedRoot;
    for (const child of groupRoot.children) index.set(child.name, child);
    this.groups = parts.flatMap((part) => {
      const node = index.get(part.name);
      return node ? [{ node, part, base: node.position.clone() }] : [];
    });
    this.applyThreads(ownedRoot);
    ownedRoot.traverse((o) => {
      const m = o as Mesh;
      if (m.isMesh) for (const mt of Array.isArray(m.material) ? m.material : [m.material]) {
        (mt as Material).side = DoubleSide;
        (mt as Material).clippingPlanes = [this.plane];
      }
    });
    const b = new Box3().setFromObject(ownedRoot);
    this.box = { min: [b.min.x, b.min.y, b.min.z], max: [b.max.x, b.max.y, b.max.z] };
    const core = this.groups.find((g) => g.part.name === 'Core' || g.part.name === 'Housing' || g.part.name === 'stator_core');
    const cb = core ? new Box3().setFromObject(core.node) : b;
    this.motorLength = Math.max(1, cb.max.z - cb.min.z);
    this.fit();
  }

  /** The #rrggbb colour of the first mesh of a node (its tree swatch), or undefined. */
  private colourOf(node: Object3D): string | undefined {
    let hex: string | undefined;
    node.traverse((o) => {
      const m = o as Mesh;
      if (hex || !m.isMesh) return;
      const mt = (Array.isArray(m.material) ? m.material[0] : m.material) as Material & { color?: Color };
      if (mt?.color) hex = '#' + mt.color.getHexString();
    });
    return hex;
  }

  private clear(): void {
    if (this.root) {
      this.scene.remove(this.root);
      this.root.traverse((o) => {
        const m = o as Mesh;
        if (m.isMesh) {
          m.geometry.dispose();
          for (const mt of Array.isArray(m.material) ? m.material : [m.material]) (mt as Material).dispose();
        }
      });
    }
    this.root = null;
    this.groups = [];
    this.threadMeshes = [];
    this.clearMarks();
  }

  private makeControls(camera: PerspectiveCamera | OrthographicCamera): OrbitControls {
    const c = new OrbitControls(camera, this.renderer.domElement);
    c.enableDamping = false;
    c.addEventListener('change', () => { this.invalidate(); this.reportCameraDirection(); });
    return c;
  }

  private reportCameraDirection(): void {
    this.camera.updateMatrixWorld();
    const d = this.camera.position.clone().sub(this.controls.target).normalize();
    this.onCameraDirection([d.x, d.y, d.z]);
    const e = this.camera.matrixWorld.elements;
    this.onCameraUp([e[4], e[5], e[6]]);
  }

  setProjection(mode: 'perspective' | 'orthographic'): void {
    if (mode === this.projection) return;
    const position = this.camera.position.clone();
    const target = this.controls.target.clone();
    this.controls.dispose();
    const aspect = Math.max(0.01, this.host.clientWidth / Math.max(1, this.host.clientHeight));
    this.camera = mode === 'perspective'
      ? new PerspectiveCamera(35, aspect, 0.1, 5000)
      : new OrthographicCamera(-100 * aspect, 100 * aspect, 100, -100, 0.1, 5000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.copy(position);
    this.controls = this.makeControls(this.camera);
    this.controls.target.copy(target);
    this.projection = mode;
    this.fit();
  }

  clearModel(): void {
    this.loadToken++;
    this.clear();
    this.box = { min: [-1, -1, -1], max: [1, 1, 1] };
    this.motorLength = 12;
    this.invalidate();
  }

  cancelPendingLoad(): void { this.loadToken++; }

  // ---------------------------------------------------------------- threads

  /** Meshes whose node carries `extras.thread` (cad/fc_scripts/threads.py) get cylindrical texture coordinates along the thread
   *  axis and the shared helical normal map; the colour and the other material settings stay those of the file.  A GLB without
   *  the extras renders exactly as before. */
  private applyThreads(root: Object3D): void {
    this.threadMeshes = [];
    root.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh || m.userData?.thread === undefined) return;
      const info = parseThread(m.userData);
      const base = Array.isArray(m.material) ? m.material[0] : m.material;
      if (!info || !(base as MeshStandardMaterial).isMeshStandardMaterial) return;
      const flat = m.geometry.index ? m.geometry.toNonIndexed() : m.geometry;
      const pos = flat.getAttribute('position');
      if (!pos || pos.count % 3 !== 0) return;
      const { axis, origin } = resolveAxis(info, pos.array);
      flat.setAttribute('uv', new Float32BufferAttribute(threadUVs(pos.array, axis, origin, info.pitch_mm, info.hand), 2));
      if (flat !== m.geometry) m.geometry.dispose();
      m.geometry = flat;
      if (!this.threadMap) {
        const t = new DataTexture(buildThreadNormalMap(THREAD_MAP_SIZE), THREAD_MAP_SIZE, THREAD_MAP_SIZE, RGBAFormat, UnsignedByteType);
        t.wrapS = t.wrapT = RepeatWrapping;
        t.magFilter = LinearFilter;
        t.minFilter = LinearMipmapLinearFilter;
        t.generateMipmaps = true;
        t.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
        t.needsUpdate = true;
        this.threadMap = t;
      }
      const mat = (base as MeshStandardMaterial).clone();
      mat.normalMap = this.threadMap;
      const r = info.radius_mm ?? info.d_mm / 2;
      const [sx, sy] = normalScaleFor(r, info.pitch_mm, info.hand);
      mat.normalScale.set(sx, sy);
      m.material = mat;
      this.threadMeshes.push({ mesh: m, info });
    });
  }

  private onPointerMove(e: PointerEvent): void {
    if (!this.hints || !this.threadMeshes.length || e.buttons) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ray.setFromCamera(new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camera);
    const hits = this.ray.intersectObjects(this.threadMeshes.map((t) => t.mesh), false).filter((h) => {
      let o: Object3D | null = h.object;
      while (o) { if (!o.visible) return false; o = o.parent; }
      return this.plane.distanceToPoint(h.point) >= 0 || this.plane.constant > 1e8;
    });
    const hit = hits.length ? this.threadMeshes.find((t) => t.mesh === hits[0].object) : undefined;
    const text = hit ? threadLabel(hit.info) : '';
    if (text !== this.hoverText) {
      this.hoverText = text;
      if (text) this.renderer.domElement.title = text; else this.renderer.domElement.removeAttribute('title');
    }
  }

  // ---------------------------------------------------------------- state

  apply(s: ViewerState): void {
    const signature = JSON.stringify([ [...s.hidden].sort(), [...(s.hiddenNodes ?? [])].sort(), s.explode,
      s.section.on ? [s.section.axis, s.section.position] : null ]);
    if (this.measurementSceneSignature && signature !== this.measurementSceneSignature) this.clearMarks();
    this.measurementSceneSignature = signature;
    for (const g of this.groups) {
      g.node.visible = !s.hidden.has(g.part.family) && !s.hiddenNodes?.has(g.part.name);
      for (const c of g.node.children) c.visible = !s.hiddenNodes?.has(c.name);          // one instance (Coil 3, Screw 2 ...)
      const shift = explodeShift(g.part.explode, this.motorLength, s.explode);
      if (g.part.explodeDirection) g.node.position.copy(g.base).add(new Vector3(...g.part.explodeDirection).multiplyScalar(shift));
      else g.node.position.copy(g.base).add(new Vector3(0, 0, shift));
    }
    this.applyHighlight(s.highlight);
    const p = sectionPlane(s.section.axis, s.section.position);
    this.plane.normal.set(...p.normal);
    this.plane.constant = s.section.on ? p.constant : 1e9;      // a plane far outside the model clips nothing
    if (!s.measure) this.clearMarks();
    this.measuring = s.measure;
    this.hints = !!s.hints;
    if (!this.hints && this.hoverText) { this.hoverText = ''; this.renderer.domElement.removeAttribute('title'); }
    this.invalidate();
  }

  private measuring = false;

  /** Highlight the nodes in `names` (selected part): they glow, every other node is dimmed; an empty set restores the model. */
  private applyHighlight(names: ReadonlySet<string> | undefined): void {
    const on = !!names && names.size > 0;
    for (const g of this.groups) {
      const groupHit = on && names!.has(g.part.name);
      g.node.traverse((o) => {
        const m = o as Mesh;
        if (!m.isMesh) return;
        // a mesh is highlighted when its group or any node between it and the group (a single Coil, a single Screw) is named
        let hit = groupHit;
        for (let n: Object3D | null = m; on && !hit && n && n !== g.node.parent; n = n.parent) hit = names!.has(n.name);
        for (const mt of Array.isArray(m.material) ? m.material : [m.material]) {
          const mm = mt as Material & { emissive?: Color; emissiveIntensity?: number };
          const keep = (mm.userData ??= {}) as { orig?: { transparent: boolean; opacity: number; depthWrite: boolean; emissive?: number; intensity?: number } };
          keep.orig ??= { transparent: mm.transparent, opacity: mm.opacity, depthWrite: mm.depthWrite,
            emissive: mm.emissive?.getHex(), intensity: mm.emissiveIntensity };
          const o0 = keep.orig;
          mm.transparent = on && !hit ? true : o0.transparent;
          mm.opacity = on && !hit ? 0.14 : o0.opacity;
          mm.depthWrite = on && !hit ? false : o0.depthWrite;
          if (mm.emissive) {
            if (on && hit) { mm.emissive.setHex(0x2563eb); mm.emissiveIntensity = 0.55; }
            else { mm.emissive.setHex(o0.emissive ?? 0x000000); mm.emissiveIntensity = o0.intensity ?? 1; }
          }
          mm.needsUpdate = true;
        }
      });
    }
  }

  sectionRange(axis: Axis): { min: number; max: number } { return sectionRange(this.box, axis); }

  fit(): void {
    const visible = new Box3();
    this.root?.updateMatrixWorld(true);
    this.root?.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh) return;
      for (let n: Object3D | null = m; n; n = n.parent) if (!n.visible) return;
      visible.expandByObject(m);
    });
    const b = visible.isEmpty() ? new Box3(new Vector3(...this.box.min), new Vector3(...this.box.max)) : visible;
    const c = b.getCenter(new Vector3());
    const halfW = Math.max(1, (b.max.x - b.min.x) / 2);
    const halfH = Math.max(1, (b.max.y - b.min.y) / 2);
    const halfD = Math.max(1, (b.max.z - b.min.z) / 2);
    const aspect = Math.max(0.01, this.host.clientWidth / Math.max(1, this.host.clientHeight));
    const r = Math.max(1, Math.hypot(halfW, halfH, halfD));
    const dir = this.camera.position.clone().sub(this.controls.target);
    if (dir.lengthSq() < 1e-8) dir.set(0.75, -1, 0.7);
    dir.normalize();
    const halfFovV = Math.atan(Math.tan((35 * Math.PI / 180) / 2) * Math.min(1, aspect));
    const d = this.camera instanceof PerspectiveCamera ? r / Math.sin(halfFovV) * 1.08 : r * Math.max(1, 1 / aspect) * 1.08;
    this.camera.position.copy(c).addScaledVector(dir, d);
    this.camera.near = d / 200;
    this.camera.far = d * 20;
    this.camera.zoom = 1;
    if (this.camera instanceof OrthographicCamera) {
      const h = r * Math.max(1, 1 / aspect) * 1.08;
      this.camera.left = -h * aspect; this.camera.right = h * aspect;
      this.camera.top = h; this.camera.bottom = -h;
    }
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(c);
    this.controls.update();
    this.invalidate();
    this.reportCameraDirection();
  }

  /** Animate to a standard face or edge direction around the current target. */
  view(view: StandardView): void {
    const direction = new Vector3(...standardViewDirection(view));
    if (Math.abs(direction.z) > 0.98) direction.x = 1e-7;
    const center = this.controls.target.clone();
    const current = Math.max(1, this.camera.position.distanceTo(center));
    this.camera.position.copy(center).addScaledVector(direction, current);
    this.camera.up.set(0, 0, 1);
    this.camera.lookAt(center);
    this.controls.update();
    this.invalidate();
    this.reportCameraDirection();
  }

  home(): void { this.view('frontTop'); this.fit(); }

  // ---------------------------------------------------------------- measure

  private onPointerUp(e: PointerEvent): void {
    if (!this.root || Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 4) return;   // a drag is an orbit
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ray.setFromCamera(new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camera);
    const hits = this.ray.intersectObjects(this.root.children, true).filter((h) => {
      let o: Object3D | null = h.object;
      while (o) { if (!o.visible) return false; o = o.parent; }
      return this.plane.distanceToPoint(h.point) >= 0 || this.plane.constant > 1e8;      // not on the clipped-away half
    });
    if (!hits.length) { if (!this.measuring) this.onSelect(null); return; }
    if (!this.measuring) {
      let node: Object3D | null = hits[0].object;
      while (node && !this.groups.some((g) => g.node === node)) node = node.parent;
      this.onSelect(node?.name ?? null);
      return;
    }
    if (this.picks.length >= 2) this.clearMarks();
    this.picks.push(hits[0].point.clone());
    const rad = Math.max(0.15, this.motorLength / 90);
    const dot = new Mesh(new SphereGeometry(rad, 12, 8), new MeshBasicMaterial({ color: new Color(this.picks.length === 1 ? '#ef4444' : '#22c55e'), depthTest: false }));
    dot.position.copy(hits[0].point);
    dot.renderOrder = 10;
    this.marks.add(dot);
    if (this.picks.length === 2) {
      const g = new BufferGeometry();
      g.setAttribute('position', new Float32BufferAttribute([...this.picks[0].toArray(), ...this.picks[1].toArray()], 3));
      const line = new Line(g, new LineBasicMaterial({ color: 0xfacc15, depthTest: false }));
      line.renderOrder = 9;
      this.marks.add(line);
      this.onMeasure(distance(this.picks[0].toArray() as Vec3, this.picks[1].toArray() as Vec3));
    } else {
      this.onMeasure(null);
    }
    this.invalidate();
  }

  private clearMarks(): void {
    for (const c of [...this.marks.children]) {
      this.marks.remove(c);
      const m = c as Mesh;
      m.geometry?.dispose();
      (m.material as Material | undefined)?.dispose();
    }
    this.picks = [];
    this.onMeasure(null);
  }

  // ---------------------------------------------------------------- render loop

  invalidate(): void { this.dirty = true; }

  private resize(): void {
    const w = Math.max(1, this.host.clientWidth);
    const h = Math.max(1, this.host.clientHeight);
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    if (this.camera instanceof PerspectiveCamera) this.camera.aspect = w / h;
    else {
      const halfH = Math.max(1, Math.abs(this.camera.top - this.camera.bottom) / 2);
      this.camera.left = -halfH * (w / h); this.camera.right = halfH * (w / h);
      this.camera.top = halfH; this.camera.bottom = -halfH;
    }
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  private loop = (): void => {
    if (this.disposed) return;
    this.frame = requestAnimationFrame(this.loop);
    if (!this.dirty) return;
    this.dirty = false;
    this.renderer.render(this.scene, this.camera);
  };

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
    this.ro.disconnect();
    this.controls.dispose();
    this.clear();
    this.threadMap?.dispose();
    this.threadMap = null;
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
}
