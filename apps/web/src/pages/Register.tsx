import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api";
import { Alert } from "@/components";
import { errorText } from "@/hooks";

export function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [done, setDone] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const res = await api.post<{ detail: string }>("/auth/register", {
        email,
        password,
        full_name: fullName,
        org_name: orgName,
      });
      setDone(res.detail);
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="page container narrow">
        <div className="card">
          <h1>Почти готово</h1>
          <Alert kind="success">{done}</Alert>
          <Link to="/login" className="btn" style={{ width: "100%" }}>
            Перейти ко входу
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="page container narrow">
      <div className="card">
        <h1>Регистрация</h1>
        <Alert kind="error">{err}</Alert>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="r-name">Имя</label>
            <input id="r-name" value={fullName} onChange={(e) => setFullName(e.target.value)} maxLength={120} />
          </div>
          <div className="field">
            <label htmlFor="r-email">E-mail</label>
            <input id="r-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="r-password">Пароль</label>
            <input id="r-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={12} />
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Не короче 12 символов. Лучше длинная фраза, чем короткий набор знаков.
            </div>
          </div>
          <div className="field">
            <label htmlFor="r-org">Название организации (необязательно)</label>
            <input id="r-org" value={orgName} onChange={(e) => setOrgName(e.target.value)} maxLength={120} />
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Если указать - будет создана организация, а вы станете её владельцем.
            </div>
          </div>
          <button type="submit" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Создание…" : "Зарегистрироваться"}
          </button>
        </form>
        <p className="muted" style={{ marginTop: 16, textAlign: "center" }}>
          Уже есть аккаунт? <Link to="/login">Войти</Link>
        </p>
      </div>
    </div>
  );
}
