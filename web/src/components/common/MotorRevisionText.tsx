/**
 * "· Rev A" next to a motor's name (owner 2026-10-27): plain text in the style of the row it sits in, no chip, no colour.
 * Renders nothing for a motor with no product revision.  Used by the Motors catalogue rows, the project variant header and the
 * production strip (which gets its letter from the production itself, see `RevisionLabel`).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { useMotorRevision } from '../../stores/motorRevisionsStore';

/** The letter as a text: "Rev A" (EN) / "版本 A" (ZH). */
export const RevisionLabel: React.FC<{ rev: string | null | undefined; separator?: boolean }> = ({ rev, separator = true }) => {
  const { t } = useTranslation('common');
  if (!rev) return null;
  return <span data-testid="motor-rev" data-rev={rev}>{separator ? ' · ' : ''}{t('revisions.label', { rev })}</span>;
};

const MotorRevisionText: React.FC<{ die: string; config: string; canonicalDie?: string | null }> = ({ die, config, canonicalDie }) => {
  const row = useMotorRevision({ die, config, canonical_die: canonicalDie ?? null });
  return <RevisionLabel rev={row?.rev} />;
};

export default MotorRevisionText;
