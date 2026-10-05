// 여름이었다 — 프론트엔드. API 주소는 config.js(빌드 시 API_BASE_URL로 생성)에서 온다.
const API = (window.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const SLOW_MS = 5000; // 이보다 오래 걸리면 콜드스타트 안내
const TIMEOUT_MS = 90000;

const $ = (id) => document.getElementById(id);
const state = { conversationId: null, editingId: null, pending: 0 };

// ---------- 공통 ----------

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const slow = setTimeout(
    () => showNotice("서버가 깨어나는 중입니다. 무료 서버라 첫 요청은 최대 1분 정도 걸릴 수 있어요."),
    SLOW_MS,
  );
  state.pending++;
  try {
    const res = await fetch(API + path, {
      ...options,
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });
    if (res.status === 204) return null;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorMessage(res.status, body));
    return body;
  } catch (err) {
    if (err.name === "AbortError") throw new Error("응답이 너무 늦습니다. 잠시 후 다시 시도해 주세요.");
    if (err instanceof TypeError) throw new Error("서버에 연결할 수 없습니다. 네트워크나 서버 주소를 확인해 주세요.");
    throw err;
  } finally {
    clearTimeout(timeout);
    clearTimeout(slow);
    if (--state.pending === 0) hideNotice();
  }
}

function errorMessage(status, body) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg.replace(/^Value error, /, "");
  if (status === 422) return "입력값을 확인해 주세요.";
  return `요청 실패 (${status})`;
}

function showNotice(text, isError = false) {
  const n = $("notice");
  n.textContent = text;
  n.classList.toggle("error", isError);
  n.hidden = false;
}

function hideNotice() {
  if (!$("notice").classList.contains("error")) $("notice").hidden = true;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c ?? "");
  return node;
}

const ym = (iso) => iso.slice(0, 7);
const fmtTime = (iso) =>
  new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

// ---------- 요약 ----------

async function loadSummary() {
  const box = $("summary");
  try {
    const s = await api("/api/data/summary");
    const decades = Object.entries(s.summer_by_decade);
    const [firstDec, firstVal] = decades[0];
    const [lastDec, lastVal] = decades[decades.length - 1];
    const card = (label, value, note = "") =>
      el("div", { class: "card" },
        el("div", { class: "label" }, label),
        el("div", { class: "value" }, String(value)),
        note ? el("div", { class: "note" }, note) : "");
    box.replaceChildren(
      card("기간", s.period),
      card("레코드", `${s.count}개월`),
      card("평균 불쾌지수", s.average),
      card("최고", s.maximum.value, ym(s.maximum.date)),
      card("최저", s.minimum.value, ym(s.minimum.date)),
      card("최근 12개월 추세", s.trend, s.trend_detail.split("(")[1]?.replace(")", "") ?? ""),
      card("여름 평균", `${firstVal} → ${lastVal}`, `${firstDec} → ${lastDec}`),
    );
  } catch (err) {
    box.replaceChildren(el("p", { class: "muted" }, `요약 없음: ${err.message}`));
  }
}

// ---------- 채팅 ----------

function renderMessage(role, content, extraClass = "") {
  const box = $("messages");
  box.querySelector(".hint")?.remove();
  const node = el("div", { class: `msg ${role} ${extraClass}`.trim() }, content);
  box.append(node);
  box.scrollTop = box.scrollHeight;
  return node;
}

// 답변을 기다리는 동안 채팅창에 . → .. → ... 이 반복되는 말풍선을 띄운다.
function showTyping() {
  const bubble = renderMessage("assistant", ".", "loading");
  bubble.setAttribute("aria-label", "답변 생성 중");
  let n = 1;
  const timer = setInterval(() => {
    n = (n % 3) + 1;
    bubble.textContent = ".".repeat(n);
  }, 400);
  return () => {
    clearInterval(timer);
    bubble.remove();
  };
}

function setChatBusy(busy) {
  $("chat-send").disabled = busy;
  $("chat-input").disabled = busy;
  $("chat-status").textContent = busy ? "답변 생성 중…" : "";
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $("chat-input");
  const message = input.value.trim();
  if (!message) {
    input.focus();
    $("chat-status").textContent = "질문을 입력해 주세요.";
    return;
  }
  renderMessage("user", message);
  input.value = "";
  const stopTyping = showTyping();
  setChatBusy(true);
  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: state.conversationId }),
    });
    stopTyping();
    renderMessage("assistant", res.reply);
    state.conversationId = res.conversation_id;
    loadHistory();
  } catch (err) {
    stopTyping();
    renderMessage("error", err.message, "error");
  } finally {
    setChatBusy(false);
    input.focus();
  }
}

