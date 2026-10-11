/** Parent portal shell: compact header, mobile-first content column. */
import { Link, Outlet } from "react-router-dom";
import { useParentAuth } from "@/auth/ParentAuth";
import { Brand } from "./StaffLayout";

export function ParentLayout() {
  const { parent } = useParentAuth();
  return (
    <div className="parent-shell">
      <header className="parent-header">
        <Link to="/parent" style={{ color: "inherit" }}>
          <Brand sub="кабинет родителя" />
        </Link>
        <Link to="/parent/settings" className="small" aria-label="Настройки">
          {parent?.full_name ? parent.full_name.split(" ")[0] : "Настройки"} ⚙
        </Link>
      </header>
      <main className="parent-main">
        <Outlet />
      </main>
    </div>
  );
}
