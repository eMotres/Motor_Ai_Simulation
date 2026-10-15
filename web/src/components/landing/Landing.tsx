/**
 * The public first page of aerostator.com: the whole project concept in one
 * scroll (owner 2026-10-14: "put the whole concept on the first page as graphs
 * and pictures, so that everything is clear").
 *
 * Order of importance: what it is and the slogan, the concept diagram (idea to
 * product around the AeroStator Core machine, the open ecosystem as the outer
 * ring), the simulation pictures, why it is open and free, applications, how to
 * start.  Sign in and Request access are at the top and again at the end.
 *
 * WHAT IT MAY TOUCH.  Nothing.  The page renders from static props, inline SVG
 * and the three WebPs in `public/landing/`; the only network the page can cause
 * is the header's own `/api/me` + `/api/version` and, when the visitor presses
 * the button, the login endpoints (every other `/api/*` route answers 401 to an
 * anonymous caller, and a landing that fires them is a console full of red).
 *
 * ONE AUTH PATH.  The button calls `useAuth().signIn()`, which opens the same
 * `LoginDialog` the header's Sign in button opens; this component never loads
 * GIS itself and only *labels* the button after the client id.
 *
 * THE PICTURES ARE OURS.  `scripts/landing_images.py` redraws them from the
 * stored per-duty fields (`--check` says whether the committed bytes match):
 *   em-field.webp           report._em_maps(...)["b"]
 *   thermal-map.webp        report._thermal_map(...)
 *   rotor-displacement.webp report._mech_extra_maps(...)["disp"]
 * all three off one machine at one operating point, the whole 360 degree ring,
 * drawn on white (the report's own ground) and shown on a white tile in both
 * themes.  No new physics images are generated for this page.
 *
 * PUBLIC-SAFE ONLY.  The technology is "patent pending" (never "patented"), no
 * application numbers, and nothing about unpublished design details.  The texts
 * live in the `landing` locale namespace (EN source, full zh-CN mirror).
 */
