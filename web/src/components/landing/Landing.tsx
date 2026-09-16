/**
 * The page a signed-out visitor lands on (user 2026-09-16: *"нужно сделать
 * красивую заставку при входе, ещё до регистрации клиента"*).
 *
 * Before this, a visitor to https://emotres.com got the header and one grey
 * line — "Sign in to see the motor catalog" — because the production backend
 * runs PUBLIC_EXHIBIT=0 and publishes nothing to the public.  That is an empty
 * shell, not a front door.
 *
 * WHAT IT MAY TOUCH.  Nothing.  The landing renders from static props and the
 * three PNGs in `public/landing/`; the only network the page can cause is the
 * header's own `/api/me` + `/api/version` and, when the visitor presses the
 * button, the login endpoints.  That is deliberate — every other `/api/*` route
 * answers 401 to an anonymous caller, and a landing page that fires them is a
 * console full of red on the first impression.
 *
 * ONE AUTH PATH.  The button calls `useAuth().signIn()`, which opens the same
 * `LoginDialog` the header's Sign in button opens — the official Google
 * Identity Services button lives there and nowhere else.  This component never
 * loads GIS itself; it only *labels* the button after the client id, so a
 * deployment without one does not promise Google and then show a password form.
 *
 * THE PICTURES ARE OURS.  All three are `report.py`'s own figures, rendered
 * off the stored per-duty fields (`motor_ai_sim.duty_fields`) — not stock art,
 * not a redraw.  `scripts/landing_images.py` is what draws them, and
 * `python scripts/landing_images.py --check` redraws them somewhere else and
 * says whether the committed bytes still match:
 *
 *   em-field.png            report._em_maps(...)["b"]
 *   thermal-map.png         report._thermal_map(...)
 *   rotor-displacement.png  report._mech_extra_maps(...)["disp"]
 *
 * all three off CIANO10 200 opt / "L155 motor" / rated 1x9 mm, so the three
 * cards are one machine at one operating point rather than three unrelated
 * pretty pictures.
 *
 * ONE CUT, THE WHOLE MACHINE (user 2026-09-16).  The mechanical solve is a full
 * 360° ring; the electromagnetic and thermal ones are stored as HALF models —
 * this machine is 12 slots / 10 poles, so its smallest periodic sector is a
 * half, and their figures came out 2.2:1 beside a round rotor.  The generator
 * completes those two to the full ring by ROTATING the solved half 180°, which
 * is the periodic continuation the solve itself assumed — its docstring is
 * where that argument is made in full; |B| is a magnitude and
 * T is a scalar, so both survive the sign flip an anti-periodic boundary would
 * carry, and the seam nodes are merged so the ring closes with no hairline.
 * Nothing in report.py changes: the report still prints the half it solved.
 *
 * They are drawn on white — the report's own background — and sit on a white
 * tile in both themes, so the figure looks the same here as it does on the page
 * it is printed on.
 */
import React from 'react';
import { Box, Button, Link, Tooltip, Typography } from '@mui/material';
import { GOOGLE_CLIENT_ID } from '../../lib/localAuth';
import { useAuth } from '../../contexts/AuthContext';

/** Where a visitor without an account writes. */
export const REQUEST_ACCESS_EMAIL = 'vadim@motresres.com';

/** The company line and the shop, in the footer. */
export const COMPANY = 'MOTRES d.o.o.';
export const SHOP_URL = 'https://aerostator.com';

export interface Feature {
  /** The card's label — SHORT (project rule: one short line, detail in a tooltip). */
  label: string;
  /** Path under `public/`, served from the site root. */
  src: string;
  /** What the picture is, for a reader who cannot see it. */
  alt: string;
  /** The tooltip — where the picture came from. */
  hint: string;
}

/** The three cards, in order.  Exported so the test can check them without a DOM. */
export const FEATURES: Feature[] = [
  {
    label: 'Electromagnetic FEM',
    src: '/landing/em-field.png',
    alt: 'Flux-density map of a permanent-magnet machine: the whole '
      + 'cross-section, twelve slots around ten magnets, coloured by |B| from '
      + '0 to 2.4 tesla, with the colour bar beside it.',
    hint: 'Flux density |B| of a 200 mm machine at its rated duty — the solved '
      + 'half repeated to the full ring, which is the periodicity the solve '
      + 'itself assumed.',
  },
  {
    label: 'Thermal & duty cycle',
    src: '/landing/thermal-map.png',
    alt: 'Temperature map of the same whole cross-section, from 69 °C at the '
      + 'outer housing to 135 °C in the rotor, with the colour bar beside it.',
    hint: 'Temperature of the same machine, coupled to the losses of the same '
      + 'run — the solved half repeated to the full ring, as above.',
  },
  {
    label: 'Mechanical simulation',
    src: '/landing/rotor-displacement.png',
    alt: 'Deformation map of the same rotor under its retaining sleeve: the '
      + 'whole ring — sleeve, magnets, rotor iron — coloured by how far each '
      + 'point moves at speed, 7 to 146 micrometres, with the colour bar '
      + 'beside it and the shape exaggerated so the bending is visible.',
    hint: 'The rotor and its sleeve at speed, coloured by displacement |u| — '
      + 'the shape is exaggerated 22x so the bending can be seen.',
  },
];

