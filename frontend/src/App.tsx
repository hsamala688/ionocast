import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { StarBackground } from './StarBackground';
import { Navbar } from './Navbar';
import { Home } from './Home';
import { HowWeMadeThis } from './HowWeMadeThis';

// App is the shell: shared chrome (star background + navbar) sits above the
// router, and each route renders its own page. Per-page state lives in the page
// component (e.g. Home owns the globe/panel state).
const App: React.FC = () => {
  return (
    <div>
      <StarBackground />
      <Navbar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/how" element={<HowWeMadeThis />} />
      </Routes>
    </div>
  );
};

export default App;
