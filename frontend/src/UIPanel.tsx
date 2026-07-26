import React from 'react';
import './UIPanel.css';
import { Calendar } from './Calendar';
import TogglePanel from './TogglePanel';

interface UIPanelProps {
  onSelectDate: (date: Date) => void;
  showTec: boolean;
  onToggleTec: (checked: boolean) => void;
}

export const UIPanel: React.FC<UIPanelProps> = ({ onSelectDate, onToggleTec }) => {
  return (
    <div className="ui-panel">
      <div className="panel-header">
        <h2>Select a Date:</h2>
      </div>
      <div className="panel-section">
        <Calendar onSelect={onSelectDate} />
      </div>
      <div className="panel-section">
        <TogglePanel onTecChange={onToggleTec} />
      </div>
    </div>
  );
};
