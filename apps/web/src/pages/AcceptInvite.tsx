import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { useAuth } from "@/auth";
import { Alert, Spinner } from "@/components";
import { errorText } from "@/hooks";

interface AcceptedOrg {
  slug: string;
  name: string;
}

export function AcceptInvitePage() {
  const { user, loading } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const [error, setError] = useState("");
  const started = useRef(false);

  useEffect(() => {
    if (!user || !token || started.current) return;
    started.current = true;

    void api
      .post<AcceptedOrg>("/orgs/invitations/accept", { token })
      .then((org) => navigate(`/app/orgs/${org.slug}`, { replace: true }))
      .catch((reason: unknown) => setError(errorText(reason)));
  }, [navigate, token, user]);

  if (loading) return <Spinner />;
  if (!token) {
    return (
      <div className="page container narrow">
        <div className="card">
          <h1>Приглашение недействительно</h1>
          <p className="muted">В ссылке отсутствует токен приглашения.</p>
        </div>
      </div>
    );
  }
  if (!user) {
    const from = `/accept-invite?token=${encodeURIComponent(token)}`;
    return <Navigate to="/login" state={{ from }} replace />;
  }
  if (error) {
    return (
      <div className="page container narrow">
        <div className="card">
          <h1>Не удалось принять приглашение</h1>
          <Alert kind="error">{error}</Alert>
        </div>
      </div>
    );
  }
  return <Spinner />;
}
