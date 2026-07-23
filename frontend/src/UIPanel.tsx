import React from 'react';
import './UIPanel.css';
import { Calendar } from './Calendar';
import TogglePanel from './TogglePanel';

export const UIPanel: React.FC = () => {
  return (
    <div className="ui-panel">
      <div className="panel-header">
        <h2>Select a Date:</h2>
      </div>
      <div className="panel-section">
        <Calendar onSelect={(date) => console.log('selected', date)} />
      </div>
      <div className="panel-section">
        <TogglePanel />
      </div>
    </div>
  );
};