import React from 'react';
import { Box, Button, Link, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { GOOGLE_CLIENT_ID } from '../../lib/localAuth';
import { useAuth } from '../../contexts/AuthContext';
import LanguageSwitcher from '../common/LanguageSwitcher';
import BrandLogo from '../common/BrandLogo';
import CookieSettingsLink from '../common/CookieSettingsLink';
import ConceptDiagram from './ConceptDiagram';
import { STEP_IDS } from './conceptLayout';
import { BoatIcon, DroneIcon, PlusIcon, RobotIcon } from './landingIcons';
import { APP_IDS, CHAIN_IDS, DEV_LINKS, OPEN_IDS } from './landingLinks';

/** Where a visitor without an account writes. */
export const REQUEST_ACCESS_EMAIL = 'support@aerostator.com';

/** The company line and the shop, in the footer. */
export const COMPANY = 'MOTRES d.o.o.';
export const SHOP_URL = 'https://aerostator.com';

export interface Feature {
  /** Key under `sim.` in the `landing` namespace (label, `<key>Alt`, `<key>Hint`). */
  key: 'em' | 'thermal' | 'mech';
  /** Path under `public/`, served from the site root. */
  src: string;
  /** Pixel size of the file (the `<img>` carries it, so the card does not shift while it loads). */
  w: number;
  h: number;
}

/** The three simulation pictures, in order.  Exported so the test can check them without a DOM. */
export const FEATURES: Feature[] = [
  { key: 'em', src: '/landing/em-field.webp', w: 884, h: 766 },
  { key: 'thermal', src: '/landing/thermal-map.webp', w: 884, h: 767 },
  { key: 'mech', src: '/landing/rotor-displacement.webp', w: 884, h: 767 },
];

/** The button's words.  Only a deployment that HAS a Google client id may
 *  promise Google; everywhere else the same dialog offers a password. */
export function signInLabel(
  clientId: string | undefined | null,
  labels: { google: string; plain: string } = { google: 'Sign in with Google', plain: 'Sign in' },
): string {
  return clientId ? labels.google : labels.plain;
}

/** Is this the visitor the landing is for?  The signed-out half of App's
 *  `signedIn = !enforced || !!user`: an unenforced backend (local dev) is
 *  never shown the landing, so the dev server boots exactly as it did. */
export function shouldShowLanding(enforced: boolean, hasUser: boolean): boolean {
  return enforced && !hasUser;
}

const mailto = (subject: string) =>
  `mailto:${REQUEST_ACCESS_EMAIL}?subject=${encodeURIComponent(subject)}`;

/** Google's mark, on the button that opens OUR dialog.  Decoration only. */
const GoogleMark: React.FC = () => (
  <Box component="svg" viewBox="0 0 48 48" width={17} height={17}
    aria-hidden="true" focusable="false">
    <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6.1C12.3 13.2 17.6 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.2-.4-4.6H24v9.1h12.4c-.5 2.9-2.1 5.3-4.6 7l7.6 5.9c4.4-4.1 6.7-10.1 6.7-17.4z" />
    <path fill="#FBBC05" d="M10.4 28.7c-.5-1.5-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C.9 16.3 0 20 0 24s.9 7.7 2.6 10.8l7.8-6.1z" />
    <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.8 2.3-8.3 2.3-6.4 0-11.7-3.7-13.6-8.9l-7.8 6.1C6.5 42.6 14.6 48 24 48z" />
  </Box>
);

/** Sign in + Request access: at the top and again at the end.
 *  E-mail is a first-class method next to Google (Google is blocked in mainland
 *  China): "Request access" opens the create-account form (confirmation link by
 *  e-mail, admin approval when mail is down) instead of a mailto: that does nothing
 *  inside WeChat's browser. */
const AuthButtons: React.FC = () => {
  const { t } = useTranslation('landing');
  const { signIn } = useAuth();
  const btn = { textTransform: 'none', fontSize: '1rem', fontWeight: 600, px: 3.5, py: 1.25,
    borderRadius: 2 } as const;
  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 1.5 }}>
      {GOOGLE_CLIENT_ID ? (
        <>
          <Button variant="contained" size="large"
            onClick={() => { void signIn({ tab: 'google' }).catch(() => {}); }}
            startIcon={<GoogleMark />} sx={btn}>
            {signInLabel(GOOGLE_CLIENT_ID, { google: t('hero.signInGoogle'), plain: t('hero.signIn') })}
          </Button>
          <Button variant="outlined" size="large"
            onClick={() => { void signIn({ tab: 'email', mode: 'signin' }).catch(() => {}); }}
            sx={btn}>
            {t('hero.signInEmail')}
          </Button>
        </>
      ) : (
        <Button variant="contained" size="large"
          onClick={() => { void signIn({ tab: 'email', mode: 'signin' }).catch(() => {}); }}
          sx={btn}>
          {signInLabel(GOOGLE_CLIENT_ID, { google: t('hero.signInGoogle'), plain: t('hero.signIn') })}
        </Button>
      )}
      <Button variant="outlined" size="large"
        onClick={() => { void signIn({ tab: 'email', mode: 'register' }).catch(() => {}); }}
        sx={btn}>
        {t('hero.request')}
      </Button>
    </Box>
  );
};

/** The slogan's arrow: idea, the five steps, product.  Same order and colours as the diagram. */
const HeroChain: React.FC = () => {
  const { t } = useTranslation('landing');
  return (
    <Box component="ol" aria-label={t('chain.label')} sx={{
      listStyle: 'none', m: 0, p: 0, display: 'flex', flexWrap: 'wrap',
      justifyContent: 'center', alignItems: 'center', gap: { xs: 0.75, sm: 1 },
    }}>
      {CHAIN_IDS.map((id, i) => {
        const end = id === 'idea' || id === 'product';
        return (
          <Box component="li" key={id} sx={{ display: 'flex', alignItems: 'center', gap: { xs: 0.75, sm: 1 } }}>
            <Box component="span" sx={{
              px: { xs: 1.1, md: 1.5 }, py: 0.5, borderRadius: 99,
              fontSize: { xs: '0.78rem', sm: '0.82rem', md: '0.9rem' },
              fontWeight: 600, border: '1.5px solid', borderColor: 'primary.main',
              ...(end
                ? { bgcolor: 'primary.main', color: '#fff' }
                : { color: 'text.primary', bgcolor: 'background.paper' }),
            }}>
              {t(`chain.${id}`)}
            </Box>
            {i < CHAIN_IDS.length - 1 && (
              <Box component="svg" viewBox="0 0 16 10" width={16} height={10} aria-hidden="true"
                focusable="false" sx={{ color: 'primary.main', flexShrink: 0 }}>
                <path d="M0 5h13M9 1l5 4-5 4" fill="none" stroke="currentColor" strokeWidth="2"
                  strokeLinecap="round" strokeLinejoin="round" />
              </Box>
            )}
          </Box>
        );
      })}
    </Box>
  );
};

