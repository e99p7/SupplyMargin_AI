const state = {
    token: null,
    user: null,
    subscription: null,
    datasets: [],
    runs: [],
    plans: [],
    poller: null
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const esc = v => String(v ?? "").replace(/[&<>'"]/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;"
}[c]));

const money = v => {
    if (v === null || v === undefined || v === "") return "—";
    const value = Number(v);
    if (!Number.isFinite(value)) return "—";
    return new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: "RUB",
        minimumFractionDigits: 2,
        maximumFractionDigits: 4
    }).format(value);
};

const date = v => v
    ? new Intl.DateTimeFormat("ru-RU", {
        dateStyle: "medium",
        timeStyle: "short"
    }).format(new Date(v))
    : "—";

function toast(message, error = false) {
    const el = $("#toast");
    if (!el) {
        if (error) console.error(message);
        else console.info(message);
        return;
    }
    el.textContent = message;
    el.className = `toast show${error ? " error" : ""}`;
    clearTimeout(el.timer);
    el.timer = setTimeout(() => el.className = "toast", 3500);
}

// ==================== API ====================


async function rawApi(path, options = {}) {
    const headers = { ...(options.headers || {}) };

    if (state.token) {
        headers.Authorization = `Bearer ${state.token}`;
    }

    const response = await fetch(path, {
        credentials: "include",
        ...options,
        headers
    });

    // Сначала читаем тело как текст: ответ может быть пустым.
    const text = await response.text();
    let body = null;

    if (text.trim()) {
        const type = response.headers.get("content-type") || "";

        if (type.includes("json")) {
            try {
                body = JSON.parse(text);
            } catch {
                if (response.ok) {
                    throw new Error("Сервер вернул некорректный JSON.");
                }
                body = { detail: text };
            }
        } else {
            body = { detail: text };
        }
    }

    if (!response.ok) {
        const detail = body?.detail;

        throw Object.assign(
            new Error(
                typeof detail === "string"
                    ? detail
                    : detail?.message || `Ошибка ${response.status}`
            ),
            { status: response.status, body }
        );
    }

    // DELETE и другие запросы могут успешно завершиться без тела.
    return body;
}


async function api(path, options = {}, retry = true) {
    try {
        return await rawApi(path, options);
    } catch (error) {
        if (error.status === 401 && retry && state.user) {
            const ok = await refreshSession();

            if (ok) {
                return api(path, options, false);
            }
        }

        throw error;
    }
}

async function refreshSession() {
    try {
        const data = await rawApi("/v1/auth/refresh", {
            method: "POST"
        });

        state.token = data.access_token;
        state.user = data.user;

        return true;
    } catch (_) {
        state.token = null;
        state.user = null;

        return false;
    }
}

// ==================== ТАРИФЫ ====================

function planCard(plan, privateView = false) {
    const featured = plan.code === "business";

    return `
        <article class="plan-card ${featured ? "featured" : ""}">
            <span class="eyebrow">
                ${featured ? "Для роста" : "Для старта"}
            </span>

            <h3>${esc(plan.name_ru)}</h3>

            <div class="plan-price">
                ${money(plan.price_rub)}
                <small>/ 30 дней</small>
            </div>

            <ul>
                <li>${plan.monthly_runs} расчётов в месяц</li>
                <li>До ${plan.max_datasets} таблиц</li>
                <li>Файлы до ${plan.max_upload_mb} МБ</li>
                <li>История, версии и Excel</li>
            </ul>

            <button
                class="button ${featured ? "primary" : "secondary"}"
                data-plan="${plan.code}"
            >
                ${privateView ? "Оплатить тариф" : "Начать работу"}
            </button>
        </article>
    `;
}

function renderPlans() {
    $("#public-plans").innerHTML = state.plans.map(p => planCard(p)).join("");
    $("#private-plans").innerHTML = state.plans.map(p => planCard(p, true)).join("");
}

async function loadPlans() {
    try {
        state.plans = await rawApi("/v1/billing/plans");
        renderPlans();
    } catch (e) {
        console.error(e);
    }
}

// ==================== АВТОРИЗАЦИЯ ====================

function openAuth(mode) {
    const login = mode === "login";

    $("#login-form").classList.toggle("hidden", !login);
    $("#register-form").classList.toggle("hidden", login);

    $("#auth-title").textContent = login ? "Вход" : "Создать аккаунт";

    $("#auth-subtitle").textContent = login
        ? "Продолжите работу с вашими таблицами."
        : "Зарегистрируйтесь и выберите подходящий тариф.";

    $("#auth-dialog").showModal();
}

