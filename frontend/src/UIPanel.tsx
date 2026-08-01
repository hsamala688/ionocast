import React from 'react';
import './UIPanel.css';
import { Calendar } from './Calendar';
import TogglePanel from './TogglePanel';
import type { TecMode } from './tecMode';
import { DIFF_SATURATION } from './Globe';

interface UIPanelProps {
  onSelectDate: (date: Date) => void;
  tecMode: TecMode;
  onTecModeChange: (mode: TecMode) => void;
  // Drawer open state (mobile only); ignored where the panel is always docked.
  isOpen?: boolean;
}

export const UIPanel: React.FC<UIPanelProps> = ({ onSelectDate, tecMode, onTecModeChange, isOpen = false }) => {
  return (
    <div className={`ui-panel${isOpen ? ' ui-panel--open' : ''}`} id="controls-panel">
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
            {tecMode === 'predicted'
              ? 'Predicted TEC'
              : tecMode === 'difference'
                ? 'Predicted vs. Actual (Error)'
                : 'Total Electron Content'}
            <span className="tec-heading__hint">
              Click the globe to pause; click again to resume.
            </span>
          </h3>
          {tecMode === 'difference' ? (
            <>
              <div className="tec-legend">
                <div className="tec-legend__bar tec-legend__bar--diff" />
                <div className="tec-legend__ticks">
                  <span>−{DIFF_SATURATION}</span>
                  <span>0</span>
                  <span>+{DIFF_SATURATION}</span>
                </div>
                <div className="tec-legend__unit">TECU</div>
              </div>
              <p className="tec-disclaimer">
                Blue is low and Red is high compared to Actual TEC. Near-zero error is transparent.
                Unfortunately some days or hours have no maps as there were gaps in the data. 
              </p>
            </>
          ) : (
            <>
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
            </>
          )}
        </div>
      )}
    </div>
  );
};