const Section: React.FC<{ id: string; title: string; sub?: string; children: React.ReactNode }> = ({
  id, title, sub, children,
}) => (
  <Box component="section" aria-labelledby={`${id}-h`} sx={{ mt: { xs: 6, sm: 9 } }}>
    <Typography component="h2" id={`${id}-h`} sx={{
      fontSize: { xs: '1.5rem', sm: '2rem' }, fontWeight: 700, letterSpacing: '-0.01em',
      textAlign: 'center', mb: sub ? 0.75 : 3,
    }}>
      {title}
    </Typography>
    {sub && (
      <Typography component="p" sx={{
        textAlign: 'center', color: 'text.secondary', maxWidth: 720, mx: 'auto',
        mb: 3.5, fontSize: { xs: '0.95rem', sm: '1.02rem' },
      }}>
        {sub}
      </Typography>
    )}
    {children}
  </Box>
);

const cardSx = {
  borderRadius: 2, border: '1px solid', borderColor: 'divider', bgcolor: 'background.paper',
  p: 2.25, display: 'flex', flexDirection: 'column', gap: 1, textAlign: 'left',
  // the green top accent on EVERY card of the page (owner 2026-10-15)
  borderTop: '3px solid', borderTopColor: 'secondary.main',
} as const;

const PictureCard: React.FC<{ feature: Feature }> = ({ feature }) => {
  const { t } = useTranslation('landing');
  return (
    <Tooltip title={t(`sim.${feature.key}Hint`)} arrow>
      <Box sx={{
        borderRadius: 2, border: '1px solid', borderColor: 'divider',
        borderTop: '3px solid', borderTopColor: 'secondary.main',
        bgcolor: 'background.paper', overflow: 'hidden', display: 'flex',
        flexDirection: 'column',
      }}>
        {/* The figure keeps the report's own white ground in both themes.  All
            three are the same cut of the machine (the whole ring), so 1.25:1
            is a box they all nearly fill.  `minHeight: 0` or a picture taller
            than the ratio pushes its box past it. */}
        <Box sx={{ bgcolor: '#ffffff', aspectRatio: '1.25 / 1', p: 0.75, minHeight: 0,
          overflow: 'hidden' }}>
          <Box component="img" src={feature.src} alt={t(`sim.${feature.key}Alt`)} width={feature.w} height={feature.h}
            loading="lazy" decoding="async" fetchPriority="low"
            sx={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }} />
        </Box>
        <Typography component="h3" sx={{
          px: 1.75, py: 1.25, fontSize: '0.95rem', fontWeight: 600,
          borderTop: '1px solid', borderColor: 'divider',
        }}>
          {t(`sim.${feature.key}`)}
        </Typography>
      </Box>
    </Tooltip>
  );
};

const APP_ICONS: Record<(typeof APP_IDS)[number], React.ReactNode> = {
  aviation: <DroneIcon />, robotics: <RobotIcon />, marine: <BoatIcon />, yours: <PlusIcon />,
};

