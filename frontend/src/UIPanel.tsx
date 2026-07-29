import React from 'react';
import './UIPanel.css';
import { Calendar } from './Calendar';
import TogglePanel from './TogglePanel';

interface UIPanelProps {
  onSelectDate: (date: Date) => void;
  showTec: boolean;
  onToggleTec: (checked: boolean) => void;
}

export const UIPanel: React.FC<UIPanelProps> = ({ onSelectDate, showTec, onToggleTec }) => {
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
      {showTec && (
        <div className="panel-section">
          <h3>Total Electron Content</h3>
          <div className="tec-legend">
            <div className="tec-legend__bar" />
            <div className="tec-legend__ticks">
              <span>0</span>
              <span>25</span>
              <span>50</span>
              <span>75</span>
              <span>100+</span>
            </div>
            <div className="tec-legend__unit">TECU</div>
          </div>
        </div>
      )}
    </div>
  );
};
