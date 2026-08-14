// Мелкие переиспользуемые представления. Держим отдельно, чтобы страницы
// оставались про логику, а не про разметку.

import { type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth";

const APP_NAME = import.meta.env.VITE_APP_NAME || "SaaS Platform";

export function Stars({ value }: { value: number }) {
  const full = Math.round(value);
  return (
    <span className="stars" aria-label={`${value} из 5`}>
      {"★".repeat(full)}
      {"☆".repeat(5 - full)}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const label: Record<string, string> = {
    pending: "на модерации",
    approved: "опубликован",
    rejected: "отклонён",
    spam: "спам",
  };
  return <span className={`badge badge-${status}`}>{label[status] ?? status}</span>;
}

export function Alert({ kind, children }: { kind: "error" | "success"; children: ReactNode }) {
  if (!children) return null;
  return <div className={`alert alert-${kind}`}>{children}</div>;
}

export function Spinner() {
  return (
    <div className="center">
      <span className="muted">Загрузка…</span>
    </div>
  );
}

export function Nav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function onLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <nav className="nav">
      <div className="container nav-inner">
        <Link to="/" className="brand">
          <span>{APP_NAME}</span>
        </Link>
        <div className="nav-spacer" />
        {user ? (
          <>
            <Link to="/app">Кабинет</Link>
            {user.is_superuser && <Link to="/admin">Админка</Link>}
            <Link to="/app/security">Безопасность</Link>
            <button className="btn-ghost btn-sm" onClick={onLogout}>
              Выйти
            </button>
          </>
        ) : (
          <>
            <Link to="/login">Вход</Link>
            <Link to="/register" className="btn btn-sm">
              Регистрация
            </Link>
          </>
        )}
      </div>
    </nav>
  );
}