function setAuthenticated(value) {
    $("#public-page").classList.toggle("hidden", value);
    $("#public-nav").classList.toggle("hidden", value);
    $("#app-page").classList.toggle("hidden", !value);
    $("#private-nav").classList.toggle("hidden", !value);
    $("#site-footer").classList.toggle("hidden", value);

    if (value) {
        $("#header-email").textContent = state.user.email;
    }
}

function showView(name) {
    $$(".view-panel").forEach(el => el.classList.add("hidden"));

    $(`#view-${name}`)?.classList.remove("hidden");

    $$(".side-link").forEach(el => {
        el.classList.toggle("active", el.dataset.view === name);
    });

    if (name === "runs") loadRuns();
    if (name === "datasets") loadDatasets();
    if (name === "billing") loadSubscription();
}

async function bootstrapPrivate() {
    setAuthenticated(true);

    await Promise.all([
        loadSubscription(),
        loadDatasets(),
        loadRuns()
    ]);

    showView("overview");
    startPolling();

    const params = new URLSearchParams(location.search);

    if (params.has("payment")) {
        showView("billing");
        pollPaymentStatus();
    }
}

// ==================== ПОДПИСКА ====================

async function loadSubscription() {
    try {
        state.subscription = await api("/v1/billing/status");

        const s = state.subscription;

        $("#subscription-warning").classList.toggle("hidden", s.active);

        $("#stat-subscription").textContent = s.active
            ? "Активна"
            : "Неактивна";

        $("#stat-subscription-date").textContent = s.current_period_end
            ? `до ${date(s.current_period_end)}`
            : "нужна оплата";

        $("#current-subscription").className =
            `subscription-card ${s.active ? "active" : ""}`;

        $("#current-subscription").innerHTML = s.active
            ? `
                <span class="status active">АКТИВНА</span>
                <h3>Тариф ${esc(s.plan_code)}</h3>
                <p>
                    Доступ действует до ${date(s.current_period_end)}.
                    Все загруженные данные и результаты сохраняются.
                </p>
            `
            : `
                <span class="status">НЕАКТИВНА</span>
                <h3>Выберите тариф</h3>
                <p>
                    После подтверждения оплаты загрузка таблиц
                    и запуск расчётов включатся автоматически.
                </p>
            `;
    } catch (e) {
        toast(e.message, true);
    }
}

// ==================== ТАБЛИЦЫ ====================

async function loadDatasets() {
    try {
        state.datasets = await api("/v1/datasets");

        $("#stat-datasets").textContent = state.datasets.length;

        renderDatasets();
    } catch (e) {
        toast(e.message, true);
    }
}

/* =====================================================
   >>> ИЩИ ЭТУ ФУНКЦИЮ: renderDatasets() <<<
   Она формирует карточки таблиц в личном кабинете.
   Здесь находится кнопка «Удалить».
   ===================================================== */

function renderDatasets() {
    const root = $("#datasets-list");

    if (!state.datasets.length) {
        root.innerHTML = `
            <div class="empty-state">
                <b>Таблиц пока нет</b>
                <span>Загрузите CSV или XLSX, чтобы начать.</span>
            </div>
        `;
        return;
    }

    root.innerHTML = state.datasets.map(d => `
        <article class="data-card">
            <span class="file-icon">
                ${(d.latest_version?.filename || "").toLowerCase().endsWith(".xlsx")
                    ? "XLS"
                    : "CSV"}
            </span>

            <div class="card-main">
                <b>${esc(d.name)}</b>

                <small>
                    Версия ${d.latest_version?.version || 1}
                    · ${d.latest_version?.row_count || 0} строк
                    · ${date(d.latest_version?.created_at)}
                </small>
            </div>

            <span class="status ${esc(d.status)}">
                ${esc(d.status).toUpperCase()}
            </span>

            <div class="card-actions">
                <button
                    class="button secondary"
                    data-dataset-details="${d.id}"
                >
                    Версии
                </button>

                <button
                    class="button primary"
                    data-run-version="${d.latest_version?.id || ""}"
                >
                    Оптимизировать
                </button>

                <button
                    class="button danger"
                    data-delete-dataset="${d.id}"
                >
                    Удалить
                </button>
            </div>
        </article>
    `).join("");
}

// ==================== УДАЛЕНИЕ ТАБЛИЦЫ ====================

