import React from 'react';
import { Link, NavLink } from 'react-router-dom';
import './Navbar.css';

// TODO: replace these with the real destinations
const GITHUB_URL = 'https://github.com/hsamala688/ionocast';

// Internal (client-routed) destinations. `short` is the compact label shown on
// mobile so long items (e.g. "How We Made This") don't overflow the bar.
const pages: { label: string; short?: string; to: string }[] = [
  { label: 'Globe', to: '/' },
  { label: 'How We Made This', short: 'How', to: '/how' },
];

const GitHubIcon: React.FC = () => (
  <svg
    className="navbar__icon"
    viewBox="0 0 16 16"
    width="18"
    height="18"
    fill="currentColor"
    aria-hidden="true"
  >
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z" />
  </svg>
);

type NavLink = {
  label: string;
  href: string;
  external?: boolean;
  icon?: React.ReactNode;
};

const links: NavLink[] = [
  { label: 'GitHub', href: GITHUB_URL, external: true, icon: <GitHubIcon /> },
];

export const Navbar: React.FC = () => {
  return (
    <nav className="navbar">
      <Link to="/" className="navbar__brand">Ionocast</Link>
      <ul className="navbar__links">
        {pages.map((page) => (
          <li key={page.to}>
            <NavLink
              to={page.to}
              end={page.to === '/'}
              className={({ isActive }) =>
                `navbar__link${isActive ? ' navbar__link--active' : ''}`
              }
            >
              <span className="navbar__label navbar__label--full">{page.label}</span>
              <span className="navbar__label navbar__label--short">{page.short ?? page.label}</span>
            </NavLink>
          </li>
        ))}
        {links.map((link) => (
          <li key={link.label}>
            <a
              href={link.href}
              className="navbar__link"
              aria-label={link.label}
              {...(link.external
                ? { target: '_blank', rel: 'noopener noreferrer' }
                : {})}
            >
              {link.icon}
              {/* Text hides on mobile (icon-only); aria-label keeps the name. */}
              <span className="navbar__label navbar__label--full">{link.label}</span>
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
};
