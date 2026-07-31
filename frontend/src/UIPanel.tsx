import React from 'react';
import './UIPanel.css';
import { Calendar } from './Calendar';
import TogglePanel from './TogglePanel';
import type { TecMode } from './tecMode';

interface UIPanelProps {
  onSelectDate: (date: Date) => void;
  tecMode: TecMode;
  onTecModeChange: (mode: TecMode) => void;
}

export const UIPanel: React.FC<UIPanelProps> = ({ onSelectDate, tecMode, onTecModeChange }) => {
  return (
    <div className="ui-panel">
      <div className="panel-header">
        <h2>Select a Date:</h2>
      </div>
      <div className="panel-section">
        <Calendar onSelect={onSelectDate} />
      </div>
      <div className="panel-section">
        <TogglePanel mode={tecMode} onModeChange={onTecModeChange} />
      </div>
      {tecMode !== 'off' && (
        <div className="panel-section">
          <h3 className="tec-heading">
            {tecMode === 'predicted' ? 'Predicted TEC' : 'Total Electron Content'}
            <span className="tec-heading__hint">
              Click the globe to pause; click again to resume.
            </span>
          </h3>
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
          <p className="tec-disclaimer">
            Unfortunately some days or hours have no maps as there were gaps in the data.
          </p>
        </div>
      )}
    </div>
  );
};
