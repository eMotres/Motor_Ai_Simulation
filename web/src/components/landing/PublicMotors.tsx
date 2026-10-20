/**
 * The public motor preview of the landing page: one card per PUBLIC catalogue motor with its
 * datasheet-level headline numbers (rated power, torque, speed, full mass, efficiency).
 *
 * Source: `GET /api/public/motors` (src/motor_ai_sim/public_motors.py) - the motors a brand-new
 * account may open at once, an allow-list of numbers, cached 5 minutes, rate-limited per IP.  The
 * list is never typed in here.  Nothing about the construction is on this page: no geometry, no
 * cross-section drawing (the catalogue's thumbnails ARE the geometry, so a card shows the
 * application icon instead), no winding, materials or CAD data.
 *
 * "Open in Configure - free account" remembers the motor (lib/landingMotors.ts) and starts the
 * same sign-up as the hero button; after sign-in AuthContext hands the choice to the app's own
 * last-motor restore, so Configure opens on it (lib/landingMotorApply.ts).
 *
 * The section is absent while loading and when the list is empty or the request fails: a visitor
 * never sees a spinner, an error or an empty frame.
 */
import React, { useEffect, useState } from 'react';
import { Box, Button, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { headlineRows, parsePublicMotors, rememberLandingMotor, type PublicMotor }
  from '../../lib/landingMotors';
import { DroneIcon, RobotIcon } from './landingIcons';
import { useSignUp } from './landingSignUp';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

const cardSx = {
  borderRadius: 2, border: '1px solid', borderColor: 'divider', bgcolor: 'background.paper',
  p: 2.25, display: 'flex', flexDirection: 'column', gap: 1.25, textAlign: 'left',
  borderTop: '3px solid', borderTopColor: 'secondary.main',
} as const;

const AppIcon: React.FC<{ application: string | null }> = ({ application }) => {
  if (application === 'robot') return <RobotIcon />;
  if (application === 'propeller' || application === 'aviation') return <DroneIcon />;
  return null;
};

const MotorCard: React.FC<{ motor: PublicMotor; onOpen: (m: PublicMotor) => void }> = ({ motor, onOpen }) => {
  const { t } = useTranslation('landing');
  const app = motor.application;
  const appLabel = app ? t(`motors.app.${app}`, { defaultValue: app }) : null;
  return (
    <Box component="li" data-testid="public-motor" sx={cardSx}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
        <Box sx={{ flexShrink: 0, '& svg': { width: 36, height: 36 } }}><AppIcon application={app} /></Box>
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h3" sx={{ fontSize: '1.05rem', fontWeight: 700, lineHeight: 1.25 }}>
            {motor.die} <Box component="span" sx={{ color: 'primary.main' }}>{motor.configuration}</Box>
          </Typography>
          {appLabel && (
            <Typography component="p" sx={{ fontSize: '0.85rem', color: 'text.secondary', m: 0 }}>
              {appLabel}
            </Typography>
          )}
        </Box>
      </Box>
      <Tooltip title={t('motors.ratedHint')} arrow>
        <Box component="dl" sx={{
          m: 0, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 0.75, columnGap: 2,
        }}>
          {headlineRows(motor).map((row) => {
            const partial = row.key === 'mass' && motor.rated.mass_is_partial;
            const cell = (
              <Box key={row.key} sx={{ minWidth: 0 }}>
                <Typography component="dt" sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
                  {t(`motors.label.${row.key}`)}
                </Typography>
                <Typography component="dd" sx={{ m: 0, fontSize: '1.02rem', fontWeight: 700 }}>
                  {partial ? '≈ ' : ''}{row.value}{' '}
                  <Box component="span" sx={{ fontSize: '0.78rem', fontWeight: 500, color: 'text.secondary' }}>
                    {row.unit}
                  </Box>
                </Typography>
              </Box>
            );
            return partial
              ? <Tooltip key={row.key} title={t('motors.massPartialHint')} arrow>{cell}</Tooltip>
              : cell;
          })}
        </Box>
      </Tooltip>
      <Tooltip title={t('motors.openHint')} arrow>
        <Button variant="outlined" onClick={() => onOpen(motor)} data-testid="public-motor-open"
          sx={{ textTransform: 'none', fontWeight: 600, mt: 'auto', borderRadius: 2 }}>
          {t('motors.open')}
        </Button>
      </Tooltip>
    </Box>
  );
};

const PublicMotors: React.FC = () => {
  const { t } = useTranslation('landing');
  const signUp = useSignUp();
  const [motors, setMotors] = useState<PublicMotor[]>([]);

  useEffect(() => {
    let alive = true;
    const ctl = typeof AbortController === 'undefined' ? null : new AbortController();
    fetch(`${API}/api/public/motors`, { headers: { Accept: 'application/json' }, signal: ctl?.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive) setMotors(parsePublicMotors(j)); })
      .catch(() => { /* the preview is a convenience: no list, no section */ });
    return () => { alive = false; ctl?.abort(); };
  }, []);

  if (motors.length === 0) return null;

  const open = (m: PublicMotor) => {
    rememberLandingMotor({ die: m.die, config: m.configuration });
    signUp.primary();
  };

  return (
    <Box component="section" aria-labelledby="motors-h" data-testid="public-motors"
      sx={{ mt: { xs: 5, sm: 7 } }}>
      <Typography component="h2" id="motors-h" sx={{
        fontSize: { xs: '1.5rem', sm: '2rem' }, fontWeight: 700, letterSpacing: '-0.01em',
        textAlign: 'center', mb: 0.75,
      }}>
        {t('motors.title')}
      </Typography>
      <Typography component="p" sx={{
        textAlign: 'center', color: 'text.secondary', maxWidth: 720, mx: 'auto', mb: 3.5,
        fontSize: { xs: '0.95rem', sm: '1.02rem' },
      }}>
        {t('motors.sub')}
      </Typography>
      <Box component="ul" sx={{
        listStyle: 'none', m: 0, p: 0, display: 'grid', gap: 2,
        gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', md: 'repeat(auto-fit, minmax(230px, 1fr))' },
      }}>
        {motors.map((m) => <MotorCard key={`${m.die}/${m.configuration}`} motor={m} onOpen={open} />)}
      </Box>
    </Box>
  );
};

export default PublicMotors;