/** The button's words.  Only a deployment that HAS a Google client id may
 *  promise Google — everywhere else the same dialog offers a password. */
export function signInLabel(clientId: string | undefined | null): string {
  return clientId ? 'Sign in with Google' : 'Sign in';
}

/** Is this the visitor the landing is for?  The signed-out half of App's
 *  `signedIn = !enforced || !!user` — an unenforced backend (local dev) is
 *  never shown the landing, so the dev server boots exactly as it did. */
export function shouldShowLanding(enforced: boolean, hasUser: boolean): boolean {
  return enforced && !hasUser;
}

/** The AeroStator mark: a stator ring with its teeth.  Inline, not an asset —
 *  it inherits `currentColor`, so it is right in both themes by construction. */
const StatorMark: React.FC<{ size?: number }> = ({ size = 44 }) => (
  <Box component="svg" viewBox="0 0 100 100" width={size} height={size}
    aria-hidden="true" focusable="false" sx={{ flexShrink: 0, color: 'primary.main' }}>
    <circle cx="50" cy="50" r="45" fill="none" stroke="currentColor"
      strokeWidth="5" opacity={0.28} />
    {Array.from({ length: 12 }, (_, i) => (
      <rect key={i} x="47.5" y="9" width="5" height="15" rx="2" fill="currentColor"
        transform={`rotate(${i * 30} 50 50)`} />
    ))}
    <circle cx="50" cy="50" r="21" fill="none" stroke="currentColor" strokeWidth="5" />
  </Box>
);

/** Google's mark, on the button that opens OUR dialog (which renders Google's
 *  own button).  Decoration only — no script, no second sign-in path. */
const GoogleMark: React.FC = () => (
  <Box component="svg" viewBox="0 0 48 48" width={17} height={17}
    aria-hidden="true" focusable="false">
    <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6.1C12.3 13.2 17.6 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.2-.4-4.6H24v9.1h12.4c-.5 2.9-2.1 5.3-4.6 7l7.6 5.9c4.4-4.1 6.7-10.1 6.7-17.4z" />
    <path fill="#FBBC05" d="M10.4 28.7c-.5-1.5-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C.9 16.3 0 20 0 24s.9 7.7 2.6 10.8l7.8-6.1z" />
    <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.8 2.3-8.3 2.3-6.4 0-11.7-3.7-13.6-8.9l-7.8 6.1C6.5 42.6 14.6 48 24 48z" />
  </Box>
);

const FeatureCard: React.FC<{ feature: Feature }> = ({ feature }) => (
  <Tooltip title={feature.hint} arrow>
    <Box
      sx={{
        borderRadius: 2,
        border: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.paper',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        transition: 'border-color .15s, transform .15s',
        '&:hover': { borderColor: 'primary.main', transform: 'translateY(-2px)' },
      }}
    >
      {/* The figure keeps the report's own white ground in both themes, so the
          picture on the card and the picture on the page are one picture.
          THE PICTURE IS THE CARD (user 2026-09-16, on the first live page: the
          images were ~180 px tall and he wanted them to dominate).  All three
          are now the SAME CUT of the machine — the whole 360° ring, 1.15:1 —
          so 1.25:1 is a box they all very nearly fill, ~354 px tall at 1440.
          They were not always: the electromagnetic and thermal solves are
          stored as half models and their pictures came out 2.2:1 beside a
          round rotor (user, on the second live page: the three must show the
          same section).  The halves are repeated to the full ring when the
          PNGs are generated — by ROTATING 180°, which is the periodicity the
          solve itself assumed, not by mirroring — and never in the report,
          which still prints the half it solved. */}
      <Box sx={{
        bgcolor: '#ffffff', aspectRatio: '1.25 / 1', p: 0.75,
        // `minHeight: 0` or the flex box's automatic minimum lets a picture
        // TALLER than the declared ratio push the box past its own
        // aspect ratio — which is how the third card came out 80 px taller
        // than the other two, with white under their labels to match.
        minHeight: 0, overflow: 'hidden',
      }}>
        <Box component="img" src={feature.src} alt={feature.alt} loading="lazy"
          sx={{ width: '100%', height: '100%', objectFit: 'contain',
            display: 'block' }} />
      </Box>
      <Typography component="h3" sx={{
        px: 1.75, py: 1.25, fontSize: '0.9rem', fontWeight: 600,
        letterSpacing: 0.1, borderTop: '1px solid', borderColor: 'divider',
      }}>
        {feature.label}
      </Typography>
    </Box>
  </Tooltip>
);

