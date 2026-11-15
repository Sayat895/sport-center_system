import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, ArrowRight, Bell, CalendarDays, Check, ChevronDown, Clock3, Download,
  Dumbbell, FileSpreadsheet, LogOut, Menu, Plus, ShieldCheck, Waves, X, Zap,
} from "lucide-react";

const API = "/api";
const sports = ["Все направления", "Тренажерный зал", "Бассейн", "Бокс", "Йога", "Кроссфит"];
const iconBySport = { "Тренажерный зал": Dumbbell, "Бассейн": Waves, "Бокс": Activity, "Йога": Activity, "Кроссфит": Zap };
const localDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

function SelectField({ value, options, onChange, className = "", placeholder = "Выберите" }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const selected = options.find((option) => String(option.value) === String(value));

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsideClick = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [open]);

  return <div ref={rootRef} className={`select-root ${open ? "select-root-open" : ""} ${className}`} onKeyDown={(event) => event.key === "Escape" && setOpen(false)}>
    <button type="button" className={`field select-trigger ${open ? "select-trigger-open" : ""}`} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
      <span className={selected ? "" : "select-placeholder"}>{selected?.label || placeholder}</span><ChevronDown size={16} className={`select-chevron ${open ? "select-chevron-open" : ""}`} />
    </button>
    {open && <div className="select-menu" role="listbox">
      {options.map((option) => <button key={option.value} type="button" role="option" aria-selected={String(option.value) === String(value)} className={`select-option ${String(option.value) === String(value) ? "select-option-active" : ""}`} onClick={() => { onChange(option.value); setOpen(false); }}>
        <span>{option.label}</span>{String(option.value) === String(value) && <Check size={15} />}
      </button>)}
      {!options.length && <span className="select-empty">Нет вариантов</span>}
    </div>}
  </div>;
}

function ReportTable({ columns, rows, emptyMessage }) {
  return <div className="report-table-scroll">
    <table className="report-table">
      <thead><tr>{columns.map((column) => <th key={column.key} className={column.numeric ? "text-right" : ""}>{column.label}</th>)}</tr></thead>
      <tbody>{rows.length ? rows.map((row, index) => <tr key={row.id || `${row.date || row.name || row.title || "row"}-${index}`}>
        {columns.map((column) => <td key={column.key} className={column.numeric ? "text-right tabular-nums" : ""}>{column.render ? column.render(row[column.key], row) : row[column.key]}</td>)}
      </tr>) : <tr><td className="report-empty" colSpan={columns.length}>{emptyMessage}</td></tr>}</tbody>
    </table>
  </div>;
}

