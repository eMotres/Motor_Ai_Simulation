// three.js viewer for the "3D model" tab: orbit / zoom, a section plane, an exploded view, part toggles, a point-to-point
// measure tool.  Plain three (not react-three-fiber): the panel needs an imperative handle and renders on demand.
//
// The GLB is in the CAD frame (mm, +Z = motor axis, +Z = propeller side); the camera is Z-up.  The GLB nodes are one
// group per part with `extras` {family, explode} (cad/fc_scripts/glb_writer.py); the viewer never shows more than the file
// contains - what a caller may see is decided on the server.
import {
  AmbientLight, Box3, BufferGeometry, Color, DirectionalLight, DoubleSide, Float32BufferAttribute, Group,
  HemisphereLight, Line, LineBasicMaterial, Material, Mesh, MeshBasicMaterial, Object3D, PerspectiveCamera, Plane,
  Raycaster, Scene, SphereGeometry, Vector2, Vector3, WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import {
  distance, explodeShift, sectionPlane, sectionRange, type Axis, type Box, type PartGroup, type Vec3,
} from '../../lib/model3d';

export interface ViewerState {
  hidden: ReadonlySet<string>;
  /** GLB node names hidden individually (a part switched off in THE part tree) */
  hiddenNodes?: ReadonlySet<string>;
  /** GLB node names to highlight (a part selected in THE part tree); the rest is dimmed.  Empty / absent = no highlight. */
  highlight?: ReadonlySet<string>;
  explode: number;             // 0..1
  section: { on: boolean; axis: Axis; position: number };
  measure: boolean;
}

export class ModelViewer {
  readonly renderer: WebGLRenderer;
  readonly scene = new Scene();
  readonly camera: PerspectiveCamera;
  readonly controls: OrbitControls;
  private root: Object3D | null = null;
  private groups: Array<{ node: Object3D; part: PartGroup }> = [];
  private plane = new Plane(new Vector3(0, 0, -1), 0);
  private ray = new Raycaster();
  private marks = new Group();
  private picks: Vector3[] = [];
  private frame = 0;
  private dirty = true;
  private ro: ResizeObserver;
  private disposed = false;
  private down = new Vector2();
  box: Box = { min: [-1, -1, -1], max: [1, 1, 1] };
  motorLength = 12;
  onMeasure: (mm: number | null) => void = () => {};
  onContextLost: () => void = () => {};

  private host: HTMLElement;

  constructor(host: HTMLElement) {
    this.host = host;
    this.renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'default' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.localClippingEnabled = true;
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = 'block';
    this.camera = new PerspectiveCamera(35, 1, 0.1, 5000);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.addEventListener('change', () => this.invalidate());
    this.scene.add(new HemisphereLight(0xffffff, 0x445066, 1.15), new AmbientLight(0xffffff, 0.35));
    const key = new DirectionalLight(0xffffff, 1.6); key.position.set(60, -80, 90);
    const fill = new DirectionalLight(0xffffff, 0.6); fill.position.set(-70, 60, -40);
    this.scene.add(key, fill, this.marks);
    const el = this.renderer.domElement;
    el.addEventListener('pointerdown', (e) => this.down.set(e.clientX, e.clientY));
    el.addEventListener('pointerup', (e) => this.onPointerUp(e));
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
    const gltf = await new GLTFLoader().parseAsync(buffer, '');
    this.clear();
    this.root = gltf.scene;
    this.scene.add(gltf.scene);
    const model = gltf.scene.getObjectByName('Model') ?? gltf.scene;
    for (const node of model.children) {
      const ex = (node.userData ?? {}) as { family?: string; explode?: number };
      this.groups.push({ node, part: { name: node.name, family: ex.family ?? node.name, explode: Number(ex.explode ?? 0) } });
    }
    this.root.traverse((o) => {
      const m = o as Mesh;
      if (m.isMesh) {
        const mats = Array.isArray(m.material) ? m.material : [m.material];
        for (const mt of mats) { (mt as Material).side = DoubleSide; (mt as Material).clippingPlanes = [this.plane]; }
      }
    });
    const b = new Box3().setFromObject(this.root);
    this.box = { min: [b.min.x, b.min.y, b.min.z], max: [b.max.x, b.max.y, b.max.z] };
    const core = this.groups.find((g) => g.part.name === 'Core' || g.part.name === 'Housing');
    const cb = core ? new Box3().setFromObject(core.node) : b;
    this.motorLength = Math.max(1, cb.max.z - cb.min.z);
    this.fit();
    return { groups: this.groups.map((g) => g.part), box: this.box };
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
    this.clearMarks();
  }

  // ---------------------------------------------------------------- state

  apply(s: ViewerState): void {
    for (const g of this.groups) {
      g.node.visible = !s.hidden.has(g.part.family) && !s.hiddenNodes?.has(g.part.name);
      g.node.position.z = explodeShift(g.part.explode, this.motorLength, s.explode);
    }
    this.applyHighlight(s.highlight);
    const p = sectionPlane(s.section.axis, s.section.position);
    this.plane.normal.set(...p.normal);
    this.plane.constant = s.section.on ? p.constant : 1e9;      // a plane far outside the model clips nothing
    if (!s.measure) this.clearMarks();
    this.measuring = s.measure;
    this.invalidate();
  }

  private measuring = false;

  /** Highlight the nodes in `names` (selected part): they glow, every other node is dimmed; an empty set restores the model. */
  private applyHighlight(names: ReadonlySet<string> | undefined): void {
    const on = !!names && names.size > 0;
    for (const g of this.groups) {
      const hit = on && names!.has(g.part.name);
      g.node.traverse((o) => {
        const m = o as Mesh;
        if (!m.isMesh) return;
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
    const c = new Vector3((this.box.min[0] + this.box.max[0]) / 2, (this.box.min[1] + this.box.max[1]) / 2, (this.box.min[2] + this.box.max[2]) / 2);
    const r = Math.max(1, Math.hypot(this.box.max[0] - this.box.min[0], this.box.max[1] - this.box.min[1], this.box.max[2] - this.box.min[2]) / 2);
    const d = r / Math.sin((this.camera.fov * Math.PI) / 360);
    this.camera.position.copy(c).add(new Vector3(0.75, -1, 0.7).normalize().multiplyScalar(d));
    this.camera.near = d / 200;
    this.camera.far = d * 20;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(c);
    this.controls.update();
    this.invalidate();
  }

  // ---------------------------------------------------------------- measure

  private onPointerUp(e: PointerEvent): void {
    if (!this.measuring || !this.root || Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 4) return;   // a drag is an orbit
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ray.setFromCamera(new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camera);
    const hits = this.ray.intersectObjects(this.root.children, true).filter((h) => {
      let o: Object3D | null = h.object;
      while (o) { if (!o.visible) return false; o = o.parent; }
      return this.plane.distanceToPoint(h.point) >= 0 || this.plane.constant > 1e8;      // not on the clipped-away half
    });
    if (!hits.length) return;
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
    this.camera.aspect = w / h;
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
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
}
