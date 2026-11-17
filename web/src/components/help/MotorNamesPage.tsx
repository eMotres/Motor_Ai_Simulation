/** Public help page "How to read a MOTRES motor name" (`/motor-names`, `/zh-cn/motor-names`; owner 2026-11-16).  Readable signed
 *  out; it calls no API: a typed name is decoded in the browser (lib/motorDesignation.ts). */
import React, { useState } from 'react';
import { Box, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { LETTER_TABLE, parseDesignation, SEGMENTS } from '../../lib/motorDesignation';
import { DesignationDecoder } from '../common/MotorDesignation';

// the 400 mm two-set motor: no letter D (owner 2026-11-17), both sets' turns counted together, the connection inside one set
const EXAMPLES = ['CILN28 200_40 24F4SY', 'CIANO28 80_12 60R4SY', 'CIANO56 400_90 30F4SY'];

const MotorNamesPage: React.FC = () => {
  const { t } = useTranslation('common');
  const [text, setText] = useState(EXAMPLES[0]);
  const fields = parseDesignation(text);
  const segments = fields ? Object.fromEntries(SEGMENTS.filter((s) => s !== 'rev' || fields.rev).map((s) => [s, { value: fields[s], source: 'name' }])) : null;
  return (
    <Box sx={{ minHeight: '100vh', bgcolor: 'var(--panel, #f7f6f3)', color: 'var(--text-0, #222)', p: { xs: 2, md: 4 } }}>
      <Box sx={{ maxWidth: 820, mx: 'auto' }}>
        <Typography component="h1" sx={{ fontSize: 24, fontWeight: 800, mb: 1 }}>{t('designation.help.title')}</Typography>
        <Typography sx={{ fontFamily: 'monospace', fontSize: 15, mb: 2 }}>{t('designation.help.format')}</Typography>
        <Paper sx={{ p: 2, mb: 2 }}>
          <TextField fullWidth size="small" label={t('designation.help.try')} value={text} onChange={(e) => setText(e.target.value)}
            error={!!text && !fields} inputProps={{ spellCheck: false, style: { fontFamily: 'monospace' } }} />
          {text && !fields && <Typography sx={{ mt: 0.5, fontSize: 12, color: '#dc2626' }}>{t('designation.help.notAName')}</Typography>}
          {segments && <DesignationDecoder segments={segments} />}
        </Paper>
        <Paper sx={{ p: 2 }}>
          <Table size="small">
            <TableHead><TableRow><TableCell>{t('designation.help.position')}</TableCell><TableCell>{t('designation.help.values')}</TableCell></TableRow></TableHead>
            <TableBody>
              {LETTER_TABLE.map(({ segment, values }) => (
                <TableRow key={segment}>
                  <TableCell sx={{ fontWeight: 700 }}>{t(`designation.segment.${segment}`)}</TableCell>
                  <TableCell>{values.map((v) => `${v || '—'} ${t(`designation.meaning.${segment}.${v || 'none'}`)}`).join(' · ')}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Typography sx={{ mt: 1.5, fontSize: 13 }}>{t('designation.help.double')}</Typography>
          <Typography sx={{ mt: 1.5, fontSize: 13 }}>{EXAMPLES.join(' · ')}</Typography>
        </Paper>
      </Box>
    </Box>
  );
};

export default MotorNamesPage;
