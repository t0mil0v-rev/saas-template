import { Link } from "react-router-dom";

const capabilities = [
  {
    number: "01",
    title: "Auth и безопасность",
    text: "Argon2id, серверные сессии, CSRF, TOTP, recovery-коды, ротация секретов и защита от перебора.",
    scope: "auth / sessions / 2fa",
  },
  {
    number: "02",
    title: "Организации и роли",
    text: "Приглашения, RBAC и транзакционные ограничения защищают границы тенантов и инварианты владельца.",
    scope: "tenants / rbac / audit",
  },
  {
    number: "03",
    title: "Почтовый контур",
    text: "Транзакционный outbox, lease и повторные попытки отделяют бизнес-операцию от доступности SMTP.",
    scope: "outbox / smtp / retry",
  },
  {
    number: "04",
    title: "Запуск и проверка",
    text: "Prefork supervisor, healthchecks, метрики, backup/restore, TLS, hardening и пороговые тесты.",
    scope: "runtime / backup / ci",
  },
];

export function LandingPage() {
  return (
    <div className="technical-landing">
      <section className="technical-hero">
        <div className="hero-primary">
          <div className="hero-content">
            <h1 className="rise rise-1">SaaS Platform</h1>
            <p className="technical-lead rise rise-2">
              Серверный SaaS-шаблон
              <br className="mobile-break" />
              с&nbsp;ролями и&nbsp;организациями
              <br />
              и&nbsp;production-контуром,
              <br className="mobile-break" />
              который можно проверить.
            </p>
            <div className="technical-actions rise rise-3">
              <div className="primary-action">
                <Link to="/register" className="btn btn-primary-tech">
                  <span className="btn-fill" aria-hidden="true" />
                  <span className="btn-label">Создать рабочее пространство</span>
                  <ArrowIcon />
                </Link>
                <span className="action-note">Локально, без привязки к&nbsp;облаку</span>
              </div>
              <a href="#capabilities" className="btn btn-outline-tech">
                Посмотреть состав
              </a>
            </div>
          </div>
        </div>

        <aside className="technical-index" aria-labelledby="index-title">
          <div className="technical-index-content">
            <div className="index-heading">
              <h2 id="index-title">Контур поставки</h2>
              <span>v1.0</span>
            </div>
            <dl>
              <IndexRow term="API" value="FastAPI / PostgreSQL" />
              <IndexRow term="Web" value="React / TypeScript" />
              <IndexRow term="Security" value="Sessions / CSRF / TOTP" />
              <IndexRow term="Runtime" value="Docker / systemd" />
              <IndexRow term="Validation" value="Unit / browser / restore" />
              <IndexRow term="Статус" value="Академически выверенный шаблон" />
            </dl>
          </div>
        </aside>
      </section>

      <section id="capabilities" className="capabilities technical-capabilities">
        <header className="capabilities-heading">
          <h2>Инженерный контур</h2>
          <p>
            Базовые решения согласованы между API, браузером, базой данных
            и&nbsp;эксплуатацией. Каждый слой можно проверить отдельно.
          </p>
        </header>

        <div className="capability-list">
          {capabilities.map((capability) => (
            <article className="capability-row" key={capability.number}>
              <span className="capability-number">{capability.number}</span>
              <h3>{capability.title}</h3>
              <p>{capability.text}</p>
              <code>{capability.scope}</code>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

function IndexRow({ term, value }: { term: string; value: string }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function ArrowIcon() {
  return (
    <svg className="btn-arrow" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 10h11M11 6l4 4-4 4" />
    </svg>
  );
}
