import { useState } from 'react';
import './TogglePanel.css';

type Toggle = { id: string; label: string; checked: boolean };

const TogglePanel = () => {
  const [toggles, setToggles] = useState<Toggle[]>([
    { id: 't1', label: 'Normal Earth', checked: true },
    { id: 't2', label: 'Predicted TEC (Coming Soon)', checked: false },
    { id: 't3', label: 'Actual TEC', checked: false },
  ]);

  const handleToggle = (id: string) => {
    setToggles((prev) =>
      prev.map((t) => (t.id === id ? { ...t, checked: !t.checked } : t))
    );
  };

  return (
    <div className="toggle-panel">
      <div className="toggle-panel__header">
        <h2>Layers</h2>
      </div>

      <div className="toggle-panel__list">
        {toggles.map((toggle) => (
          <label key={toggle.id} className="toggle-item">
            <span className="toggle-text">{toggle.label}</span>
            <span className="switch">
              <input
                type="checkbox"
                checked={toggle.checked}
                onChange={() => handleToggle(toggle.id)}
              />
              <span className="switch__slider" />
            </span>
          </label>
        ))}
      </div>
    </div>
  );
};

export default TogglePanel;
