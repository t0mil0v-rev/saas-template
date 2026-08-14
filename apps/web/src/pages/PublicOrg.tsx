// Публичная страница организации: карточка, опубликованные отзывы и форма.
// Доступна без входа. Форма содержит honeypot-поле `website`, скрытое CSS.

import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError, type Paged, type Review, type ReviewStats } from "@/api";
import { Alert, Spinner, Stars } from "@/components";
import { useFetch, errorText } from "@/hooks";

interface PublicOrg {
  slug: string;
  name: string;
  description: string;
  reviews_count: number;
  average_rating: number;
}

export function PublicOrgPage() {
  const { slug = "" } = useParams();

  const org = useFetch<PublicOrg>((s) => api.get(`/public/orgs/${slug}`, s), [slug]);
  const stats = useFetch<ReviewStats>((s) => api.get(`/public/orgs/${slug}/reviews/stats`, s), [slug]);
  const reviews = useFetch<Paged<Review>>(
    (s) => api.get(`/public/orgs/${slug}/reviews?limit=50`, s),
    [slug],
  );

  if (org.loading) return <Spinner />;
  if (org.error || !org.data) {
    return (
      <div className="page container narrow">
        <div className="card">
          <h1>Страница не найдена</h1>
          <p className="muted">Организация не существует или скрыта владельцем.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page container">
      <div className="card">
        <div className="row between">
          <div>
            <h1>{org.data.name}</h1>
            {org.data.description && <p className="muted">{org.data.description}</p>}
          </div>
          {stats.data && stats.data.total > 0 && (
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 32, fontWeight: 700 }}>{stats.data.average.toFixed(1)}</div>
              <Stars value={stats.data.average} />
              <div className="muted">{stats.data.total} отзывов</div>
            </div>
          )}
        </div>
      </div>

      <div className="grid" style={{ marginTop: 20, gridTemplateColumns: "1.4fr 1fr" }}>
        <div>
          <h2>Отзывы</h2>
          {reviews.loading && <p className="muted">Загрузка отзывов…</p>}
          {reviews.data?.items.length === 0 && (
            <div className="card">
              <p className="muted" style={{ margin: 0 }}>
                Пока нет опубликованных отзывов. Будьте первым!
              </p>
            </div>
          )}
          {reviews.data?.items.map((r) => (
            <div className="card" key={r.id}>
              <div className="row between">
                <strong>{r.author_name}</strong>
                <Stars value={r.rating} />
              </div>
              {r.title && <div style={{ fontWeight: 600, marginTop: 6 }}>{r.title}</div>}
              <p style={{ marginBottom: 0 }}>{r.body}</p>
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                {new Date(r.created_at).toLocaleDateString("ru-RU")}
              </div>
            </div>
          ))}
        </div>

        <ReviewForm slug={slug} onSubmitted={() => reviews.reload()} />
      </div>
    </div>
  );
}

function ReviewForm({ slug, onSubmitted }: { slug: string; onSubmitted: () => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [rating, setRating] = useState(5);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const res = await api.post<{ detail: string }>(`/public/orgs/${slug}/reviews`, {
        author_name: name,
        author_email: email || null,
        rating,
        title,
        body,
        website,
      });
      setMsg(res.detail);
      setName("");
      setEmail("");
      setTitle("");
      setBody("");
      setRating(5);
      onSubmitted();
    } catch (e2) {
      if (e2 instanceof ApiError && e2.status === 429) {
        setErr("Слишком много отзывов за короткое время. Попробуйте позже.");
      } else {
        setErr(errorText(e2));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ alignSelf: "start", position: "sticky", top: 78 }}>
      <h2 style={{ fontSize: 17 }}>Оставить отзыв</h2>
      <Alert kind="success">{msg}</Alert>
      <Alert kind="error">{err}</Alert>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="rv-name">Ваше имя</label>
          <input id="rv-name" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={80} />
        </div>
        <div className="field">
          <label htmlFor="rv-email">E-mail (не публикуется)</label>
          <input id="rv-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={320} />
        </div>
        <div className="field">
          <label htmlFor="rv-rating">Оценка</label>
          <select id="rv-rating" value={rating} onChange={(e) => setRating(Number(e.target.value))}>
            {[5, 4, 3, 2, 1].map((n) => (
              <option key={n} value={n}>
                {"★".repeat(n)} ({n})
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="rv-title">Заголовок</label>
          <input id="rv-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
        </div>
        <div className="field">
          <label htmlFor="rv-body">Отзыв</label>
          <textarea id="rv-body" value={body} onChange={(e) => setBody(e.target.value)} required minLength={10} maxLength={5000} rows={4} />
        </div>
        {/* honeypot: скрыт от людей, заполняется ботами */}
        <div className="honeypot" aria-hidden="true">
          <label htmlFor="website">Не заполняйте это поле</label>
          <input id="website" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
        </div>
        <button type="submit" disabled={busy}>
          {busy ? "Отправка…" : "Отправить отзыв"}
        </button>
      </form>
    </div>
  );
}
