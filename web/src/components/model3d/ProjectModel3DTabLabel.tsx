// The label of the project's "3D model" tab (owner 2026-11-22), in its own small file so the project page loads the viewer
// (ProjectModel3D, three.js) only when the tab opens.  The strings live with the 3-D model (namespace model3d).
import React from 'react';
import { useTranslation } from 'react-i18next';

const ProjectModel3DTabLabel: React.FC = () => {
  const { t } = useTranslation('model3d');
  return <>{t('project.tab', { defaultValue: '3D model' })}</>;
};

export default ProjectModel3DTabLabel;