function newConversation() {
  state.conversationId = null;
  $("messages").replaceChildren(el("p", { class: "muted hint" }, "새 대화를 시작하세요."));
  highlightHistory();
  $("chat-input").focus();
}

// ---------- 대화 기록 ----------

async function loadHistory() {
  const list = $("history-list");
  try {
    const items = await api("/api/conversations?limit=50");
    if (!items.length) {
      list.replaceChildren(el("li", { class: "muted small" }, "저장된 대화가 없습니다."));
      return;
    }
    list.replaceChildren(
      ...items.map((c) =>
        el("li", { "data-id": c.id },
          el("button", { class: "open", type: "button", onclick: () => openConversation(c.id) },
            el("span", { class: "title" }, c.title),
            el("span", { class: "meta" }, `${fmtTime(c.updated_at)} · ${c.message_count}개`)),
          el("button", { class: "link danger", type: "button", "aria-label": "대화 삭제", onclick: () => deleteConversation(c.id) }, "삭제"))),
    );
    highlightHistory();
  } catch (err) {
    list.replaceChildren(el("li", { class: "muted small" }, err.message));
  }
}

function highlightHistory() {
  for (const li of $("history-list").children) {
    li.classList.toggle("active", li.dataset.id === state.conversationId);
  }
}

async function openConversation(id) {
  try {
    const conv = await api(`/api/conversations/${encodeURIComponent(id)}`);
    state.conversationId = conv.id;
    $("messages").replaceChildren();
    conv.messages.forEach((m) => renderMessage(m.role, m.content));
    highlightHistory();
  } catch (err) {
    showNotice(err.message, true);
  }
}

