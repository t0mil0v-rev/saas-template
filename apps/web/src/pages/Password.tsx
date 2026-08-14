import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { Alert } from "@/components";
import { errorText } from "@/hooks";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.post<{ detail: string }>("/auth/password/forgot", { email });
      setDone(res.detail);
    } catch {
      // Ответ сервера намеренно не зависит от существования адреса -
      // показываем нейтральное сообщение в любом случае.
      setDone("Если такой адрес зарегистрирован, на него отправлено письмо с инструкцией.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page container narrow">
      <div className="card">
        <h1>Восстановление пароля</h1>
        {done ? (
          <Alert kind="success">{done}</Alert>
        ) : (
          <form onSubmit={submit}>
            <div className="field">
              <label htmlFor="fp-email">E-mail</label>
              <input id="fp-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <button type="submit" disabled={busy} style={{ width: "100%" }}>
              {busy ? "Отправка…" : "Отправить ссылку"}
            </button>
          </form>
        )}
        <p className="muted" style={{ marginTop: 16, textAlign: "center" }}>
          <Link to="/login">Вернуться ко входу</Link>
        </p>
      </div>
    </div>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await api.post("/auth/password/reset", { token, new_password: password });
      navigate("/login", { replace: true });
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page container narrow">
      <div className="card">
        <h1>Новый пароль</h1>
        {!token && <Alert kind="error">Ссылка не содержит токен. Запросите восстановление заново.</Alert>}
        <Alert kind="error">{err}</Alert>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="rp-password">Новый пароль</label>
            <input id="rp-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={12} disabled={!token} />
          </div>
          <button type="submit" disabled={busy || !token} style={{ width: "100%" }}>
            {busy ? "Сохранение…" : "Сохранить пароль"}
          </button>
        </form>
      </div>
    </div>
  );
}
