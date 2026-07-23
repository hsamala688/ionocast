import React, { useState } from 'react';
import './Calendar.css';

// --- Allowed selection window ---
const MIN_YEAR = 2023;
const MAX_YEAR = 2025;
const MIN = { year: MIN_YEAR, month: 0 }; // Jan 2023
const MAX = { year: MAX_YEAR, month: 11 }; // Dec 2025

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const YEARS = Array.from(
  { length: MAX_YEAR - MIN_YEAR + 1 },
  (_, i) => MIN_YEAR + i
);

// "January" + 6 -> "Jan 6th"
function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

interface CalendarProps {
  onSelect?: (date: Date) => void;
}

export const Calendar: React.FC<CalendarProps> = ({ onSelect }) => {
  // Start on January 2023 (the beginning of the allowed range)
  const [view, setView] = useState({ year: MIN_YEAR, month: 0 });
  const [selected, setSelected] = useState<Date | null>(new Date(2023, 0, 6));

  const atMin = view.year === MIN.year && view.month === MIN.month;
  const atMax = view.year === MAX.year && view.month === MAX.month;

  const goPrev = () => {
    if (atMin) return;
    setView(({ year, month }) =>
      month === 0 ? { year: year - 1, month: 11 } : { year, month: month - 1 }
    );
  };

  const goNext = () => {
    if (atMax) return;
    setView(({ year, month }) =>
      month === 11 ? { year: year + 1, month: 0 } : { year, month: month + 1 }
    );
  };

  const pick = (day: number) => {
    const date = new Date(view.year, view.month, day);
    setSelected(date);
    onSelect?.(date);
  };

  // Leading blanks + day numbers, then pad to a fixed 6 rows (42 cells) so the
  // grid height never changes between months and can't resize the panel.
  const firstWeekday = new Date(view.year, view.month, 1).getDay();
  const daysInMonth = new Date(view.year, view.month + 1, 0).getDate();
  const cells: (number | null)[] = [
    ...Array(firstWeekday).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
  while (cells.length < 42) cells.push(null);

  const isSelected = (day: number) =>
    !!selected &&
    selected.getFullYear() === view.year &&
    selected.getMonth() === view.month &&
    selected.getDate() === day;

  return (
    <div className="calendar">
      <div className="calendar__header">
        <span className="calendar__field">
          <span className="calendar__field-text calendar__month">
            {MONTHS[view.month]}
          </span>
          <select
            className="calendar__select"
            value={view.month}
            onChange={(e) =>
              setView((v) => ({ ...v, month: Number(e.target.value) }))
            }
            aria-label="Select month"
          >
            {MONTHS.map((m, i) => (
              <option key={m} value={i}>
                {m}
              </option>
            ))}
          </select>
        </span>

        {selected && (
          <span className="calendar__day-label">
            {ordinal(selected.getDate())},
          </span>
        )}

        <span className="calendar__field">
          <span className="calendar__field-text calendar__year">
            {view.year}
          </span>
          <select
            className="calendar__select"
            value={view.year}
            onChange={(e) =>
              setView((v) => ({ ...v, year: Number(e.target.value) }))
            }
            aria-label="Select year"
          >
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </span>

        <div className="calendar__arrows">
          <button onClick={goPrev} disabled={atMin} aria-label="Previous month">
            &#8249;
          </button>
          <button onClick={goNext} disabled={atMax} aria-label="Next month">
            &#8250;
          </button>
        </div>
      </div>

      <div className="calendar__grid calendar__weekdays">
        {WEEKDAYS.map((d) => (
          <span key={d} className="calendar__weekday">
            {d}
          </span>
        ))}
      </div>

      <div className="calendar__grid">
        {cells.map((day, i) => {
          if (day === null) return <span key={`blank-${i}`} />;
          const col = i % 7; // 0 = Sunday, 6 = Saturday
          const weekend = col === 0 || col === 6;
          const classes = [
            'calendar__day',
            weekend ? 'calendar__day--weekend' : '',
            isSelected(day) ? 'calendar__day--selected' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <button key={day} className={classes} onClick={() => pick(day)}>
              {day}
            </button>
          );
        })}
      </div>
    </div>
  );
};
