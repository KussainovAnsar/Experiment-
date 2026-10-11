/** Route guards for the staff area and the parent portal. */
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Skeleton } from "@/components/ui";
import { useParentAuth } from "./ParentAuth";
import { useStaffAuth } from "./StaffAuth";

export function RequireStaff() {
  const { token, loading } = useStaffAuth();
  const location = useLocation();
  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (loading) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <Skeleton lines={4} />
        </div>
      </div>
    );
  }
  return <Outlet />;
}

/** Only owner/methodist may open directories and the manager tab. */
export function RequireManager() {
  const { canManagePeople } = useStaffAuth();
  if (!canManagePeople) return <Navigate to="/" replace />;
  return <Outlet />;
}

export function RequireParent() {
  const { token } = useParentAuth();
  if (!token) return <Navigate to="/parent/expired" replace />;
  return <Outlet />;
}

/** Already logged-in staff are sent to the dashboard from /login and /register. */
export function RedirectIfStaff() {
  const { token } = useStaffAuth();
  if (token) return <Navigate to="/" replace />;
  return <Outlet />;
}
