import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { Alert, Spinner } from "@/components";
import { errorText } from "@/hooks";

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [msg, setMsg] = useState("");
  // StrictMode в dev монтирует компонент дважды; токен одноразовый, поэтому
  // страхуемся от повторной отправки того же запроса.
  const sent = useRef(false);

  useEffect(() => {
    if (!token) {
      setState("error");
      setMsg("Ссылка не содержит токен подтверждения.");
      return;
    }
    if (sent.current) return;
    sent.current = true;
    api
      .post("/auth/verify-email", { token })
      .then(() => setState("ok"))
      .catch((e) => {
        setState("error");
        setMsg(errorText(e));
      });
  }, [token]);

  if (state === "loading") return <Spinner />;

  return (
    <div className="page container narrow">
      <div className="card">
        <h1>Подтверждение адреса</h1>
        {state === "ok" ? (
          <>
            <Alert kind="success">Адрес подтверждён. Теперь можно войти.</Alert>
            <Link to="/login" className="btn" style={{ width: "100%" }}>
              Войти
            </Link>
          </>
        ) : (
          <>
            <Alert kind="error">{msg}</Alert>
            <Link to="/login" className="muted">
              Вернуться ко входу
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
