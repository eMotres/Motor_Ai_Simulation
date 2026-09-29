import React from 'react';
import { Box, Typography } from '@mui/material';

interface Props { children: React.ReactNode; label?: string }
interface State { error: Error | null; stack: string }

/** Catches render errors in a subtree so one broken panel can't blank the whole
 *  app. Shows the error message + logs the full stack to the console. */
export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, stack: '' };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', this.props.label ?? '', error?.message, error?.stack, info.componentStack);
    this.setState({ stack: (error?.stack ?? '') + '\n' + (info.componentStack ?? '') });
  }

  render() {
    if (this.state.error) {
      return (
        <Box sx={{ p: 3, color: '#fca5a5', overflow: 'auto', maxHeight: '100%' }}>
          <Typography sx={{ fontWeight: 700, mb: 1, color: '#f87171' }}>
            This panel hit an error{this.props.label ? ` — ${this.props.label}` : ''}
          </Typography>
          <Typography component="pre" sx={{ fontSize: '0.72rem', color: '#fda4af', whiteSpace: 'pre-wrap', m: 0 }}>
            {String(this.state.error?.message ?? this.state.error)}
          </Typography>
          <Typography component="pre" sx={{ fontSize: '0.62rem', color: '#9ca3af', whiteSpace: 'pre-wrap', mt: 1, m: 0 }}>
            {this.state.stack.slice(0, 1200)}
          </Typography>
        </Box>
      );
    }
    return this.props.children as React.ReactElement;
  }
}
