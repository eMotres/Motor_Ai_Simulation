export interface MigrationPreviewView {
  source_revision: number;
  source_sha256: string;
  requires_acknowledgement: boolean;
  draft: Record<string, unknown>;
  issues: Array<{ code: string; message?: string }>;
}
export interface S1Candidate { torque_Nm: number; speed_rpm: number; shaft_power_W: number }

/** Format only the acknowledged known S1 point; retained values on unknown answers are never presented as a candidate. */
export function migrationS1Candidate(preview: MigrationPreviewView | null): S1Candidate | null {
  const fields = preview?.draft.fields;
  const answer = fields && typeof fields === 'object' ? (fields as Record<string, { status?: string; value?: unknown }>)['1_4'] : undefined;
  if (!answer || answer.status !== 'filled' || !answer.value || typeof answer.value !== 'object') return null;
  const value = answer.value as Record<string, unknown>;
  const torque = value.torque_Nm, speed = value.speed_rpm;
  if (typeof torque !== 'number' || typeof speed !== 'number' || !Number.isFinite(torque) || !Number.isFinite(speed)) return null;
  return { torque_Nm: torque, speed_rpm: speed, shaft_power_W: torque * 2 * Math.PI * speed / 60 };
}

/** Client guard complements the server's hash/revision lock; it never mutates or migrates a draft. */
export function canApplyMigrationPreview(preview: MigrationPreviewView | null, currentRevision: number, acknowledged: boolean): boolean {
  return !!preview && preview.source_revision === currentRevision && (!preview.requires_acknowledgement || acknowledged);
}
