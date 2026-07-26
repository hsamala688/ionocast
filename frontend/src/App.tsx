import React, { useState } from 'react';
import { UIPanel } from './UIPanel';
import { StarBackground } from './StarBackground';
import { Navbar } from './Navbar';
import { Globe } from './Globe';
import './Globe.css';

const App: React.FC = () => {
  // Shared state: the panel sets these, the globe reads them.
  const [selectedDate, setSelectedDate] = useState<Date | null>(new Date(2023, 0, 6));
  const [showTec, setShowTec] = useState(false);

  return (
    <div>
      <StarBackground />
      <Navbar />
      <Globe selectedDate={selectedDate} showTec={showTec} />
      <UIPanel
        onSelectDate={setSelectedDate}
        showTec={showTec}
        onToggleTec={setShowTec}
      />
    </div>
  );
};

export default App;
