/** The cover image of a project (the board's top view, a render, a photo): a small WebP thumbnail made by the server.
 *  Nothing is rendered while the project has no cover or the image cannot be loaded (the card keeps its usual layout). */
import { useEffect, useState } from 'react';
import { Box } from '@mui/material';
import { loadProjectCover } from '../api';

interface Props {
  projectId: string;
  /** the cache key from the project summary (`cover_version`); null/undefined = no cover */
  version?: string | null;
  height: number;
  alt?: string;
  sx?: Record<string, unknown>;
}

export default function ProjectCover({ projectId, version, height, alt = '', sx }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    setUrl(null);
    if (!version) return;
    let cancelled = false;
    void loadProjectCover(projectId, version).then((u) => { if (!cancelled) setUrl(u); }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId, version]);
  if (!version || !url) return null;
  return <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height, bgcolor: 'var(--panel)', borderRadius: 1, overflow: 'hidden', ...sx }}>
    <Box component="img" src={url} alt={alt} loading="lazy" sx={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
  </Box>;
}
