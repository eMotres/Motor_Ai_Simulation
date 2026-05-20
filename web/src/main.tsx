import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Suppress THREE.js deprecation warnings (from library internals)
const originalConsoleWarn = console.warn;
console.warn = (...args) => {
  const message = args[0];
  if (typeof message === 'string' && (
    message.includes('THREE.Clock') || 
    message.includes('THREE.WebGLShadowMap') ||
    message.includes('PCFSoftShadowMap')
  )) {
    return; // Suppress these warnings
  }
  originalConsoleWarn.apply(console, args);
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