const Landing: React.FC = () => {
  const { t } = useTranslation('landing');

  return (
    <Box component="main" sx={{
      // A flex child of the app's column shell (header above it): it takes the
      // rest of the window and scrolls inside itself.
      flex: '1 1 auto', minHeight: 0, overflowY: 'auto', overflowX: 'hidden',
      bgcolor: 'background.default',
      backgroundImage: (th) =>
        `radial-gradient(1100px 420px at 50% -120px, ${th.palette.primary.main}22, transparent 70%)`,
    }}>
      <Box sx={{ maxWidth: 1200, mx: 'auto', px: { xs: 2, sm: 4 }, pt: { xs: 5, sm: 7 }, pb: 4,
        position: 'relative' }}>
        {/* Language, visible before sign-in (the first visit is auto-detected). */}
        <Box sx={{ position: 'absolute', top: { xs: 8, sm: 12 }, right: { xs: 16, sm: 32 } }}>
          <LanguageSwitcher variant="inline" />
        </Box>
        {/* ── Hero: brand, slogan, one line of what it is, the way in ── */}
        <Box sx={{ textAlign: 'center' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1.5,
            mb: { xs: 3, sm: 4 } }}>
            <BrandLogo size={56} />
            <Typography component="span" sx={{
              fontSize: { xs: '1.5rem', sm: '1.8rem' }, fontWeight: 700,
              letterSpacing: '-0.01em', lineHeight: 1,
            }}>
              AeroStator{' '}
              <Box component="span" sx={{ fontWeight: 300, opacity: 0.65 }}>Core</Box>
            </Typography>
          </Box>
          <Typography component="h1" sx={{
            fontSize: { xs: '2rem', sm: '3.2rem' }, fontWeight: 800,
            letterSpacing: '-0.025em', lineHeight: 1.12, mb: 1.75, maxWidth: 900, mx: 'auto',
          }}>
            {t('hero.title')}
          </Typography>
          <Typography component="h2" data-testid="hero-agents" sx={{
            fontSize: { xs: '1.2rem', sm: '1.7rem' }, fontWeight: 600, color: 'text.secondary',
            letterSpacing: '-0.015em', lineHeight: 1.2, mb: 1.75, maxWidth: 900, mx: 'auto',
          }}>
            {t('hero.agents')}{' '}
            <Link href="#developers" underline="hover" sx={{
              fontSize: '0.6em', fontWeight: 600, letterSpacing: 0, whiteSpace: 'nowrap',
              verticalAlign: 'middle',
            }}>
              {t('hero.agentsHow')} →
            </Link>
          </Typography>
          <Typography component="p" sx={{
            fontSize: { xs: '1rem', sm: '1.15rem' }, color: 'text.secondary',
            maxWidth: 720, mx: 'auto', mb: 3,
          }}>
            {t('hero.sub')}
          </Typography>
          <Box sx={{ mb: 3.5 }}><HeroChain /></Box>
          <AuthButtons />
        </Box>

        {/* ── The concept diagram (wide screens) / the same five steps as cards ── */}
        <Section id="concept" title={t('concept.title')} sub={t('concept.sub')}>
          <Box sx={{ display: 'none', '@media (min-width:700px)': { display: 'block' },
            maxWidth: 1100, mx: 'auto', mb: 4 }}
            data-testid="concept-diagram">
            <ConceptDiagram />
          </Box>
          <Box component="ol" sx={{
            listStyle: 'none', m: 0, p: 0, display: 'grid', gap: 2,
            gridTemplateColumns: {
              xs: '1fr', sm: 'repeat(2, 1fr)', md: 'repeat(5, 1fr)',
            },
          }}>
            {STEP_IDS.map((id, i) => (
              <Box component="li" key={id} sx={cardSx}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box component="span" aria-hidden="true" sx={{
                    width: 28, height: 28, borderRadius: '50%', bgcolor: 'primary.main',
                    color: '#fff', display: 'inline-flex', alignItems: 'center',
                    justifyContent: 'center', fontWeight: 700, fontSize: '0.9rem', flexShrink: 0,
                  }}>{i + 1}</Box>
                  <Typography component="h3" sx={{ fontSize: '1.05rem', fontWeight: 700 }}>
                    {t(`chain.${id}`)}
                  </Typography>
                </Box>
                <Typography component="p" sx={{ fontSize: '0.92rem', color: 'text.secondary' }}>
                  {t(`steps.${id}`)}
                </Typography>
              </Box>
            ))}
          </Box>
        </Section>

        {/* ── Step 2 in pictures: the simulation renders ── */}
        <Section id="sim" title={t('sim.title')} sub={t('sim.sub')}>
          <Box sx={{
            display: 'grid', gap: { xs: 2, sm: 2.5 },
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' },
          }}>
            {FEATURES.map((f) => <PictureCard key={f.key} feature={f} />)}
          </Box>
        </Section>

        {/* ── Open and free ── */}
        <Section id="open" title={t('open.title')} sub={t('open.sub')}>
          <Box sx={{
            display: 'grid', gap: 2,
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' },
          }}>
            {OPEN_IDS.map((id) => (
              <Box key={id} sx={cardSx}>
                <Typography component="h3" sx={{ fontSize: '1.05rem', fontWeight: 700 }}>
                  {t(`open.${id}.title`)}
                </Typography>
                <Typography component="p" sx={{ fontSize: '0.92rem', color: 'text.secondary' }}>
                  {t(`open.${id}.text`)}
                </Typography>
              </Box>
            ))}
          </Box>
        </Section>

        {/* ── Applications ── */}
        <Section id="apps" title={t('apps.title')} sub={t('apps.sub')}>
          <Box sx={{
            display: 'grid', gap: 2,
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' },
          }}>
            {APP_IDS.map((id) => (
              <Box key={id} sx={cardSx}>
                {APP_ICONS[id]}
                <Typography component="h3" sx={{ fontSize: '1.05rem', fontWeight: 700 }}>
                  {t(`apps.${id}.title`)}
                </Typography>
                <Typography component="p" sx={{ fontSize: '0.92rem', color: 'text.secondary' }}>
                  {t(`apps.${id}.text`)}
                </Typography>
              </Box>
            ))}
          </Box>
        </Section>

        {/* ── How to start ── */}
        <Section id="start" title={t('start.title')}>
          <Box sx={{
            display: 'grid', gap: 2,
            gridTemplateColumns: { xs: '1fr', md: 'repeat(3, 1fr)' },
          }}>
            <Box sx={cardSx}>
              <Typography component="h3" sx={{ fontSize: '1.05rem', fontWeight: 700 }}>
                {t('start.everyone.title')}
              </Typography>
              <Typography component="p" sx={{ fontSize: '0.92rem', color: 'text.secondary' }}>
                {t('start.everyone.text')}
              </Typography>
            </Box>
            <Box sx={cardSx}>
              <Typography component="h3" sx={{ fontSize: '1.05rem', fontWeight: 700 }}>
                {t('start.engineers.title')}
              </Typography>
              <Typography component="p" sx={{ fontSize: '0.92rem', color: 'text.secondary' }}>
                {t('start.engineers.text')}
              </Typography>
              <Link href={mailto('AeroStator Core engineer role')} underline="hover"
                sx={{ fontWeight: 600, fontSize: '0.92rem' }}>
                {t('start.engineers.link')}
              </Link>
            </Box>
            <Box id="developers" sx={{ ...cardSx, scrollMarginTop: 24 }}>
              <Typography component="h3" sx={{ fontSize: '1.05rem', fontWeight: 700 }}>
                {t('start.developers.title')}
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2.25, display: 'grid', gap: 0.5 }}>
                {DEV_LINKS.map((l) => (
                  <li key={l.id}>
                    <Link href={l.href} target="_blank" rel="noopener noreferrer" underline="hover"
                      sx={{ fontWeight: 600, fontSize: '0.92rem' }}>
                      {t(`start.links.${l.id}`)}
                    </Link>
                  </li>
                ))}
              </Box>
            </Box>
          </Box>
        </Section>

        {/* ── The way in, once more ── */}
        <Box component="section" aria-labelledby="cta-h" sx={{ mt: { xs: 6, sm: 9 }, textAlign: 'center' }}>
          <Typography component="h2" id="cta-h" sx={{
            fontSize: { xs: '1.5rem', sm: '2rem' }, fontWeight: 700, mb: 2.5,
            letterSpacing: '-0.01em',
          }}>
            {t('cta.title')}
          </Typography>
          <AuthButtons />
        </Box>

        {/* ── Footer ── */}
        <Box component="footer" sx={{
          mt: { xs: 6, sm: 8 }, pt: 2.5, borderTop: '1px solid', borderColor: 'divider',
          fontSize: '0.82rem', color: 'text.secondary', textAlign: 'center',
        }}>
          <Typography component="p" sx={{ fontSize: 'inherit', mb: 0.75 }}>
            {t('footer.licence')}
          </Typography>
          <Typography component="p" sx={{ fontSize: 'inherit' }}>
            {COMPANY}{' · '}
            <Link href={SHOP_URL} target="_blank" rel="noopener noreferrer"
              underline="hover" color="inherit">
              aerostator.com
            </Link>
            <CookieSettingsLink prefix=" · " />
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

export default Landing;
