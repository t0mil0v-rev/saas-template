// Страница безопасности: смена пароля, управление 2FA, список сессий.

import { useState, type FormEvent } from "react";
import { api } from "@/api";
import { useAuth } from "@/auth";
import { Alert, Spinner } from "@/components";
import { useFetch, errorText } from "@/hooks";

interface SessionRow {
  id: string;
  ip_hash: string;
  user_agent: string;
  created_at: string;
  last_seen_at: string;
  current: boolean;
}

export function SecurityPage() {
  return (
    <div className="page container">
      <h1>Безопасность</h1>
      <ChangePassword />
      <TwoFactor />
      <Sessions />
    </div>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    try {
      await api.post("/auth/password/change", { current_password: current, new_password: next });
      setMsg("Пароль изменён. Остальные сеансы завершены.");
      setCurrent("");
      setNext("");
    } catch (e2) {
      setErr(errorText(e2));
    }
  }

  return (
    <div className="card">
      <h2 style={{ fontSize: 17 }}>Смена пароля</h2>
      <Alert kind="success">{msg}</Alert>
      <Alert kind="error">{err}</Alert>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="cp-cur">Текущий пароль</label>
          <input id="cp-cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="cp-new">Новый пароль</label>
          <input id="cp-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={12} />
        </div>
        <button type="submit">Изменить пароль</button>
      </form>
    </div>
  );
}

function TwoFactor() {
  const { user, refresh } = useAuth();
  const status = useFetch<{ enabled: boolean; recovery_codes_left: number }>(
    (s) => api.get("/auth/2fa/status", s),
    [user?.totp_enabled],
  );
  const [secret, setSecret] = useState("");
  const [uri, setUri] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [password, setPassword] = useState("");
  const [recoveryPassword, setRecoveryPassword] = useState("");
  const [recoveryTotp, setRecoveryTotp] = useState("");
  const [err, setErr] = useState("");

  async function setup() {
    setErr("");
    try {
      const res = await api.post<{ secret: string; provisioning_uri: string }>("/auth/2fa/setup");
      setSecret(res.secret);
      setUri(res.provisioning_uri);
    } catch (e) {
      setErr(errorText(e));
    }
  }

  async function enable(e: FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      const res = await api.post<{ codes: string[] }>("/auth/2fa/enable", { code });
      setCodes(res.codes);
      setSecret("");
      setUri("");
      setCode("");
      await refresh();
      status.reload();
    } catch (e2) {
      setErr(errorText(e2));
    }
  }

  async function disable(e: FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      await api.post("/auth/2fa/disable", { password });
      setPassword("");
      await refresh();
      status.reload();
    } catch (e2) {
      setErr(errorText(e2));
    }
  }

  async function regenerateCodes(e: FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      const res = await api.post<{ codes: string[] }>("/auth/2fa/recovery-codes", {
        password: recoveryPassword,
        totp_code: recoveryTotp,
      });
      setCodes(res.codes);
      setRecoveryPassword("");
      setRecoveryTotp("");
      status.reload();
    } catch (e) {
      setErr(errorText(e));
    }
  }

  return (
    <div className="card">
      <h2 style={{ fontSize: 17 }}>Двухфакторная аутентификация</h2>
      <Alert kind="error">{err}</Alert>

      {codes.length > 0 && (
        <Alert kind="success">
          <div>
            <strong>Сохраните резервные коды.</strong> Показываются один раз. Каждый работает единожды.
            <pre style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>{codes.join("   ")}</pre>
          </div>
        </Alert>
      )}

      {status.loading ? (
        <p className="muted">Загрузка…</p>
      ) : status.data?.enabled ? (
        <>
          <p className="muted">
            2FA включена. Резервных кодов осталось: {status.data.recovery_codes_left}.
          </p>
          <form onSubmit={disable} className="row" style={{ gap: 12, alignItems: "flex-end" }}>
            <div className="field" style={{ flex: 1, marginBottom: 0 }}>
              <label htmlFor="tf-pass">Пароль для отключения</label>
              <input id="tf-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            <button type="submit" className="btn-danger">Отключить 2FA</button>
          </form>
          <form onSubmit={regenerateCodes} style={{ marginTop: 20 }}>
            <h3 style={{ fontSize: 15 }}>Перевыпустить резервные коды</h3>
            <p className="muted">Старые коды перестанут работать. Подтвердите пароль и код из приложения.</p>
            <div className="row" style={{ gap: 12, alignItems: "flex-end" }}>
              <div className="field" style={{ flex: 1, marginBottom: 0 }}>
                <label htmlFor="rc-pass">Пароль</label>
                <input id="rc-pass" type="password" autoComplete="current-password" value={recoveryPassword} onChange={(e) => setRecoveryPassword(e.target.value)} required />
              </div>
              <div className="field" style={{ flex: 1, marginBottom: 0 }}>
                <label htmlFor="rc-totp">Код 2FA</label>
                <input id="rc-totp" inputMode="numeric" autoComplete="one-time-code" value={recoveryTotp} onChange={(e) => setRecoveryTotp(e.target.value)} required />
              </div>
              <button type="submit" className="btn-ghost">Перевыпустить</button>
            </div>
          </form>
        </>
      ) : uri ? (
        <>
          <p className="muted">
            Добавьте ключ в приложение-аутентификатор. Секрет для ручного ввода:
          </p>
          <code style={{ display: "block", padding: 10, background: "var(--surface-2)", borderRadius: 6, wordBreak: "break-all" }}>
            {secret}
          </code>
          <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            otpauth-ссылка (для генерации QR на вашей стороне): <br />
            <code style={{ wordBreak: "break-all" }}>{uri}</code>
          </p>
          <form onSubmit={enable} className="row" style={{ gap: 12, alignItems: "flex-end", marginTop: 12 }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <label htmlFor="tf-code">Код из приложения</label>
              <input id="tf-code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" required />
            </div>
            <button type="submit">Подтвердить и включить</button>
          </form>
        </>
      ) : (
        <>
          <p className="muted">Дополнительный код при входе делает угон пароля бесполезным.</p>
          <button onClick={setup}>Настроить 2FA</button>
        </>
      )}
    </div>
  );
}

function Sessions() {
  const sessions = useFetch<SessionRow[]>((s) => api.get("/auth/sessions", s), []);

  async function revoke(id: string) {
    await api.del(`/auth/sessions/${id}`);
    sessions.reload();
  }

  if (sessions.loading) return <Spinner />;

  return (
    <div className="card overflow">
      <h2 style={{ fontSize: 17 }}>Активные сеансы</h2>
      <table>
        <thead>
          <tr>
            <th>Устройство</th>
            <th>Начат</th>
            <th>Активность</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {sessions.data?.map((s) => (
            <tr key={s.id}>
              <td>
                {s.current && <span className="badge badge-approved" style={{ marginRight: 8 }}>текущий</span>}
                {s.user_agent || "неизвестно"}
              </td>
              <td>{new Date(s.created_at).toLocaleDateString("ru-RU")}</td>
              <td>{new Date(s.last_seen_at).toLocaleString("ru-RU")}</td>
              <td>
                {!s.current && (
                  <button className="btn-ghost btn-sm" onClick={() => revoke(s.id)}>
                    Завершить
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