const Landing: React.FC = () => {
  const { signIn } = useAuth();
  const label = signInLabel(GOOGLE_CLIENT_ID);

  return (
    <Box
      component="main"
      sx={{
        // It is a flex child of the app's column shell (header above it), so it
        // takes the rest of the window and scrolls inside itself — `height:
        // 100%` alone left it sized to its content with dead space under it.
        flex: '1 1 auto',
        minHeight: 0,
        overflowY: 'auto',
        bgcolor: 'background.default',
        // One soft brand wash behind the hero — the only decoration on the page.
        backgroundImage: (t) =>
          `radial-gradient(1100px 420px at 50% -120px, ${t.palette.primary.main}22, transparent 70%)`,
      }}
    >
      {/* `minHeight: 100%` + centring, not `height` — the page sits in the
          middle of a tall window and still SCROLLS on a short one. */}
      <Box sx={{
        // 1400, not 1040: the pictures are the page, and three of them across a
        // 1040 row came out 340 px wide (user 2026-09-16 on the live page).  The
        // HEADLINE keeps its own narrower measure below — a line of type this
        // size is unreadable at 1400.
        maxWidth: 1400, mx: 'auto', px: { xs: 2.5, sm: 4 },
        py: { xs: 5, sm: 7 }, textAlign: 'center',
        minHeight: '100%', display: 'flex', flexDirection: 'column',
        justifyContent: 'center',
      }}>
        {/* ── Brand ── */}
        <Box sx={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          gap: 1.5, mb: { xs: 3, sm: 4 },
        }}>
          <StatorMark />
          <Typography component="span" sx={{
            fontSize: { xs: '1.5rem', sm: '1.8rem' }, fontWeight: 700,
            letterSpacing: '-0.01em', lineHeight: 1,
          }}>
            AeroStator{' '}
            <Box component="span" sx={{ fontWeight: 300, opacity: 0.65 }}>Core</Box>
          </Typography>
        </Box>

        {/* ── One headline, one supporting line ── */}
        <Typography component="h1" sx={{
          fontSize: { xs: '1.75rem', sm: '2.6rem' }, fontWeight: 700,
          letterSpacing: '-0.02em', lineHeight: 1.15, mb: 1.5,
          maxWidth: 1100, mx: 'auto',
        }}>
          Design, simulate and document electric motors
        </Typography>
        <Typography component="p" sx={{
          fontSize: { xs: '0.95rem', sm: '1.05rem' }, color: 'text.secondary',
          maxWidth: 620, mx: 'auto', mb: { xs: 3.5, sm: 4.5 },
        }}>
          Coupled electromagnetic, thermal and mechanical FEM — from geometry to a
          signed datasheet, in the browser.
        </Typography>

        {/* ── The one door in.  Same dialog as the header's Sign in button. ── */}
        <Button
          variant="contained"
          size="large"
          onClick={() => { void signIn().catch(() => {}); }}
          startIcon={GOOGLE_CLIENT_ID ? <GoogleMark /> : undefined}
          sx={{
            // `alignSelf`, because the column above is a flex box and a button
            // stretched across the whole row is not a button, it is a bar.
            alignSelf: 'center',
            textTransform: 'none', fontSize: '1rem', fontWeight: 600,
            px: 3.5, py: 1.25, borderRadius: 2,
          }}
        >
          {label}
        </Button>

        {/* ── Three cards, stacked on a phone ── */}
        <Box sx={{
          mt: { xs: 5, sm: 7 },
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' },
          gap: { xs: 2, sm: 2.5 },
          textAlign: 'left',
        }}>
          {FEATURES.map((f) => <FeatureCard key={f.label} feature={f} />)}
        </Box>

        {/* ── No account yet ── */}
        <Typography component="p" sx={{
          mt: { xs: 4, sm: 5 }, fontSize: '0.85rem', color: 'text.secondary',
        }}>
          No account yet?{' '}
          <Link href={`mailto:${REQUEST_ACCESS_EMAIL}?subject=AeroStator%20Core%20access`}
            underline="hover" sx={{ fontWeight: 600 }}>
            Request access
          </Link>
        </Typography>

        {/* ── Footer ── */}
        <Box component="footer" sx={{
          mt: { xs: 4, sm: 6 }, pt: 2.5, borderTop: '1px solid',
          borderColor: 'divider', fontSize: '0.78rem', color: 'text.secondary',
        }}>
          {COMPANY}{' · '}
          <Link href={SHOP_URL} target="_blank" rel="noopener noreferrer"
            underline="hover" color="inherit">
            aerostator.com
          </Link>
        </Box>
      </Box>
    </Box>
  );
};

export default Landing;
