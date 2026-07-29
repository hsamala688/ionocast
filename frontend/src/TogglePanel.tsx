import { useState } from 'react';
import './TogglePanel.css';

type Toggle = { id: string; label: string; checked: boolean };

const TogglePanel = ({ onTecChange }: { onTecChange?: (checked: boolean) => void }) => {
  const [toggles, setToggles] = useState<Toggle[]>([
    { id: 't1', label: 'Predicted TEC (Coming Soon)', checked: false },
    { id: 't2', label: 'Actual TEC', checked: false },
  ]);

  const handleToggle = (id: string) => {
    const next = toggles.map((t) => (t.id === id ? { ...t, checked: !t.checked } : t));
    setToggles(next);
    if (id === 't2') onTecChange?.(next.find((t) => t.id === 't2')!.checked);
  };

  return (
    <div className="toggle-panel">
      <div className="toggle-panel__header">
        <h2>Layers</h2>
      </div>

      <div className="toggle-panel__list">
        {toggles.map((toggle) => (
          <div key={toggle.id} className="toggle-item">
            <span className="toggle-text">{toggle.label}</span>
            <label className="switch">
              <input
                type="checkbox"
                checked={toggle.checked}
                onChange={() => handleToggle(toggle.id)}
              />
              <span className="switch__slider" />
            </label>
          </div>
        ))}
      </div>
    </div>
  );
};

export default TogglePanel;