async function deleteConversation(id) {
  if (!confirm("이 대화를 삭제할까요?")) return;
  try {
    await api(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (state.conversationId === id) newConversation();
    loadHistory();
  } catch (err) {
    showNotice(err.message, true);
  }
}

// ---------- 데이터 관리 ----------

function dataMessage(text, kind = "") {
  const m = $("data-msg");
  m.textContent = text;
  m.className = `small ${kind}`;
}

async function loadData() {
  const tbody = $("data-rows");
  try {
    const items = await api("/api/data");
    $("data-count").textContent = `${items.length}건`;
    tbody.replaceChildren(
      ...items.reverse().map((d) =>
        el("tr", { "data-id": d.id, class: d.id === state.editingId ? "editing" : "" },
          el("td", {}, ym(d.date)),
          el("td", { class: "num" }, d.value.toFixed(2)),
          el("td", { class: "memo" }, d.memo ?? ""),
          el("td", { class: "actions" },
            el("button", { class: "link", type: "button", onclick: () => startEdit(d) }, "수정"),
            el("button", { class: "link danger", type: "button", onclick: () => deleteData(d.id) }, "삭제")))),
    );
  } catch (err) {
    tbody.replaceChildren(el("tr", {}, el("td", { colspan: "4", class: "muted" }, err.message)));
  }
}

// ---------- 월 입력 규칙: 현재 달 포함 6개월까지, 현재·미래 달은 예측값임을 알린다 ----------

const FORECAST_MONTHS = 6;
const pad2 = (n) => String(n).padStart(2, "0");
const monthOf = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`;
const todayText = () => { const d = new Date(); return `${monthOf(d)}-${pad2(d.getDate())}`; };

function addMonths(ymText, k) {
  const [y, m] = ymText.split("-").map(Number);
  const i = y * 12 + (m - 1) + k;
  return `${Math.floor(i / 12)}-${pad2((i % 12) + 1)}`;
}

function latestAllowedMonth() {
  return addMonths(monthOf(new Date()), FORECAST_MONTHS - 1);
}

// null: 과거 달(안내 없음) / {block}: 입력 불가 / {text}: 예측값 안내
function monthNotice(ymText) {
  const now = monthOf(new Date());
  const limit = latestAllowedMonth();
  if (ymText > limit) return { block: true, text: `현재 달을 포함해 6개월(${limit})까지만 입력할 수 있습니다.` };
  if (ymText === now) return { text: `${ymText}은 아직 끝나지 않은 달입니다(오늘 ${todayText()}). 월 전체 관측값이 아니라 예측값을 입력하시는 건가요?` };
  if (ymText > now) return { text: `${ymText}은 아직 오지 않은 달입니다(오늘 ${todayText()}). 미래 예측값을 입력하시는 건가요?` };
  return null;
}

function showMonthNotice() {
  const v = $("f-date").value;
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(v)) return dataMessage("");
  const n = monthNotice(v);
  dataMessage(n ? n.text : "", n?.block ? "error" : n ? "warn" : "");
}

function startEdit(item) {
  state.editingId = item.id;
  $("f-date").value = ym(item.date);
  $("f-date").disabled = true; // 날짜가 문서 ID라 수정 대상이 아니다
  $("f-value").value = item.value;
  $("f-memo").value = item.memo ?? "";
  $("f-submit").textContent = "수정 저장";
  $("f-cancel").hidden = false;
  const notice = monthNotice(ym(item.date));
  dataMessage(notice ? `${ym(item.date)} 수정 중 — ${notice.text}` : `${ym(item.date)} 수정 중`, notice ? "warn" : "");
  document.querySelectorAll("#data-rows tr").forEach((tr) => tr.classList.toggle("editing", tr.dataset.id === item.id));
  $("f-value").focus();
}

function resetForm() {
  state.editingId = null;
  $("data-form").reset();
  $("f-date").disabled = false;
  $("f-submit").textContent = "추가";
  $("f-cancel").hidden = true;
  document.querySelectorAll("#data-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
}

async function saveData(event) {
  event.preventDefault();
  const value = Number($("f-value").value);
  const memo = $("f-memo").value.trim() || null;
  const editing = state.editingId;
  const month = editing ? ym(editing) : $("f-date").value;
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return dataMessage("월을 YYYY-MM 형식으로 입력해 주세요.", "error");
  if ($("f-value").value === "" || !Number.isFinite(value)) return dataMessage("불쾌지수를 숫자로 입력해 주세요.", "error");
  const notice = monthNotice(month);
  if (notice?.block) return dataMessage(notice.text, "error");
  if (notice && !confirm(notice.text)) return;
  const tag = notice ? " (예측값)" : "";

  $("f-submit").disabled = true;
  try {
    if (editing) {
      await api(`/api/data/${editing}`, { method: "PUT", body: JSON.stringify({ value, memo }) });
      dataMessage(`${month} 수정 완료${tag}`, "ok");
    } else {
      const created = await api("/api/data", {
        method: "POST",
        body: JSON.stringify({ date: `${month}-01`, value, memo }),
      });
      dataMessage(`${ym(created.date)} 추가 완료${tag}`, "ok");
    }
    resetForm();
    await Promise.all([loadData(), loadSummary()]); // 요약도 즉시 갱신
  } catch (err) {
    dataMessage(err.message, "error");
  } finally {
    $("f-submit").disabled = false;
  }
}

async function deleteData(id) {
  if (!confirm(`${ym(id)} 데이터를 삭제할까요?`)) return;
  try {
    await api(`/api/data/${id}`, { method: "DELETE" });
    if (state.editingId === id) resetForm();
    dataMessage(`${ym(id)} 삭제 완료`, "ok");
    await Promise.all([loadData(), loadSummary()]);
  } catch (err) {
    dataMessage(err.message, "error");
  }
}

// ---------- 화면 전환: 소개 ↔ 대시보드 ----------

function showScreen() {
  const inApp = location.hash === "#app";
  $("intro").hidden = inApp;
  $("app").hidden = !inApp;
}

$("start").addEventListener("click", () => { location.hash = "app"; });
window.addEventListener("hashchange", showScreen);
showScreen();

// ---------- 시작 ----------

$("chat-form").addEventListener("submit", sendMessage);
$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
});
$("new-chat").addEventListener("click", newConversation);
$("data-form").addEventListener("submit", saveData);
$("f-date").max = latestAllowedMonth();
$("f-date").addEventListener("change", showMonthNotice);
$("f-date").addEventListener("input", showMonthNotice);
$("f-cancel").addEventListener("click", () => { resetForm(); dataMessage(""); });

loadSummary();
loadHistory();
loadData();
