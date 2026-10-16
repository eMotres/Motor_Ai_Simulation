export type ViewerToolbarSpec = 'full' | 'simplified';
export type ViewerControl = 'navigation' | 'cube' | 'fit' | 'home' | 'projection' | 'tree' | 'section' | 'explode' | 'measure';
export function controlsFor(spec: ViewerToolbarSpec): ViewerControl[] {
  const basic: ViewerControl[] = ['navigation','cube','fit','home','projection'];
  return spec === 'full' ? [...basic,'tree','section','explode','measure'] : basic;
}
export const treeStartsCollapsed = (width: number): boolean => width < 900;
