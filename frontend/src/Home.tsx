import React, { useState } from 'react';
import { UIPanel } from './UIPanel';
import { Globe } from './Globe';
import { UtcClock } from './UtcClock';
import type { TecMode } from './tecMode';
import './Globe.css';

// The main visualization page: the interactive globe + controls panel.
// Shared chrome (star background, navbar) lives in App, above the router.
export const Home: React.FC = () => {
  // Shared state: the panel sets these, the globe reads them.
  const [selectedDate, setSelectedDate] = useState<Date | null>(new Date(2023, 0, 6));
  const [tecMode, setTecMode] = useState<TecMode>('off');
  const [utcHour, setUtcHour] = useState(12); // hour of the map on the globe
  // Mobile-only drawer state. At >=768px the panel is always docked (the toggle
  // and scrim are hidden by CSS), so this flag is inert there.
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <>
      {tecMode !== 'off' && <UtcClock hour={utcHour} />}
      <Globe selectedDate={selectedDate} tecMode={tecMode} onHourChange={setUtcHour} />

      {/* Floating hamburger/close button — only visible on mobile (see UIPanel.css). */}
      <button
        type="button"
        className="drawer-toggle"
        aria-label={drawerOpen ? 'Close controls' : 'Open controls'}
        aria-expanded={drawerOpen}
        aria-controls="controls-panel"
        onClick={() => setDrawerOpen((o) => !o)}
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          {drawerOpen ? (
            <>
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </>
          ) : (
            <>
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </>
          )}
        </svg>
      </button>

      {/* Scrim behind the open drawer; tap to dismiss. Rendered only when open,
          and hidden by CSS above the mobile breakpoint. */}
      {drawerOpen && (
        <div className="drawer-scrim" onClick={() => setDrawerOpen(false)} />
      )}

      <UIPanel
        onSelectDate={setSelectedDate}
        tecMode={tecMode}
        onTecModeChange={setTecMode}
        isOpen={drawerOpen}
      />
    </>
  );
};
