import React, { useState } from 'react';
import { UIPanel } from './UIPanel';
import { StarBackground } from './StarBackground';
import { Navbar } from './Navbar';
import { Globe } from './Globe';
import { UtcClock } from './UtcClock';
import type { TecMode } from './tecMode';
import './Globe.css';

const App: React.FC = () => {
  // Shared state: the panel sets these, the globe reads them.
  const [selectedDate, setSelectedDate] = useState<Date | null>(new Date(2023, 0, 6));
  const [tecMode, setTecMode] = useState<TecMode>('off');
  const [utcHour, setUtcHour] = useState(12); // hour of the map on the globe

  return (
    <div>
      <StarBackground />
      <Navbar />
      {tecMode !== 'off' && <UtcClock hour={utcHour} />}
      <Globe selectedDate={selectedDate} tecMode={tecMode} onHourChange={setUtcHour} />
      <UIPanel
        onSelectDate={setSelectedDate}
        tecMode={tecMode}
        onTecModeChange={setTecMode}
      />
    </div>
  );
};

export default App;
