// Which source the project's 3D tab opens on (owner 2026-11-23: "in projects there will be the real model, not the simplified one").

export type ModelSource = 'generated' | 'uploaded';

/** A controller project has no motor to generate from: Uploaded only.  Otherwise the user's own last choice in THIS project, else
 *  Uploaded as soon as one uploaded model exists, else Generated. */
export function openingSource(o: { controller: boolean; remembered: ModelSource | null; liveUploads: number }): ModelSource {
  if (o.controller) return 'uploaded';
  if (o.remembered) return o.remembered;
  return o.liveUploads > 0 ? 'uploaded' : 'generated';
}

/** The remembered choice is per project, so one project's choice never opens another project on the wrong source. */
export const sourceScope = (projectId: string) => `project.model3d.source.${projectId}`;
