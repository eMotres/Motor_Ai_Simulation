// The status the person sees once the NDA is signed but the applicant verification is not complete (docs/APPLICANT_VERIFICATION.md):
// "Verification in progress" or "We need one more thing from you" with a link to the personal reply page (server-rendered, works
// without JavaScript), or the "not approved" line.  One short line; the explanation is a tooltip.
import React from 'react';
import { Box, Link, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { statusLine, type AccessInfo } from '../../lib/verification';

const VerificationStatus: React.FC<{ access: AccessInfo; dense?: boolean }> = ({ access, dense = true }) => {
  const { t, i18n } = useTranslation('model3d');
  const line = statusLine(access);
  if (!line) return null;
  const zh = (i18n.resolvedLanguage ?? 'en') === 'zh-CN';
  const href = line.link ? `${line.link}${line.link.includes('?') ? '&' : '?'}lang=${zh ? 'zh' : 'en'}` : null;
  return (
    <Box data-testid="verification-status" data-state={access.state} sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
      <Typography sx={{ fontSize: dense ? 12 : 13, color: access.state === 'need_info' ? 'warning.main' : 'var(--text-3)', fontWeight: access.state === 'need_info' ? 700 : 400 }}>
        {t(`verification.${line.key}`)}
      </Typography>
      {href && (
        <Link href={href} target="_blank" rel="noopener noreferrer" underline="always" sx={{ fontSize: dense ? 12 : 13 }}>
          {t(access.state === 'need_info' ? 'verification.open' : 'verification.details')}
        </Link>
      )}
      <HelpTip title={t(`verification.${line.key}Help`)} />
    </Box>
  );
};

export default VerificationStatus;
