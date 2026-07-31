import React from 'react';
import './UtcClock.css';

// Small HUD under the navbar brand showing the UT hour of the map currently on
// the globe. It ticks as the globe spins (the overlay swaps to that hour) and
// holds steady when the globe is paused. `hour` is 0..23.
export const UtcClock: React.FC<{ hour: number }> = ({ hour }) => {
  const hh = String(hour).padStart(2, '0');
  return (
    <div className="utc-clock" aria-live="polite">
      <span className="utc-clock__label">Map time</span>
      <span className="utc-clock__time">
        {hh}:00 <span className="utc-clock__zone">UTC</span>
      </span>
    </div>
  );
};
