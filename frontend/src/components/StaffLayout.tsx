/** Staff shell: sidebar navigation (role-aware) + content outlet. */
import { NavLink, Outlet } from "react-router-dom";
import { useStaffAuth } from "@/auth/StaffAuth";
import { ROLE_LABELS } from "@/lib/status";

export function Brand({ sub }: { sub?: string }) {
  return (
    <div className="brand">
      <span className="brand-mark">LL</span>
      <span>
        LessonLens
        {sub && <span className="brand-sub"> · {sub}</span>}
      </span>
    </div>
  );
}

export function StaffLayout() {
  const { user, logout, canManagePeople } = useStaffAuth();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <nav className="nav" aria-label="Основная навигация">
          <NavLink to="/" end>
            Обзор
          </NavLink>
          <NavLink to="/lessons">Уроки</NavLink>
          <NavLink to="/lessons/upload">Загрузить запись</NavLink>
          <NavLink to="/students">Ученики</NavLink>
          {canManagePeople && <NavLink to="/teachers">Преподаватели</NavLink>}
          {canManagePeople && <NavLink to="/parents">Родители</NavLink>}
        </nav>
        <div className="sidebar-footer">
          <div style={{ minWidth: 0 }}>
            <strong title={user?.full_name}>{user?.full_name ?? "—"}</strong>
            <span className="xs">
              {user ? ROLE_LABELS[user.role] ?? user.role : ""}
              {user?.center_name ? ` · ${user.center_name}` : ""}
            </span>
          </div>
          <button type="button" className="link-button small mt-2" onClick={logout}>
            Выйти
          </button>
        </div>
      </aside>
      <main className="main">
        <div className="main-inner">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
