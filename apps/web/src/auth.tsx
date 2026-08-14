// Контекст аутентификации.
//
// Источник правды о пользователе - сервер: при загрузке приложения делаем
// GET /auth/me. Никакого хранения токенов в localStorage - сессия живёт в
// HttpOnly-cookie, недоступной JavaScript, и это правильно: украсть её
// через XSS нельзя.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError, type User } from "@/api";

interface AuthState {
  user: User | null;
  loading: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string, totp?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setUser(await api.get<User>("/auth/me"));
    } catch (err) {
      // 401 здесь - норма: пользователь просто не вошёл.
      if (!(err instanceof ApiError) || err.status !== 401) {
        console.error("Не удалось получить профиль", err);
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (email: string, password: string, totp?: string) => {
      const res = await api.post<{ user: User }>("/auth/login", {
        email,
        password,
        totp_code: totp ?? "",
      });
      setUser(res.user);
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, refresh, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth вне AuthProvider");
  return ctx;
}
