import './TogglePanel.css';
import type { TecMode } from './tecMode';

// A single shared switch between the two map sources (Predicted / Actual), with
// an Off button above it. Off returns the switch to a neutral state where
// neither side is selected. State lives in the parent; this is presentational.
const TogglePanel = ({
  mode,
  onModeChange,
}: {
  mode: TecMode;
  onModeChange: (mode: TecMode) => void;
}) => {
  return (
    <div className="toggle-panel">
      <div className="toggle-panel__header">
        <h2>Layers</h2>
      </div>

      <button
        type="button"
        className="tec-off-btn"
        onClick={() => onModeChange('off')}
        disabled={mode === 'off'}
      >
        Turn Off
      </button>

      <div className="tec-switch" role="group" aria-label="TEC map source">
        <button
          type="button"
          className={`tec-switch__option${mode === 'predicted' ? ' is-active' : ''}`}
          aria-pressed={mode === 'predicted'}
          onClick={() => onModeChange('predicted')}
        >
          Predicted
        </button>
        <button
          type="button"
          className={`tec-switch__option${mode === 'actual' ? ' is-active' : ''}`}
          aria-pressed={mode === 'actual'}
          onClick={() => onModeChange('actual')}
        >
          Actual
        </button>
        <button
          type="button"
          className={`tec-switch__option${mode === 'difference' ? ' is-active' : ''}`}
          aria-pressed={mode === 'difference'}
          onClick={() => onModeChange('difference')}
        >
          Difference
        </button>
      </div>
    </div>
  );
};

export default TogglePanel;
