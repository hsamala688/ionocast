import React from 'react';
import { UIPanel } from './UIPanel';
import { StarBackground } from './StarBackground';
import { Navbar } from './Navbar';
import { Globe } from './Globe';
import './Globe.css';

const App: React.FC = () => {
  return (
    <div>
      <StarBackground />
      <Navbar />
      <Globe />
      <UIPanel/>
    </div>
  );
};

export default App;
