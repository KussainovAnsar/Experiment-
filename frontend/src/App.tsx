/** Route table (CONTRACT §7). Staff routes live under StaffAuthProvider, parent routes under ParentAuthProvider. */
import { Outlet, Route, Routes } from "react-router-dom";
import { ParentAuthProvider } from "@/auth/ParentAuth";
import { StaffAuthProvider } from "@/auth/StaffAuth";
import { RedirectIfStaff, RequireManager, RequireParent, RequireStaff } from "@/auth/guards";
import { ParentLayout } from "@/components/ParentLayout";
import { StaffLayout } from "@/components/StaffLayout";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ExpiredPage } from "@/pages/parent/ExpiredPage";
import { MagicLinkPage } from "@/pages/parent/MagicLinkPage";
import { ParentHomePage } from "@/pages/parent/ParentHomePage";
import { ParentLessonPage } from "@/pages/parent/ParentLessonPage";
import { ParentSettingsPage } from "@/pages/parent/ParentSettingsPage";
import { ParentStudentPage } from "@/pages/parent/ParentStudentPage";
import { DashboardPage } from "@/pages/staff/DashboardPage";
import { LessonsPage } from "@/pages/staff/LessonsPage";
import { LoginPage } from "@/pages/staff/LoginPage";
import { ParentsPage } from "@/pages/staff/ParentsPage";
import { RegisterPage } from "@/pages/staff/RegisterPage";
import { StudentDetailPage } from "@/pages/staff/StudentDetailPage";
import { StudentsPage } from "@/pages/staff/StudentsPage";
import { TeachersPage } from "@/pages/staff/TeachersPage";
import { UploadPage } from "@/pages/staff/UploadPage";
import { LessonPage } from "@/pages/staff/lesson/LessonPage";

function StaffRoot() {
  return (
    <StaffAuthProvider>
      <Outlet />
    </StaffAuthProvider>
  );
}

function ParentRoot() {
  return (
    <ParentAuthProvider>
      <Outlet />
    </ParentAuthProvider>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<ParentRoot />}>
        <Route path="/p/:token" element={<MagicLinkPage />} />
        <Route path="/parent/expired" element={<ExpiredPage />} />
        <Route element={<RequireParent />}>
          <Route element={<ParentLayout />}>
            <Route path="/parent" element={<ParentHomePage />} />
            <Route path="/parent/settings" element={<ParentSettingsPage />} />
            <Route path="/parent/students/:id" element={<ParentStudentPage />} />
            <Route path="/parent/students/:id/lessons/:lessonId" element={<ParentLessonPage />} />
          </Route>
        </Route>
      </Route>

      <Route element={<StaffRoot />}>
        <Route element={<RedirectIfStaff />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
        </Route>
        <Route element={<RequireStaff />}>
          <Route element={<StaffLayout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/lessons" element={<LessonsPage />} />
            <Route path="/lessons/upload" element={<UploadPage />} />
            <Route path="/lessons/:id" element={<LessonPage />} />
            <Route path="/students" element={<StudentsPage />} />
            <Route path="/students/:id" element={<StudentDetailPage />} />
            <Route element={<RequireManager />}>
              <Route path="/teachers" element={<TeachersPage />} />
              <Route path="/parents" element={<ParentsPage />} />
            </Route>
          </Route>
        </Route>
      </Route>

      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
