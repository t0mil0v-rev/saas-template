// Административный контур платформы. Виден только суперпользователю; сервер
// всё равно перепроверяет права на каждом запросе.

import { useState } from "react";
import { api, type Paged } from "@/api";
import { Alert, Spinner } from "@/components";
import { useFetch, errorText } from "@/hooks";

interface Stats {
  users_total: number;
  users_active: number;
  orgs_total: number;
  reviews_total: number;
  reviews_pending: number;
}

interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  totp_enabled: boolean;
  last_login_at: string | null;
  orgs_count: number;
}

export function AdminPage() {
  const stats = useFetch<Stats>((s) => api.get("/admin/stats", s), []);
  return (
    <div className="page container">
      <h1>Администрирование</h1>
      {stats.loading ? (
        <Spinner />
      ) : stats.data ? (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
          <Stat label="Пользователей" value={stats.data.users_total} />
          <Stat label="Активных" value={stats.data.users_active} />
          <Stat label="Организаций" value={stats.data.orgs_total} />
          <Stat label="Отзывов" value={stats.data.reviews_total} />
          <Stat label="Ждут модерации" value={stats.data.reviews_pending} />
        </div>
      ) : null}
      <Users />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card" style={{ textAlign: "center" }}>
      <div style={{ fontSize: 28, fontWeight: 700 }}>{value}</div>
      <div className="muted">{label}</div>
    </div>
  );
}

function Users() {
  const [q, setQ] = useState("");
  const users = useFetch<Paged<AdminUser>>(
    (s) => api.get(`/admin/users?q=${encodeURIComponent(q)}&limit=50`, s),
    [q],
  );
  const [err, setErr] = useState("");

  async function toggle(u: AdminUser) {
    setErr("");
    try {
      await api.post(`/admin/users/${u.id}/${u.is_active ? "deactivate" : "activate"}`);
      users.reload();
    } catch (e) {
      setErr(errorText(e));
    }
  }

  return (
    <div className="card overflow" style={{ marginTop: 20 }}>
      <div className="row between" style={{ marginBottom: 12 }}>
        <h2 style={{ fontSize: 17, margin: 0 }}>Пользователи</h2>
        <input
          placeholder="Поиск по e-mail"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ maxWidth: 260 }}
        />
      </div>
      <Alert kind="error">{err}</Alert>
      <table>
        <thead>
          <tr>
            <th>E-mail</th>
            <th>Организаций</th>
            <th>2FA</th>
            <th>Вход</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {users.data?.items.map((u) => (
            <tr key={u.id}>
              <td>
                {u.email}
                {u.is_superuser && <span className="badge" style={{ marginLeft: 6 }}>admin</span>}
              </td>
              <td>{u.orgs_count}</td>
              <td>{u.totp_enabled ? "✓" : "-"}</td>
              <td>{u.last_login_at ? new Date(u.last_login_at).toLocaleDateString("ru-RU") : "-"}</td>
              <td>
                <span className={`badge badge-${u.is_active ? "approved" : "rejected"}`}>
                  {u.is_active ? "активен" : "отключён"}
                </span>
              </td>
              <td>
                <button className="btn-ghost btn-sm" onClick={() => toggle(u)}>
                  {u.is_active ? "Отключить" : "Включить"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
