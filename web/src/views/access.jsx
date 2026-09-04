import { useEffect, useState } from "react";
import { KeyRound, Trash2, UserPlus, Users } from "lucide-react";
import { apiUrl } from "../profile-client.js";

const readPayload = async (response) => {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(payload?.error?.message || "La richiesta non e stata completata.");
  return payload;
};

export default function AccessView({ apiBase, compact = false }) {
  const [session, setSession] = useState(null);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [newUser, setNewUser] = useState({ username: "", password: "" });
  const [passwords, setPasswords] = useState({ current: "", next: "", confirm: "" });

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const current = await fetch(apiUrl("/api/auth/session", apiBase)).then(readPayload);
      setSession(current);
      if (current.is_admin) {
        const payload = await fetch(apiUrl("/api/auth/users", apiBase)).then(readPayload);
        setUsers(payload.users || []);
      }
    } catch (cause) {
      setError(cause.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [apiBase]);

  const createUser = async (event) => {
    event.preventDefault();
    setBusy("create");
    setError("");
    setMessage("");
    try {
      const payload = await fetch(apiUrl("/api/auth/users", apiBase), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newUser),
      }).then(readPayload);
      setUsers((current) => [...current, payload.user].sort((a, b) => a.username.localeCompare(b.username)));
      setNewUser({ username: "", password: "" });
      setMessage(`Credenziali create per ${payload.user.username}.`);
    } catch (cause) {
      setError(cause.message);
    } finally {
      setBusy("");
    }
  };

  const changePassword = async (event) => {
    event.preventDefault();
    setError("");
    setMessage("");
    if (passwords.next !== passwords.confirm) {
      setError("Le due nuove password non coincidono.");
      return;
    }
    setBusy("password");
    try {
      await fetch(apiUrl("/api/auth/password", apiBase), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: passwords.current,
          new_password: passwords.next,
        }),
      }).then(readPayload);
      setPasswords({ current: "", next: "", confirm: "" });
      setMessage("Password modificata. Premi 'Accedi con la nuova password' per autenticarti di nuovo.");
    } catch (cause) {
      setError(cause.message);
    } finally {
      setBusy("");
    }
  };

  const deleteUser = async (username) => {
    if (!window.confirm(`Eliminare l'accesso di ${username}?`)) return;
    setBusy(`delete:${username}`);
    setError("");
    setMessage("");
    try {
      await fetch(apiUrl(`/api/auth/users/${encodeURIComponent(username)}`, apiBase), { method: "DELETE" }).then(readPayload);
      setUsers((current) => current.filter((user) => user.username !== username));
      setMessage(`Accesso di ${username} eliminato.`);
    } catch (cause) {
      setError(cause.message);
    } finally {
      setBusy("");
    }
  };

  if (loading) return <p className="muted access-loading">Carico gli account...</p>;

  return (
    <section className={`access-view${compact ? " access-view--compact" : ""}`}>
      {!compact ? (
        <div className="page-head">
          <span className="kicker">Sicurezza</span>
          <h1>Accessi</h1>
          <p>Cambia la tua password e crea credenziali separate per chi collabora con te.</p>
        </div>
      ) : null}
      {error ? <p className="notice notice--stop" role="alert">{error}</p> : null}
      {message ? <p className="notice notice--go" role="status">{message}</p> : null}

      <div className="access-grid">
        <form className="card access-card" onSubmit={changePassword}>
          <div className="access-card-head">
            <span className="access-icon"><KeyRound /></span>
            <div><h2>Cambia password</h2><p>Account: <strong>{session?.username || "locale"}</strong></p></div>
          </div>
          <label className="field">
            <span className="field-label">Password attuale</span>
            <input className="input" type="password" autoComplete="current-password" value={passwords.current} onChange={(event) => setPasswords({ ...passwords, current: event.target.value })} required />
          </label>
          <label className="field">
            <span className="field-label">Nuova password</span>
            <input className="input" type="password" autoComplete="new-password" minLength={10} value={passwords.next} onChange={(event) => setPasswords({ ...passwords, next: event.target.value })} required />
            <span className="field-help">Almeno 10 caratteri.</span>
          </label>
          <label className="field">
            <span className="field-label">Ripeti nuova password</span>
            <input className="input" type="password" autoComplete="new-password" minLength={10} value={passwords.confirm} onChange={(event) => setPasswords({ ...passwords, confirm: event.target.value })} required />
          </label>
          <button className="btn btn--primary" type="submit" disabled={Boolean(busy)}>{busy === "password" ? "Modifica..." : "Modifica password"}</button>
          {message.startsWith("Password modificata") ? <button className="btn" type="button" onClick={() => window.location.reload()}>Accedi con la nuova password</button> : null}
        </form>

        {session?.is_admin ? (
          <form className="card access-card" onSubmit={createUser}>
            <div className="access-card-head">
              <span className="access-icon"><UserPlus /></span>
              <div><h2>Nuova persona</h2><p>Avra un account personale non amministratore.</p></div>
            </div>
            <label className="field">
              <span className="field-label">Nome utente</span>
              <input className="input" type="text" autoComplete="off" minLength={3} maxLength={32} pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,31}" value={newUser.username} onChange={(event) => setNewUser({ ...newUser, username: event.target.value })} required />
            </label>
            <label className="field">
              <span className="field-label">Password iniziale</span>
              <input className="input" type="password" autoComplete="new-password" minLength={10} value={newUser.password} onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} required />
              <span className="field-help">Comunicala alla persona in modo privato; potra poi cambiarla.</span>
            </label>
            <button className="btn btn--primary" type="submit" disabled={Boolean(busy)}>{busy === "create" ? "Creo..." : "Crea credenziali"}</button>
          </form>
        ) : null}
      </div>

      {session?.is_admin ? (
        <section className="card access-users">
          <div className="access-card-head">
            <span className="access-icon"><Users /></span>
            <div><h2>Persone abilitate</h2><p>{users.length} account con accesso al sito.</p></div>
          </div>
          <div className="access-user-list">
            {users.map((user) => (
              <div className="access-user" key={user.username}>
                <span className="access-avatar">{user.username.slice(0, 2).toUpperCase()}</span>
                <span><strong>{user.username}</strong><small>{user.is_admin ? "Amministratore" : "Collaboratore"}</small></span>
                {!user.is_admin ? (
                  <button className="btn btn--danger btn--sm" type="button" disabled={Boolean(busy)} onClick={() => deleteUser(user.username)} aria-label={`Elimina ${user.username}`}><Trash2 size={15} /> Elimina</button>
                ) : <span className="access-admin-badge">Admin</span>}
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}