async function deleteDataset(id) {
    const dataset = state.datasets.find(d => d.id === id);

    if (!dataset) return;

    const confirmed = confirm(
        `Удалить таблицу «${dataset.name}»?\n\n` +
        "Все загруженные версии этой таблицы также будут удалены."
    );

    if (!confirmed) return;

    try {
        await api(`/v1/datasets/${id}`, {
            method: "DELETE"
        });

        state.datasets = state.datasets.filter(d => d.id !== id);

        $("#stat-datasets").textContent = state.datasets.length;

        renderDatasets();

        toast("Таблица удалена");
    } catch (e) {
        toast(e.message, true);
    }
}


async function deleteRun(id) {
    const run = state.runs.find(r => r.id === id);
    if (!run) return;

    if (["queued", "running"].includes(run.status)) {
        toast("Нельзя удалить расчёт, пока он выполняется.", true);
        return;
    }

    const confirmed = confirm(
        `Удалить расчёт «${run.dataset_name || "Расчёт"}»?\n\n` +
        "Результаты этого расчёта также будут удалены."
    );

    if (!confirmed) return;

    try {
        await api(`/v1/runs/${id}`, { method: "DELETE" });

        state.runs = state.runs.filter(r => r.id !== id);

        $("#stat-runs").textContent = state.runs.length;
        $("#stat-completed").textContent =
            state.runs.filter(r => r.status === "completed").length;

        renderRuns();
        renderRecentRuns();

        toast("Расчёт удалён");
    } catch (e) {
        toast(e.message, true);
    }
}



async function showDataset(id) {
    try {
        const d = await api(`/v1/datasets/${id}`);

        const versions = d.versions
            .map(v =>
                `v${v.version} — ${esc(v.filename)}, ` +
                `${v.row_count} строк, ${date(v.created_at)}`
            )
            .join("\n");

        alert(`${d.name}\n\n${versions}`);
    } catch (e) {
        toast(e.message, true);
    }
}

// ==================== РАСЧЁТЫ ====================

async function loadRuns() {
    try {
        state.runs = await api("/v1/runs");

        $("#stat-runs").textContent = state.runs.length;

        $("#stat-completed").textContent =
            state.runs.filter(r => r.status === "completed").length;

        renderRuns();
        renderRecentRuns();
    } catch (e) {
        toast(e.message, true);
    }
}

function runCard(r) {
    const metric = r.summary
        ? `Прирост: ${money(r.summary.expected_profit_gain)} · ` +
          `${r.summary.validation_available === false || r.summary.validation_wape == null
              ? "Качество прогноза не оценивалось (базовый режим)"
              : `WAPE ${(Number(r.summary.validation_wape) * 100).toFixed(1)}%`}`
        : r.error || "Ожидает обработки";

    return `
        <article class="run-card">
            <span class="file-icon">AI</span>

            <div class="card-main">
                <b>${esc(r.dataset_name || "Расчёт")}</b>
                <small>${date(r.created_at)} · ${esc(metric)}</small>
            </div>

            <span class="status ${esc(r.status)}">
                ${esc(r.status).toUpperCase()}
            </span>

            ${["queued", "running"].includes(r.status) ? (() => {
                const progress = r.progress || {
                    percent: 0,
                    message: r.status === "queued" ? "Ожидание обработчика" : "Расчёт выполняется"
                };
                const percent = Math.max(0, Math.min(99, Number(progress.percent) || 0));
                return `
                    <div class="run-progress" role="status" aria-live="polite">
                        <div class="run-progress-head">
                            <span>${esc(progress.message || "Расчёт выполняется")}</span>
                            <b>${percent}%</b>
                        </div>
                        <div class="run-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">
                            <div class="run-progress-fill" style="width:${percent}%"></div>
                        </div>
                    </div>
                `;
            })() : ""}

            <div class="card-actions">
                ${r.status === "completed" ? `
                    <button class="button secondary" data-result="${r.id}">
                        Открыть
                    </button>
                    <button class="button primary" data-download="${r.id}">
                        Excel
                    </button>
                ` : ""}

                ${r.status === "failed" ? `
                    <button class="button secondary" data-retry="${r.id}">
                        Повторить
                    </button>
                ` : ""}

                ${!["queued", "running"].includes(r.status) ? `
                    <button class="button danger" data-delete-run="${r.id}">
                        Удалить
                    </button>
                ` : ""}
            </div>
        </article>
    `;
}

