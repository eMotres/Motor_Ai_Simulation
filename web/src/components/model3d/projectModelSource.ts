// Which source the project's 3D tab opens on (owner 2026-11-23: "in projects there will be the real model, not the simplified one").

export type ModelSource = 'generated' | 'uploaded';

/** Checked against what exists (Astra on #835): a controller project has no motor to generate from, so Uploaded always.  A choice
 *  the user makes NOW wins (staff open Uploaded to add the first model).  With no live upload the tab opens on Generated, whatever
 *  was remembered; otherwise the choice remembered for THIS project, else Uploaded. */
export function openingSource(o: { controller: boolean; pickedNow?: ModelSource | null; remembered: ModelSource | null; liveUploads: number }): ModelSource {
  if (o.controller) return 'uploaded';
  if (o.pickedNow) return o.pickedNow;
  if (o.liveUploads === 0) return 'generated';
  return o.remembered ?? 'uploaded';
}

/** The remembered choice is per project, so one project's choice never opens another project on the wrong source. */
export const sourceScope = (projectId: string) => `project.model3d.source.${projectId}`;
