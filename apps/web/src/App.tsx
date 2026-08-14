// Маршрутизация и защита приватных страниц.

import { type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "@/auth";
import { Nav, Spinner } from "@/components";
import { LandingPage } from "@/pages/Landing";
import { PublicOrgPage } from "@/pages/PublicOrg";
import { LoginPage } from "@/pages/Login";
import { RegisterPage } from "@/pages/Register";
import { VerifyEmailPage } from "@/pages/VerifyEmail";
import { AcceptInvitePage } from "@/pages/AcceptInvite";
import { ForgotPasswordPage, ResetPasswordPage } from "@/pages/Password";
import { DashboardPage } from "@/pages/Dashboard";
import { OrgPage } from "@/pages/Org";
import { SecurityPage } from "@/pages/Security";
import { AdminPage } from "@/pages/Admin";
import { NotFoundPage } from "@/pages/NotFound";

function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <Spinner />;
  if (!user) {
    return <Navigate to="/login" state={{ from: `${location.pathname}${location.search}` }} replace />;
  }
  if (admin && !user.is_superuser) return <Navigate to="/app" replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <>
      <Nav />
      <main>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/o/:slug" element={<PublicOrgPage />} />

          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/verify-email" element={<VerifyEmailPage />} />
          <Route path="/accept-invite" element={<AcceptInvitePage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />

          <Route
            path="/app"
            element={
              <RequireAuth>
                <DashboardPage />
              </RequireAuth>
            }
          />
          <Route
            path="/app/security"
            element={
              <RequireAuth>
                <SecurityPage />
              </RequireAuth>
            }
          />
          <Route
            path="/app/orgs/:slug"
            element={
              <RequireAuth>
                <OrgPage />
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth admin>
                <AdminPage />
              </RequireAuth>
            }
          />

          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </>
  );
}