function renderRuns() {
    const root = $("#runs-list");

    root.innerHTML = state.runs.length
        ? state.runs.map(runCard).join("")
        : `
            <div class="empty-state">
                <b>Расчётов пока нет</b>
                <span>Откройте таблицу и запустите оптимизацию.</span>
            </div>
        `;
}

function renderRecentRuns() {
    const root = $("#recent-runs");
    root.className = "cards-list";

    root.innerHTML = state.runs.length
        ? state.runs.slice(0, 4).map(runCard).join("")
        : `
            <div class="empty-state compact">
                <b>Расчётов пока нет</b>
                <span>Загрузите первую таблицу и запустите оптимизацию.</span>
            </div>
        `;
}

function startPolling() {
    clearInterval(state.poller);

    state.poller = setInterval(() => {
        if (state.runs.some(r => ["queued", "running"].includes(r.status))) {
            loadRuns();
        }
    }, 3500);
}

// ==================== ОПЛАТА ====================

async function pollPaymentStatus() {
    for (let i = 0; i < 20; i++) {
        await new Promise(r => setTimeout(r, 2500));

        await loadSubscription();

        if (state.subscription?.active) {
            toast("Оплата подтверждена. Доступ активирован.");
            history.replaceState({}, "", location.pathname);
            return;
        }
    }

    toast("Платёж ещё обрабатывается. Нажмите «Обновить» через минуту.");
}

async function checkout(plan) {
    if (!state.user) {
        openAuth("register");
        return;
    }

    try {
        const data = await api("/v1/billing/checkout", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ plan_code: plan })
        });

        location.href = data.confirmation_url;
    } catch (e) {
        toast(e.message, true);
    }
}

// ==================== РЕЗУЛЬТАТЫ ====================

async function downloadRun(id) {
    try {
        const response = await fetch(`/v1/runs/${id}/download`, {
            headers: {
                Authorization: `Bearer ${state.token}`
            }
        });

        if (!response.ok) {
            throw new Error("Не удалось скачать файл");
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");

        a.href = url;
        a.download = `supplymargin_${id}.xlsx`;
        a.click();

        URL.revokeObjectURL(url);
    } catch (e) {
        toast(e.message, true);
    }
}

async function showResult(id) {
    try {
        const data = await api(`/v1/runs/${id}/result`);
        const s = data.summary;

        $("#result-summary").innerHTML = `
            <article>
                <small>Прирост прибыли</small>
                <b>${money(s.expected_profit_gain)}</b>
            </article>
            <article>
                <small>SKU</small>
                <b>${s.sku_count}</b>
            </article>
            <article>
                <small>WAPE</small>
                <b>${s.validation_available === false || s.validation_wape == null
                    ? "н/д (базовый прогноз)"
                    : `${(Number(s.validation_wape) * 100).toFixed(1)}%`}</b>
            </article>
            <article>
                <small>Качество данных</small>
                <b>${esc(s.history_quality_grade)}</b>
            </article>
        `;

        $("#result-table").innerHTML = data.recommendations
            .slice(0, 100)
            .map(r => `
                <tr>
                    <td>${esc(r.sku)}</td>
                    <td>${money(r.current_sell_price || r.current_price)}</td>
                    <td>${money(r.recommended_sell_price)}</td>
                    <td>${Math.round(Number(
                        r.recommended_order_qty ||
                        r.recommended_order_quantity || 0
                    ))}</td>
                    <td>${money(r.expected_profit_after)}</td>
                </tr>
            `)
            .join("");

        $("#result-download").dataset.download = id;
        $("#result-dialog").showModal();
    } catch (e) {
        toast(e.message, true);
    }
}

// ==================== ОБРАБОТЧИК КЛИКОВ ====================

document.addEventListener("click", async e => {
    const t = e.target.closest("button, a");

    if (!t) return;

    if (t.dataset.action === "open-login") openAuth("login");
    if (t.dataset.action === "open-register") openAuth("register");

    if (t.dataset.action === "close-dialog") {
        $("#auth-dialog").close();
    }

    if (t.dataset.action === "close-run-dialog") {
        $("#run-dialog").close();
    }

    if (t.dataset.action === "close-result-dialog") {
        $("#result-dialog").close();
    }

    if (t.dataset.action === "logout") {
        await rawApi("/v1/auth/logout", { method: "POST" }).catch(() => {});

        state.token = null;
        state.user = null;

        clearInterval(state.poller);
        setAuthenticated(false);
        toast("Вы вышли из аккаунта");
    }

    if (t.dataset.view) showView(t.dataset.view);
    if (t.dataset.viewTarget) showView(t.dataset.viewTarget);
    if (t.dataset.plan) checkout(t.dataset.plan);

    if (t.dataset.datasetDetails) {
        showDataset(t.dataset.datasetDetails);
    }

    // >>> ОБРАБОТКА КНОПКИ «УДАЛИТЬ» <<<
    if (t.dataset.deleteDataset) {
        await deleteDataset(t.dataset.deleteDataset);
        return;
    }

    if (t.dataset.runVersion) {
        $("#run-version-id").value = t.dataset.runVersion;
        $("#run-dialog").showModal();
    }

    if (t.dataset.result) showResult(t.dataset.result);
    if (t.dataset.download) downloadRun(t.dataset.download);

    if (t.dataset.retry) {
        try {
            await api(`/v1/runs/${t.dataset.retry}/retry`, {
                method: "POST"
            });

            toast("Расчёт снова поставлен в очередь");
            loadRuns();
        } catch (err) {
            toast(err.message, true);
        }
    }
    if (t.dataset.deleteRun) {
        await deleteRun(t.dataset.deleteRun);
        return;
    }


    if (t.dataset.action === "refresh-runs") {
        loadRuns();
    }
});

// ==================== ФОРМЫ ====================

$("#login-form").addEventListener("submit", async e => {
    e.preventDefault();

    const form = new FormData(e.currentTarget);

    try {
        const data = await rawApi("/v1/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(Object.fromEntries(form))
        });

        state.token = data.access_token;
        state.user = data.user;

        $("#auth-dialog").close();
        toast("Добро пожаловать");

        bootstrapPrivate();
    } catch (err) {
        toast(err.message, true);
    }
});

