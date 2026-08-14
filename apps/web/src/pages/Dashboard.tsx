import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type OrgSummary } from "@/api";
import { useAuth } from "@/auth";
import { Alert, Spinner } from "@/components";
import { useFetch, errorText } from "@/hooks";

export function DashboardPage() {
  const { user } = useAuth();
  const orgs = useFetch<OrgSummary[]>((s) => api.get("/orgs", s), []);
  const [creating, setCreating] = useState(false);

  return (
    <div className="page container">
      <div className="row between">
        <div>
          <h1>Здравствуйте, {user?.full_name || user?.email}</h1>
          <p className="muted">Ваши организации и их состояние.</p>
        </div>
        <button onClick={() => setCreating((v) => !v)}>
          {creating ? "Отмена" : "Создать организацию"}
        </button>
      </div>

      {user && !user.email_verified_at && (
        <Alert kind="error">
          Адрес не подтверждён - часть возможностей ограничена. Проверьте почту.
        </Alert>
      )}

      {creating && <CreateOrgForm onCreated={() => { setCreating(false); orgs.reload(); }} />}

      {orgs.loading ? (
        <Spinner />
      ) : orgs.error ? (
        <Alert kind="error">{orgs.error}</Alert>
      ) : orgs.data && orgs.data.length > 0 ? (
        <div className="grid" style={{ marginTop: 20 }}>
          {orgs.data.map((org) => (
            <Link to={`/app/orgs/${org.slug}`} className="card" key={org.id} style={{ color: "inherit" }}>
              <div className="row between">
                <h2 style={{ fontSize: 17, margin: 0 }}>{org.name}</h2>
                <span className="badge">{org.role}</span>
              </div>
              <p className="muted" style={{ margin: "8px 0 0" }}>
                /{org.slug} · {org.members_count} участн. · план {org.plan}
              </p>
              {org.pending_reviews > 0 && (
                <p style={{ margin: "10px 0 0", fontSize: 14 }}>
                  <span className="badge badge-pending">
                    {org.pending_reviews} отзывов ждут модерации
                  </span>
                </p>
              )}
            </Link>
          ))}
        </div>
      ) : (
        <div className="card" style={{ marginTop: 20 }}>
          <p className="muted" style={{ margin: 0 }}>
            У вас пока нет организаций. Создайте первую - станете её владельцем.
          </p>
        </div>
      )}
    </div>
  );
}

function CreateOrgForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  function autoSlug(value: string) {
    setName(value);
    if (!slug || slug === slugify(name)) setSlug(slugify(value));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await api.post("/orgs", { name, slug, description: "" });
      onCreated();
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ fontSize: 17 }}>Новая организация</h2>
      <Alert kind="error">{err}</Alert>
      <form onSubmit={submit}>
        <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label htmlFor="co-name">Название</label>
            <input id="co-name" value={name} onChange={(e) => autoSlug(e.target.value)} required minLength={2} maxLength={120} />
          </div>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label htmlFor="co-slug">Слаг (в адресе)</label>
            <input id="co-slug" value={slug} onChange={(e) => setSlug(slugify(e.target.value))} required minLength={3} maxLength={64} pattern="[a-z0-9-]+" />
          </div>
        </div>
        <button type="submit" disabled={busy} style={{ marginTop: 16 }}>
          {busy ? "Создание…" : "Создать"}
        </button>
      </form>
    </div>
  );
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}