function App() {
  const [token, setToken] = useState(localStorage.getItem("sport-token") || "");
  const [user, setUser] = useState(null);
  const [workouts, setWorkouts] = useState([]);
  const [bookings, setBookings] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [filter, setFilter] = useState("Все направления");
  const [tab, setTab] = useState("schedule");
  const [toast, setToast] = useState("");
  const [loginOpen, setLoginOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [report, setReport] = useState(null);
  const [googleUnavailable, setGoogleUnavailable] = useState(false);
  const googleButtonRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [users, setUsers] = useState([]);
  const [adminBookings, setAdminBookings] = useState([]);
  const [trainers, setTrainers] = useState([]);
  const [loginForm, setLoginForm] = useState({ email: "", password: "" });
  const [trainingForm, setTrainingForm] = useState({ title: "Новая тренировка", sport: "Йога", trainer_id: "", date: localDate(new Date()), start_time: "18:00", duration_minutes: 60, capacity: 12, difficulty: "Средний" });
  const [savingUser, setSavingUser] = useState(null);

  const request = useCallback(async (path, options = {}) => {
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || "Ошибка сервера");
    }
    return response;
  }, [token]);

  const notify = useCallback((message) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3400);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const workoutResponse = await request("/workouts");
      setWorkouts(await workoutResponse.json());
      if (token) {
        const [bookingResponse, notificationResponse] = await Promise.all([request("/bookings/mine"), request("/notifications")]);
        setBookings(await bookingResponse.json());
        setNotifications(await notificationResponse.json());
      } else {
        setBookings([]);
        setNotifications([]);
      }
    } catch (error) {
      notify(error.message || "Не удалось загрузить данные");
    }
  }, [request, token]);

  useEffect(() => {
    if (!token) { setUser(null); return; }
    request("/auth/me").then((response) => response.json()).then(setUser).catch((error) => {
      localStorage.removeItem("sport-token"); setToken(""); notify(error.message);
    });
  }, [request, token]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (user?.role === "admin") {
      Promise.all([request("/admin/users"), request("/trainers"), request("/admin/bookings")])
        .then(async ([usersResponse, trainersResponse, bookingsResponse]) => {
          setUsers(await usersResponse.json());
          const available = await trainersResponse.json();
          setTrainers(available);
          setAdminBookings(await bookingsResponse.json());
          if (available[0]) setTrainingForm((current) => ({ ...current, trainer_id: current.trainer_id || String(available[0].id) }));
        })
        .catch((error) => notify(error.message));
    }
  }, [request, user]);

  const days = useMemo(() => {
    const result = [];
    for (let offset = 0; offset < 7; offset += 1) {
      const d = new Date(); d.setDate(d.getDate() + offset);
      result.push({ iso: localDate(d), label: offset === 0 ? "Сегодня" : offset === 1 ? "Завтра" : d.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" }) });
    }
    return result;
  }, []);

  const visibleWorkouts = workouts.filter((item) => filter === "Все направления" || item.sport === filter);

  async function login(event, demoRole) {
    event?.preventDefault();
    const credentials = demoRole === "admin"
      ? { email: "admin@sport.kz", password: "Admin123!" }
      : demoRole === "client" ? { email: "client@sport.kz", password: "Client123!" } : loginForm;
    try {
      const response = await fetch(`${API}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(credentials) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Не удалось войти");
      localStorage.setItem("sport-token", body.access_token);
      setToken(body.access_token); setUser(body.user); setLoginOpen(false);
      notify(`С возвращением, ${body.user.full_name.split(" ")[0]}!`);
    } catch (error) { notify(error.message); }
  }

  const loginWithGoogle = useCallback(async (credential) => {
    try {
      const response = await fetch(`${API}/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Не удалось войти через Google");
      localStorage.setItem("sport-token", body.access_token);
      setToken(body.access_token); setUser(body.user); setLoginOpen(false);
      notify(`Добро пожаловать, ${body.user.full_name.split(" ")[0]}!`);
    } catch (error) { notify(error.message); }
  }, [notify]);

  useEffect(() => {
    if (!loginOpen || !googleClientId || !googleButtonRef.current) return undefined;
    let active = true;
    const renderGoogleButton = () => {
      if (!active || !window.google?.accounts?.id || !googleButtonRef.current) return;
      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: (response) => loginWithGoogle(response.credential),
      });
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        theme: "filled_black", size: "large", shape: "pill", text: "continue_with", width: 350, locale: "ru",
      });
      setGoogleUnavailable(false);
    };
    if (window.google?.accounts?.id) {
      renderGoogleButton();
    } else {
      let script = document.querySelector('script[src="https://accounts.google.com/gsi/client"]');
      if (!script) {
        script = document.createElement("script");
        script.src = "https://accounts.google.com/gsi/client";
        script.async = true;
        script.defer = true;
        script.onload = renderGoogleButton;
        script.onerror = () => active && setGoogleUnavailable(true);
        document.head.appendChild(script);
      } else {
        script.addEventListener("load", renderGoogleButton, { once: true });
        script.addEventListener("error", () => active && setGoogleUnavailable(true), { once: true });
      }
    }
    return () => { active = false; };
  }, [loginOpen, loginWithGoogle]);

  async function book(item) {
    if (!user) { setLoginOpen(true); return; }
    try { await request(`/workouts/${item.id}/book`, { method: "POST" }); notify("Вы записаны на тренировку!"); await refresh(); }
    catch (error) { notify(error.message); }
  }

  async function cancel(bookingId) {
    try {
      await request(`/bookings/${bookingId}`, { method: "DELETE" });
      notify("Запись отменена");
      await refresh();
      if (user?.role === "admin") setAdminBookings(await (await request("/admin/bookings")).json());
    }
    catch (error) { notify(error.message); }
  }

  async function saveUser(person) {
    try {
      const response = await request(`/admin/users/${person.id}`, { method: "PATCH", body: JSON.stringify({ full_name: person.full_name, phone: person.phone, membership: person.membership, role: person.role }) });
      const updated = await response.json();
      setUsers((current) => current.map((item) => item.id === updated.id ? updated : item));
      notify("Данные пользователя сохранены");
      setSavingUser(null);
    } catch (error) { notify(error.message); }
  }

  async function addWorkout(event) {
    event.preventDefault();
    try {
      await request("/admin/workouts", { method: "POST", body: JSON.stringify({ ...trainingForm, trainer_id: Number(trainingForm.trainer_id), duration_minutes: Number(trainingForm.duration_minutes), capacity: Number(trainingForm.capacity) }) });
      notify("Тренировка добавлена в расписание"); await refresh();
    } catch (error) { notify(error.message); }
  }

  async function viewReport() {
    try {
      const response = await request("/admin/report");
      setReport(await response.json());
      setReportOpen(true);
    } catch (error) { notify(error.message); }
  }

  async function downloadReport(format = "xlsx") {
    try {
      const extension = format === "csv" ? "csv" : "xlsx";
      const response = await request(`/admin/report.${extension}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `sport-center-report.${extension}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify(extension === "csv" ? "CSV-отчет сформирован" : "Оформленный Excel-отчет сформирован");
    } catch (error) { notify(error.message); }
  }

  function logout() {
    localStorage.removeItem("sport-token"); setToken(""); setUser(null); setTab("schedule"); notify("Вы вышли из аккаунта");
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-slate-100">
      <header className="sticky top-0 z-30 border-b border-white/5 bg-zinc-950/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-7">
          <button className="flex items-center gap-3" onClick={() => setTab("schedule")} aria-label="На главную">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-lime-400 text-zinc-950"><Activity size={23} strokeWidth={2.7} /></span>
            <span className="text-left"><span className="block font-black tracking-tight">SPORT<span className="text-lime-400">CLUB</span></span><span className="hidden text-[10px] uppercase tracking-[.22em] text-zinc-500 sm:block">Твой ритм. Твоя сила.</span></span>
          </button>
          <nav className="hidden items-center gap-2 md:flex">
            <button onClick={() => setTab("schedule")} className={`nav-link ${tab === "schedule" ? "nav-active" : ""}`}>Расписание</button>
            {user && <button onClick={() => setTab("account")} className={`nav-link ${tab === "account" ? "nav-active" : ""}`}>Мой кабинет</button>}
            {user?.role === "admin" && <button onClick={() => setTab("admin")} className={`nav-link ${tab === "admin" ? "nav-active" : ""}`}><ShieldCheck size={15} />Админ-панель</button>}
          </nav>
          <div className="flex items-center gap-3">
            {user ? <><div className="hidden text-right sm:block"><div className="text-sm font-semibold">{user.full_name}</div><div className="text-[11px] text-lime-400">{user.role === "admin" ? "Администратор" : user.membership}</div></div><button className="icon-btn" onClick={logout} title="Выйти"><LogOut size={18} /></button></> :
              <button onClick={() => setLoginOpen(true)} className="rounded-xl bg-lime-400 px-4 py-2.5 text-sm font-bold text-zinc-950 transition hover:bg-lime-300">Войти <ArrowRight className="ml-1 inline" size={15} /></button>}
            <button className="icon-btn md:hidden" onClick={() => setMenuOpen(!menuOpen)} aria-label="Меню"><Menu size={20} /></button>
          </div>
        </div>
        {menuOpen && <div className="flex flex-col border-t border-white/5 p-3 md:hidden"><button className="mobile-nav" onClick={() => { setTab("schedule"); setMenuOpen(false); }}>Расписание</button>{user && <button className="mobile-nav" onClick={() => { setTab("account"); setMenuOpen(false); }}>Мой кабинет</button>}{user?.role === "admin" && <button className="mobile-nav" onClick={() => { setTab("admin"); setMenuOpen(false); }}>Админ-панель</button>}</div>}
      </header>

      <main className="mx-auto max-w-7xl px-4 pb-20 pt-7 sm:px-7 sm:pt-12">
        {tab === "schedule" && <>
          <section className="relative mb-10 overflow-hidden rounded-3xl border border-white/5 bg-gradient-to-br from-zinc-900 via-zinc-900 to-lime-950/40 p-6 sm:p-10">
            <div className="hero-glow" />
            <div className="relative max-w-2xl">
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-lime-400/20 bg-lime-400/10 px-3 py-1 text-xs font-semibold text-lime-300"><span className="live-dot" />ОТКРОЙ СВОЙ ПОТЕНЦИАЛ</div>
              <h1 className="text-4xl font-black leading-[1.05] tracking-tight sm:text-6xl">Тренируйся<br />в своём <span className="text-lime-400">ритме.</span></h1>
              <p className="mt-4 max-w-md text-sm leading-6 text-zinc-400 sm:text-base">Выбирай занятие, бронируй место и двигайся к цели каждый день. Твоя следующая тренировка уже здесь.</p>
              <button className="mt-6 rounded-xl bg-lime-400 px-5 py-3 text-sm font-bold text-zinc-950 transition hover:scale-[1.02] hover:bg-lime-300" onClick={() => document.getElementById("schedule")?.scrollIntoView({ behavior: "smooth" })}>Выбрать тренировку <ArrowRight className="ml-2 inline" size={16} /></button>
            </div>
            <div className="hero-mark"><Dumbbell size={180} strokeWidth={1} /></div>
            <div className="absolute bottom-5 right-6 hidden gap-6 text-right sm:flex"><div><div className="text-xl font-black">5</div><div className="text-[10px] uppercase tracking-wider text-zinc-500">направлений</div></div><div><div className="text-xl font-black">15+</div><div className="text-[10px] uppercase tracking-wider text-zinc-500">занятий</div></div></div>
          </section>
          <section id="schedule">
            <div className="mb-6 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
              <div><p className="section-kicker">НАЙДИ СВОЮ ТРЕНИРОВКУ</p><h2 className="mt-1 text-2xl font-extrabold tracking-tight sm:text-3xl">Расписание занятий</h2></div>
              <div className="flex items-center gap-2 text-sm text-zinc-400"><CalendarDays size={16} className="text-lime-400" />Ближайшие 7 дней</div>
            </div>
            <div className="mb-5 flex gap-2 overflow-x-auto pb-2">{sports.map((sport) => <button key={sport} onClick={() => setFilter(sport)} className={`filter-chip ${filter === sport ? "filter-active" : ""}`}>{sport}</button>)}</div>
            {workouts.length === 0 && token ? <div className="panel p-8 text-center text-zinc-400">Загружаем расписание…</div> : null}
            {!token && <div className="panel mb-5 flex items-center justify-between gap-3 p-4 text-sm text-zinc-400"><span>Войдите, чтобы записываться и управлять бронированиями.</span><button className="text-lime-400" onClick={() => setLoginOpen(true)}>Войти →</button></div>}
            <div className="space-y-8">{days.map((day) => {
              const items = visibleWorkouts.filter((item) => item.date === day.iso);
              if (!items.length) return null;
              return <div key={day.iso}><div className="mb-3 flex items-center gap-3"><h3 className="font-bold">{day.label}</h3><span className="h-px flex-1 bg-white/5" /><span className="text-xs text-zinc-600">{items.length} {items.length === 1 ? "занятие" : "занятия"}</span></div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{items.map((item) => {
                  const SportIcon = iconBySport[item.sport] || Activity;
                  const percent = Math.min(100, Math.round(item.booked_count / item.capacity * 100));
                  return <article className="workout-card group" key={item.id}>
                    <div className="flex items-start justify-between"><div className="flex items-center gap-3"><span className="sport-icon"><SportIcon size={20} /></span><div><div className="font-bold">{item.sport}</div><div className="mt-0.5 text-xs text-zinc-500">{item.trainer}</div></div></div><span className="difficulty">{item.difficulty}</span></div>
                    <p className="mt-4 text-sm font-semibold text-zinc-300">{item.title}</p>
                    <div className="mt-3 flex items-center gap-4 text-xs text-zinc-400"><span className="flex items-center gap-1.5"><Clock3 size={14} className="text-lime-400" />{item.start_time} · {item.duration_minutes} мин</span><span>{item.date.split("-").reverse().join(".")}</span></div>
                    <div className="mt-4"><div className="mb-1.5 flex justify-between text-[11px] text-zinc-500"><span>Заполненность группы</span><span>{item.booked_count}/{item.capacity} мест</span></div><div className="h-1.5 overflow-hidden rounded-full bg-zinc-800"><div className={`h-full rounded-full transition-all ${percent > 80 ? "bg-blue-500" : "bg-lime-400"}`} style={{ width: `${percent}%` }} /></div></div>
                    <button disabled={item.booked_by_me || item.booked_count >= item.capacity} onClick={() => book(item)} className={`mt-4 flex w-full items-center justify-center gap-2 rounded-xl py-2.5 text-sm font-bold transition ${item.booked_by_me ? "border border-lime-400/20 bg-lime-400/10 text-lime-300" : item.booked_count >= item.capacity ? "bg-zinc-800 text-zinc-500" : "bg-white/5 text-white hover:bg-lime-400 hover:text-zinc-950"}`}>{item.booked_by_me ? <><Check size={16} />Вы записаны</> : item.booked_count >= item.capacity ? "Мест нет" : "Записаться"}</button>
                  </article>;
                })}</div>
              </div>;
            })}</div>
            {visibleWorkouts.length === 0 && <div className="panel p-8 text-center text-sm text-zinc-500">На эту неделю занятий по выбранному направлению нет.</div>}
          </section>
        </>}

        {tab === "account" && <section><div className="mb-7"><p className="section-kicker">ЛИЧНОЕ ПРОСТРАНСТВО</p><h1 className="mt-1 text-3xl font-black">Мой кабинет</h1></div><div className="grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
          <div className="panel p-5 sm:p-7"><h2 className="mb-4 flex items-center gap-2 text-lg font-bold"><CalendarDays className="text-lime-400" size={19} />Мои тренировки</h2>{bookings.length ? <div className="client-booking-list">{bookings.map((item) => <div key={item.id} className="client-booking-row"><span className="client-booking-sport">{item.sport}</span><span className="client-booking-meta">{item.date.split("-").reverse().join(".")} · {item.start_time} · {item.trainer}</span><button className="client-booking-cancel" onClick={() => cancel(item.id)}>Отменить запись</button></div>)}</div> : <p className="empty-state">Пока нет активных записей. Выберите тренировку в расписании.</p>}</div>
          <div className="panel p-5 sm:p-7"><h2 className="mb-4 flex items-center gap-2 text-lg font-bold"><Bell className="text-lime-400" size={19} />Уведомления <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400">{notifications.length}</span></h2>{notifications.length ? <div className="notification-list">{notifications.map((item) => <div key={item.id} className="notification-row"><p>{item.message}</p><time>{new Date(item.created_at).toLocaleString("ru-RU")}</time></div>)}</div> : <p className="empty-state">Важные сообщения появятся здесь.</p>}</div>
        </div></section>}

        {tab === "admin" && user?.role === "admin" && <section>
          <div className="mb-7 flex flex-wrap items-end justify-between gap-4"><div><p className="section-kicker">УПРАВЛЕНИЕ ЦЕНТРОМ</p><h1 className="mt-1 text-3xl font-black">Админ-панель</h1></div><div className="flex flex-wrap gap-2"><button onClick={viewReport} className="rounded-xl border border-white/10 bg-zinc-900 px-4 py-2.5 text-sm font-bold text-white transition hover:border-lime-400/40 hover:text-lime-300"><Activity className="mr-2 inline" size={16} />Посмотреть отчет</button><button onClick={() => downloadReport("xlsx")} className="rounded-xl bg-lime-400 px-4 py-2.5 text-sm font-bold text-zinc-950 transition hover:bg-lime-300"><FileSpreadsheet className="mr-2 inline" size={16} />Скачать Excel</button><button onClick={() => downloadReport("csv")} className="rounded-xl bg-blue-500 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-blue-400"><Download className="mr-2 inline" size={16} />CSV</button></div></div>
          <div className="mb-6 grid gap-3 sm:grid-cols-3"><div className="stat-card"><span>Пользователи</span><strong>{users.length}</strong><span className="text-lime-400">в системе</span></div><div className="stat-card"><span>Тренировки</span><strong>{workouts.length}</strong><span className="text-blue-400">в расписании</span></div><div className="stat-card"><span>Активные записи</span><strong>{adminBookings.length}</strong><span className="text-lime-400">всего</span></div></div>
          <div className="mb-6 panel p-5 sm:p-6"><h2 className="mb-4 flex items-center gap-2 font-bold"><Plus size={18} className="text-lime-400" />Добавить тренировку</h2><form onSubmit={addWorkout} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <input className="field" placeholder="Название" value={trainingForm.title} onChange={(e) => setTrainingForm({ ...trainingForm, title: e.target.value })} required />
            <SelectField className="training-select" value={trainingForm.sport} onChange={(value) => setTrainingForm({ ...trainingForm, sport: value })} options={sports.slice(1).map((sport) => ({ value: sport, label: sport }))} placeholder="Выберите направление" />
            <SelectField className="training-select" value={trainingForm.trainer_id} onChange={(value) => setTrainingForm({ ...trainingForm, trainer_id: value })} options={trainers.map((trainer) => ({ value: String(trainer.id), label: trainer.full_name }))} placeholder="Выберите тренера" />
            <input className="field" type="date" value={trainingForm.date} onChange={(e) => setTrainingForm({ ...trainingForm, date: e.target.value })} required />
            <input className="field" type="time" value={trainingForm.start_time} onChange={(e) => setTrainingForm({ ...trainingForm, start_time: e.target.value })} required />
            <input className="field" type="number" min="1" max="100" placeholder="Мест" value={trainingForm.capacity} onChange={(e) => setTrainingForm({ ...trainingForm, capacity: e.target.value })} required />
            <SelectField className="training-select" value={trainingForm.difficulty} onChange={(value) => setTrainingForm({ ...trainingForm, difficulty: value })} options={["Начальный", "Средний", "Продвинутый"].map((level) => ({ value: level, label: level }))} placeholder="Сложность" />
            <button className="rounded-xl bg-lime-400 px-4 py-2 text-sm font-bold text-zinc-950 hover:bg-lime-300">Добавить в расписание</button>
          </form></div>
          <div className="panel overflow-hidden"><div className="flex items-center justify-between border-b border-white/5 p-5"><h2 className="font-bold">Пользователи</h2><span className="text-xs text-zinc-500">{users.length} записей</span></div><div className="overflow-x-auto"><table className="admin-table w-full min-w-[740px] text-left text-sm"><thead className="text-xs uppercase tracking-wider text-zinc-600"><tr><th className="px-5 py-3">ФИО / Email</th><th className="px-3 py-3">Телефон</th><th className="px-3 py-3">Абонемент</th><th className="px-3 py-3">Роль</th><th className="px-4 py-3">Действие</th></tr></thead><tbody>{users.map((person) => <tr key={person.id} className="border-t border-white/5"><td className="px-5 py-3"><div className="font-semibold">{savingUser?.id === person.id ? <input className="field w-40 py-1" value={savingUser.full_name} onChange={(e) => setSavingUser({ ...savingUser, full_name: e.target.value })} /> : person.full_name}</div><div className="text-xs text-zinc-500">{person.email}</div></td><td className="px-3 py-3 text-zinc-400">{savingUser?.id === person.id ? <input className="field w-32 py-1" value={savingUser.phone} onChange={(e) => setSavingUser({ ...savingUser, phone: e.target.value })} /> : person.phone}</td><td className="px-3 py-3"><select className="field py-1" value={savingUser?.id === person.id ? savingUser.membership : person.membership} onChange={(e) => savingUser?.id === person.id ? setSavingUser({ ...savingUser, membership: e.target.value }) : setSavingUser({ ...person, membership: e.target.value })}><option>Стандарт</option><option>Премиум</option><option>Утренний</option></select></td><td className="px-3 py-3"><select className="field py-1" value={savingUser?.id === person.id ? savingUser.role : person.role} onChange={(e) => setSavingUser({ ...(savingUser?.id === person.id ? savingUser : person), role: e.target.value })}><option value="client">Клиент</option><option value="admin">Админ</option></select></td><td className="px-4 py-3"><button className="rounded-lg px-2 py-1 text-xs font-semibold text-lime-400 transition hover:bg-lime-400/10 hover:text-lime-300" onClick={() => savingUser?.id === person.id ? saveUser(savingUser) : setSavingUser({ ...person })}>{savingUser?.id === person.id ? "Сохранить" : "Изменить"}</button></td></tr>)}</tbody></table></div></div>
          <div className="mt-6 panel p-5"><div className="mb-4 flex items-center justify-between"><h2 className="font-bold">Записи клиентов — отмена администратором</h2><span className="rounded-full bg-zinc-800 px-2.5 py-1 text-xs text-zinc-400">{adminBookings.length} записей</span></div><div className="grid gap-3 md:grid-cols-2">{adminBookings.map((booking) => <div key={booking.id} className="booking-list-row"><span className="booking-date">{booking.date.split("-").reverse().join(".")} · {booking.start_time}</span><span className="booking-sport">{booking.sport}</span><span className="booking-name">{booking.user_name}</span><span className="booking-title">{booking.workout_title}</span><button className="booking-cancel" onClick={() => cancel(booking.id)}>Отменить</button></div>)}</div>{adminBookings.length === 0 && <p className="empty-state">Активных записей нет.</p>}<p className="mt-3 text-xs text-zinc-600">Клиент получит уведомление в личном кабинете после отмены.</p></div>
        </section>}
      </main>
      <footer className="border-t border-white/5 px-4 py-6 text-center text-xs text-zinc-600">© {new Date().getFullYear()} SPORTCLUB · Стань сильнее сегодня</footer>

      {reportOpen && report && <div className="modal-backdrop report-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setReportOpen(false)}>
        <section className="report-dialog" role="dialog" aria-modal="true" aria-labelledby="report-title">
          <header className="report-header"><div><p className="section-kicker">АНАЛИТИКА СПОРТЦЕНТРА</p><h2 id="report-title" className="mt-1 text-2xl font-black">Отчет по расписанию и записям</h2><p className="mt-1 text-xs text-zinc-500">Сформирован: {new Date(report.generated_at).toLocaleString("ru-RU")}</p></div><button className="icon-btn" onClick={() => setReportOpen(false)} aria-label="Закрыть отчет"><X size={18} /></button></header>
          <div className="report-content">
            <div className="report-summary">
              <div className="report-stat"><span>Активных записей</span><strong>{report.summary.total_bookings}</strong></div>
              <div className="report-stat"><span>Клиентов</span><strong>{report.summary.total_clients}</strong></div>
              <div className="report-stat"><span>Тренеров</span><strong>{report.summary.total_trainers}</strong></div>
              <div className="report-stat"><span>Предстоящих занятий</span><strong>{report.summary.total_workouts}</strong></div>
            </div>
            <section className="report-section"><div className="report-section-title"><div><h3>Загрузка по тренерам</h3><p>Количество занятий, записей и оставшихся мест</p></div><span>{report.trainers.length} тренеров</span></div>
              <ReportTable columns={[{ key: "name", label: "Тренер" }, { key: "training_count", label: "Занятий", numeric: true }, { key: "bookings", label: "Записей", numeric: true }, { key: "capacity", label: "Всего мест", numeric: true }, { key: "available", label: "Свободно", numeric: true }]} rows={report.trainers} emptyMessage="Данных о тренерах пока нет." />
            </section>
            <section className="report-section"><div className="report-section-title"><div><h3>Популярность направлений</h3><p>Спрос на занятия и заполненность групп</p></div><span>{report.sports.length} направлений</span></div>
              <ReportTable columns={[{ key: "name", label: "Направление" }, { key: "training_count", label: "Занятий", numeric: true }, { key: "bookings", label: "Записей", numeric: true }, { key: "capacity", label: "Всего мест", numeric: true }, { key: "available", label: "Свободно", numeric: true }]} rows={report.sports} emptyMessage="Направлений пока нет." />
            </section>
            <section className="report-section"><div className="report-section-title"><div><h3>Подробное расписание</h3><p>Свободные места и тренер для каждого занятия</p></div><span>{report.schedule.length} занятий</span></div>
              <ReportTable columns={[{ key: "date", label: "Дата", render: (value) => value.split("-").reverse().join(".") }, { key: "time", label: "Время" }, { key: "sport", label: "Направление" }, { key: "title", label: "Занятие" }, { key: "trainer", label: "Тренер" }, { key: "booked", label: "Записано", numeric: true }, { key: "capacity", label: "Мест", numeric: true }, { key: "available", label: "Свободно", numeric: true }]} rows={report.schedule} emptyMessage="Расписание пока пустое." />
            </section>
          </div>
          <footer className="report-footer"><button className="report-download-secondary" onClick={() => downloadReport("csv")}><Download size={16} /> Скачать CSV</button><button className="report-download" onClick={() => downloadReport("xlsx")}><FileSpreadsheet size={16} /> Скачать Excel</button></footer>
        </section>
      </div>}

      {loginOpen && <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setLoginOpen(false)}><div className="modal-card">
        <button onClick={() => setLoginOpen(false)} className="icon-btn absolute right-4 top-4" aria-label="Закрыть"><X size={18} /></button>
        <div className="mb-5 grid h-12 w-12 place-items-center rounded-xl bg-lime-400 text-zinc-950"><Activity /></div><h2 className="text-2xl font-black">С возвращением</h2><p className="mb-5 mt-1 text-sm text-zinc-500">Войдите, чтобы выбрать свою тренировку.</p>
        <div className="google-login">
          {googleClientId ? <><div ref={googleButtonRef} className="google-button" /><p>Войти или зарегистрироваться как клиент</p>{googleUnavailable && <p className="google-error">Не удалось загрузить Google Sign-In. Проверьте подключение к интернету.</p>}</> :
            <div className="google-setup"><span className="google-g">G</span><div><strong>Вход через Google</strong><p>Добавьте GOOGLE_CLIENT_ID в .env, чтобы включить вход и регистрацию.</p></div></div>}
        </div>
        <div className="login-divider"><span />или по email<span /></div>
        <form onSubmit={(e) => login(e)} className="space-y-3"><input className="field w-full" type="email" placeholder="Email" value={loginForm.email} onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })} required /><input className="field w-full" type="password" placeholder="Пароль" value={loginForm.password} onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })} required /><button className="w-full rounded-xl bg-lime-400 py-3 font-bold text-zinc-950 hover:bg-lime-300">Войти</button></form>
        <div className="my-4 flex items-center gap-2 text-[10px] uppercase tracking-wider text-zinc-600"><span className="h-px flex-1 bg-zinc-800" />Быстрый демо-вход<span className="h-px flex-1 bg-zinc-800" /></div><div className="grid grid-cols-2 gap-2"><button className="rounded-xl border border-white/10 py-2.5 text-xs font-semibold hover:bg-white/5" onClick={(e) => login(e, "client")}>Войти как клиент</button><button className="rounded-xl border border-white/10 py-2.5 text-xs font-semibold hover:bg-white/5" onClick={(e) => login(e, "admin")}>Войти как админ</button></div>
      </div></div>}
      {toast && <div role="status" className="toast"><span className="grid h-7 w-7 place-items-center rounded-lg bg-lime-400/15 text-lime-300"><Check size={16} /></span>{toast}<button onClick={() => setToast("")} className="ml-2 text-zinc-500"><X size={15} /></button></div>}
    </div>
  );
}

export default App;