$("#register-form").addEventListener("submit", async e => {
    e.preventDefault();

    const form = new FormData(e.currentTarget);

    try {
        const data = await rawApi("/v1/auth/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(Object.fromEntries(form))
        });

        state.token = data.access_token;
        state.user = data.user;

        $("#auth-dialog").close();
        toast("Аккаунт создан");

        bootstrapPrivate();
    } catch (err) {
        toast(err.message, true);
    }
});

$("#dataset-file").addEventListener("change", e => {
    $("#chosen-file").textContent =
        e.target.files[0]?.name || "Файл не выбран";
});

$("#upload-form").addEventListener("submit", async e => {
    e.preventDefault();
    // currentTarget доступен только во время синхронного вызова обработчика.
    // Сохраняем ссылку на форму до первого await.
    const formElement = e.currentTarget;

    if (!state.subscription?.active) {
        showView("billing");
        toast("Сначала активируйте подписку", true);
        return;
    }

    const file = $("#dataset-file").files[0];

    if (!file) return;

    const body = new FormData();
    body.append("file", file);
    body.append("name", $("#dataset-name").value);

    const button = e.currentTarget.querySelector('button[type="submit"]');

    button.disabled = true;
    button.textContent = "Проверяем…";

    try {
        await api("/v1/datasets", {
            method: "POST",
            body
        });

        toast("Таблица загружена и проверена");

        formElement.reset();
        $("#chosen-file").textContent = "Файл не выбран";

        await loadDatasets();
    } catch (err) {
        toast(err.message, true);
    } finally {
        button.disabled = false;
        button.textContent = "Загрузить и проверить";
    }
});

$("#run-form").addEventListener("submit", async e => {
    e.preventDefault();

    const payload = {
        dataset_version_id: $("#run-version-id").value,
        options: {
            horizon: Number($("#run-horizon").value),
            min_margin: Number($("#run-margin").value),
            max_price_change: Number($("#run-price-change").value),
            service_level: Number($("#run-service-level").value),
            risk_aversion: 0.25,
            random_seed: 42
        }
    };

    try {
        await api("/v1/runs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        $("#run-dialog").close();
        toast("Расчёт поставлен в очередь");
        showView("runs");
    } catch (err) {
        toast(err.message, true);
    }
});

// ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================

(async () => {
    await loadPlans();

    const ok = await refreshSession();

    if (ok) {
        bootstrapPrivate();
    } else {
        setAuthenticated(false);
    }
})();

const style = document.createElement("style");

style.textContent = `
.button.danger {
    background: #dc2626;
    color: #fff;
    border-color: #dc2626;
}

.button.danger:hover {
    background: #b91c1c;
    border-color: #b91c1c;
}
`;

document.head.appendChild(style);