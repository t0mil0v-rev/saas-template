import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "@/api";
import { useAuth } from "@/auth";
import { Alert } from "@/components";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string })?.from ?? "/app";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await login(email, password, needTotp ? totp : undefined);
      navigate(from, { replace: true });
    } catch (e2) {
      if (e2 instanceof ApiError && e2.code === "totp_required") {
        // Первый шаг прошёл - сервер запросил второй фактор.
        setNeedTotp(true);
        setErr("Введите код из приложения-аутентификатора.");
      } else if (e2 instanceof ApiError) {
        setErr(e2.message);
      } else {
        setErr("Не удалось войти. Повторите попытку.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page container narrow">
      <div className="card">
        <h1>Вход</h1>
        <Alert kind="error">{err}</Alert>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="email">E-mail</label>
            <input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="password">Пароль</label>
            <input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {needTotp && (
            <div className="field">
              <label htmlFor="totp">Код двухфакторной аутентификации</label>
              <input id="totp" inputMode="numeric" autoComplete="one-time-code" value={totp} onChange={(e) => setTotp(e.target.value)} placeholder="123456" autoFocus />
            </div>
          )}
          <button type="submit" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Вход…" : "Войти"}
          </button>
        </form>
        <div className="row between" style={{ marginTop: 16 }}>
          <Link to="/forgot-password" className="muted">
            Забыли пароль?
          </Link>
          <Link to="/register" className="muted">
            Создать аккаунт
          </Link>
        </div>
      </div>
    </div>
  );
}
