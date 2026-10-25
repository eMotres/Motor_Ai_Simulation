import { useEffect, useState } from 'react';
import { TextField, Tooltip } from '@mui/material';

/** Max length of a design-step note; mirrors the server (`MAX_STEP_NOTE`). */
export const MAX_STEP_NOTE = 160;

/** The short design-step note of a linked motor ("baseline", "optimised", ...). Saved when the field loses focus or on Enter.
 *  Rendered only for people who may edit it (project engineer/owner/admin); the server enforces the same rule. */
export function MotorNoteField({ value, disabled, t, onSave, width = 220 }: { value: string; disabled?: boolean; t: (key: string) => string; onSave: (note: string) => Promise<boolean>; width?: number }) {
  const [text, setText] = useState(value);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setText(value); setFailed(false); }, [value]);
  const commit = async () => {
    const clean = text.replace(/\s+/g, ' ').trim();
    if (clean === value.trim()) { setText(value); return; }
    const ok = await onSave(clean);
    setFailed(!ok);
    if (!ok) setText(value);
  };
  return <Tooltip title={t('stepNote.tip')}>
    <TextField size="small" value={text} disabled={disabled} error={failed}
      placeholder={t('stepNote.placeholder')} aria-label={t('stepNote.label')}
      inputProps={{ maxLength: MAX_STEP_NOTE }}
      onChange={(e) => setText(e.target.value)} onBlur={() => void commit()}
      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur(); } }}
      sx={{ width, '& input': { py: 0.4, fontSize: 11 } }} />
  </Tooltip>;
}
