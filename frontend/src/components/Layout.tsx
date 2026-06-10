import { NavLink, Outlet } from "react-router";
import { useAuth } from "../auth";
import { Icon } from "./ui";

const NAV = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/connections", label: "Connections", icon: "db" },
  { to: "/datasets", label: "Datasets", icon: "table" },
  { to: "/checks", label: "Checks", icon: "shield" },
  { to: "/runs", label: "Runs", icon: "play" },
  { to: "/exceptions", label: "Exceptions", icon: "alert" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">
          <span className="logo-mark">
            <Icon name="shield" size={16} />
          </span>
          DQ Sentinel
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
            >
              <Icon name={n.icon} />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        <nav>
          <NavLink to="/settings" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <Icon name="settings" />
            Settings
          </NavLink>
        </nav>
        <div className="user-box">
          <div className="email" title={user?.email}>
            {user?.name || user?.email}
          </div>
          <div style={{ color: "var(--text-light)" }}>{user?.role}</div>
          <button className="small ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
