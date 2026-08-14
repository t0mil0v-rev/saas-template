import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="page container narrow">
      <div className="card" style={{ textAlign: "center" }}>
        <h1 style={{ fontSize: 48, margin: 0 }}>404</h1>
        <p className="muted">Такой страницы нет.</p>
        <Link to="/" className="btn">
          На главную
        </Link>
      </div>
    </div>
  );
}
