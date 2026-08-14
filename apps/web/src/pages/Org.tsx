// Страница организации для участника: вкладки «Модерация», «Участники»,
// «Настройки». Права проверяет и фронт (скрывает элементы), и сервер
// (отклоняет запрос) - фронтовая проверка только для удобства, не защита.

import { useState, type FormEvent, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { api, type Paged, type ReviewAdmin } from "@/api";
import { Alert, Spinner, StatusBadge, Stars } from "@/components";
import { useFetch, errorText } from "@/hooks";

interface OrgDetail {
  id: string;
  slug: string;
  name: string;
  description: string;
  plan: string;
  is_public: boolean;
  auto_approve_reviews: boolean;
}

interface Member {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  joined_at: string;
}

type Tab = "moderation" | "members" | "settings";

export function OrgPage() {
  const { slug = "" } = useParams();
  const org = useFetch<OrgDetail>((s) => api.get(`/orgs/${slug}`, s), [slug]);
  const [tab, setTab] = useState<Tab>("moderation");

  if (org.loading) return <Spinner />;
  if (org.error || !org.data) {
    return (
      <div className="page container">
        <Alert kind="error">{org.error || "Организация не найдена"}</Alert>
      </div>
    );
  }

  return (
    <div className="page container">
      <div className="row between">
        <div>
          <h1>{org.data.name}</h1>
          <p className="muted">
            /{org.data.slug} · публичная страница:{" "}
            <a href={`/o/${org.data.slug}`} target="_blank" rel="noreferrer">
              открыть
            </a>
          </p>
        </div>
      </div>

      <div className="row" style={{ gap: 8, margin: "16px 0" }}>
        <TabButton active={tab === "moderation"} onClick={() => setTab("moderation")}>
          Модерация
        </TabButton>
        <TabButton active={tab === "members"} onClick={() => setTab("members")}>
          Участники
        </TabButton>
        <TabButton active={tab === "settings"} onClick={() => setTab("settings")}>
          Настройки
        </TabButton>
      </div>

      {tab === "moderation" && <Moderation slug={slug} />}
      {tab === "members" && <Members slug={slug} />}
      {tab === "settings" && <SettingsTab org={org.data} onSaved={org.reload} />}
    </div>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button className={active ? "btn-sm" : "btn-ghost btn-sm"} onClick={onClick}>
      {children}
    </button>
  );
}

function Moderation({ slug }: { slug: string }) {
  const [filter, setFilter] = useState("pending");
  const q = filter ? `?status=${filter}&limit=50` : "?limit=50";
  const reviews = useFetch<Paged<ReviewAdmin>>((s) => api.get(`/orgs/${slug}/reviews${q}`, s), [slug, filter]);
  const [err, setErr] = useState("");

  async function moderate(id: string, status: string) {
    setErr("");
    try {
      await api.post(`/orgs/${slug}/reviews/${id}/moderate`, { status, note: "" });
      reviews.reload();
    } catch (e) {
      setErr(errorText(e));
    }
  }

  return (
    <div>
      <div className="row" style={{ gap: 8, marginBottom: 16 }}>
        {["pending", "approved", "rejected", "spam", ""].map((f) => (
          <button key={f || "all"} className={filter === f ? "btn-sm" : "btn-ghost btn-sm"} onClick={() => setFilter(f)}>
            {f === "" ? "Все" : { pending: "На модерации", approved: "Опубликованные", rejected: "Отклонённые", spam: "Спам" }[f]}
          </button>
        ))}
      </div>
      <Alert kind="error">{err}</Alert>
      {reviews.loading ? (
        <Spinner />
      ) : reviews.data?.items.length === 0 ? (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>Отзывов в этой категории нет.</p>
        </div>
      ) : (
        reviews.data?.items.map((r) => (
          <div className="card" key={r.id}>
            <div className="row between">
              <div className="row" style={{ gap: 10 }}>
                <strong>{r.author_name}</strong>
                <Stars value={r.rating} />
                <StatusBadge status={r.status} />
              </div>
              <span className="muted" style={{ fontSize: 12 }}>
                {new Date(r.created_at).toLocaleString("ru-RU")}
              </span>
            </div>
            {r.title && <div style={{ fontWeight: 600, marginTop: 6 }}>{r.title}</div>}
            <p>{r.body}</p>
            {r.author_email && <p className="muted" style={{ fontSize: 12 }}>Контакт: {r.author_email}</p>}
            <div className="row" style={{ gap: 8 }}>
              {r.status !== "approved" && (
                <button className="btn-sm" onClick={() => moderate(r.id, "approved")}>Опубликовать</button>
              )}
              {r.status !== "rejected" && (
                <button className="btn-ghost btn-sm" onClick={() => moderate(r.id, "rejected")}>Отклонить</button>
              )}
              {r.status !== "spam" && (
                <button className="btn-danger btn-sm" onClick={() => moderate(r.id, "spam")}>Спам</button>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function Members({ slug }: { slug: string }) {
  const members = useFetch<Member[]>((s) => api.get(`/orgs/${slug}/members`, s), [slug]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function invite(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    try {
      await api.post(`/orgs/${slug}/invitations`, { email, role });
      setMsg(`Приглашение отправлено на ${email}`);
      setEmail("");
    } catch (e2) {
      setErr(errorText(e2));
    }
  }

  async function changeRole(userId: string, newRole: string) {
    setErr("");
    try {
      await api.patch(`/orgs/${slug}/members/${userId}`, { role: newRole });
      members.reload();
    } catch (e) {
      setErr(errorText(e));
    }
  }

  async function remove(userId: string) {
    setErr("");
    try {
      await api.del(`/orgs/${slug}/members/${userId}`);
      members.reload();
    } catch (e) {
      setErr(errorText(e));
    }
  }

  return (
    <div>
      <div className="card">
        <h2 style={{ fontSize: 17 }}>Пригласить участника</h2>
        <Alert kind="success">{msg}</Alert>
        <Alert kind="error">{err}</Alert>
        <form onSubmit={invite} className="row" style={{ gap: 12, alignItems: "flex-end" }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label htmlFor="inv-email">E-mail</label>
            <input id="inv-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="inv-role">Роль</label>
            <select id="inv-role" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="member">member</option>
              <option value="admin">admin</option>
              <option value="owner">owner</option>
            </select>
          </div>
          <button type="submit">Пригласить</button>
        </form>
      </div>

      <div className="card overflow">
        <table>
          <thead>
            <tr>
              <th>E-mail</th>
              <th>Имя</th>
              <th>Роль</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {members.data?.map((m) => (
              <tr key={m.user_id}>
                <td>{m.email}</td>
                <td>{m.full_name || "-"}</td>
                <td>
                  <select value={m.role} onChange={(e) => changeRole(m.user_id, e.target.value)} style={{ width: "auto" }}>
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                    <option value="owner">owner</option>
                  </select>
                </td>
                <td>
                  <button className="btn-danger btn-sm" onClick={() => remove(m.user_id)}>
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SettingsTab({ org, onSaved }: { org: OrgDetail; onSaved: () => void }) {
  const [name, setName] = useState(org.name);
  const [description, setDescription] = useState(org.description);
  const [isPublic, setIsPublic] = useState(org.is_public);
  const [autoApprove, setAutoApprove] = useState(org.auto_approve_reviews);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function save(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    try {
      await api.patch(`/orgs/${org.slug}`, {
        name,
        description,
        is_public: isPublic,
        auto_approve_reviews: autoApprove,
      });
      setMsg("Сохранено");
      onSaved();
    } catch (e2) {
      setErr(errorText(e2));
    }
  }

  return (
    <div className="card">
      <Alert kind="success">{msg}</Alert>
      <Alert kind="error">{err}</Alert>
      <form onSubmit={save}>
        <div className="field">
          <label htmlFor="s-name">Название</label>
          <input id="s-name" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={120} />
        </div>
        <div className="field">
          <label htmlFor="s-desc">Описание</label>
          <textarea id="s-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} maxLength={2000} />
        </div>
        <div className="field row" style={{ gap: 10 }}>
          <input id="s-public" type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} style={{ width: "auto" }} />
          <label htmlFor="s-public" style={{ margin: 0 }}>Показывать публичную страницу с отзывами</label>
        </div>
        <div className="field row" style={{ gap: 10 }}>
          <input id="s-auto" type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} style={{ width: "auto" }} />
          <label htmlFor="s-auto" style={{ margin: 0 }}>Публиковать отзывы без ручной модерации</label>
        </div>
        <button type="submit">Сохранить</button>
      </form>
    </div>
  );
}
