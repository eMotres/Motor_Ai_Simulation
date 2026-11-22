// The label of the project's "Contract" tab (owner 2026-11-22), in its own small file so the section loads only when it opens.
import React from 'react';
import { useTranslation } from 'react-i18next';

const ContractTabLabel: React.FC = () => {
  const { t } = useTranslation('contract');
  return <>{t('tab', { defaultValue: 'Contract' })}</>;
};

export default ContractTabLabel;
