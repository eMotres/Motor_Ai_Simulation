// Free orbit for the 3D model viewer (owner 2026-10-17: the trackball of #242 felt erratic).  CAD-style turning:
//   left drag   - horizontal motion turns the model about the SCREEN's vertical axis, vertical motion about the screen's
//                 horizontal axis; no pole stop (unlike OrbitControls) and no roll that depends on where the cursor is
//                 (unlike TrackballControls); a drag across the view height turns 180 degrees
//   right / middle drag, or shift + left drag - pan
//   wheel       - zoom
// The camera moves around `target`; nothing runs between drags (the viewer renders on demand from 'change').
import { EventDispatcher, OrthographicCamera, PerspectiveCamera, Quaternion, Vector3 } from 'three';

type Events = { change: object; start: object; end: object };

export class FreeOrbitControls extends EventDispatcher<Events> {
  readonly target = new Vector3();
  enabled = true;
  rotateSpeed = 1.0;
  zoomSpeed = 1.0;
  private mode: 'rotate' | 'pan' | null = null;
  private lastX = 0;
  private lastY = 0;
  private pointerId = -1;

  object: PerspectiveCamera | OrthographicCamera;
  private readonly dom: HTMLElement;

  constructor(object: PerspectiveCamera | OrthographicCamera, dom: HTMLElement) {
    super();
    this.object = object;
    this.dom = dom;
    dom.addEventListener('pointerdown', this.onDown);
    dom.addEventListener('pointermove', this.onMove);
    dom.addEventListener('pointerup', this.onUp);
    dom.addEventListener('pointercancel', this.onUp);
    dom.addEventListener('wheel', this.onWheel, { passive: false });
    dom.addEventListener('contextmenu', this.onContextMenu);
  }

  /** Look at the target again after the camera or the target was set from outside. */
  update(): void {
    this.object.lookAt(this.target);
  }

  handleResize(): void { /* nothing cached */ }

  dispose(): void {
    this.dom.removeEventListener('pointerdown', this.onDown);
    this.dom.removeEventListener('pointermove', this.onMove);
    this.dom.removeEventListener('pointerup', this.onUp);
    this.dom.removeEventListener('pointercancel', this.onUp);
    this.dom.removeEventListener('wheel', this.onWheel);
    this.dom.removeEventListener('contextmenu', this.onContextMenu);
  }

  /** Screen axes of the current view: [right, up], both unit and orthogonal to the view direction. */
  private axes(): [Vector3, Vector3] {
    const forward = this.target.clone().sub(this.object.position).normalize();
    let up = this.object.up.clone().normalize();
    let right = new Vector3().crossVectors(forward, up);
    if (right.lengthSq() < 1e-12) {                       // up parallel to the view: take any perpendicular
      up = Math.abs(forward.z) < 0.9 ? new Vector3(0, 0, 1) : new Vector3(0, 1, 0);
      right = new Vector3().crossVectors(forward, up);
    }
    right.normalize();
    up = new Vector3().crossVectors(right, forward).normalize();
    return [right, up];
  }

  /** Turn the view by a screen drag of (dx, dy) pixels. */
  rotateBy(dx: number, dy: number): void {
    const h = Math.max(1, this.dom.clientHeight);
    const k = (Math.PI / h) * this.rotateSpeed;
    const [right, up] = this.axes();
    const q = new Quaternion().setFromAxisAngle(up, -dx * k)
      .multiply(new Quaternion().setFromAxisAngle(right, -dy * k));
    const offset = this.object.position.clone().sub(this.target).applyQuaternion(q);
    this.object.up.copy(up.applyQuaternion(q));
    this.object.position.copy(this.target).add(offset);
    this.object.lookAt(this.target);
    this.dispatchEvent({ type: 'change' });
  }

  /** Move the view by a screen drag of (dx, dy) pixels (the point under the cursor follows it). */
  panBy(dx: number, dy: number): void {
    const h = Math.max(1, this.dom.clientHeight);
    const cam = this.object;
    const worldPerPixel = cam instanceof OrthographicCamera
      ? (cam.top - cam.bottom) / cam.zoom / h
      : 2 * cam.position.distanceTo(this.target) * Math.tan((cam.fov * Math.PI) / 360) / h;
    const [right, up] = this.axes();
    const move = right.multiplyScalar(-dx * worldPerPixel).add(up.multiplyScalar(dy * worldPerPixel));
    cam.position.add(move);
    this.target.add(move);
    this.dispatchEvent({ type: 'change' });
  }

  /** Zoom by a wheel delta (positive = out). */
  zoomBy(deltaY: number): void {
    const f = Math.exp(-deltaY * 0.0015 * this.zoomSpeed);
    const cam = this.object;
    if (cam instanceof OrthographicCamera) {
      cam.zoom = Math.min(200, Math.max(0.02, cam.zoom * f));
      cam.updateProjectionMatrix();
    } else {
      const offset = cam.position.clone().sub(this.target);
      const d = Math.min(4000, Math.max(0.5, offset.length() / f));
      cam.position.copy(this.target).add(offset.setLength(d));
    }
    this.dispatchEvent({ type: 'change' });
  }

  private onDown = (e: PointerEvent): void => {
    if (!this.enabled || this.mode) return;
    this.mode = e.button === 0 && !e.shiftKey ? 'rotate' : (e.button === 0 || e.button === 1 || e.button === 2) ? 'pan' : null;
    if (!this.mode) return;
    this.pointerId = e.pointerId;
    this.lastX = e.clientX;
    this.lastY = e.clientY;
    try { this.dom.setPointerCapture(e.pointerId); } catch { /* not capturable */ }
    this.dispatchEvent({ type: 'start' });
  };

  private onMove = (e: PointerEvent): void => {
    if (!this.mode || e.pointerId !== this.pointerId) return;
    const dx = e.clientX - this.lastX;
    const dy = e.clientY - this.lastY;
    this.lastX = e.clientX;
    this.lastY = e.clientY;
    if (dx === 0 && dy === 0) return;
    if (this.mode === 'rotate') this.rotateBy(dx, dy);
    else this.panBy(dx, dy);
  };

  private onUp = (e: PointerEvent): void => {
    if (!this.mode || e.pointerId !== this.pointerId) return;
    this.mode = null;
    try { this.dom.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    this.dispatchEvent({ type: 'end' });
  };

  private onWheel = (e: WheelEvent): void => {
    if (!this.enabled) return;
    e.preventDefault();
    this.zoomBy(e.deltaY);
  };

  private onContextMenu = (e: Event): void => { e.preventDefault(); };
}
